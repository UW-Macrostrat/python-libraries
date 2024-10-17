import datetime

from sqlalchemy import create_engine, Engine
from sqlalchemy import select, update
from sqlalchemy.orm import sessionmaker, declarative_base, Session

from .schema import Token


# TODO:
# - remove globals in favor of contextlib
# - integrate with macrostrat.database code


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


engine: Engine | None = None
base: declarative_base = None
session: Session | None = None


def get_engine() -> Engine:
    return engine


def get_base() -> declarative_base:
    return base


def connect_engine(uri: str, schema: str):
    global engine
    global session
    global base

    engine = create_engine(uri)
    session = session

    base = declarative_base()
    base.metadata.reflect(get_engine())
    base.metadata.reflect(get_engine(), schema=schema, views=True)


def dispose_engine():
    global engine
    engine.dispose()


def get_session_maker() -> sessionmaker:
    return sessionmaker(autocommit=False, autoflush=False, bind=get_engine())


def get_session() -> Session:
    with get_session_maker()() as s:
        yield s
