from click import echo, secho
from sqlalchemy.exc import ProgrammingError, IntegrityError, InternalError
from sqlparse import split, format
from sqlalchemy.sql import ClauseElement
from sqlalchemy.orm import sessionmaker, Session
from sqlalchemy.engine import Engine, Connection, Transaction
from sqlalchemy.schema import Table
from sqlalchemy import MetaData, create_engine, text
from contextlib import contextmanager
from sqlalchemy_utils import create_database, database_exists, drop_database
from sqlalchemy.exc import InvalidRequestError
from macrostrat.utils import cmd, get_logger
from time import sleep
from typing import Union, IO
from pathlib import Path
from warnings import warn
from psycopg2.sql import SQL, Composable, Composed
from re import search

log = get_logger(__name__)


def db_session(engine):
    factory = sessionmaker(bind=engine)
    return factory()


def infer_is_sql_text(_string: str) -> bool:
    """
    Return True if the string is a valid SQL query,
    false if it should be interpreted as a file path.
    """
    # If it's a byte string, decode it
    if isinstance(_string, bytes):
        _string = _string.decode("utf-8")

    keywords = [
        "SELECT",
        "INSERT",
        "UPDATE",
        "CREATE",
        "DROP",
        "DELETE",
        "ALTER",
        "SET",
    ]
    lines = _string.split("\n")
    if len(lines) > 1:
        return True
    _string = _string.lower()
    for i in keywords:
        if _string.strip().startswith(i.lower()):
            return True
    return False


def canonicalize_query(file_or_text: Union[str, Path, IO]) -> Union[str, Path]:
    if isinstance(file_or_text, Path):
        return file_or_text
    # If it's a file-like object, read it
    if hasattr(file_or_text, "read"):
        return file_or_text.read()
    # Otherwise, assume it's a string
    if infer_is_sql_text(file_or_text):
        return file_or_text
    pth = Path(file_or_text)
    if pth.exists() and pth.is_file():
        return pth
    return file_or_text


def get_dataframe(connectable, filename_or_query, **kwargs):
    """
    Run a query on a SQL database (represented by
    a SQLAlchemy database object) and turn it into a
    `Pandas` dataframe.
    """
    from pandas import read_sql

    sql = get_sql_text(filename_or_query)

    return read_sql(sql, connectable, **kwargs)


def pretty_print(sql, **kwargs):
    for line in sql.split("\n"):
        for i in [
            "SELECT",
            "INSERT",
            "UPDATE",
            "CREATE",
            "DROP",
            "DELETE",
            "ALTER",
        ]:
            if not line.startswith(i):
                continue
            start = line.split("(")[0].strip().rstrip(";").replace(" AS", "")
            secho(start, **kwargs)
            return


def get_sql_text(sql, interpret_as_file=None, echo_file_name=True):
    if interpret_as_file:
        sql = Path(sql).read_text()
    elif interpret_as_file is None:
        sql = canonicalize_query(sql)

    if isinstance(sql, Path):
        if echo_file_name:
            secho(sql.name, fg="cyan", bold=True)
        sql = sql.read_text()

    return sql

def _get_queries(sql, interpret_as_file=None):
    if isinstance(sql, (list, tuple)):
        queries = []
        for i in sql:
            queries.extend(_get_queries(i, interpret_as_file=interpret_as_file))
        return queries
    if isinstance(sql, SQL):
        return [sql]

    if sql in [None, ""]:
        return
    if interpret_as_file:
        sql = Path(sql).read_text()
    elif interpret_as_file is None:
        sql = canonicalize_query(sql)

    if isinstance(sql, Path):
        sql = sql.read_text()

    return split(sql)

def _is_prebind_param(param):
    return isinstance(param, Composable)

def _split_params(params):
    params = params or []
    new_params = []
    new_bind_params = []
    if isinstance(params, (list, tuple)):
        for i in params:
            if _is_prebind_param(i):
                new_bind_params.append(i)
            else:
                new_params.append(i)
    elif isinstance(params, dict):
        new_params = {}
        new_bind_params = {}
        for k, v in params.items():
            if _is_prebind_param(v):
                new_bind_params[k] = v
            else:
                new_params[k] = v
    if len(new_bind_params) == 0:
        new_bind_params = None
    return new_params, new_bind_params

def _get_cursor(connectable):
    if isinstance(connectable, Engine):
        conn = connectable.connect()

    # Find a connection or cursor object for the connectable
    conn = connectable
    if hasattr(conn, "raw_connection"):
        conn = conn.raw_connection()
    while hasattr(conn, "connection"):
        if callable(conn.connection):
            conn = conn.connection()
        else:
            conn = conn.connection
    if hasattr(conn, "cursor"):
        conn = conn.cursor()

    return conn

def _get_connection(connectable):
    if isinstance(connectable, Engine):
        return connectable.connect()
    if isinstance(connectable, Connection):
        return connectable
    if not hasattr(connectable, "connection"):
        return connectable
    conn = connectable.connection
    if callable(conn):
        return conn()
    return conn
    

def _render_query(query: Union[SQL, Composed], connectable: Union[Engine, Connection]):
    """Render a query to a SQL string."""
    if not isinstance(query, (Composed, SQL)):
        return query
    # Find a connection or cursor object for the connectable
    conn = _get_cursor(connectable)
    return query.as_string(conn)

            
def _run_sql(connectable, sql, **kwargs):
    if isinstance(connectable, Engine):
        with connectable.connect() as conn:
            yield from _run_sql(conn, sql, **kwargs)
            return

    params = kwargs.pop("params", None)
    stop_on_error = kwargs.pop("stop_on_error", False)
    raise_errors = kwargs.pop("raise_errors", False)
    if stop_on_error:
        raise_errors = True
        warn(DeprecationWarning("stop_on_error is deprecated, use raise_errors"))

    interpret_as_file = kwargs.pop("interpret_as_file", None)

    queries = _get_queries(sql, interpret_as_file=interpret_as_file)

    if queries is None:
        return

    # check if parameters is a list of the same length as the number of queries
    if not isinstance(params, list) or not len(params) == len(queries):
        params = [params] * len(queries)

    for query, params in zip(queries, params):
        trans = None
        try:
            trans = connectable.begin()
        except InvalidRequestError:
            trans = None
        try:
            params, pre_bind_params = _split_params(params)

            if pre_bind_params is not None:
                if not isinstance(query, SQL):
                    query = SQL(query)
                # Pre-bind the parameters using PsycoPG2
                query = query.format(**pre_bind_params)

            if isinstance(query, (SQL, Composed)):
                query = _render_query(query, connectable)

            sql_text = query
            has_server_binds = False
            if isinstance(query, str):
                sql_text = format(query, strip_comments=True).strip()
                if sql_text == "":
                    continue
                # Check for server-bound parameters in sql native style. If there are none, use
                # the SQLAlchemy text() function, otherwise use the raw query string
                has_server_binds = "%s" in sql_text or search(r'%\(\w+\)s', sql_text)

            log.debug("Executing SQL: \n %s", query)
            if has_server_binds:
                conn = _get_connection(connectable)
                res = conn.exec_driver_sql(query, params)
            else:
                res = connectable.execute(text(query), params=params)
            yield res
            if trans is not None:
                trans.commit()
            elif hasattr(connectable, "commit"):
                connectable.commit()
            pretty_print(sql_text, dim=True)
        except (ProgrammingError, IntegrityError, InternalError) as err:
            _err = str(err.orig).strip()
            dim = "already exists" in _err
            if trans is not None:
                trans.rollback()
            elif hasattr(connectable, "rollback"):
                connectable.rollback()
            pretty_print(sql_text, fg=None if dim else "red", dim=True)
            if dim:
                _err = "  " + _err
            secho(_err, fg="red", dim=dim)
            log.error(err)
            if raise_errors:
                raise err


def run_sql_file(connectable, filename, **kwargs):
    return run_sql(connectable, filename, interpret_as_file=True, **kwargs)


def run_sql(*args, **kwargs):
    res = _run_sql(*args, **kwargs)
    if kwargs.pop("yield_results", False):
        return res
    return list(res)


def execute(connectable, sql, params=None, stop_on_error=False):
    sql = format(sql, strip_comments=True).strip()
    if sql == "":
        return
    try:
        connectable.begin()
        res = connectable.execute(text(sql), params=params)
        if hasattr(connectable, "commit"):
            connectable.commit()
        pretty_print(sql, dim=True)
        return res
    except (ProgrammingError, IntegrityError) as err:
        err = str(err.orig).strip()
        dim = "already exists" in err
        if hasattr(connectable, "rollback"):
            connectable.rollback()
        pretty_print(sql, fg=None if dim else "red", dim=True)
        if dim:
            err = "  " + err
        secho(err, fg="red", dim=dim)
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
def temp_database(conn_string, drop=True, ensure_empty=False):
    """Create a temporary database and tear it down after tests."""
    if ensure_empty:
        drop_database(conn_string)
    if not database_exists(conn_string):
        create_database(conn_string)
    try:
        yield create_engine(conn_string)
    finally:
        if drop:
            drop_database(conn_string)


def connection_args(engine):
    """Get PostgreSQL connection arguments for an engine"""
    _psql_flags = {"-U": "username", "-h": "host", "-p": "port", "-P": "password"}

    if isinstance(engine, str):
        # We passed a connection url!
        engine = create_engine(engine)
    flags = ""
    for flag, _attr in _psql_flags.items():
        val = getattr(engine.url, _attr)
        if val is not None:
            flags += f" {flag} {val}"
    return flags, engine.url.database


def db_isready(engine_or_url):
    args, _ = connection_args(engine_or_url)
    c = cmd("pg_isready", args, capture_output=True)
    return c.returncode == 0


def wait_for_database(engine_or_url, quiet=False):
    msg = "Waiting for database..."
    while not db_isready(engine_or_url):
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
    return Table(
        tablename,
        meta,
        *column_args,
        autoload=True,
        autoload_with=engine,
        **kwargs,
    )
