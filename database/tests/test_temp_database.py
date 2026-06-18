"""
Tests for functionality to create temporary databases
"""

from pytest import mark
from sqlalchemy_utils import database_exists

from macrostrat.database.query import run_query
from macrostrat.database.utils import temporary_database, template_database


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
