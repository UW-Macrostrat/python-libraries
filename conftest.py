from os import environ

from docker.client import DockerClient
from dotenv import load_dotenv
from pytest import fixture
from sqlalchemy.exc import OperationalError

from macrostrat.database.utils import create_engine
from macrostrat.dinosaur.cluster import database_cluster

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
def docker_client():
    client = DockerClient.from_env()
    return client


@fixture(scope="session")
def database_url(docker_client):
    """Creates a testing database using an enviornment-provided connection string or by spinning up a Docker container."""
    testing_db = environ.get("TESTING_DATABASE")
    image = environ.get("POSTGRES_IMAGE", "postgis/postgis:14-3.3")

    # Check if testing_db is accessible
    if testing_db is not None:
        try:
            engine = create_engine(testing_db)
            engine.connect()
            engine.dispose()
        except OperationalError:
            testing_db = None

    if testing_db is not None:
        yield testing_db
    elif docker_client is not None:
        with database_cluster(image, in_memory=True, docker_client=docker_client) as db:
            yield db.engine.url

    else:
        raise ValueError(
            "Please set TESTING_DATABASE to a PostgreSQL connection string in .env file or environment or ensure Docker is running."
        )
