"""
Tests for the engine-reuse behaviour of Database and create_engine.

These tests use SQLite in-memory databases and do not require Docker / PostgreSQL,
so they can run without the session-level ``database_url`` fixture.
"""

import sqlalchemy as sa
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url

from macrostrat.database import Database
from macrostrat.database.utils import create_engine


# ---------------------------------------------------------------------------
# create_engine helpers
# ---------------------------------------------------------------------------


def test_create_engine_reuses_engine():
    """A pre-built Engine must be returned as-is (identity preserved)."""
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    result = create_engine(eng)
    assert result is eng


def test_create_engine_from_string_builds_new_engine():
    """A str URL must produce a fresh Engine (not the same object)."""
    url_str = "sqlite+pysqlite:///:memory:"
    eng = create_engine(url_str)
    assert isinstance(eng, Engine)
    assert str(eng.url) == url_str


def test_create_engine_from_url_object():
    """A SQLAlchemy URL object must produce a fresh Engine."""
    url = make_url("sqlite+pysqlite:///:memory:")
    eng = create_engine(url)
    assert isinstance(eng, Engine)


def test_create_engine_postgresql_driver_rewrite():
    """postgres:// / postgresql:// URLs must have their driver rewritten to psycopg."""
    url_str = "postgresql://localhost/mydb"
    eng = create_engine(url_str)
    assert eng.url.drivername == "postgresql+psycopg"


def test_create_engine_kwargs_ignored_for_prebuilt_engine():
    """Passing kwargs alongside a pre-built Engine must still return the same Engine."""
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    # The warning is emitted through the logger (not warnings.warn), so we just
    # verify the engine identity is preserved regardless.
    result = create_engine(eng, echo=True)
    assert result is eng


# ---------------------------------------------------------------------------
# Database class
# ---------------------------------------------------------------------------


def test_database_engine_identity():
    """Database(engine).engine must be the exact same object that was passed in."""
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    db = Database(eng)
    assert db.engine is eng


def test_database_connect_listener_fires():
    """A 'connect' event listener attached before passing the engine to Database
    must still fire when Database opens a connection."""
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    fired = []
    sa.event.listen(eng, "connect", lambda dbapi_conn, rec: fired.append(1))

    db = Database(eng)
    # Execute a query to force a connection
    with db.engine.connect() as conn:
        conn.execute(sa.text("SELECT 1"))

    assert fired, "connect listener was not called – engine was rebuilt"


def test_database_sqlite_in_memory_query():
    """End-to-end: create a table and query it via a passed-in SQLite engine."""
    eng = sa.create_engine("sqlite+pysqlite:///:memory:")
    db = Database(eng)

    with db.engine.begin() as conn:
        conn.execute(sa.text("CREATE TABLE t (id INTEGER PRIMARY KEY, val TEXT)"))
        conn.execute(sa.text("INSERT INTO t VALUES (1, 'hello')"))

    result = db.run_query("SELECT val FROM t WHERE id = 1").scalar()
    assert result == "hello"
