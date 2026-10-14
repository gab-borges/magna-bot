"""Helpers for commands that run both as %prefix and as /slash commands."""
import hashlib
import json
import logging

from magna.storage import data_file, load_json, save_json

logger = logging.getLogger(__name__)

SYNC_STATE_FILE = 'app_commands.json'


async def defer(ctx):
    """Acknowledge a slash command that may take longer than Discord's 3 seconds.

    Prefix commands (and slash commands that already replied) are left alone.
    Replies sent more than 15 minutes later, when Discord no longer accepts
    them for the interaction, go to the channel instead (ctx.send does that).
    """
    interaction = getattr(ctx, 'interaction', None)
    if interaction is not None and not interaction.response.is_done():
        await interaction.response.defer()


def commands_fingerprint(tree):
    """A hash of the global slash commands exactly as they're sent to Discord."""
    payload = sorted(
        (command.to_dict(tree) for command in tree.get_commands()),
        key=lambda command: command['name'],
    )
    encoded = json.dumps(payload, sort_keys=True, default=str).encode()
    return hashlib.sha256(encoded).hexdigest()


async def sync_commands(bot, *, force=False):
    """Register the slash commands with Discord when they changed.

    Discord rate-limits syncing, so a restart with the same commands skips it.
    Returns True when the commands were synced.
    """
    path = data_file(SYNC_STATE_FILE)
    state = load_json(path, {})
    key = str(bot.application_id)
    fingerprint = commands_fingerprint(bot.tree)
    if not force and state.get(key) == fingerprint:
        logger.info("Slash commands unchanged; skipping sync")
        return False

    synced = await bot.tree.sync()
    state[key] = fingerprint
    save_json(path, state)
    logger.info("Synced %d slash commands", len(synced))
    return True
