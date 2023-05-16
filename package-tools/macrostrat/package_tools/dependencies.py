from pathlib import Path
from toml import load


def ensure_list(fn):
    def wrapper(*args, **kwargs):
        res = fn(*args, **kwargs)
        # Flatten iterator
        if hasattr(res, "__iter__"):
            res = [i for i in res]
        return res

    return wrapper


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


@ensure_list
def get_local_dependencies(poetry_cfg: dict[str, dict[str, str]]):
    all_deps = get_all_dependencies(poetry_cfg)
    for k, v in all_deps.items():
        if "path" in v and v.get("develop", False):
            yield v["path"]


def load_poetry_config(fp: Path):
    if fp.is_dir():
        fp = fp / "pyproject.toml"
    with fp.open("r") as f:
        data = load(f)
        return data["tool"]["poetry"]
