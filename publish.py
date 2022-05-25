#!/usr/bin/env python

from os import chdir, environ
import requests
from toml import load
from rich import print

modules = ["database", "dinosaur", "utils"]

from macrostrat.utils import cmd, relative_path, setup_stderr_logs

setup_stderr_logs("macrostrat")

environ["POETRY_VIRTUALENVS_CREATE"] = "False"

def process_module(module):
    fp = relative_path(__file__, module)
    print("Importing module: " + module)
    exec("import %s" % module)

    chdir(fp)
    cmd("poetry export -f requirements.txt > requirements.txt", shell=True)
    cmd("poetry build")

    version = get_version()
    if package_exists(module, version):
        print(f"{module} {version} already exists on PyPI")
        return
    cmd("poetry publish")
    

def get_version():
    with open("pyproject.toml", "r") as f:
        data = load(f)
        return data["tool"]["poetry"]["version"]

def package_exists(module_name, version):
    uri = f"http://pypi.python.org/pypi/{module_name}/{version}/json"
    response = requests.get(uri)
    return response.status_code == 200

# You should get a PyPI API token from https://pypi.org/account/
# and set the environment variable POETRY_PYPI_TOKEN to it.
if __name__ == "__main__":
    for module in modules:
        process_module(module)
