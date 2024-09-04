# Typer command-line application

import sys
import typing as t
from os import environ
from time import sleep

import click
import typer
from click import Group
from typer import rich_utils
from typer import Context, Option, Typer
from typer.core import TyperGroup
from typer.models import TyperInfo

from macrostrat.utils import get_logger

from .compose import check_status, compose
from .core import Application
from .follow_logs import Result, command_stream, follow_logs

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


    def get_params(self, ctx: Context) -> t.List["Parameter"]:
        """ Don't show the completion options in the help text, to avoid cluttering the output """
        return [p for p in self.params if not p.name in ("install_completion", "show_completion")]

class ControlCommand(Typer):
    name: str

    app: Application
    _click: Group

    def __init__(
        self,
        app: Application,
        **kwargs,
    ):
        kwargs.setdefault("add_completion", False)
        kwargs.setdefault("no_args_is_help", True)
        kwargs.setdefault("cls", OrderCommands)
        kwargs.setdefault("name", app.name)
        super().__init__(**kwargs)
        self.app = app
        self.name = app.name

        # Make sure the help text is not dimmed after the first line
        rich_utils.STYLE_HELPTEXT = None


        verbose_envvar = self.app.envvar_prefix + "VERBOSE"

        def callback(
            ctx: Context,
            verbose: bool = Option(False, "--verbose", envvar=verbose_envvar),
        ):
            ctx.obj = self.app
            # Setting the environment variable allows nested commands to pick up
            # the verbosity setting, if needed.
            if verbose:
                environ[verbose_envvar] = "1"
            self.app.setup_logs(verbose=verbose)

        callback.__doc__ = f"""{self.app.name} command-line interface"""

        self.registered_callback = TyperInfo(callback=callback)

        self.build_commands()

    def build_commands(self):
        for cmd in [up, down, restart]:
            if cmd.__doc__ is not None:
                cmd.__doc__ = self.app.replace_names(cmd.__doc__)
            self.command(rich_help_panel="System")(cmd)
        self.add_click_command(_compose, "compose", rich_help_panel="System")

    def add_command(self, cmd, *args, **kwargs):
        """Simple wrapper around command"""
        self.command(*args, **kwargs)(cmd)

    def add_click_command(self, cmd, *args, **kwargs):
        """Add a click command
        params:
            cmd: callable
            args: arguments to pass to typer.command
            kwargs: keyword arguments to pass to typer.command
        """
        def _click_command(ctx: typer.Context):
            cmd(ctx.args)

        _click_command.__doc__ = cmd.__doc__

        self.add_command(_click_command,*args, **kwargs)



def up(
    ctx: Context, container: str = typer.Argument(None), force_recreate: bool = False
):
    """Start the :app_name: server and follow logs."""
    app = ctx.find_object(Application)
    if app is None:
        raise ValueError("Could not find application config")

    start_app(app, container=container, force_recreate=force_recreate)
    proc = follow_logs(app, container)
    try:
        for res in command_stream(refresh_rate=1):
            # Stop the logs process and wait for it to exit
            if res == Result.RESTART:
                app.info("Restarting :app_name: server...", style="bold")
                start_app(app, container=container, force_recreate=True)
            elif res == Result.EXIT:
                app.info("Stopping :app_name: server...", style="bold")
                ctx.invoke(down, ctx)
                return
            elif res == Result.CONTINUE:
                app.info(
                    "[bold]Detaching from logs[/bold] [dim](:app_name: will continue to run)[/dim]",
                    style="bold",
                )
                return
    except Exception as e:
        proc.kill()
        proc.wait()


def start_app(
    app: Application,
    container: str = typer.Argument(None),
    force_recreate: bool = False,
    single_stage: bool = False,
):
    """Start the :app_name: server and follow logs."""

    if not single_stage:
        build_args = ["build"]
        if container is not None:
            build_args.append(container)
        res = compose(*build_args)
        fail_with_message(app, res, "Build images")
        sleep(0.1)

    args = ["up", "--remove-orphans"]
    if not single_stage:
        args += ["--no-start", "--no-build"]
    if force_recreate:
        args.append("--force-recreate")
    if container is not None:
        args.append(container)

    res = compose(*args)
    fail_with_message(app, res, "Create containers")

    # Get list of currently running containers
    running_containers = check_status(app.name, app.command_name)

    if not single_stage:
        app.info("Starting :app_name: server...", style="bold")
        res = compose("start")
        fail_with_message(app, res, "Start :app_name:")

    run_restart_commands(app, running_containers)

def fail_with_message(app, res, stage_name):
    if res.returncode != 0:
        app.info(
            f"{stage_name} failed, aborting.",
            style="red bold",
        )
        sys.exit(res.returncode)
    else:
        app.info(f"{stage_name} succeeded.", style="green bold")
        print()


def run_restart_commands(app, running_containers):
    for c, command in app.restart_commands.items():
        if c in running_containers:
            app.info(f"Reloading {c}...", style="bold")
            compose("exec", c, command)
    print()


def down(ctx: Context):
    """Stop all :app_name: services."""
    app = ctx.find_object(Application)
    if app is None:
        raise ValueError("Could not find application config")
    app.info("Stopping :app_name: server...", style="bold")
    compose("down", "--remove-orphans")


def restart(ctx: Context, container: str = typer.Argument(None)):
    """Restart the :app_name: server and follow logs."""
    ctx.invoke(up, ctx, container, force_recreate=True)


@click.command(
    "compose",
    context_settings=dict(
        ignore_unknown_options=True,
        help_option_names=[],
        max_content_width=160,
        # Doesn't appear to have landed in Click 7? Or some other reason we can't access...
        # short_help_width=160,
    ),
)
@click.argument("args", nargs=-1, type=click.UNPROCESSED)
def _compose(args):
    """Run docker compose commands in the appropriate context"""
    compose(*args, collect_args=False)
