# Skeletal testing file
from pathlib import Path
from macrostrat.utils import relative_path
from macrostrat.database import Database

from pytest import fixture
from os import environ
from macrostrat.database.utils import wait_for_database, temp_database, create_database
from macrostrat.database import Database
from dotenv import load_dotenv

load_dotenv()


@fixture(scope="session")
def engine(database_url, pytestconfig):
    with temp_database(database_url, drop=pytestconfig.option.teardown) as engine:
        yield engine


@fixture(scope="session")
def db(engine):
    return Database(engine.url)


def test_database(db):
    # Get schema files
    schema_files = Path(relative_path(__file__, "test-fixtures")).glob("*.sql")

    file_list = list(schema_files)
    assert len(file_list) == 1

    # Create tables
    for sqlfile in file_list:
        db.exec_sql(sqlfile)

    db.automap(schemas=["public", "geology"])

    # Test that tables exist
    assert "sample" in db.model
    assert "geology_formation" in db.model


def test_database_mapper(db):
    Sample = db.model.sample
    Formation = db.model.geology_formation

    assert Sample.__table__.name == "sample"

    s = Sample(name="Test")
    f = Formation(name="Test")
    s._formation = f

    assert isinstance(s._formation, Formation)
