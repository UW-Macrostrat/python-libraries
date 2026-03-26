# Typer command-line application

from os import environ
from typing import Optional

from typer import Context, Option, rich_utils
from typer.models import TyperInfo

from macrostrat.utils import get_logger
from .core import Application
from .utils import CommandBase, ControlCommandGroup, get_env_boolean  # noqa

log = get_logger(__name__)

class ControlCommand(CommandBase):
    name: str
    app: Application

    def __init__(
        self,
        app: Application,
        *,
        name: Optional[str] = None,
        **kwargs,
    ):
        name = name or app.name

        super().__init__(**kwargs)
        self.app = app
        self.name = name

        # Make sure the help text is not dimmed after the first line
        rich_utils.STYLE_HELPTEXT = None

        def callback(
            ctx: Context,
            verbose: bool = Option(False, "--verbose"),
        ):
            """:app_name: command-line interface"""
            ctx.obj = self.app
            # Setting the environment variable allows nested commands to pick up
            # the verbosity setting, if needed.
            verbose_envvar = self.app.envvar_prefix + "VERBOSE"
            _env_verbose = get_env_boolean(verbose_envvar)
            if verbose or _env_verbose:
                environ[verbose_envvar] = "1"
            # This is kind of a weird inversion of control
            self.app.setup_logs(verbose=verbose)

        self.registered_callback = TyperInfo(callback=self._update_docstring(callback))

    def _update_docstring(self, func):
        if func.__doc__ is not None:
            func.__doc__ = self.app.replace_names(func.__doc__)
        return func

    def command(self, *args, **kwargs):
        """Simple wrapper around command that replaces names in the docstring"""
        wrapper = super().command(*args, **kwargs)
        return lambda func: wrapper(self._update_docstring(func))
