import warnings
from contextlib import contextmanager
from typing import Optional

from sqlalchemy import create_engine, inspect, MetaData, text
from sqlalchemy.orm import sessionmaker, scoped_session, Session
from sqlalchemy.exc import IntegrityError
from macrostrat.utils import get_logger
from sqlalchemy.ext.compiler import compiles
from sqlalchemy.sql.expression import Insert

from .utils import (
    run_sql,
    get_or_create,
    reflect_table,
    get_dataframe,
)
from .mapper import DatabaseMapper
from .postgresql import prefix_inserts, on_conflict  # noqa


metadata = MetaData()

log = get_logger(__name__)


class Database(object):
    mapper: Optional[DatabaseMapper] = None
    metadata: MetaData
    session: Session
    __inspector__ = None

    def __init__(self, db_conn, app=None, echo_sql=False, **kwargs):
        """
        We can pass a connection string, a **Flask** application object
        with the appropriate configuration, or nothing, in which
        case we will try to infer the correct database from
        the SPARROW_BACKEND_CONFIG file, if available.
        """

        compiles(Insert, "postgresql")(prefix_inserts)

        log.info(f"Setting up database connection '{db_conn}'")
        self.engine = create_engine(
            db_conn, executemany_mode="batch", echo=echo_sql, **kwargs
        )
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

    def run_sql(self, fn, **kwargs):
        """Executes SQL files passed"""
        return iter(run_sql(self.session, fn, **kwargs))

    def exec_sql(self, sql, **kwargs):
        """Executes SQL files passed"""
        warnings.warn("exec_sql is deprecated. Use run_sql instead", DeprecationWarning)
        return self.run_sql(sql, **kwargs)

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
