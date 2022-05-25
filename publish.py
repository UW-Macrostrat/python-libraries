#!/usr/bin/env python

from os import chdir, environ
import requests
from toml import load
from rich import print

modules = ["database", "dinosaur", "utils"]

from macrostrat.utils import cmd, relative_path, setup_stderr_logs

setup_stderr_logs("macrostrat")

environ["POETRY_VIRTUALENVS_CREATE"] = "False"

def process_module(fp):
    chdir(fp)
    cmd("poetry lock")
    cmd("poetry export -f requirements.txt > requirements.txt", shell=True)
    cmd("poetry build")


def publish_module(fp):
    chdir(fp)
    pkg = get_package_data()
    if package_exists(pkg):
        return

    cmd("poetry publish")
    

def get_package_data():
    with open("pyproject.toml", "r") as f:
        data = load(f)
        return data["tool"]["poetry"]

def package_exists(pkg):
    name = pkg["name"]
    version = pkg["version"]
    vstr = f"[cyan]{name}[/cyan] ([bold]{version}[/bold])"
    print(f"Checking if {vstr} exists on PyPI")
    uri = f"https://pypi.python.org/pypi/{name}/{version}/json"
    response = requests.get(uri)
    pkg_exists =  response.status_code == 200
    if pkg_exists:
        print(f"{vstr} already exists on PyPI")
    else:
        print(f"Preparing to publish {vstr}")
    return pkg_exists

# You should get a PyPI API token from https://pypi.org/account/
# and set the environment variable POETRY_PYPI_TOKEN to it.
if __name__ == "__main__":
    module_dirs = [relative_path(__file__, module) for module in modules]
    for fp in module_dirs:
        process_module(fp)

    for fp in module_dirs:
        publish_module(fp)
