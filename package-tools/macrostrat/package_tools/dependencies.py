from pathlib import Path

from toml import load


def get_all_dependencies(
    poetry_cfg: dict[str, dict[str, str]]
) -> dict[str, dict[str, str]]:
    """Get all poetry dependencies, including dev dependencies and other groups, in a flattened dict."""
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
    vals = {}
    for k, v in all_deps.items():
        if "path" in v and v.get("develop", False):
            vals[k] = v
    return vals


def load_poetry_config(fp: Path):
    if fp.is_dir():
        fp = fp / "pyproject.toml"
    with fp.open("r") as f:
        data = load(f)
        return data["tool"]["poetry"]
