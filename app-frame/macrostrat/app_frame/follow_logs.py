import fcntl
import os
import sys
import termios
from contextlib import contextmanager
from enum import Enum
from subprocess import Popen
from time import sleep

from macrostrat.utils import get_logger

from .core import Application

log = get_logger(__name__)


def follow_logs(app: Application, container: str, **kwargs):
    app.info("Following container logs", style="green bold")
    app.info(
        f"- Press [bold]q[/bold] or [bold]Ctrl+c[/bold] to exit logs (:app_name: will keep running)."
    )
    app.info(
        f"- Press [bold]r[/bold] or run [cyan]:command_name: restart[/cyan] to restart :app_name:."
    )
    app.info(
        f"- Press [bold]x[/bold] or run [cyan]:command_name: down[/cyan] to stop :app_name:.",
    )
    # Should integrate this into the macrostrat.utils.cmd function
    # env = kwargs.pop("env", _build_compose_env())
    args = ["docker", "compose", "logs", "-f", "--since=1s"]
    log.debug(" ".join(args))
    sleep(0.1)
    app.console.print()

    if container is not None and container != "":
        args.append(container)
    return Popen(args, **kwargs)


class Result(Enum):
    CONTINUE = 1
    RESTART = 2
    EXIT = 3


def follow_logs_with_reloader(
    app: Application,
    container: str,
) -> Result:
    proc = follow_logs(app, container)
    try:
        with wait_for_keys():
            while True:
                # Read input from stdin
                try:
                    key = sys.stdin.read(1)
                    if key == "q" or key == "Q":
                        return Result.CONTINUE
                    elif key == "r" or key == "R":
                        return Result.RESTART
                    elif key == "x" or key == "X":
                        return Result.EXIT
                except IOError:
                    pass
    finally:
        proc.kill()
        proc.wait()


@contextmanager
def wait_for_keys():
    fd = sys.stdin.fileno()

    oldterm = termios.tcgetattr(fd)
    newattr = termios.tcgetattr(fd)
    newattr[3] = newattr[3] & ~termios.ICANON & ~termios.ECHO
    termios.tcsetattr(fd, termios.TCSANOW, newattr)

    oldflags = fcntl.fcntl(fd, fcntl.F_GETFL)
    fcntl.fcntl(fd, fcntl.F_SETFL, oldflags | os.O_NONBLOCK)

    try:
        yield
    finally:
        termios.tcsetattr(fd, termios.TCSAFLUSH, oldterm)
        fcntl.fcntl(fd, fcntl.F_SETFL, oldflags)
