"""Tests for the ``on_error`` statement-recovery hook.

The SQLite tests cover the generic mechanism; the PostgreSQL tests cover
behavior that only a real backend exercises — chiefly that a statement error
aborts the transaction and recovery only succeeds because the loop rolls back
first, plus the actual ``CREATE OR REPLACE VIEW`` (SQLSTATE 42P16) scenario the
schema-management view rebuild depends on.
"""

from sqlalchemy import create_engine, make_url, text
from sqlalchemy.exc import ProgrammingError
from pytest import fixture, raises

from macrostrat.database import Database
from macrostrat.database.query import run_sql, StatementContext, StatementDirective
from macrostrat.database.utils import temporary_database


# --- SQLite: the generic mechanism ---------------------------------------


def test_on_error_recovers_and_reexecutes():
    """A failing statement can be recovered by returning replacement statements."""
    engine = create_engine("sqlite://")
    seen: list[StatementContext] = []

    def recover(ctx, err, connectable):
        seen.append(ctx)
        assert connectable is not None  # the recovery handler gets the connection
        # Create the missing table, then retry the original statement.
        return [
            StatementDirective(query="CREATE TABLE t (id INTEGER)"),
            StatementDirective(query=ctx.query),
        ]

    with engine.connect() as conn:
        run_sql(conn, "INSERT INTO t (id) VALUES (1)", on_error=recover, raise_errors=True)
        count = conn.execute(text("SELECT count(*) FROM t")).scalar()

    assert count == 1
    assert len(seen) == 1
    assert "insert into t" in seen[0].sql_text.lower()


def test_on_error_declining_falls_through_to_raise():
    """Returning None from the handler leaves normal error handling in place."""
    engine = create_engine("sqlite://")

    def decline(ctx, err, connectable):
        return None

    with engine.connect() as conn:
        with raises(Exception):
            run_sql(
                conn,
                "INSERT INTO missing (id) VALUES (1)",
                on_error=decline,
                raise_errors=True,
            )


def test_on_error_recovery_statements_do_not_recurse():
    """A failing recovery statement is not itself handed back to the handler."""
    engine = create_engine("sqlite://")
    calls = []

    def recover(ctx, err, connectable):
        calls.append(ctx.sql_text)
        return [StatementDirective(query="INSERT INTO still_missing (id) VALUES (1)")]

    with engine.connect() as conn:
        with raises(Exception):
            run_sql(
                conn,
                "INSERT INTO missing (id) VALUES (1)",
                on_error=recover,
                raise_errors=True,
            )
    assert len(calls) == 1  # handler fired once, recovery did not recurse


# --- PostgreSQL: real-backend behavior -----------------------------------


@fixture(scope="module")
def pg_db(database_url, pytestconfig):
    """A throwaway PostgreSQL database for exercising on_error on a real backend."""
    url = make_url(str(database_url)).set(database="macrostrat_on_error_test")
    with temporary_database(
        url,
        drop=pytestconfig.option.teardown,
        force_drop=True,
        ensure_empty=True,
    ) as engine:
        yield Database(engine)


def test_pg_recovery_after_aborted_transaction(pg_db):
    """After a statement errors (aborting the txn), recovery still runs.

    This only passes because the loop rolls back before invoking the handler —
    PostgreSQL would otherwise reject every subsequent command in the aborted
    transaction. SQLite does not exercise this.
    """
    db = pg_db
    db.run_sql("DROP TABLE IF EXISTS oops CASCADE", raise_errors=True)

    def recover(ctx, err, connectable):
        return [
            StatementDirective(query="CREATE TABLE oops (id int)"),
            StatementDirective(query=ctx.query),  # retry the original insert
        ]

    db.run_sql(
        "INSERT INTO oops (id) VALUES (1)", on_error=recover, raise_errors=True
    )

    assert db.run_query("SELECT count(*) FROM oops").scalar() == 1


def test_pg_view_replace_conflict_recovery(pg_db):
    """The real view-rebuild scenario: CREATE OR REPLACE fails with 42P16, and
    recovery drops & recreates. Also validates the SQLSTATE constant views.py uses."""
    db = pg_db
    db.run_sql("DROP VIEW IF EXISTS v CASCADE", raise_errors=True)
    db.run_sql("DROP TABLE IF EXISTS t CASCADE", raise_errors=True)
    db.run_sql("CREATE TABLE t (a int, b int)", raise_errors=True)
    db.run_sql("CREATE VIEW v AS SELECT a, b FROM t", raise_errors=True)

    seen = {}

    def recover(ctx, err, connectable):
        orig = getattr(err, "orig", None)
        seen["sqlstate"] = getattr(orig, "sqlstate", None) or getattr(orig, "pgcode", None)
        return [
            StatementDirective(query="DROP VIEW IF EXISTS v CASCADE"),
            StatementDirective(query="CREATE VIEW v AS SELECT a FROM t"),
        ]

    # Dropping column b makes CREATE OR REPLACE VIEW illegal → SQLSTATE 42P16.
    db.run_sql(
        "CREATE OR REPLACE VIEW v AS SELECT a FROM t",
        on_error=recover,
        raise_errors=True,
    )

    assert seen["sqlstate"] == "42P16"  # the constant _is_replace_conflict relies on
    n_cols = db.run_query(
        "SELECT count(*) FROM information_schema.columns WHERE table_name = 'v'"
    ).scalar()
    assert n_cols == 1  # the view was recreated with only column a


def test_pg_decline_reraises(pg_db):
    """Declining recovery surfaces the real PostgreSQL error."""
    db = pg_db
    with raises(ProgrammingError):
        db.run_sql(
            "INSERT INTO definitely_missing_xyz (id) VALUES (1)",
            on_error=lambda ctx, err, connectable: None,
            raise_errors=True,
        )
