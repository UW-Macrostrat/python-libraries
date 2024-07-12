"""
PostgreSQL/PostGIS cluster upgrade tests reliant onto Docker.
"""
import random
import time
from os import environ
from pathlib import Path
from re import match

from docker.client import DockerClient
from pytest import fixture
from sqlalchemy import create_engine, inspect, text

from macrostrat.database import Database
from macrostrat.database.utils import run_sql_file
from macrostrat.dinosaur import create_schema_clone, dump_schema
from macrostrat.utils import get_logger

log = get_logger(__name__)

random_hex = lambda: "%08x" % random.randrange(16**8)

from macrostrat.dinosaur.upgrade_cluster import (
    default_version_images,
    upgrade_database_cluster,
)
from macrostrat.dinosaur.upgrade_cluster.describe import check_database_cluster_version
from macrostrat.dinosaur.upgrade_cluster.utils import (
    database_cluster,
    ensure_empty_docker_volume,
    get_unused_port,
)

root_dir = Path(__file__).parent
fixtures = root_dir / "fixtures"

environ.setdefault("DOCKER_HOST", "unix:///var/run/docker.sock")

# Sparrow used mdillon/postgis:11 image for PostgreSQL 11. However,
# this does not work on Apple Silicon. We can use a newer image, but this prevents
# us from testing older PostGIS versions (2.5), so we keep this configurable.
old_postgres_image = environ.get("OLD_POSTGRES_IMAGE", "mdillon/postgis:11")
new_postgres_image = environ.get("POSTGRES_IMAGE", "postgis/postgis:14-3.3")

version_images = {}
# Extract versions from image names

def get_major_version(image_name):
    return int(match(r".*:(\d+)", image_name).group(1))

old_postgres_major_version = get_major_version(old_postgres_image)
new_postgres_major_version = get_major_version(new_postgres_image)

for image_name in [old_postgres_image, new_postgres_image]:
    version = get_major_version(image_name)
    version_images[version] = image_name

log.info(f"Testing upgrade from {old_postgres_image} to {new_postgres_image}")

client = DockerClient.from_env()


@fixture(scope="module")
def postgres_11_cluster_volume():
    """Create a PostgreSQL 11 cluster."""

    volume_name = f"test_postgres_{old_postgres_major_version}_{random_hex()}"
    database_name = "test_database"

    port = get_unused_port()

    ensure_empty_docker_volume(client, volume_name)

    with database_cluster(
        client, old_postgres_image, volume_name, port=port
    ) as container:
        container.exec_run("createdb -U postgres " + database_name, user="postgres")

        # Connect to cluster
        url = f"postgresql://postgres@localhost:{port}/{database_name}"
        db = Database(url)

        fn = fixtures / "test-cluster-1.sql"
        run_sql_file(db.session, fn)

    yield volume_name

    client.volumes.get(volume_name).remove(force=True)


@fixture(scope="module")
def postgres_11_db(postgres_11_cluster_volume):
    port = get_unused_port()
    with database_cluster(
        client, old_postgres_image, postgres_11_cluster_volume, port=port
    ) as container:
        yield Database(f"postgresql://postgres@localhost:{port}/test_database")


def test_dump_schema(postgres_11_db):
    """Test dumping of a PostgreSQL schema."""
    schema_sql = dump_schema(
        postgres_11_db.engine, image_name=version_images[old_postgres_major_version]
    )
    assert "CREATE EXTENSION IF NOT EXISTS postgis" in schema_sql
    assert "CREATE TABLE public.sample" in schema_sql


def test_schema_clone(postgres_11_db):
    """Test dumping of a PostgreSQL schema."""

    url = postgres_11_db.engine.url
    url_clone = url.set(database="test_schema_clone")

    with create_schema_clone(
        postgres_11_db.engine, url_clone, image_name=version_images[old_postgres_major_version]
    ) as clone:
        conn = clone.connect()
        assert conn.execute(text("SELECT 1")).fetchone()[0] == 1
        insp = inspect(clone)
        # TODO: this doesn't work with schema=None
        assert insp.has_table("sample", schema="public")
        assert insp.has_table("spatial_ref_sys", schema="public")


def test_upgrade_cluster(postgres_11_cluster_volume):
    """Test upgrade of a PostgreSQL cluster."""

    assert check_database_cluster_version(client, postgres_11_cluster_volume) == old_postgres_major_version

    upgrade_database_cluster(client, postgres_11_cluster_volume, new_postgres_major_version, ["test_database"], version_images)

    assert check_database_cluster_version(client, postgres_11_cluster_volume) == new_postgres_major_version
