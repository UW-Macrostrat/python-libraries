import asyncio
from types import SimpleNamespace

from click.testing import CliRunner

from macrostrat.database.transfer import dump_database, restore_database
from macrostrat.database.transfer.cli import cli


def test_dump_to_stdout_inherits_standard_output(monkeypatch):
    calls = {}

    async def fake_dump(engine, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(stderr=object(), wait=fake_wait)

    async def fake_wait():
        calls["waited"] = True

    async def fake_print_stdout(stream):
        calls["stderr"] = stream

    monkeypatch.setattr(dump_database, "pg_dump", fake_dump)
    monkeypatch.setattr(dump_database, "print_stdout", fake_print_stdout)

    asyncio.run(dump_database.pg_dump_to_file(object(), None))

    assert calls["stdout"] is None
    assert calls["stderr"] is not None
    assert calls["waited"] is True


def test_restore_from_stdin_inherits_standard_input(monkeypatch):
    calls = {}

    async def fake_restore(engine, **kwargs):
        calls.update(kwargs)
        return SimpleNamespace(stderr=object(), wait=fake_wait)

    async def fake_wait():
        calls["waited"] = True

    async def fake_print_stdout(stream):
        calls["stderr"] = stream

    monkeypatch.setattr(restore_database, "pg_restore", fake_restore)
    monkeypatch.setattr(restore_database, "print_stdout", fake_print_stdout)

    asyncio.run(restore_database.pg_restore_from_file(None, object()))

    assert calls["stdin"] is None
    assert calls["stderr"] is not None
    assert calls["waited"] is True


def test_cli_streams_schema_dump_to_standard_output(monkeypatch):
    calls = {}

    async def fake_dump(engine, destination, **kwargs):
        calls["url"] = str(engine.url)
        calls["destination"] = destination
        calls["args"] = kwargs["args"]

    monkeypatch.setattr(
        "macrostrat.database.transfer.cli.pg_dump_to_file", fake_dump
    )

    result = CliRunner().invoke(
        cli, ["--database", "postgresql://localhost/source", "dump", "-n", "temp", "-"]
    )

    assert result.exit_code == 0, result.output
    assert calls == {
        "url": "postgresql://localhost/source",
        "destination": None,
        "args": ["--schema", "temp"],
    }


def test_cli_restores_from_standard_input(monkeypatch):
    calls = {}

    async def fake_restore(source, engine):
        calls["source"] = source
        calls["url"] = str(engine.url)

    monkeypatch.setattr(
        "macrostrat.database.transfer.cli.pg_restore_from_file", fake_restore
    )

    result = CliRunner().invoke(
        cli, ["--database", "postgresql://localhost/target", "restore", "-"]
    )

    assert result.exit_code == 0, result.output
    assert calls == {"source": None, "url": "postgresql://localhost/target"}
