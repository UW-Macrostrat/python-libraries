from contextlib import contextmanager
from pathlib import Path
from re import search
from time import sleep
from typing import IO, Union
from warnings import warn

from click import echo, secho
from psycopg2.extensions import set_wait_callback
from psycopg2.extras import wait_select
from psycopg2.sql import SQL, Composable, Composed
from rich.console import Console
from sqlalchemy import MetaData, create_engine, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import (
    IntegrityError,
    InternalError,
    InvalidRequestError,
    ProgrammingError,
    OperationalError
)
from sqlalchemy.orm import sessionmaker
from sqlalchemy.schema import Table
from sqlalchemy.sql.elements import ClauseElement, TextClause
from sqlalchemy_utils import create_database as _create_database
from sqlalchemy_utils import database_exists, drop_database
from sqlparse import format, split

from macrostrat.utils import cmd, get_logger

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
        "GRANT",
        "WITH",
    ]
    lines = _string.split("\n")
    if len(lines) > 1:
        return True
    _string = _string.lower()
    for i in keywords:
        if _string.strip().startswith(i.lower() + " "):
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
            "SET",
            "GRANT",
            "WITH",
            "NOTIFY",
            "COPY",
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
    if isinstance(sql, TextClause):
        return [sql]
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

    return split(format(sql, strip_comments=True))


def _is_prebind_param(param):
    return isinstance(param, Composable)


def _split_params(params):
    if params is None:
        return None, None
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
    while hasattr(conn, "driver_connection") or hasattr(conn, "connection"):
        if hasattr(conn, "driver_connection"):
            conn = conn.driver_connection
        else:
            conn = conn.connection
        if callable(conn):
            conn = conn()
    if hasattr(conn, "cursor"):
        conn = conn.cursor()

    return conn


def _get_connection(connectable) -> Connection:
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


def infer_has_server_binds(sql):
    return "%s" in sql or search(r"%\(\w+\)s", sql)


def _run_sql(connectable, sql, params=None, **kwargs):
    """
    Internal function for running a query on a SQLAlchemy connectable,
    which always returns an iterator. The wrapper function adds the option
    to return a list of results.
    """
    if isinstance(connectable, Engine):
        with connectable.connect() as conn:
            yield from _run_sql(conn, sql, params, **kwargs)
            return

    stop_on_error = kwargs.pop("stop_on_error", False)
    raise_errors = kwargs.pop("raise_errors", False)
    has_server_binds = kwargs.pop("has_server_binds", None)
    ensure_single_query = kwargs.pop("ensure_single_query", False)

    if stop_on_error:
        raise_errors = True
        warn(DeprecationWarning("stop_on_error is deprecated, use raise_errors"))

    interpret_as_file = kwargs.pop("interpret_as_file", None)

    queries = _get_queries(sql, interpret_as_file=interpret_as_file)

    if queries is None:
        return

    if ensure_single_query and len(queries) > 1:
        raise ValueError("Multiple queries passed when only one was expected")

    # check if parameters is a list of the same length as the number of queries
    if not isinstance(params, list) or not len(params) == len(queries):
        params = [params] * len(queries)

    for query, params in zip(queries, params):
        trans = None
        # This only does something for postgresql, but it's harmless to run it for other engines
        set_wait_callback(wait_select)

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

            sql_text = str(query)
            if isinstance(query, str):
                sql_text = format(query, strip_comments=True).strip()
                if sql_text == "":
                    continue
                # Check for server-bound parameters in sql native style. If there are none, use
                # the SQLAlchemy text() function, otherwise use the raw query string
                if has_server_binds is None:
                    has_server_binds = infer_has_server_binds(sql_text)

            log.debug("Executing SQL: \n %s", query)
            if has_server_binds:
                conn = _get_connection(connectable)
                res = conn.exec_driver_sql(query, params)
            else:
                if not isinstance(query, TextClause):
                    query = text(query)
                res = connectable.execute(query, params)
            yield res
            if trans is not None:
                trans.commit()
            elif hasattr(connectable, "commit"):
                connectable.commit()
            pretty_print(sql_text, dim=True)
        except (ProgrammingError, IntegrityError, InternalError, OperationalError) as err:
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
        finally:
            set_wait_callback(None)


def run_sql_file(connectable, filename, params=None, **kwargs):
    return run_sql(connectable, filename, params, interpret_as_file=True, **kwargs)


def run_query(connectable, query, params=None, **kwargs):
    return next(
        iter(
            _run_sql(
                connectable,
                query,
                params,
                ensure_single_query=True,
                yield_results=False,
                raise_errors=True,
                **kwargs,
            )
        )
    )


def get_sql_files(
    fixtures: Union[Path, list[Path]], recursive=False, order_by_name=True
):
    files = []
    if isinstance(fixtures, Path):
        fixtures = [fixtures]
    for fixture in fixtures:
        files.extend(_get_sql_files(fixture, recursive))
    if order_by_name:
        files = sorted(files)
    return files


def _get_sql_files(fixture: Path, recursive=False):
    if not fixture.exists():
        raise FileNotFoundError(f"Fixture {fixture} does not exist.")
    if fixture.is_file() and fixture.suffix == ".sql":
        return [fixture]
    _fn = "rglob" if recursive else "glob"
    files = getattr(fixture, _fn)("*.sql")
    return [r for r in files if r.is_file()]


def run_fixtures(connectable, fixtures: Union[Path, list[Path]], params=None, **kwargs):
    """
    Run a set of SQL fixture files on a database. Fixtures can be passed as a list of file paths or a directory.
    Fixtures are ordered by name by default, but this can be disabled.
    """
    recursive = kwargs.pop("recursive", False)
    order_by_name = kwargs.pop("order_by_name", True)
    console = kwargs.pop("console", Console(stderr=True))
    files = get_sql_files(fixtures, recursive=recursive, order_by_name=order_by_name)

    for fixture in files:
        console.print(f"[cyan bold]{fixture}[/]")
        run_sql_file(connectable, fixture, params, **kwargs)
        console.print()


def run_sql(*args, **kwargs):
    """
    Run a query on a SQLAlchemy connectable.

    Parameters
    ----------
    connectable : Union[Engine, Connection]
        A SQLAlchemy engine or connection object.
    sql : Union[str, Path, IO, SQL, Composed]
        A SQL query, or a file containing a SQL query.
    params : Union[dict, list, tuple]
        Parameters to bind to the query. If a list or tuple, the parameters
        will be bound to the query in order. If a dict, the parameters will
        be bound to the query by name.
    stop_on_error : bool
        If True, stop running queries if an error is encountered.
    raise_errors : bool
        If True, raise errors encountered while running queries.
    has_server_binds : bool
        Interpret the query to have server-side bind parameters (requiring execution
        with the backend driver). By default, this is inferred from the query string,
        but inference is not always reliable.
    interpret_as_file : bool
        If True, force interpreting the query as a file path.
    yield_results : bool
        If True, yield the results of the query as they are executed, rather than
        returning a list after completion.
    ensure_single_query : bool
        If True, raise an error if multiple queries are passed when only one is expected.
    """
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
    create_database(conn_string, exists_ok=True, replace=ensure_empty)
    try:
        yield create_engine(conn_string)
    finally:
        if drop:
            drop_database(conn_string)


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
    return Table(tablename, meta, *column_args, autoload_with=engine, **kwargs)
