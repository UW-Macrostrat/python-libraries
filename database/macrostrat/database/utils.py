from contextlib import contextmanager
from time import sleep

from click import echo
from macrostrat.utils import cmd, get_logger
from sqlalchemy import MetaData
from sqlalchemy import create_engine as base_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url, URL
from sqlalchemy.exc import (
    IntegrityError,
    OperationalError,
    ProgrammingError,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import Table
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy_utils import create_database as _create_database
from sqlalchemy_utils import database_exists, drop_database as _drop_database
from sqlparse import format

from .query import get_sql_text

log = get_logger(__name__)

# Ensure that old import structure still works
from .query import run_sql, run_query, run_sql_file, run_fixtures  # noqa: F401


def get_dataframe(connectable, filename_or_query, **kwargs):
    """
    Run a query on a SQL database (represented by
    a SQLAlchemy database object) and turn it into a
    `Pandas` dataframe.
    """
    from pandas import read_sql

    sql = get_sql_text(filename_or_query)

    return read_sql(sql, connectable, **kwargs)


def db_session(engine):
    factory = sessionmaker(bind=engine)
    return factory()


def execute(connectable, sql, params=None, stop_on_error=False, **kwargs):
    output_file = kwargs.pop("output_file", None)
    output_mode = kwargs.pop("output_mode", None)
    sql = format(sql, strip_comments=True).strip()
    if sql == "":
        return
    try:
        connectable.begin()
        res = connectable.execute(text(sql), params=params)
        if hasattr(connectable, "commit"):
            connectable.commit()
        pretty_print(sql, dim=True, file=output_file, mode=output_mode)
        return res
    except (ProgrammingError, IntegrityError) as err:
        if hasattr(connectable, "rollback"):
            connectable.rollback()
        _print_error(sql, dim=True, file=output_file, mode=output_mode)
        if stop_on_error:
            return
    finally:
        if hasattr(connectable, "close"):
            connectable.close()


def get_or_create(session, model, defaults=None, **kwargs):
    """
    Get an instance of a model, or create it if it doesn't
    exist.

    https://stackoverflow.com/questions/2546207
    """
    instance = session.query(model).filter_by(**kwargs).first()
    if instance:
        instance._created = False
        return instance
    else:
        params = dict(
            (k, v) for k, v in kwargs.items() if not isinstance(v, ClauseElement)
        )
        params.update(defaults or {})
        instance = model(**params)
        session.add(instance)
        instance._created = True
        return instance


def get_db_model(db, model_name: str):
    return getattr(db.model, model_name)


@contextmanager
def temp_database(conn_string, drop=True, ensure_empty=False, force_drop=True):
    """Create a temporary database and tear it down after tests."""
    create_database(conn_string, exists_ok=True, replace=ensure_empty)
    if not drop:
        force_drop = False
    try:
        engine = create_engine(conn_string)
        yield engine
    finally:
        drop_database(engine, force=force_drop)


def drop_database(engine_or_url, force=None, allow_missing=False):
    """Drop a database.

    Parameters
    ----------
    engine : Database, Engine or str
        A SQLAlchemy engine or database URL.
    force: bool
        If true, use the `force` parameter
    """
    from . import Database

    db = engine_or_url
    if (
        isinstance(engine_or_url, str)
        or isinstance(engine_or_url, URL)
        or isinstance(engine_or_url, Engine)
    ):
        db = Database(engine_or_url)
    url = db.engine.url
    # Check that the database exists
    if not database_exists(url):
        if not allow_missing:
            raise ValueError(f"Database {url} does not exist")
        else:
            return

    if "postgres" in url.drivername and force is not False:
        # Check if we can force-drop and do so if we can
        database_name = url.database
        user_url = url._replace(database=None)
        user_db = Database(user_url, isolation_level="AUTOCOMMIT")

        version = user_db.run_query("SHOW server_version").scalar()
        major_version = int(version.split(".")[0])
        can_use_modern_force = major_version >= 13
        user_db.session.close()

        sql = f"DROP DATABASE {database_name}"

        with user_db.engine.connect() as conn:
            if can_use_modern_force:
                sql += " WITH (FORCE)"
            else:
                run_sql(
                    conn,
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :database",
                    dict(database=database_name),
                )
            conn.execute(text("COMMIT"))
            conn.execute(text(sql))
        return
    else:
        # Drop the database without force
        _drop_database(url)


def create_database(url, **kwargs):
    """Create a database if it doesn't exist.

    Parameters
    ----------
    url : str
        A SQLAlchemy database URL.
    exists_ok : bool
        If True, don't raise an error if the database already exists.
    replace : bool
        If True, drop the database if it exists and create a new one.
    kwargs : dict
        Additional keyword arguments to pass to `sqlalchemy_utils.create_database`.
    """
    db_exists = database_exists(url)

    should_replace = kwargs.pop("replace", False)
    exists_ok = kwargs.pop("exists_ok", False)

    if should_replace and db_exists:
        drop_database(url)
        db_exists = False

    if exists_ok and db_exists:
        return
    _create_database(url, **kwargs)


def create_engine(db_conn, **kwargs):
    if isinstance(db_conn, Engine):
        log.info(f"Set up database connection with engine {db_conn.url}")
        if db_conn.driver == "psycopg2":
            log.warning(
                "The psycopg2 driver is deprecated. Please use psycopg3 instead."
            )
        return db_conn
    else:
        log.info(f"Setting up database connection with URL '{db_conn}'")
        url = db_conn
        if isinstance(url, str):
            url = make_url(url)
        # Set the driver to psycopg if not already set
        if "postgres" in url.drivername:
            url = url.set(drivername="postgresql+psycopg")

        return base_create_engine(url, **kwargs)


def connection_args(engine, with_password=False):
    """Get PostgreSQL connection arguments for an engine"""
    _psql_flags = {"-U": "username", "-h": "host", "-p": "port", "-P": "password"}

    if isinstance(engine, str):
        # We passed a connection url!
        engine = create_engine(engine)
    flags = ""
    for flag, _attr in _psql_flags.items():
        val = getattr(engine.url, _attr)
        if flag == "-P" and not with_password:
            continue
        if val is not None:
            flags += f" {flag} {val}"
    return flags, engine.url.database


def db_isready(engine_or_url, use_shell_command=False):
    if use_shell_command:
        args, _ = connection_args(engine_or_url, with_password=True)
        c = cmd("pg_isready", args, capture_output=True)
        return c.returncode == 0
    # Use a more typical sqlalchemy connection approach
    engine = create_engine(engine_or_url)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def wait_for_database(engine_or_url, *, quiet=False, use_shell_command=False):
    msg = "Waiting for database..."
    while not db_isready(engine_or_url, use_shell_command=use_shell_command):
        if not quiet:
            echo(msg, err=True)
        log.info(msg)
        sleep(1)


def reflect_table(engine, tablename, *column_args, **kwargs):
    """
    One-off reflection of a database table or view. Note: for most purposes,
    it will be better to use the database tables automapped at runtime in the
    `self.tables` object. However, this function can be useful for views (which
    are not reflected automatically), or to customize type definitions for mapped
    tables.

    A set of `column_args` can be used to pass columns to override with the mapper, for
    instance to set up foreign and primary key constraints.
    https://docs.sqlalchemy.org/en/13/core/reflection.html#reflecting-views
    """
    schema = kwargs.pop("schema", "public")
    meta = MetaData(schema=schema)
    return Table(tablename, meta, *column_args, autoload_with=engine, **kwargs)
