from psycopg2.sql import Literal
from pytest import fixture, raises
from sqlalchemy.exc import StatementError

from .test_database import db, empty_db, engine


@fixture
def db_with_instance_params(db):
    db.instance_params = {"test": "value", "test2": Literal("value2")}
    yield db
    db.instance_params = {}


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


def test_apply_fixtures(db):
    assert True
