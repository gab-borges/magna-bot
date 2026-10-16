"""discord.py's default %help, with its text in Portuguese."""
import discord
from discord.ext import commands


class HelpCommand(commands.DefaultHelpCommand):
    def __init__(self):
        super().__init__(
            commands_heading="Comandos:",
            arguments_heading="Argumentos:",
            no_category="Outros",
            default_argument_description="Sem descrição",
            command_attrs={'help': "Mostra esta mensagem"},
        )

    def get_ending_note(self):
        command = f"{self.context.clean_prefix}{self.invoked_with}"
        return (
            f"Digite {command} <comando> para saber mais sobre um comando.\n"
            f"Você também pode digitar {command} <categoria> para saber mais sobre uma categoria."
        )

    def command_not_found(self, string, /):
        return f'Nenhum comando chamado "{string}" foi encontrado.'

    def subcommand_not_found(self, command, string, /):
        if isinstance(command, commands.Group) and command.all_commands:
            return f'O comando "{command.qualified_name}" não tem um subcomando chamado {string}.'
        return f'O comando "{command.qualified_name}" não tem subcomandos.'

    # Same as DefaultHelpCommand's, which has "default" built in.
    def add_command_arguments(self, command, /):
        arguments = command.clean_params.values()
        if not arguments:
            return

        self.paginator.add_line(self.arguments_heading)
        max_size = self.get_max_size(arguments)

        get_width = discord.utils._string_width
        for argument in arguments:
            name = argument.displayed_name or argument.name
            width = max_size - (get_width(name) - len(name))
            entry = f'{self.indent * " "}{name:<{width}} {argument.description or self.default_argument_description}'
            entry = self.shorten_text(entry)
            if argument.displayed_default is not None:
                entry += f' (padrão: {argument.displayed_default})'

            self.paginator.add_line(entry)
