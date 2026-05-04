from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from enum import Enum
from typing import Any, Sequence, TYPE_CHECKING

from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.dml import Insert
from sqlalchemy.sql.expression import text

if TYPE_CHECKING:
    from ..database import Database


class OnConflictAction(str, Enum):
    DO_NOTHING = "do-nothing"
    DO_UPDATE = "do-update"
    RESTRICT = "restrict"


_insert_mode = ContextVar("insert-mode", default="restrict")


# https://stackoverflow.com/questions/33307250/postgresql-on-conflict-in-sqlalchemy/62305344#62305344
@contextmanager
def on_conflict(action="restrict"):
    token = _insert_mode.set(action)
    try:
        yield
    finally:
        _insert_mode.reset(token)


@compiles(Insert, "postgresql")
def prefix_inserts(insert, compiler, **kw):
    """Conditionally adapt insert statements to use on-conflict resolution (a PostgreSQL feature)"""

    if insert._post_values_clause is not None:
        return compiler.visit_insert(insert, **kw)

    action = _insert_mode.get()
    if action == "do-update":
        try:
            params = insert.compile().params
        except CompileError:
            params = {}
        vals = {
            name: value
            for name, value in params.items()
            if (
                name not in insert.table.primary_key
                and name in insert.table.columns
                and value is not None
            )
        }
        if vals:
            insert._post_values_clause = postgresql.dml.OnConflictDoUpdate(
                index_elements=insert.table.primary_key, set_=vals
            )
        else:
            action = "do-nothing"
    if action == "do-nothing":
        insert._post_values_clause = postgresql.dml.OnConflictDoNothing(
            index_elements=insert.table.primary_key
        )
    return compiler.visit_insert(insert, **kw)


def upsert(
    table,
    values: dict[str, Any],
    *,
    index_elements: Sequence[str] | None = None,
    on_conflict: OnConflictAction = OnConflictAction.DO_UPDATE,
):
    _index = index_elements
    stmt = postgresql.insert(table).values(values)
    if on_conflict == "restrict":
        return stmt

    if on_conflict == "do-nothing":
        return stmt.on_conflict_do_nothing(index_elements=_index)

    if _index is None:
        _index = table.primary_key.columns.keys()
    _index = list(_index)

    update_values = {
        column.name: getattr(stmt.excluded, column.name)
        for column in table.columns
        if column.name not in _index
    }

    if len(update_values) == 0:
        return stmt

    return stmt.on_conflict_do_update(
        index_elements=_index,
        set_=update_values,
    )


def table_exists(db: Database, table_name: str, schema: str = "public") -> bool:
    """Check if a table exists in a PostgreSQL database."""
    sql = """SELECT EXISTS (
        SELECT FROM information_schema.tables
        WHERE table_schema = :schema
          AND table_name = :table_name
    );"""

    return db.session.execute(
        text(sql), params=dict(schema=schema, table_name=table_name)
    ).scalar()
