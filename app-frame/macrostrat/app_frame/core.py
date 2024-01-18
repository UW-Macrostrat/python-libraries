import logging
from os import environ
from pathlib import Path
from typing import Callable, Optional

from dotenv import load_dotenv
from rich.console import Console

from macrostrat.utils import get_logger, setup_stderr_logs

log = get_logger(__name__)


class ApplicationBase:
    name: str
    command_name: str
    app_module: Optional[str]
    root_dir: Path


EnvironmentDependency = dict[str, str] | Callable[[ApplicationBase], dict[str, str]]
ComposeFilesDependency = list[Path] | Callable[[ApplicationBase], list[Path]]


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
        restart_commands: dict[str, str] = {},
        log_modules: Optional[str | list[str]] = None,
        root_dir: Path | Callable[[Path], Path] = Path.cwd(),
        compose_files: ComposeFilesDependency = [],
        env: EnvironmentDependency = {},
        load_dotenv: bool | Path | list[Path] = False,
    ):
        self.name = name
        self.command_name = command_name or name.lower()
        self.project_prefix = project_prefix or name.lower().replace(" ", "_")
        self.envvar_prefix = self.project_prefix.upper() + "_"
        self.console = Console()
        self.restart_commands = restart_commands

        if isinstance(log_modules, str):
            log_modules = [log_modules]
        if log_modules is not None:
            self._log_modules = log_modules

        self._dotenv_cfg = load_dotenv

        # Root dir and compose files can be specified using dependency injection.
        if callable(root_dir):
            root_dir = root_dir(Path.cwd())
        self.root_dir = root_dir

        if callable(compose_files):
            compose_files = compose_files(self)
        self.compose_files = compose_files
        # Environment setup should possibly be postponed until within a command context.
        self.setup_environment(env)

    def replace_names(self, text: str) -> str:
        text = text.replace(":app_name:", self.name)
        return text.replace(":command_name:", self.name.lower())

    def info(self, text, style=None):
        self.console.print(self.replace_names(text), style=style)

    def load_dotenv(self):
        if isinstance(self._dotenv_cfg, list):
            for path in self._dotenv_cfg:
                load_dotenv(path)
        elif isinstance(self._dotenv_cfg, Path):
            load_dotenv(self._dotenv_cfg)
        elif load_dotenv is True:
            load_dotenv()

    def setup_environment(self, env: EnvironmentDependency):
        environ["DOCKER_SCAN_SUGGEST"] = "false"
        environ["DOCKER_BUILDKIT"] = "1"

        # Set up environment for docker-compose
        # We may need to move this to a context where it is only
        # applied for docker-compose commands
        environ["COMPOSE_PROJECT_NAME"] = self.project_prefix
        compose_files = ":".join([str(f) for f in self.compose_files])
        environ["COMPOSE_FILE"] = compose_files

        # Additional user-specified environment variables
        if callable(env):
            env = env(self)
        for k, v in env.items():
            environ[k] = v

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
