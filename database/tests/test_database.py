"""
Test the database module.

NOTE: At the moment, these tests are not independent and must run in order.
"""

from io import StringIO, TextIOWrapper
from pathlib import Path
from sys import stdout

from dotenv import load_dotenv
from psycopg2.errors import SyntaxError
from psycopg2.extensions import AsIs
from psycopg2.sql import SQL, Identifier, Literal, Placeholder
from pytest import fixture, raises, warns
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.sql import text

from macrostrat.database import Database, run_sql
from macrostrat.database.postgresql import table_exists
from macrostrat.database.utils import (
    _print_error,
    infer_is_sql_text,
    temp_database,
    run_fixtures,
)
from macrostrat.utils import get_logger, relative_path

load_dotenv()

log = get_logger(__name__)


@fixture(scope="session")
def engine(database_url, pytestconfig):
    with temp_database(database_url, drop=pytestconfig.option.teardown) as engine:
        yield engine


@fixture(scope="session")
def empty_db(engine):
    return Database(engine.url)


@fixture(scope="session")
def db(empty_db):
    # Get schema files
    schema_files = Path(relative_path(__file__, "fixtures")).glob("*.sql")

    file_list = list(schema_files)
    assert len(file_list) == 1

    # Create tables
    for sqlfile in file_list:
        res = run_sql(empty_db.engine, sqlfile)
        assert len(res) == 3
    return empty_db


@fixture(scope="function")
def conn(db):
    """A connection managed by the database session."""
    connection = db.session.connection()
    yield connection
    db.session.rollback()


def test_database(db):
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


def test_sql_text_inference_4():
    assert not infer_is_sql_text("select.sql")


def test_sql_text_inference_5():
    assert not infer_is_sql_text("SELECT.sql")


insert_sample_query = "INSERT INTO sample (name) VALUES (:name)"


def test_sql_text_inference_6():
    assert infer_is_sql_text(insert_sample_query)


def test_sql_statement_filtering(db):
    sql = """
    INSERT INTO sample (name) VALUES (:name);

    DELETE FROM sample WHERE name = :name;
    """

    assert infer_is_sql_text(sql)

    with db.transaction(rollback="always"):
        # Make sure there are no samples
        assert _get_sample_count(db) == 0

        # Run the SQL, filtering out the DELETE statement

        def filter_func(statement, params):
            return not statement.startswith("DELETE")

        res = db.run_sql(
            sql,
            params=dict(name="Test"),
            raise_errors=True,
            statement_filter=filter_func,
            yield_results=False,
        )

        assert len(res) == 1
        assert _get_sample_count(db) == 1


def _get_sample_count(db):
    return db.run_query("SELECT count(*) FROM sample").scalar()


def test_sql_interpolation_psycopg(db):
    db.run_sql(insert_sample_query, params=dict(name="Test"), raise_errors=True)
    db.session.commit()

    sql1 = "SELECT * FROM sample WHERE name = :name"
    res = list(db.run_sql(sql1, params=dict(name="Test"), raise_errors=True))[0]
    assert res.first().name == "Test"


def test_extraneous_argument(db):
    # db.engine.execute(sql, name="Test")
    db.run_sql(insert_sample_query, params=dict(name="Test2", extraneous="TestA"))


def test_sql_identifier(db):
    sql = (
        SQL("SELECT name FROM {table} WHERE name = {name}")
        .format(table=Identifier("sample"), name=Literal("Test"))
        .as_string(db.engine.raw_connection().cursor())
    )
    assert infer_is_sql_text(sql)
    res = list(db.run_sql(sql, raise_errors=True))
    assert len(res) == 1
    assert res[0].scalar() == "Test"


def test_raises_deprecation(db):
    sql = (
        SQL("SELECT name FROM {table} WHERE name = {name}")
        .format(table=Identifier("sample"), name=Literal("Test"))
        .as_string(db.engine.raw_connection().cursor())
    )
    with warns(DeprecationWarning):
        db.run_sql(sql, stop_on_error=True)


def test_partial_identifier(db):
    """https://www.postgresql.org/docs/current/sql-prepare.html"""
    conn = db.engine.raw_connection()
    cursor = conn.cursor()
    sql = (
        SQL("SELECT name FROM sam{partial_table} WHERE name = {name}")
        .format(name=Placeholder("name"), partial_table=SQL("ple"))
        .as_string(cursor)
    )

    with db.engine.begin() as conn:
        res = conn.exec_driver_sql(sql, dict(name="Test")).scalar()
        assert res == "Test"


def test_deprecated_keyword(db):
    sql1 = "SELECT * FROM sample WHERE name = :name"
    # Check that it raises the appropriate warning
    with warns(DeprecationWarning):
        db.run_sql(sql1, params=dict(name="Test"), stop_on_error=True)


def test_query_error(db):
    sql1 = "SELECT * FROM samplea WHERE name = :name"
    with raises(ProgrammingError), warns(DeprecationWarning):
        db.run_sql(sql1, params=dict(name="Test"), stop_on_error=True)


def test_query_error_1(db):
    sql1 = "SELECT * FROM samplea WHERE name = :name"
    with raises(ProgrammingError):
        db.run_sql(sql1, params=dict(name="Test"), raise_errors=True)


def test_sql_object(db):
    sql = SQL("SELECT name FROM {table} WHERE name = {name}")
    params = dict(table=Identifier("sample"), name=Literal("Test"))

    res = list(db.run_sql(sql, raise_errors=True, params=params))
    assert len(res) == 1
    assert res[0].scalar() == "Test"


def test_sqlalchemy_bound_parameters(db):
    """Some of the parameters should be pre-bound."""
    sql = "SELECT {column} FROM {table} WHERE {column} = :value"
    params = dict(column=Identifier("name"), table=Identifier("sample"), value="Test")
    db.run_sql(sql, params=params, raise_errors=True)


def test_server_bound_parameters(db):
    """If we have Postgres-style string bind parameters, make sure we don't try to bind SQLAlchemy parameters."""
    sql = "SELECT name FROM sample WHERE name = %(name)s"
    params = dict(name="Test")
    res = list(db.run_sql(sql, params=params, raise_errors=True))
    assert len(res) == 1
    assert res[0].scalar() == "Test"


def test_server_bound_parameters_mixed(db):
    sql = "SELECT name FROM {table_name} WHERE name = %(name)s"
    res = db.run_query(sql, {"name": "Test", "table_name": Identifier("sample")})
    assert res.scalar() == "Test"


def test_server_bound_parameters_invalid(db):
    sql = "SELECT name FROM %(table_name)s WHERE name = %(name)s"
    with raises(KeyError):
        db.run_query(sql, {"name": "Test", "table_name": Identifier("sample")})


def test_server_bound_parameters_invalid_2(db):
    sql = "SELECT name FROM %(table_name)s WHERE name = %(name)s"
    try:
        db.run_query(sql, {"name": "Test", "table_name": "sample"})
    except ProgrammingError as e:
        assert isinstance(e.orig, SyntaxError)
    else:
        assert False


def test_server_bound_parameters_invalid_3(db):
    sql = "SELECT name FROM {table_name} WHERE name = :name"
    try:
        db.run_query(sql, {"name": "Test", "table_name": "sample"})
    except ProgrammingError as e:
        assert isinstance(e.orig, SyntaxError)
    else:
        assert False


def test_server_bound_parameters_dbapi_extensions(db):
    sql = "SELECT name FROM %(table_name)s WHERE name = %(name)s"
    res = db.run_query(sql, {"name": "Test", "table_name": AsIs("sample")})
    assert res.scalar() == "Test"


def test_server_parameters_function_def(db):
    """Make sure we don't select all % as bound parameters."""
    sql = """
    CREATE OR REPLACE FUNCTION throw_error()
    RETURNS void AS $$
    BEGIN
    IF true THEN
        RAISE NOTICE 'prop %s, pattern %, schema %', prop, pattern, schema->'patternProperties'->pattern;
    END IF;
    END;
    $$ LANGUAGE plpgsql;
    """
    with raises(TypeError):
        db.run_sql(sql, raise_errors=True)
    # This should not raise
    db.run_sql(sql, raise_errors=True, has_server_binds=False)


def test_long_running_sql(db):
    sql = "SELECT pg_sleep(0.5)"
    res = list(db.run_sql(sql, raise_errors=True))
    assert len(res) == 1
    assert res[0].scalar() == ""


def test_run_query(db):
    sql = "SELECT name FROM sample WHERE name = %(name)s"
    res = db.run_query(sql, dict(name="Test"))
    assert res.scalar() == "Test"


def test_run_query_2(db):
    sql = "SELECT name FROM sample WHERE name = %(name)s"
    res = db.run_query(sql, dict(name="Test"))
    r1 = list(res)
    assert len(r1) == 1
    assert r1[0][0] == "Test"
    assert r1[0].name == "Test"


def test_ambiguous_comment_bind(db):
    sql = """
    /* This is a comment {with_fake_param} */
    SELECT name FROM sample WHERE name = {name}
    """
    res = db.run_query(sql, dict(name=Literal("Test")))
    data = res.scalar()
    assert data == "Test"


def test_copy_statement(db):
    pg_conn = db.engine.connect().connection
    cur = pg_conn.cursor()

    cur.copy_expert("COPY sample (name) TO STDOUT", stdout)


def test_close_connection(conn):
    """
    Basic test demonstrating the underlying capability to kill a long-running query
    by closing the connection to the database.
    """

    import threading

    from psycopg2.extensions import QueryCanceledError
    from sqlalchemy.exc import DBAPIError

    sql = text("SELECT pg_sleep(10)")

    seconds = 1
    t = threading.Timer(seconds, conn.connection.cancel)
    t.start()

    try:
        conn.execute(sql)
        assert False
    except DBAPIError as e:
        if type(e.orig) == QueryCanceledError:
            print("Long running query was cancelled.")
            assert True
    t.cancel()


def test_sigint_cancel(db):
    """
    Basic test demonstrating the underlying capability to kill a long-running query
    by sending a SIGINT.
    """

    import signal
    import subprocess
    import time

    db_url = str(db.engine.url)

    # Time how long it takes to run the script
    start = time.time()

    script = relative_path(__file__, "scripts/test-long-running-query")

    p = subprocess.Popen(
        [
            str(script),
            db_url,
        ],
    )
    time.sleep(0.5)
    p.send_signal(signal.SIGINT)
    p.wait()
    assert p.returncode == 1
    # Make sure it didn't take too long
    dT = time.time() - start
    assert dT < 2


def test_check_table_exists(db):
    tables = db.inspector.get_table_names()
    assert "sample" in tables
    assert "samplea" not in tables


def test_check_table_exists_postgresql(db):
    assert table_exists(db, "sample")
    assert not table_exists(db, "samplea")


def test_database_schema_refresh(db):
    # Create a new table
    sql = "CREATE TABLE new_table (name TEXT)"
    db.run_sql(sql)
    names = db.inspector.get_table_names()
    assert "new_table" not in names
    db.refresh_schema(automap=False)
    assert "new_table" in db.inspector.get_table_names()


def test_print_error():
    _print_error("SELECT * FROM test", Exception("Test error"))


def _check_text(_stdout: TextIOWrapper, _text: str):
    _stdout.seek(0)
    assert _stdout.read() == _text


def test_printing(db):
    # Check that nothing was printed to stderr
    # Collect printed statements
    with StringIO() as _stdout:
        run_sql(db.session, "SELECT 1", output_file=_stdout)
        _check_text(_stdout, "SELECT 1\n")


def test_no_printing(db):
    # Check that nothing was printed to stderr
    # Collect printed statements
    with StringIO() as _stdout:
        run_sql(db.session, "SELECT 1", output_mode="none", output_file=_stdout)
        _check_text(_stdout, "")


def test_no_printing_fixtures(db, capsys):
    # Check that nothing was printed to stderr
    # Collect printed statements

    fd = Path(relative_path(__file__, "fixtures"))
    with StringIO() as _stdout:
        run_fixtures(db.session, fd, output_mode="none", output_file=_stdout)
        _check_text(_stdout, "")
