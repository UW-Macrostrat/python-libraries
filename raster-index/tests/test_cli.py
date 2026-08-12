"""The CLI's database wiring.

The commands themselves are thin wrappers over `RasterIndex` (covered in
test_raster_index.py); what's worth testing here is how a connection is
resolved — explicitly, from a host application's default, or not at all.
"""

from pytest import fixture
from typer.testing import CliRunner

from macrostrat.raster_index.cli import cli, set_default_connection


@fixture
def runner():
    return CliRunner()


@fixture
def database_arg(populated_index):
    return ["--database", str(populated_index.engine.url)]


@fixture(autouse=True)
def no_host_default():
    """Each test starts with no host-configured connection."""
    set_default_connection(None)
    yield
    set_default_connection(None)


def test_help_needs_no_database(runner):
    """`--help` must work before anything is configured."""
    result = runner.invoke(cli, ["--help"])
    assert result.exit_code == 0
    assert "Manage indexed raster datasets" in result.output


def test_missing_database_is_a_parameter_error(runner):
    result = runner.invoke(cli, ["layers"])
    assert result.exit_code != 0
    assert "No database configured" in result.output


def test_database_option(runner, database_arg):
    result = runner.invoke(cli, [*database_arg, "layers"])
    assert result.exit_code == 0, result.output
    assert "minerals" in result.output


def test_host_supplied_default(runner, populated_index):
    """What Macrostrat does: configure once, commands need no --database."""
    set_default_connection(lambda: str(populated_index.engine.url))
    result = runner.invoke(cli, ["layers"])
    assert result.exit_code == 0, result.output
    assert "minerals" in result.output


def test_option_overrides_host_default(runner, populated_index):
    set_default_connection("postgresql://nonexistent/should-not-be-used")
    result = runner.invoke(
        cli, ["--database", str(populated_index.engine.url), "layers"]
    )
    assert result.exit_code == 0, result.output
    assert "minerals" in result.output


def test_environment_variable(runner, populated_index, monkeypatch):
    monkeypatch.setenv("RASTER_INDEX_DATABASE", str(populated_index.engine.url))
    result = runner.invoke(cli, ["rasters"])
    assert result.exit_code == 0, result.output
    assert "fine" in result.output


def test_assets_command(runner, database_arg):
    result = runner.invoke(
        cli, [*database_arg, "assets", "12", "853", "1549", "--layer", "minerals"]
    )
    assert result.exit_code == 0, result.output


def test_unprovisioned_database_is_reported(runner, raster_index, monkeypatch):
    """A database without the schema gets a pointed message, not a traceback."""
    monkeypatch.setattr(type(raster_index), "schema_exists", lambda self: False)
    result = runner.invoke(cli, ["--database", str(raster_index.engine.url), "layers"])
    assert result.exit_code == 1
    assert "raster_layers" in result.output
