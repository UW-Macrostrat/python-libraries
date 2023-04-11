# Skeletal testing file
from pathlib import Path
from pytest import fixture
from dotenv import load_dotenv

from macrostrat.utils import relative_path, get_logger
from macrostrat.database import Database, run_sql
from macrostrat.database.utils import temp_database


load_dotenv()

log = get_logger(__name__)


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
        res = run_sql(db.engine, sqlfile)
        assert len(res) == 3

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
