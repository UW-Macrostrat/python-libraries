"""
PostgreSQL/PostGIS cluster upgrade tests reliant onto Docker.
"""
from pytest import fixture
from docker.client import DockerClient
from sqlalchemy import create_engine
from macrostrat.database import Database
from macrostrat.database.utils import run_sql_file
from macrostrat.utils import get_logger
from macrostrat.dinosaur import dump_schema, create_schema_clone
from pathlib import Path
from os import environ
import time
import random

log = get_logger(__name__)

random_hex = lambda: "%08x" % random.randrange(16**8)

from macrostrat.dinosaur.upgrade_cluster.utils import (
    database_cluster,
    get_unused_port,
    ensure_empty_docker_volume,
)

from macrostrat.dinosaur.upgrade_cluster import upgrade_database_cluster
from macrostrat.dinosaur.upgrade_cluster.describe import check_database_cluster_version

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
        db = Database(url)

        fn = fixtures / "test-cluster-1.sql"
        run_sql_file(db.session, fn)

    yield volume_name

    client.volumes.get(volume_name).remove(force=True)


def test_dump_schema(postgres_11_cluster):
    """Test dumping of a PostgreSQL schema."""

    port = get_unused_port()
    with database_cluster(
        client, "mdillon/postgis:11", postgres_11_cluster, port=port
    ) as container:
        # Connect to cluster
        url = f"postgresql://postgres@localhost:{port}/test_database"
        db = Database(url)

        schema_sql = dump_schema(db.engine)
        assert "CREATE EXTENSION IF NOT EXISTS postgis" in schema_sql
        assert "CREATE TABLE public.sample" in schema_sql


def test_schema_clone(postgres_11_cluster):
    """Test dumping of a PostgreSQL schema."""

    port = get_unused_port()
    with database_cluster(
        client, "mdillon/postgis:11", postgres_11_cluster, port=port
    ) as container:
        # Connect to cluster
        url = f"postgresql://postgres@localhost:{port}/test_database"
        db = Database(url)
        with create_schema_clone(
            db.engine, f"postgresql://postgres@localhost:{port}/test_schema_clone"
        ) as clone:
            assert clone.engine.has_table("spatial_ref_sys")
            assert clone.engine.has_table("sample")


def test_upgrade_cluster(postgres_11_cluster):
    """Test upgrade of a PostgreSQL cluster."""

    assert check_database_cluster_version(client, postgres_11_cluster) == 11

    upgrade_database_cluster(client, postgres_11_cluster, 14, ["test_database"])

    assert check_database_cluster_version(client, postgres_11_cluster) == 14
