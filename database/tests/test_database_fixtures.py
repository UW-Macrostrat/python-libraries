from pathlib import Path

from psycopg2.sql import Literal
from pytest import fixture, raises
from sqlalchemy.exc import StatementError

from macrostrat.database.postgresql import table_exists

from .test_database import db, empty_db, engine


@fixture
def db_with_instance_params(db):
    db.instance_params = {"test": "value", "test2": Literal("value2")}
    yield db
    db.instance_params = {}


@fixture
def rollback_db(db):
    with db.transaction(rollback=True):
        yield db


@fixture
def savepoint_db(db):
    with db.savepoint(rollback=True):
        yield db


def test_instance_params(db_with_instance_params):
    res = db_with_instance_params.run_query("SELECT :test").scalar()
    assert res == "value"


def test_instance_params_literal(db_with_instance_params):
    res = db_with_instance_params.run_query("SELECT {test2}").scalar()
    assert res == "value2"


def test_instance_params(db_with_instance_params):
    try:
        db_with_instance_params.run_query(
            "SELECT :test", use_instance_params=False
        ).scalar()
        assert False
    except StatementError:
        assert True


fixture_dir = Path(__file__).parent / "fixtures" / "schema-dir"


def test_apply_fixtures_transaction(db):
    with db.transaction(rollback=True):
        db.run_fixtures(fixture_dir)
        assert table_exists(db, "table1", schema="test1")
    assert not table_exists(db, "table1", schema="test1")


def test_apply_fixtures_savepoint(db):
    with db.savepoint(rollback=True):
        db.run_fixtures(fixture_dir)
        assert table_exists(db, "table1", schema="test1")
    assert not table_exists(db, "table1", schema="test1")


def test_nested_savepoint(db):
    with db.savepoint(rollback=False):
        db.run_fixtures(fixture_dir)
        assert table_exists(db, "table1", schema="test1")
        with db.savepoint(rollback=True):
            db.run_query("DROP TABLE test1.table1")
            assert not table_exists(db, "table1", schema="test1")
        assert table_exists(db, "table1", schema="test1")
        with db.savepoint(rollback=False):
            db.run_query("DROP TABLE test1.table2")
            assert not table_exists(db, "table2", schema="test1")
        assert not table_exists(db, "table2", schema="test1")
    assert table_exists(db, "table1", schema="test1")
    assert not table_exists(db, "table2", schema="test1")
