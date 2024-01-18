# Typer command-line application

import sys
from os import environ
from time import sleep

import click
import typer
from click import Group
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
        return list(self.commands)  # get commands using self.commands


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

        # Click commands must be added after Typer commands in the current design.
        self._click_commands = []

        self.build_commands()

    def build_commands(self):
        for cmd in [up, down, restart]:
            if cmd.__doc__ is not None:
                cmd.__doc__ = self.app.replace_names(cmd.__doc__)
            self.command(rich_help_panel="System")(cmd)
        self.add_click_command(_compose, "compose", rich_help_panel="System")

    def add_click_command(self, cmd, *args, **kwargs):
        """Add a click command for lazy initialization
        params:
            cmd: click command
            args: args to pass to click.add_command
            kwargs: kwargs to pass to click.add_command
            rich_help_panel: name of rich help panel to add to
        """
        rich_help_panel = kwargs.pop("rich_help_panel", None)
        if rich_help_panel is not None:
            setattr(cmd, "rich_help_panel", rich_help_panel)
        cfunc = lambda _click: _click.add_command(cmd, *args, **kwargs)
        self._click_commands.append(cfunc)

    def __call__(self):
        """Run this command using its underlying click object."""
        cmd = typer.main.get_command(self)
        assert isinstance(cmd, click.Group)
        self._click = cmd
        for cfunc in self._click_commands:
            cfunc(self._click)
        return self._click()


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
):
    """Start the :app_name: server and follow logs."""

    if container:
        compose("build", container)
    else:
        compose("build")

    sleep(0.1)

    args = ["up", "--no-start", "--remove-orphans"]
    if force_recreate:
        args.append("--force-recreate")
    if container:
        args.append(container)

    res = compose(*args)
    if res.returncode != 0:
        app.info(
            "One or more containers did not build successfully, aborting.",
            style="red bold",
        )
        sys.exit(res.returncode)
    else:
        app.info("All containers built successfully.", style="green bold")
    print()

    # Get list of currently running containers
    running_containers = check_status(app.name, app.command_name)

    app.info("Starting :app_name: server...", style="bold")
    compose("start")
    print()

    run_restart_commands(app, running_containers)


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
