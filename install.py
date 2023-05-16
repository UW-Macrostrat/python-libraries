from os import environ
from macrostrat.utils import cmd
from pathlib import Path
from toml import load
from rich import print

path = Path(".")


def get_all_dependencies(poetry_cfg: dict[str, dict[str, str]]):
    deps = poetry_cfg["dependencies"]
    dev_deps = poetry_cfg.get("dev-dependencies", {})
    all_deps = {**deps, **dev_deps}

    # For newer poetry
    groups = poetry_cfg.get("group", {})
    for k, cfg in groups.items():
        group_deps = cfg.get("dependencies", {})
        all_deps = {**all_deps, **group_deps}
    return all_deps


def get_local_dependencies(poetry_cfg: dict[str, dict[str, str]]):
    all_deps = get_all_dependencies(poetry_cfg)
    for k, v in all_deps.items():
        if "path" in v and v.get("develop", False):
            yield v["path"]


local_deps = []
with path / "pyproject.toml" as f:
    data = load(f)
    local_deps = list(get_local_dependencies(data["tool"]["poetry"]))

for _dir in local_deps:
    fp = path / _dir
    cmd(
        "poetry install",
        cwd=fp,
        env={**environ, "POETRY_VIRTUALENVS_CREATE": "False"},
    )

cmd("poetry lock --no-update")
cmd("poetry install")
