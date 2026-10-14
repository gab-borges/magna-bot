import os
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from google.genai import errors, types

from cogs.gemini import DEFAULT_MODEL, PERSONA, Gemini, split_message


class Typing:
    async def __aenter__(self):
        return None

    async def __aexit__(self, *exc):
        return False


def make_ctx():
    return SimpleNamespace(send=AsyncMock(), typing=Typing, interaction=None)


def make_client(response=None, error=None):
    generate = AsyncMock(return_value=response, side_effect=error)
    return SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))


def text_response(text):
    return types.GenerateContentResponse(candidates=[types.Candidate(
        content=types.Content(role='model', parts=[types.Part(text=text)]),
    )])


def sent(ctx):
    return [call.args[0] for call in ctx.send.await_args_list]


class AskTests(unittest.IsolatedAsyncioTestCase):
    async def ask(self, client, question="Who are you?"):
        cog = Gemini(bot=None, client=client)
        ctx = make_ctx()
        await Gemini.ask.callback(cog, ctx, question=question)
        return cog, ctx

    async def test_sends_the_answer_using_the_persona_as_system_instruction(self):
        client = make_client(text_response("I am Magna Shanoa!"))

        with patch.dict(os.environ, {'GEMINI_MODEL': ''}):
            _, ctx = await self.ask(client)

        self.assertEqual(sent(ctx), ["I am Magna Shanoa!"])
        kwargs = client.aio.models.generate_content.await_args.kwargs
        self.assertEqual(kwargs['model'], DEFAULT_MODEL)
        self.assertEqual(kwargs['contents'], "Who are you?")
        self.assertEqual(kwargs['config'].system_instruction, PERSONA)

    async def test_model_can_be_pinned_with_an_env_var(self):
        client = make_client(text_response("ok"))

        with patch.dict(os.environ, {'GEMINI_MODEL': 'gemini-3.6-flash'}):
            await self.ask(client)

        self.assertEqual(
            client.aio.models.generate_content.await_args.kwargs['model'], 'gemini-3.6-flash'
        )

    async def test_long_answers_are_split_into_discord_sized_messages(self):
        client = make_client(text_response("a" * 4500))

        _, ctx = await self.ask(client)

        self.assertEqual([len(message) for message in sent(ctx)], [2000, 2000, 500])

    async def test_blocked_prompts_are_reported(self):
        response = types.GenerateContentResponse(
            prompt_feedback=types.GenerateContentResponsePromptFeedback(
                block_reason=types.BlockedReason.SAFETY,
            ),
        )

        _, ctx = await self.ask(make_client(response))

        self.assertIn("blocked for safety", sent(ctx)[0])

    async def test_api_errors_are_logged_and_reported(self):
        error = errors.ClientError(429, {'error': {'message': 'quota', 'status': 'RESOURCE_EXHAUSTED'}})

        with self.assertLogs('cogs.gemini', level='ERROR'):
            _, ctx = await self.ask(make_client(error=error))

        self.assertEqual(sent(ctx), ["❌ Error with Magna's AI"])

    async def test_without_an_api_key_the_command_explains_it(self):
        with patch.dict(os.environ, {'GEMINI_API_KEY': ''}), self.assertLogs('cogs.gemini', 'WARNING'):
            cog = Gemini(bot=None)
        ctx = make_ctx()

        await Gemini.ask.callback(cog, ctx, question="hi")

        self.assertIn("GEMINI_API_KEY", sent(ctx)[0])


class SplitMessageTests(unittest.TestCase):
    def test_prefers_breaking_at_newlines(self):
        text = "a" * 1500 + "\n" + "b" * 1500

        self.assertEqual(split_message(text), ["a" * 1500, "b" * 1500])

    def test_short_text_is_one_message(self):
        self.assertEqual(split_message("hello"), ["hello"])


if __name__ == "__main__":
    unittest.main()
