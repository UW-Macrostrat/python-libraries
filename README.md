# Macrostrat Python libraries

A monorepo containing Python-based tools and libraries for Earth data projects.

- This is still very early-stage.
- The intent is to share common subsystems between Sparrow, Macrostrat and other
  tools.
- All modules can be consumed as PyPI packages, or embedded locally as a
  submodule (though this is less-recommended).

## Modules

- `macrostrat.app_frame`: A control framework for managing Dockerized
  applications. Currently used by Sparrow, Mapboard GIS, and Macrostrat.
- `macrostrat.auth_system`: Authentication utilities
- `macrostrat.database`: Database connection and query utilities geared towards
  PostgreSQL
- `macrostrat.dinosaur`: Utilities for on-the-fly database migration and
  conformance testing
- `macrostrat.package_tools`: Monorepo versioning and PyPI publishing utilities
- `macrostrat.utils`: Helpers for logging and command-line apps

## Development

You need `python >= 3.10` and the [`uv`](https://docs.astral.sh/uv/) package
manager to develop the modules here.
Running `uv sync` (aliased to `make install`) bootstraps the project in a local
virtual environment.

Dependencies can be installed by adding them to the respective `pyproject.toml`
files or by running `uv add ...`.
Keep development dependencies (e.g., for testing) separate from core package
dependencies using `uv add --dev ...`.

## Testing

Tests can be run using `make test`, or, for added control, `uv run pytest ...`.
Docker is required to run all tests, as some of them require several containers.

### Testing the `macrostrat.app_frame` module

The `app_frame` module can be tested using a simple mock application,
which can be controlled using the `uv run test-app` command. This command
presents the application's CLI interface, which can be used to start and stop
the application, e.g. with `uv run test-app up`.

## Releasing on PyPI

This repository is designed to facilitate rapid iteration of its components
and release to PyPI. All modules are part of the `macrostrat` namespace package:
`macrostrat.database`, `macrostrat.dinosaur`, `macrostrat.utils`, etc.

To release a new version of a module, increment its version in the appropriate
`pyproject.toml` file and run `make publish`. This runs `uv run mono publish`,
which checks for current versions and publishes any that are new.

## Structure and similar projects

- [Macrostrat's web component libraries](https://github.com/UW-Macrostrat/web-components)
  are also structured as a monorepo.
- [Opendoor Labs' Python monorepo](https://medium.com/opendoor-labs/our-python-monorepo-d34028f2b6fa)
  is a reference for code organization
