#!/usr/bin/env python

from pathlib import Path

import requests
from rich import print

from macrostrat.utils import cmd, working_directory
from macrostrat.utils.shell import git_has_changes
from .dependencies import get_local_dependencies, load_pkg_config


def prepare_module(fp: Path):
    with working_directory(fp):
        cmd("uv lock")
        # cmd("poetry export -f requirements.txt > requirements.txt", shell=True)
        cmd("uv build")


def publish_module(fp):
    with working_directory(fp):
        res = cmd("uv publish")
        if res.returncode != 0:
            print(f"Failed to publish {module_version_string(fp)}")
            return
        tag = module_version_string(fp)
        msg = module_version_string(fp, long=True)
        cmd(f"git tag -a {tag} -m '{msg}'", shell=True)


def package_exists(pyproj: dict):
    pkg = pyproj["project"]
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


def modules_to_publish(modules: list[Path], omit: list[str] = []):
    return [f for f in modules if not package_exists(load_pkg_config(f))]


def module_version_string(fp: Path, long: bool = False):
    pyproj = load_pkg_config(fp)
    pkg = pyproj["project"]
    if long:
        return f"{pkg['name']} version {pkg['version']}"
    return f"{pkg['name']}-v{pkg['version']}"


# You should get a PyPI API token from https://pypi.org/account/
# and set the environment variable POETRY_PYPI_TOKEN to it.
def publish_packages(path: Path = Path.cwd(), omit: list[str] = []):
    """Publish all packages that need to be published."""
    cfg = load_pkg_config(path)
    local_deps = get_local_dependencies(cfg)
    # Filter omitted packages
    local_deps = {k: v for k, v in local_deps.items() if k not in omit}

    # environ["POETRY_VIRTUALENVS_CREATE"] = "False"

    module_dirs = [path / v["path"] for k, v in local_deps.items() if k not in omit]
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
