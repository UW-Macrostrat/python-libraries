import warnings
from contextlib import contextmanager
from enum import Enum
from pathlib import Path
from typing import Optional, Union

from psycopg2.errors import InvalidSavepointSpecification
from psycopg2.sql import Identifier
from sqlalchemy import URL, MetaData, create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, InternalError
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.orm import Session, scoped_session, sessionmaker
from sqlalchemy.sql.expression import Insert

from macrostrat.utils import get_logger

from .mapper import DatabaseMapper
from .postgresql import on_conflict, prefix_inserts  # noqa
from .utils import (  # noqa
    create_database,
    database_exists,
    drop_database,
    get_dataframe,
    get_or_create,
    reflect_table,
    run_fixtures,
    run_query,
    run_sql,
)

metadata = MetaData()

log = get_logger(__name__)


class Database(object):
    mapper: Optional[DatabaseMapper] = None
    metadata: MetaData
    session: Session
    instance_params: dict

    __inspector__ = None

    def __init__(self, db_conn: Union[str, URL], *, echo_sql=False, **kwargs):
        """
        Wrapper for interacting with a database using SQLAlchemy.
        Optimized for use with PostgreSQL, but usable with SQLite
        as well.

        Args:
            db_conn (str): Connection string for the database.

        Keyword Args:
            echo_sql (bool): If True, will echo SQL commands to the
                console. Default is False.
            instance_params (dict): Parameters to
                pass to queries and other database operations.
        """

        compiles(Insert, "postgresql")(prefix_inserts)

        self.instance_params = kwargs.pop("instance_params", {})

        log.info(f"Setting up database connection '{db_conn}'")
        self.engine = create_engine(db_conn, echo=echo_sql, **kwargs)
        self.metadata = kwargs.get("metadata", metadata)

        # Scoped session for database
        # https://docs.sqlalchemy.org/en/13/orm/contextual.html#unitofwork-contextual
        # https://docs.sqlalchemy.org/en/13/orm/session_basics.html#session-faq-whentocreate
        self._session_factory = sessionmaker(bind=self.engine)
        self.session = scoped_session(self._session_factory)
        # Use the self.session_scope function to more explicitly manage sessions.

    def create_tables(self):
        """
        Create all tables described by the database's metadata instance.
        """
        metadata.create_all(bind=self.engine)

    def automap(self, **kwargs):
        log.info("Automapping the database")
        self.mapper = DatabaseMapper(self)
        self.mapper.reflect_database(**kwargs)

    @contextmanager
    def session_scope(self, commit=True):
        """Provide a transactional scope around a series of operations."""
        # self.__old_session = self.session
        # session = self._session_factory()
        session = self.session
        try:
            yield session
            if commit:
                session.commit()
        except Exception as err:
            session.rollback()
            raise err
        finally:
            session.close()

    def _flush_nested_objects(self, session):
        """
        Flush objects remaining in a session (generally these are objects loaded
        during schema-based importing).
        """
        for object in session:
            try:
                session.flush(objects=[object])
                log.debug(f"Successfully flushed instance {object}")
            except IntegrityError as err:
                session.rollback()
                log.debug(err)

    def run_sql(self, fn, params=None, **kwargs):
        """Executes SQL files or query strings using the run_sql function.

        Args:
            fn (str|Path): SQL file or query string to execute.
            params (dict): Parameters to pass to the query.

        Keyword Args:
            use_instance_params (bool): If True, will use the instance_params set on
                the Database object. Default is True.

        Returns: Iterator of results from the query.
        """
        params = self._setup_params(params, kwargs)
        return iter(run_sql(self.session, fn, params, **kwargs))

    def run_query(self, sql, params=None, **kwargs):
        """Run a single query on the database object, returning the result.

        Args:
            sql (str): SQL file or query to execute.
            params (dict): Parameters to pass to the query.

        Keyword Args:
            use_instance_params (bool): If True, will use the instance_params set on
                the Database object. Default is True.
        """
        params = self._setup_params(params, kwargs)
        return run_query(self.session, sql, params, **kwargs)

    def run_fixtures(self, fixtures: Union[Path, list[Path]], params=None, **kwargs):
        """Run a set of fixtures on the database object.

        Args:
            fixtures (Path|list[Path]): Path to a directory of fixtures or a list of paths to fixture files.
            params (dict): Parameters to pass to the query.

        Keyword Args:
            use_instance_params (bool): If True, will use the instance_params set on
                the Database object. Default is True.
        """
        params = self._setup_params(params, kwargs)
        return run_fixtures(self.session, fixtures, params, **kwargs)

    def _setup_params(self, params, kwargs):
        use_instance_params = kwargs.pop("use_instance_params", True)
        if params is None:
            params = {}
        if use_instance_params:
            params.update(self.instance_params)
        return params

    def exec_sql(self, sql, params=None, **kwargs):
        """Executes SQL files passed"""
        warnings.warn(
            "exec_sql is deprecated and will be removed in version 4.0. Use run_sql instead",
            DeprecationWarning,
        )
        return self.run_sql(sql, params, **kwargs)

    def get_dataframe(self, *args):
        """Returns a Pandas DataFrame from a SQL query"""
        return get_dataframe(self.engine, *args)

    @property
    def inspector(self):
        if self.__inspector__ is None:
            self.__inspector__ = inspect(self.engine)
        return self.__inspector__

    def entity_names(self, **kwargs):
        """
        Returns an iterator of names of *schema objects*
        (both tables and views) from a the database.
        """
        yield from self.inspector.get_table_names(**kwargs)
        yield from self.inspector.get_view_names(**kwargs)

    def get(self, model, *args, **kwargs):
        if isinstance(model, str):
            model = getattr(self.model, model)
        return self.session.query(model).get(*args, **kwargs)

    def get_or_create(self, model, **kwargs):
        """
        Get an instance of a model, or create it if it doesn't
        exist.
        """
        if isinstance(model, str):
            model = getattr(self.model, model)
        return get_or_create(self.session, model, **kwargs)

    def reflect_table(self, *args, **kwargs):
        """
        One-off reflection of a database table or view. Note: for most purposes,
        it will be better to use the database tables automapped at runtime using
        `self.automap()`. Then, tables can be accessed using the
        `self.table` object. However, this function can be useful for views (which
        are not reflected automatically), or to customize type definitions for mapped
        tables.

        A set of `column_args` can be used to pass columns to override with the mapper, for
        instance to set up foreign and primary key constraints.
        https://docs.sqlalchemy.org/en/13/core/reflection.html#reflecting-views
        """
        warnings.warn(
            "reflect_table is deprecated and will be removed in version 4.0. Shift away from table refection, or use reflect_table from the macrostrat.database.utils module.",
            DeprecationWarning,
        )

        return reflect_table(self.engine, *args, **kwargs)

    @property
    def table(self):
        """
        Map of all tables in the database as SQLAlchemy table objects
        """
        if self.mapper is None or self.mapper._tables is None:
            self.automap()
        return self.mapper._tables

    @property
    def model(self):
        """
        Map of all tables in the database as SQLAlchemy models

        https://docs.sqlalchemy.org/en/latest/orm/extensions/automap.html
        """
        if self.mapper is None or self.mapper._models is None:
            self.automap()
        return self.mapper._models

    @property
    def mapped_classes(self):
        return self.model

    @contextmanager
    def transaction(self, *, rollback="on-error", connection=None, raise_errors=True):
        """Create a database session that can be rolled back after use.
        This is similar to the `session_scope` method but includes
        more fine-grained control over transactions. The two methods may be integrated
        in the future.

        This is based on the Sparrow's implementation:
        https://github.com/EarthCubeGeochron/Sparrow/blob/main/backend/conftest.py

        It can be effectively used in a Pytest fixture like so:
        ```
        @fixture(scope="class")
        def db(base_db):
            with base_db.transaction(rollback=True):
                yield base_db
        """
        if connection is None:
            connection = self.engine.connect()
        transaction = connection.begin()
        session = Session(bind=connection)
        prev_session = self.session
        self.session = session

        should_rollback = rollback == "always"

        try:
            yield self
        except Exception as e:
            should_rollback = rollback != "never"
            if raise_errors:
                raise e
        finally:
            if should_rollback:
                transaction.rollback()
            else:
                transaction.commit()
            session.close()
            self.session = prev_session

    savepoint_counter = 0

    @contextmanager
    def savepoint(self, name=None, rollback="on-error", connection=None):
        """A PostgreSQL-specific savepoint context manager. This is similar to the
        `transaction` context manager but uses savepoints directly for simpler operation.
        Notably, it supports nested savepoints, a feature that is difficult in SQLAlchemy's `transaction`
        model.

        This function is not yet drop-in compatible with the `transaction` context manager, but that
        is a future goal.
        """
        if name is None:
            name = f"sp_{self.savepoint_counter}"
            self.savepoint_counter += 1

        _prev_session = self.session

        if connection is None:
            connection = self.session.connection()
        params = {"name": Identifier(name)}
        run_query(connection, "SAVEPOINT {name}", params)
        should_rollback = rollback == "always"
        self.session = Session(bind=connection)
        try:
            yield name
        except Exception as e:
            should_rollback = rollback != "never"
            raise e
        finally:
            _clear_savepoint(connection, name, rollback=should_rollback)
            self.session.close()
            self.session = _prev_session


def _clear_savepoint(connection, name, rollback=True):
    params = {"name": Identifier(name)}
    try:
        if rollback:
            run_query(connection, "ROLLBACK TO SAVEPOINT {name}", params)
        else:
            run_query(connection, "RELEASE SAVEPOINT {name}", params)
    except InternalError as err:
        if isinstance(err.orig, InvalidSavepointSpecification):
            log.warning(
                f"Savepoint {name} does not exist; we may have already rolled back."
            )
            run_query(connection, "ROLLBACK")
