# Macrostrat Python libraries

A monorepo containing Python-based tools and libraries for Earth data projects

- This is still very early-stage.
- The intent is to share common functionality between Sparrow and other tools.
- This can be consumed as a local submodule or maybe eventually as PyPI packages.

## Development

You need `python >= 3.8` and the `poetry` package manager (installed separately) to develop the modules here.
Running `poetry install` (aliased to `make`) bootstraps the project in a local virtual environment.

Dependencies can be installed by adding them to the respective `pyproject.toml` files or by running `poetry add ...`.
Make sure to keep development dependencies (e.g., for testing) separate from core package dependencies.
`poetry add -D ...` adds dependencies that will only be installed in development, analogous to NPM and Yarn.

## Releasing on PyPI

This repository is designed to facilitate rapid iteration of its components
and release to PyPI. All modules are part of the `macrostrat` namespace package:
`macrostrat.database`, `macrostrat.dinosaur`, `macrostrat.utils`, etc.

To release a new version of a module, increment its `pyproject.toml` file and
run `make publish`. This will run a publication script that checks for current
versions and publishes if none exist.

## Structure and similar projects

- [Macrostrat's web component libraries](https://github.com/UW-Macrostrat/web-components) are also structured as a monorepo.
- [Opendoor Labs' Python monorepo](https://medium.com/opendoor-labs/our-python-monorepo-d34028f2b6fa) is a reference for code organization
