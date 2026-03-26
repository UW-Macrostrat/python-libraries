import logging
from os import environ
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from rich.console import Console

from macrostrat.utils import get_logger, setup_stderr_logs
from .subsystems import ApplicationBase

log = get_logger(__name__)

EnvironmentDependency = dict[str, str] | Callable[[ApplicationBase], dict[str, str]]
DotenvDependency = bool | Path | list[Path]


class Application(ApplicationBase):
    console: Console
    _dotenv_cfg: bool | Path | list[Path]
    _log_modules: list[str] = []

    def __init__(
        self,
        name: str,
        *,
        command_name: Optional[str] = None,
        project_prefix: Optional[str] = None,
        log_modules: Optional[str | list[str]] = None,
        env: EnvironmentDependency = {},
        load_dotenv: DotenvDependency = None,
        env_prefix: Optional[str] = None,
    ):
        self.name = name
        self.command_name = command_name or name.lower()
        self.project_prefix = project_prefix or name.lower().replace(" ", "_")

        _env_prefix = env_prefix or self.project_prefix.upper() + "_"

        self.envvar_prefix = _env_prefix
        self.console = Console()

        if isinstance(log_modules, str):
            log_modules = [log_modules]
        if log_modules is not None:
            self._log_modules = log_modules

        self._dotenv_cfg = load_dotenv

        # Environment setup should possibly be postponed until within a command context.
        self.setup_environment(env)

    def replace_names(self, text: str) -> str:
        text = text.replace(":app_name:", self.name)
        return text.replace(":command_name:", self.name.lower())

    def info(self, text, style=None):
        self.console.print(self.replace_names(text), style=style)

    def load_dotenv(self, dependency: DotenvDependency = None):
        if dependency is None:
            dependency = self._dotenv_cfg

        if isinstance(dependency, list):
            for path in dependency:
                self.load_dotenv(path)
        elif isinstance(dependency, Path):
            load_dotenv(dependency)
        elif dependency is True:
            load_dotenv()

    def setup_environment(self, env: EnvironmentDependency):
        environ["DOCKER_SCAN_SUGGEST"] = "false"
        # environ["DOCKER_BUILDKIT"] = "1"

        # Additional user-specified environment variables
        if callable(env):
            env = env(self)
        for k, v in env.items():
            environ[k] = v

        # Load other environment variables
        self.load_dotenv()

    def setup_logs(self, verbose: bool = False):
        if len(self._log_modules) == 0:
            log.warning("No modules specified, not setting up logs")
            return
        if verbose:
            setup_stderr_logs(*self._log_modules)
        else:
            # Disable all logging
            # TODO: This is a hack, we shouldn't have to explicitly disable
            # logging in the CLI. Perhaps there's somewhere that it's being
            # enabled that we haven't chased down?
            setup_stderr_logs("", level=logging.CRITICAL)

    def control_command(self, *args, **kwargs):
        from .control_command import ControlCommand

        return ControlCommand(self, *args, **kwargs)
