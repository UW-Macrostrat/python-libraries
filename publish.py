#!/usr/bin/env python

from operator import mod
from os import chdir, environ
from macrostrat.utils.shell import git_has_changes
from pathlib import Path
import requests
from toml import load
from rich import print

modules = ["database", "dinosaur", "utils", "app-frame"]

from macrostrat.utils import cmd, relative_path, setup_stderr_logs, working_directory

setup_stderr_logs("macrostrat")

environ["POETRY_VIRTUALENVS_CREATE"] = "False"


def prepare_module(fp: Path):
    with working_directory(fp):
        cmd("poetry lock --no-update")
        # cmd("poetry export -f requirements.txt > requirements.txt", shell=True)
        cmd("poetry build")


def publish_module(fp):
    with working_directory(fp):
        res = cmd("poetry publish")
        if res.returncode != 0:
            print(f"Failed to publish {module_version_string(fp)}")
            return
        tag = module_version_string(fp)
        msg = module_version_string(fp, long=True)
        cmd(f"git tag -a {tag} -m '{msg}'", shell=True)


def get_package_data(fp: Path = Path(".")):
    conf = fp / "pyproject.toml"
    with conf.open("r") as f:
        data = load(f)
        return data["tool"]["poetry"]


def package_exists(pkg):
    name = pkg["name"]
    version = pkg["version"]
    vstr = f"[cyan]{name}[/cyan] ([bold]{version}[/bold])"
    uri = f"https://pypi.python.org/pypi/{name}/{version}/json"
    response = requests.get(uri)
    pkg_exists = response.status_code == 200
    if pkg_exists:
        print(f"{vstr} already exists on PyPI")
    else:
        print(f"{vstr} will be published")
    return pkg_exists


def modules_to_publish(modules: list[Path]):
    return [f for f in modules if not package_exists(get_package_data(f))]


def module_version_string(fp: Path, long: bool = False):
    pkg = get_package_data(fp)
    if long:
        return f"{pkg['name']} version {pkg['version']}"
    return f"{pkg['name']}-v{pkg['version']}"


# You should get a PyPI API token from https://pypi.org/account/
# and set the environment variable POETRY_PYPI_TOKEN to it.
if __name__ == "__main__":
    module_dirs = [relative_path(__file__, module) for module in modules]

    module_dirs = modules_to_publish(module_dirs)
    if len(module_dirs) == 0:
        print("[green]All modules are already published.")
    elif git_has_changes():
        print(
            "[red]You have uncommitted changes in your git repository. Please commit or stash them before continuing."
        )
        exit(1)

    for fp in module_dirs:
        prepare_module(fp)

    if len(module_dirs) > 0:
        msg = "Synced lock files for updated dependencies."
        cmd(f"git add .")
        cmd(f"git commit -m '{msg}'", shell=True)

    for fp in module_dirs:
        publish_module(fp)
