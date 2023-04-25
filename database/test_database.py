# Skeletal testing file
from pathlib import Path
from pytest import fixture
from dotenv import load_dotenv
from psycopg2.sql import SQL, Identifier, Literal, Placeholder
from sqlalchemy.exc import ProgrammingError

from macrostrat.utils import relative_path, get_logger
from macrostrat.database import Database, run_sql
from macrostrat.database.utils import temp_database, infer_is_sql_text
from pytest import warns, raises


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


def test_sql_text_inference():
    assert infer_is_sql_text("SELECT * FROM sample")


def test_sql_text_inference_2():
    assert infer_is_sql_text(b"SELECT * FROM sample")


def test_sql_text_inference_3():
    assert not infer_is_sql_text("sample.sql")


def test_sql_interpolation_psycopg(db):
    sql = "INSERT INTO sample (name) VALUES (:name)"
    assert infer_is_sql_text(sql)

    # db.engine.execute(sql, name="Test")
    db.run_sql(sql, params=dict(name="Test"), stop_on_error=True)
    db.session.commit()

    sql1 = "SELECT * FROM sample WHERE name = :name"
    res = list(db.run_sql(sql1, params=dict(name="Test"), stop_on_error=True))[0]
    assert res.first().name == "Test"


def test_extraneous_argument(db):
    sql = "INSERT INTO sample (name) VALUES (:name)"
    assert infer_is_sql_text(sql)

    # db.engine.execute(sql, name="Test")
    db.run_sql(sql, params=dict(name="Test2", extraneous="TestA"))



def test_sql_identifier(db):
    sql = SQL("SELECT name FROM {table} WHERE name = {name}").format(
        table=Identifier("sample"),
        name=Literal("Test")
    ).as_string(db.engine.raw_connection().cursor())
    assert infer_is_sql_text(sql)
    res = list(db.run_sql(sql, stop_on_error=True))
    assert len(res) == 1
    assert res[0].scalar() == "Test"

def test_partial_identifier(db):
    """https://www.postgresql.org/docs/current/sql-prepare.html"""
    conn = db.engine.raw_connection()
    cursor = conn.cursor()
    sql = SQL("SELECT name FROM sam{partial_table} WHERE name = {name}").format(
        name=Placeholder("name"),
        partial_table=SQL("ple")
    ).as_string(cursor)

    res = db.engine.execute(sql, name="Test").scalar()
    assert res == "Test"
    
def test_deprecated_keyword(db):
    sql1 = "SELECT * FROM sample WHERE name = :name"
    # Check that it raises the appropriate warning
    with warns(DeprecationWarning):
        db.run_sql(sql1, params=dict(name="Test"), stop_on_error=True)

def test_query_error(db):
    sql1 = "SELECT * FROM samplea WHERE name = :name"
    with raises(ProgrammingError):
        db.run_sql(sql1, params=dict(name="Test"), stop_on_error=True)