"""
Tests for Database.get_table() and Database.get_model().
"""

from pathlib import Path

import pytest
from pytest import fixture
from sqlalchemy import Table, insert, select

from macrostrat.database import Database
from macrostrat.database.query import run_sql

fixtures_dir = Path(__file__).parent / "fixtures"


@fixture(scope="module")
def db(database_url):
    """Database with the test schema applied."""
    db = Database(database_url)
    run_sql(db.engine, fixtures_dir / "test-schema.sql")
    yield db
    db.cleanup()


@fixture
def fresh_db(database_url):
    """A freshly constructed Database with no mapper state."""
    db = Database(database_url)
    yield db
    db.cleanup()


# ---------------------------------------------------------------------------
# get_table
# ---------------------------------------------------------------------------


def test_get_table_bare_name(db):
    tbl = db.get_table("sample")
    assert isinstance(tbl, Table)
    assert tbl.name == "sample"


def test_get_table_qualified_name(db):
    tbl = db.get_table("public.sample")
    assert tbl.name == "sample"

    tbl2 = db.get_table("geology.formation")
    assert tbl2.name == "formation"


def test_get_table_tuple_form(db):
    tbl = db.get_table(("geology", "formation"))
    assert tbl.name == "formation"


def test_get_table_schema_kwarg(db):
    tbl = db.get_table("formation", schema="geology")
    assert tbl.name == "formation"


def test_get_table_caching(db):
    """Repeated calls return the exact same Table object."""
    t1 = db.get_table("sample")
    t2 = db.get_table("sample")
    assert t1 is t2

    # "public.sample" and "sample" are the same cache entry
    t3 = db.get_table("public.sample")
    assert t1 is t3


def test_get_table_subscript(db):
    """db[name] is a shorthand for db.get_table(name)."""
    assert db["public.sample"] is db.get_table("sample")
    assert db["geology.formation"] is db.get_table("geology.formation")


def test_get_table_insert(db):
    """get_table result works with SQLAlchemy Core insert/select builders."""
    tbl = db.get_table("geology.formation")
    db.session.execute(insert(tbl).values(name="Shale", description="Test"))
    db.session.commit()

    row = db.session.execute(
        select(tbl).where(tbl.c.name == "Shale")
    ).fetchone()
    assert row is not None
    assert row.name == "Shale"

    db.session.execute(tbl.delete().where(tbl.c.name == "Shale"))
    db.session.commit()


# ---------------------------------------------------------------------------
# get_model
# ---------------------------------------------------------------------------


def test_get_model_lazy_automap(fresh_db):
    """get_model triggers automap for the schema on first use."""
    assert fresh_db.mapper is None
    model = fresh_db.get_model("sample")
    assert fresh_db.mapper is not None
    assert "public" in fresh_db.mapper._reflected_schemas
    assert model.__table__.name == "sample"


def test_get_model_qualified_name(fresh_db):
    model = fresh_db.get_model("geology.formation")
    assert model.__table__.name == "formation"
    assert "geology" in fresh_db.mapper._reflected_schemas


def test_get_model_no_automap_raises(fresh_db):
    """automap=False raises LookupError instead of triggering reflection."""
    with pytest.raises(LookupError):
        fresh_db.get_model("sample", automap=False)


def test_get_model_caching(fresh_db):
    """Repeated get_model calls return the same class."""
    m1 = fresh_db.get_model("sample")
    m2 = fresh_db.get_model("sample")
    assert m1 is m2


def test_get_model_matches_explicit_automap(fresh_db):
    """After an explicit automap(), get_model returns the same class."""
    fresh_db.automap(schemas=["public"])
    model_via_automap = fresh_db.model.sample
    model_via_get = fresh_db.get_model("sample")
    assert model_via_automap is model_via_get


def test_get_table_reuses_automapped_table(fresh_db):
    """get_table returns the mapper's Table after automap, without re-reflecting."""
    fresh_db.automap(schemas=["public"])
    automap_table = fresh_db.model.sample.__table__
    cached_table = fresh_db.get_table("sample")
    assert cached_table is automap_table
