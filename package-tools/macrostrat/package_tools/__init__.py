from typer import Typer

from .install import install_packages
from .publish import publish_packages

mono = Typer(no_args_is_help=True)
mono.command(name="install")(install_packages)
mono.command(name="publish")(publish_packages)
