# Typer command-line application

from enum import Enum
from os import environ

from typer import Context, Option
from typer import rich_utils
from typer.models import TyperInfo

from macrostrat.utils import get_logger
from .compose import add_docker_compose_commands
from .core import Application
from .utils import CommandBase, ControlCommandGroup  # noqa

log = get_logger(__name__)


class BackendType(str, Enum):
    DockerCompose = "docker-compose"
    Kubernetes = "kubernetes"


class ControlCommand(CommandBase):
    name: str
    app: Application

    def __init__(
        self,
        app: Application,
        *,
        backend: BackendType = BackendType.DockerCompose,
        **kwargs,
    ):
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
            """:app_name: command-line interface"""
            ctx.obj = self.app
            # Setting the environment variable allows nested commands to pick up
            # the verbosity setting, if needed.
            if verbose:
                environ[verbose_envvar] = "1"
            self.app.setup_logs(verbose=verbose)

        self.registered_callback = TyperInfo(callback=self._update_docstring(callback))

        if backend == BackendType.DockerCompose:
            add_docker_compose_commands(self)
        # We don't have Kubernetes support yet, but will work to add it.

    def _update_docstring(self, func):
        if func.__doc__ is not None:
            func.__doc__ = self.app.replace_names(func.__doc__)
        return func

    def command(self, *args, **kwargs):
        """Simple wrapper around command that replaces names in the docstring"""
        wrapper = super().command(*args, **kwargs)
        return lambda func: wrapper(self._update_docstring(func))
