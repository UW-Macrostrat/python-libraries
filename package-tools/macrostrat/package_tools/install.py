from os import environ
from macrostrat.utils import cmd
from pathlib import Path

from .dependencies import get_local_dependencies, load_poetry_config


def install_packages(
    path: Path = Path.cwd(), omit: list[Path] = [], root=True, virtualenvs=False
):
    """Install all packages in the root project's virtual environment."""
    cfg = load_poetry_config(path)
    local_deps = get_local_dependencies(cfg["tool"]["poetry"])

    extra_env = {}
    if not virtualenvs:
        extra_env = {"POETRY_VIRTUALENVS_CREATE": "False"}

    for _dir in local_deps:
        if _dir in omit:
            continue
        fp = path / _dir
        cmd(
            "poetry lock --no-update",
            cwd=fp,
            env={**environ, **extra_env},
        )
    if root:
        cmd("poetry lock --no-update")
        cmd("poetry install")
