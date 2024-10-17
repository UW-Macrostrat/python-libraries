import datetime
from contextvars import ContextVar
from typing import Optional

from sqlalchemy import Engine
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from macrostrat.database import Database
from .schema import Token


def get_access_token(token: str):
    """The sole database call"""

    session_maker = get_session_maker()
    with session_maker() as session:

        select_stmt = select(Token).where(Token.token == token)

        # Check that the token exists
        result = (session.scalars(select_stmt)).first()

        # Check if it has expired
        if result.expires_on < datetime.datetime.now(datetime.timezone.utc):
            return None

        # Update the used_on column
        if result is not None:
            stmt = (
                update(Token)
                .where(Token.token == token)
                .values(used_on=datetime.datetime.utcnow())
            )
            session.execute(stmt)
            session.commit()

        return (session.scalars(select_stmt)).first()


_database: ContextVar[Optional[Database]] = ContextVar("database", default=None)
_base: ContextVar[Optional[declarative_base]] = ContextVar(
    "declarative_base", default=None
)


def get_database():
    return _database.get()


def get_engine() -> Engine:
    return get_database().engine


def get_base() -> declarative_base:
    return _base.get()


def connect_engine(uri: str, schema: str):
    database = Database(uri)

    base = declarative_base()
    base.metadata.reflect(database.engine)
    base.metadata.reflect(database.engine, schema=schema, views=True)


def dispose_engine():
    get_engine().dispose()


def get_session_maker() -> sessionmaker:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_session() -> Session:
    with get_session_maker()() as s:
        yield s
