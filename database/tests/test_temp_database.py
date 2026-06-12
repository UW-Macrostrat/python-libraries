"""
Tests for functionality to create temporary databases
"""

from macrostrat.database.utils import temp_database


def test_temp_database(database_url):
    new_database_url = database_url.set(database="temp_test_database_2")

    with temp_database(new_database_url, drop=True, force_drop=True) as engine:
        # Create a connection to test whether the database can be dropped with active connections
        conn = engine.connect()
