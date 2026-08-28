from contextlib import contextmanager
from time import sleep
from typing import Union
from uuid import uuid4
from warnings import warn

from click import echo
from psycopg.errors import AdminShutdown
from psycopg.sql import Identifier
from sqlalchemy import MetaData
from sqlalchemy import create_engine as base_create_engine
from sqlalchemy import text
from sqlalchemy.engine import Engine
from sqlalchemy.engine.url import make_url, URL
from sqlalchemy.exc import (
    OperationalError,
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import Table
from sqlalchemy.sql.elements import ClauseElement
from sqlalchemy_utils import (
    create_database as _create_database,
    database_exists,
    drop_database as _drop_database,
)

from macrostrat.utils import cmd, get_logger
from .query import get_sql_text, execute  # noqa

log = get_logger(__name__)

# Ensure that old import structure still works
from .query import run_sql, run_query, run_sql_file, run_fixtures  # noqa: F401

DatabaseInput = Union["Database", Engine, str, URL]


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
def temp_database(*args, **kwargs):
    warn(
        "temp_database is deprecated, use temporary_database instead",
        DeprecationWarning,
    )
    with temporary_database(*args, **kwargs) as engine:
        yield engine


@contextmanager
def temporary_database(
    _input: DatabaseInput,
    *,
    drop=True,
    ensure_empty=False,
    exists_ok=True,
    template=None,
    force_drop=False,
):
    """Create a temporary database and tear it down after tests."""
    url = get_database_url(_input)
    create_database(url, exists_ok=exists_ok, replace=ensure_empty, template=template)
    engine = create_engine(url)
    try:
        yield engine
        engine.dispose()
    finally:
        if drop:
            drop_database(engine, force=force_drop)


def drop_database(_input: DatabaseInput, force=None, allow_missing=False):
    """Drop a database.

    Parameters
    ----------
    engine : Database, Engine or str
        A SQLAlchemy engine or database URL.
    force: bool
        If true, use the `force` parameter
    """
    url = get_database_url(_input)
    if not database_exists(url):
        if not allow_missing:
            raise ValueError(f"Database {url} does not exist")
    elif "postgres" in url.drivername and force is not False:
        # Check if we can force-drop and do so if we can
        _force_drop_postgresql_database(url)
    else:
        # Drop the database without force
        _drop_database(url)


# Databases that exist on every PostgreSQL cluster, used when we need a connection
# for cluster-level work (creating or dropping some *other* database).
_MAINTENANCE_DATABASES = ("postgres", "template1")


def maintenance_url(url: URL, exclude: str = None) -> URL:
    """A URL for a database that is safe to connect to for cluster-level work.

    Never leave the database unset for this. libpq falls back to ``PGDATABASE``
    (and ``macrostrat.core.config``, among others, exports one), so a URL with no
    database silently targets whatever database that names — which either is not
    the cluster you meant or does not exist there, surfacing as a confusing
    "database ... does not exist" for a database nobody mentioned.

    ``exclude`` names a database that must not be connected to — normally the one
    about to be dropped or copied. Defaults to ``url``'s own database.
    """
    exclude = exclude if exclude is not None else url.database
    for candidate in _MAINTENANCE_DATABASES:
        if candidate != exclude:
            return url.set(database=candidate)
    # Both maintenance databases excluded shouldn't happen, but stay well-defined.
    return url.set(database=_MAINTENANCE_DATABASES[-1])


def _maintenance_engine(url: URL, exclude: str = None) -> Engine:
    """An AUTOCOMMIT engine against a maintenance database on ``url``'s cluster."""
    exclude = exclude if exclude is not None else url.database
    last_error = None
    for candidate in _MAINTENANCE_DATABASES:
        if candidate == exclude:
            continue
        engine = create_engine(
            url.set(database=candidate),
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect():
                return engine
        except OperationalError as err:  # pragma: no cover - cluster-specific
            last_error = err
            engine.dispose()
    raise last_error


def _force_drop_postgresql_database(url):
    # Check if we can force-drop and do so if we can
    database_name = url.database
    user_engine = _maintenance_engine(url, exclude=database_name)
    # Get postgresql version from engine
    major_version = 0
    with allow_shutdown(user_engine) as conn:
        conn.autocommit = True
        pg_version = user_engine.dialect.server_version_info
        major_version = pg_version[0]
        can_use_modern_force = major_version >= 13
        sql = "DROP DATABASE {database_name}"
        params = dict(database_name=Identifier(database_name))
        if can_use_modern_force:
            sql += " WITH (FORCE)"
        else:
            close_all_connections(user_engine, database=database_name)
        run_sql(conn, sql, params=params, raise_errors=True, use_transaction=False)
    user_engine.dispose()


@contextmanager
def allow_shutdown(engine):
    with engine.connect() as conn:
        conn.autocommit = True
        try:
            yield conn
        except AdminShutdown:
            pass
        except OperationalError as exc:
            if isinstance(exc.orig, AdminShutdown):
                pass
            else:
                raise exc


def close_all_connections(engine: Engine, database: str = None):
    """Close all connections to the database."""
    if database is None:
        database = engine.url.database
    sql = "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = :database"
    params = dict(database=database)
    with allow_shutdown(engine) as conn:
        run_sql(conn, sql, params=params, raise_errors=True, use_transaction=False)


def get_database_url(_input: DatabaseInput) -> URL:
    from .core import Database

    if isinstance(_input, Database):
        return _input.engine.url
    elif isinstance(_input, Engine):
        return _input.url
    elif isinstance(_input, str) or isinstance(_input, URL):
        return make_url(_input)
    else:
        raise ValueError(f"Invalid input type: {_input}")


def copy_database_settings(source: DatabaseInput, target: DatabaseInput):
    """Copy database-level settings (``ALTER DATABASE ... SET ...``) between databases.

    ``CREATE DATABASE ... TEMPLATE`` copies schema and data but *not*
    ``pg_db_role_setting``, so a copy can behave differently from the database it
    was cloned from. PostGIS is the common case: ``CREATE EXTENSION
    postgis_topology`` sets a database-level ``search_path`` including ``topology``,
    and without it a clone resolves topogeometry columns and topology-referencing
    views differently from its source — enough to make a schema diff between two
    otherwise identical databases report drift.

    Only cluster-wide (roleless) settings are copied; per-role overrides are left
    alone. Values are replayed as Postgres stored them, which is already valid
    ``SET`` syntax.
    """
    source_url = get_database_url(source)
    target_url = get_database_url(target)

    engine = _maintenance_engine(source_url, exclude=target_url.database)
    try:
        with engine.connect() as conn:
            settings = list(
                conn.execute(
                    text(
                        "SELECT unnest(setconfig) FROM pg_db_role_setting"
                        " WHERE setdatabase = ("
                        "   SELECT oid FROM pg_database WHERE datname = :database)"
                        "   AND setrole = 0"
                    ),
                    {"database": source_url.database},
                ).scalars()
            )
            quoted_target = '"' + target_url.database.replace('"', '""') + '"'
            for setting in settings:
                key, _, value = setting.partition("=")
                conn.execute(
                    text(f"ALTER DATABASE {quoted_target} SET {key} = {value}")
                )
    finally:
        engine.dispose()


@contextmanager
def template_database(
    _input: DatabaseInput,
    *,
    name: str = None,
    force_drop=True,
    close_source_connections=False,
    copy_settings=True,
):
    """Create a temporary database copied from an existing one.

    ``close_source_connections`` evicts sessions on the source first: PostgreSQL
    refuses to copy a template that anything else is connected to. The eviction
    runs from a *maintenance* database, because a connection to the source is
    itself a session on the source and so cannot clear the last one.

    ``copy_settings`` replays the source's database-level settings onto the copy
    (see :func:`copy_database_settings`); pass ``False`` for a copy of the schema
    and data alone.
    """

    url = get_database_url(_input)
    db_name = url.database

    template_db_name = name
    if name is None:
        uid = str(uuid4())[:8]
        template_db_name = db_name + "_template_" + uid
    new_db_url = url.set(database=template_db_name)

    if close_source_connections:
        engine = _maintenance_engine(url, exclude=db_name)
        try:
            close_all_connections(engine, database=db_name)
        finally:
            engine.dispose()

    with temporary_database(
        new_db_url, drop=True, exists_ok=False, template=db_name, force_drop=force_drop
    ) as engine:
        if copy_settings:
            copy_database_settings(url, new_db_url)
        yield engine


def create_database(_input: DatabaseInput, **kwargs):
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
    url = get_database_url(_input)
    db_exists = database_exists(url)

    should_replace = kwargs.pop("replace", False)
    exists_ok = kwargs.pop("exists_ok", False)

    if should_replace and db_exists:
        drop_database(url)
        db_exists = False

    if exists_ok and db_exists:
        return
    _create_database(url, **kwargs)


def create_engine(_input: DatabaseInput, **kwargs):
    from .core import Database

    db_conn = _input
    if isinstance(_input, Database):
        db_conn = _input.engine
    elif isinstance(_input, str):
        db_conn = make_url(_input)
    elif isinstance(_input, URL):
        db_conn = _input

    if isinstance(db_conn, Engine):
        if kwargs:
            log.warning(
                "create_engine: ignoring kwargs %s for a pre-built Engine",
                sorted(kwargs),
            )
        if db_conn.driver == "psycopg2":
            log.warning(
                "The psycopg2 driver is deprecated. Please use psycopg3 instead."
            )
        return db_conn

    if not isinstance(db_conn, URL):
        raise ValueError(f"Invalid input type: {_input}")
    url = db_conn

    log.info(f"Setting up database connection with URL '{url}'")
    # Set the driver to psycopg if not already set
    if "postgres" in url.drivername:
        url = url.set(drivername="postgresql+psycopg")

    return base_create_engine(url, **kwargs)


def connection_args(_input: DatabaseInput, with_password=False):
    """Get PostgreSQL connection arguments for an engine"""
    _psql_flags = {"-U": "username", "-h": "host", "-p": "port", "-P": "password"}
    url = get_database_url(_input)

    flags = ""
    for flag, _attr in _psql_flags.items():
        val = getattr(url, _attr)
        if flag == "-P" and not with_password:
            continue
        if val is not None:
            flags += f" {flag} {val}"
    return flags, url.database


def db_isready(_input: DatabaseInput, use_shell_command=False):
    if use_shell_command:
        args, _ = connection_args(_input, with_password=True)
        c = cmd("pg_isready", args, capture_output=True)
        return c.returncode == 0
    # Use a more typical sqlalchemy connection approach
    engine = create_engine(_input)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except OperationalError:
        return False


def wait_for_database(_input: DatabaseInput, *, quiet=False, use_shell_command=False):
    msg = "Waiting for database..."
    while not db_isready(_input, use_shell_command=use_shell_command):
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
