from pathlib import Path

from toml import load


def get_local_dependencies(pkg_cfg: dict[str, dict[str, str]]):
    """Get UV source packages that are local to the project."""

    deps = pkg_cfg["tool"]["uv"]["sources"]
    return deps

def load_pkg_config(fp: Path):
    if fp.is_dir():
        fp = fp / "pyproject.toml"
    with fp.open("r") as f:
        data = load(f)
        return data
