from pytest import fixture
from os import environ
from macrostrat.database.utils import wait_for_database, temp_database, create_database
from dotenv import load_dotenv

load_dotenv()


def pytest_addoption(parser):
    parser.addoption(
        "--teardown",
        action="store_true",
        dest="teardown",
        default=False,
        help="Tear down database after tests run",
    )


@fixture(scope="session")
def db(pytestconfig):
    testing_db = environ.get("TESTING_DATABASE")
    if testing_db is None:
        raise ValueError(
            "Please set TESTING_DATABASE to a PostgreSQL connection string in .env file or environment."
        )
    # wait_for_database(testing_db)
    with temp_database(testing_db, drop=pytestconfig.option.teardown) as engine:
        yield engine
