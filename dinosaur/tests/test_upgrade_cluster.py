"""
PostgreSQL/PostGIS cluster upgrade tests reliant onto Docker.
"""
from pytest import fixture
from docker.client import DockerClient
from sqlalchemy import create_engine
from macrostrat.database.utils import run_sql_file
from pathlib import Path
from os import environ
import time
import random

random_hex = lambda: "%08x" % random.randrange(16**8)

from macrostrat.dinosaur.upgrade_cluster.utils import (
    database_cluster,
    get_unused_port,
    wait_for_cluster,
    ensure_empty_docker_volume,
)

from macrostrat.dinosaur.upgrade_cluster import upgrade_database_cluster

root_dir = Path(__file__).parent
fixtures = root_dir / "fixtures"

environ.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")


client = DockerClient.from_env()


@fixture(scope="module")
def postgres_11_cluster():
    """Create a PostgreSQL 11 cluster."""

    volume_name = f"test_postgres_11_postgis_25_{random_hex()}"
    database_name = "test_database"

    port = get_unused_port()

    ensure_empty_docker_volume(client, volume_name)

    with database_cluster(
        client, "mdillon/postgis:11", volume_name, port=port
    ) as container:

        container.exec_run("createdb -U postgres " + database_name, user="postgres")

        # Connect to cluster
        url = f"postgresql://postgres@localhost:{port}/{database_name}"
        engine = create_engine(url)

        fn = fixtures / "test-cluster-1.sql"
        run_sql_file(engine, fn)

    yield volume_name

    client.volumes.get(volume_name).remove(force=True)


def test_upgrade_cluster(postgres_11_cluster):
    """Test upgrade of a PostgreSQL cluster."""

    volume_name = postgres_11_cluster

    upgrade_database_cluster(client, volume_name, 14, ["test_database"])
