"""Utilities for Typer and Click command-line interfaces."""

from typing import List

import typer
from click import Parameter
from typer import Context, Typer
from typer.core import TyperGroup

from macrostrat.utils import get_logger

log = get_logger(__name__)


class OrderCommands(TyperGroup):
    def list_commands(self, ctx: Context):
        """Return list of commands in the order of appearance."""
        deprecated = []
        commands = []

        for name, command in self.commands.items():
            if command.deprecated:
                deprecated.append(name)
            else:
                commands.append(name)
        return commands + deprecated

    def get_params(self, ctx: Context) -> List[Parameter]:
        """Don't show the completion options in the help text, to avoid cluttering the output"""
        return [
            p
            for p in self.params
            if not p.name in ("install_completion", "show_completion")
        ]


def add_click_command(base: Typer, cmd, *args, **kwargs):
    """Add a click command to a Typer app
    params:
        base: Typer
        cmd: callable
        args: arguments to pass to typer.command
        kwargs: keyword arguments to pass to typer.command
    """

    def _click_command(ctx: typer.Context):
        cmd(ctx.args)

    _click_command.__doc__ = cmd.__doc__

    kwargs["context_settings"] = {
        "allow_extra_args": True,
        "ignore_unknown_options": True,
        "help_option_names": [],
        **kwargs.get("context_settings", {}),
    }

    base.command(*args, **kwargs)(_click_command)
