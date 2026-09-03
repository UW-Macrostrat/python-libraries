"""A basic command-line interface for PostgreSQL database transfer.

For example, stream a schema between databases:

    database-transfer --database "$SOURCE_DATABASE" dump -n temp - \
      | database-transfer --database "$TARGET_DATABASE" restore -
"""

import asyncio
from pathlib import Path

import click
from sqlalchemy import create_engine

from .dump_database import pg_dump_to_file
from .restore_database import pg_restore_from_file


@click.group()
@click.option(
    "--database",
    envvar="DATABASE_URL",
    required=True,
    help="PostgreSQL connection URL.",
)
@click.pass_context
def cli(ctx, database):
    """Dump and restore PostgreSQL databases."""
    ctx.ensure_object(dict)
    ctx.obj["database"] = database


def _engine(ctx):
    return create_engine(ctx.obj["database"])


@cli.command()
@click.option("-n", "--schema", multiple=True, help="Schema to dump.")
@click.argument("destination")
@click.pass_context
def dump(ctx, schema, destination):
    """Dump to DESTINATION, or '-' for standard output."""
    args = [arg for name in schema for arg in ("--schema", name)]
    engine = _engine(ctx)
    try:
        asyncio.run(
            pg_dump_to_file(
                engine, None if destination == "-" else Path(destination), args=args
            )
        )
    finally:
        engine.dispose()


@cli.command()
@click.argument("source")
@click.pass_context
def restore(ctx, source):
    """Restore from SOURCE, or '-' for standard input."""
    engine = _engine(ctx)
    try:
        asyncio.run(
            pg_restore_from_file(None if source == "-" else Path(source), engine)
        )
    finally:
        engine.dispose()


if __name__ == "__main__":
    cli()
