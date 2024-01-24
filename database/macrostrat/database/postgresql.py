from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from typing import TYPE_CHECKING

import psycopg2
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import CompileError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import Insert, text

if TYPE_CHECKING:
    from ..database import Database

_insert_mode = ContextVar("insert-mode", default="do-nothing")


# https://stackoverflow.com/questions/33307250/postgresql-on-conflict-in-sqlalchemy/62305344#62305344
@contextmanager
def on_conflict(action="restrict"):
    token = _insert_mode.set(action)
    try:
        yield
    finally:
        _insert_mode.reset(token)


# @compiles(Insert, "postgresql")
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
