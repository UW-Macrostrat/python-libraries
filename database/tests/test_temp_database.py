"""
Tests for functionality to create temporary databases
"""

from pytest import mark
from sqlalchemy import create_engine, text
from sqlalchemy_utils import database_exists

from macrostrat.database.query import run_query
from macrostrat.database.utils import (
    create_database,
    drop_database,
    maintenance_url,
    template_database,
    temporary_database,
)


@mark.parametrize("force_drop", [True, False])
def test_temp_database(database_url, force_drop):
    new_database_url = database_url.set(database="temp_test_database_2")
    with temporary_database(
        new_database_url, drop=True, force_drop=force_drop
    ) as engine:
        # Create a connection to test whether the database can be dropped with active connections
        with engine.connect() as conn:
            res = run_query(conn, "SELECT 1").scalar()
            assert res == 1

    # Check that the database was dropped
    assert database_exists(new_database_url) == False


def test_template_database(database_url):
    with template_database(database_url, close_source_connections=True) as engine:
        # Create a connection to test whether the database can be dropped with active connections
        with engine.connect() as conn:
            res = run_query(conn, "SELECT 1").scalar()
            assert res == 1


def test_force_drop_does_not_rely_on_libpq_defaults(database_url, monkeypatch):
    """Dropping must name its maintenance database explicitly.

    Cluster-level work used to connect with no database at all, leaving libpq to
    fall back to ``PGDATABASE``. Anything that exports one (``macrostrat.core.config``
    does) then sends the drop to an unrelated database — failing with a confusing
    "database ... does not exist" naming a database the caller never mentioned, or
    worse, succeeding against the wrong one.
    """
    monkeypatch.setenv("PGDATABASE", "a_database_that_does_not_exist")

    new_database_url = database_url.set(database="temp_test_force_drop")
    create_database(new_database_url, exists_ok=True)
    assert database_exists(new_database_url)

    drop_database(new_database_url, force=True)
    assert not database_exists(new_database_url)


def test_maintenance_url_avoids_the_target_database(database_url):
    """The maintenance connection must not be to the database being operated on."""
    assert maintenance_url(database_url.set(database="postgres")).database == "template1"
    assert maintenance_url(database_url.set(database="anything_else")).database == "postgres"
    assert maintenance_url(database_url, exclude="postgres").database == "template1"


def test_template_database_copies_database_settings(database_url):
    """A copy should behave like its source, not just contain the same objects.

    CREATE DATABASE ... TEMPLATE does not copy pg_db_role_setting, so without this
    a clone silently differs in search_path — which is how PostGIS topology
    databases end up reporting schema drift against their own copies.
    """
    source_url = database_url.set(database="temp_test_settings_source")
    create_database(source_url, exists_ok=True)
    try:
        engine = create_engine(
            source_url, execution_options={"isolation_level": "AUTOCOMMIT"}
        )
        with engine.connect() as conn:
            conn.execute(
                text(
                    'ALTER DATABASE "temp_test_settings_source"'
                    ' SET search_path = "$user", public, topology'
                )
            )
        engine.dispose()

        with template_database(source_url, close_source_connections=True) as clone:
            with clone.connect() as conn:
                assert "topology" in conn.execute(text("show search_path")).scalar()

        # Opt out and the copy is left with the cluster default.
        with template_database(
            source_url, close_source_connections=True, copy_settings=False
        ) as clone:
            with clone.connect() as conn:
                assert "topology" not in conn.execute(text("show search_path")).scalar()
    finally:
        drop_database(source_url, force=True, allow_missing=True)
