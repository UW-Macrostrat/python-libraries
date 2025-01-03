import asyncio

from sqlalchemy.engine import Engine

from macrostrat.utils import get_logger

from .dump_database import pg_dump
from .restore_database import pg_restore
from .stream_utils import print_stdout, print_stream_progress

log = get_logger(__name__)


async def move_tables(
    from_database: Engine,
    to_database: Engine,
    *,
    tables: list | None = None,
    schemas: list | None = None,
    dump_args: list | None = None,
    restore_args: list | None = None,
    **kwargs,
):
    """Transfer tables from one database to another."""

    # Prevent mutation of default arguments
    if tables is None:
        tables = []
    if schemas is None:
        schemas = []
    if dump_args is None:
        dump_args = []
    if restore_args is None:
        restore_args = []

    if len(tables) == 0 and len(schemas) == 0:
        raise ValueError("Must specify at least one table or schema to transfer")

    for schema in schemas:
        dump_args += ["--schema", schema]
    for table in tables:
        dump_args += ["--table", table]

    log.debug(f"Transfer tables: {tables}")
    log.debug(f"Dump args: {dump_args}")
    log.debug(f"Restore args: {restore_args}")

    source = await pg_dump(from_database, **kwargs, args=dump_args)
    dest = await pg_restore(to_database, **kwargs, args=restore_args)

    await asyncio.gather(
        asyncio.create_task(print_stream_progress(source.stdout, dest.stdin)),
        asyncio.create_task(print_stdout(source.stderr)),
        asyncio.create_task(print_stdout(dest.stderr)),
    )
