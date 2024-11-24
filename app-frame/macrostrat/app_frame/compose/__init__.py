"""
Integration with docker-compose
"""
import sys
from os import environ
from time import sleep

import click
import typer
from typer import Context, Typer

from macrostrat.utils import get_logger
from .base import check_status, compose
from .follow_logs import Result, command_stream, follow_logs
from ..core import Application
from ..utils import add_click_command

# Typer command-line application

log = get_logger(__name__)


def add_docker_compose_commands(command: Typer):
    rich_help_panel = "System (Docker Compose)"
    for cmd in [up, down, restart]:
        command.command(rich_help_panel=rich_help_panel)(cmd)
    add_click_command(command, _compose, "compose", rich_help_panel=rich_help_panel)


def up(
    ctx: Context,
    container: str = typer.Argument(None),
    force_recreate: bool = False,
    offline: bool = False,
):
    """Start the :app_name: server and follow logs."""
    app = ctx.find_object(Application)
    if app is None:
        raise ValueError("Could not find application config")

    # Remove DOCKER_BUILDKIT=1 from the environment if we are in offline mode
    # This should possible be merged in upstream
    if offline:
        environ["DOCKER_BUILDKIT"] = "0"
        log.info("Disabling Docker BuildKit for offline mode")

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


def restart(ctx: Context, container: str = typer.Argument(None), offline: bool = False):
    """Restart the :app_name: server and follow logs."""
    ctx.invoke(up, ctx, container, force_recreate=True, offline=offline)


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
