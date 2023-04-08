"""
PostgreSQL/PostGIS cluster upgrade tests reliant onto Docker.
"""
from pytest import fixture
from docker.client import DockerClient
from sqlalchemy import create_engine
from macrostrat.database.utils import run_sql_file
from pathlib import Path
from os import environ

from macrostrat.dinosaur.upgrade_cluster.utils import (
    database_cluster,
    get_unused_port,
    wait_for_cluster,
)

from macrostrat.dinosaur.upgrade_cluster import upgrade_database_cluster

root_dir = Path(__file__).parent
fixtures = root_dir / "fixtures"

environ.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")


client = DockerClient.from_env()


@fixture(scope="module")
def postgres_11_cluster():
    """Create a PostgreSQL 11 cluster."""

    volume_name = "test_postgres_11_postgis_25"
    database_name = "test_database"

    port = get_unused_port()

    with database_cluster(
        client, "mdillon/postgis:11", volume_name, port=port
    ) as container:
        wait_for_cluster(container)

        client.exec_run("createdb -U postgres " + database_name, user="postgres")

        # Connect to cluster
        url = f"postgresql://postgres@localhost:{port}/{database_name}"
        engine = create_engine(url)

        with (fixtures / "test-cluster-1.sql").open() as f:
            run_sql_file(engine, f)

    yield volume_name

    client.volumes.get(volume_name).remove(force=True)


def test_upgrade_cluster(postgres_11_cluster):
    """Test upgrade of a PostgreSQL cluster."""

    volume_name = postgres_11_cluster

    upgrade_database_cluster(client, volume_name, 14, ["test_database"])
