import time
import docker
import subprocess
from pathlib import Path
from docker.client import DockerClient
from macrostrat.utils import get_logger
from rich import Console
from typing import List

from .utils import database_cluster, ensure_empty_docker_volume, replace_docker_volume
from .restore import pg_restore
from .describe import (
    check_database_exists,
    count_database_tables,
    check_database_cluster_version,
)

log = get_logger(__name__)

console = Console()


class DatabaseUpgradeError(Exception):
    pass


version_images = {11: "mdillon/postgis:11", 14: "postgis/postgis:14-3.3"}


def upgrade_database_cluster(
    client: DockerClient,
    cluster_volume_name: str,
    target_version: int,
    databases: List[str],
):
    """
    Upgrade a PostgreSQL cluster in a Docker volume
    under a managed installation of Sparrow.
    """

    cluster_new_name = cluster_volume_name + "_new"

    current_version = check_database_cluster_version(client, cluster_volume_name)

    if current_version not in version_images:
        raise DatabaseUpgradeError("No upgrade path available")

    if target_version not in version_images:
        raise DatabaseUpgradeError("Target PostgreSQL version is not supported")

    # Create the volume for the new cluster
    dest_volume = ensure_empty_docker_volume(client, cluster_new_name)

    print(
        f"Upgrading database cluster from version {current_version} to {target_version}..."
    )

    with database_cluster(
        client, version_images[current_version], cluster_volume_name
    ) as source, database_cluster(
        client,
        version_images[target_version],
        dest_volume.name,
        environment={"POSTGRES_HOST_AUTH_METHOD": "trust"},
    ) as target:
        # Dump the database
        time.sleep(2)

        # Check if the database exists
        dbname = "sparrow"

        print("Dumping database...")

        # Run PG_Restore asynchronously
        for dbname in databases:
            if check_database_exists(source, dbname):
                print(f"Database {dbname} exists in source cluster")
            else:
                print(f"Database {dbname} does not exist in source, skipping dump.")
                return

            n_tables = count_database_tables(source, dbname)

            print("Creating database")

            res = target.exec_run("createdb -U postgres sparrow", user="postgres")
            print(res)

            if not check_database_exists(target, dbname):
                raise DatabaseUpgradeError("Database not created")

        pg_restore(source, target, dbname)

        db_exists = check_database_exists(target, dbname)
        new_n_tables = count_database_tables(target, dbname)

        if db_exists:
            print(f"Database {dbname} exists in target cluster.")
        else:
            print(f"Database {dbname} does not exist in target, dump failed.")
            dest_volume.remove()
            return

        if new_n_tables >= n_tables:
            print(f"{new_n_tables} tables were restored.")
        else:
            print(f"Expected {n_tables} tables, got {new_n_tables}")
            console.print("The migration failed.", style="bold red")
            dest_volume.remove()
            return

    time.sleep(1)

    client = cfg.docker_client

    # Remove the old volume
    backup_volume_name = cluster_volume_name + "_backup"
    console.print(f"Backing up old volume to {backup_volume_name}", style="bold")
    ensure_empty_docker_volume(client, backup_volume_name)
    replace_docker_volume(client, cluster_volume_name, backup_volume_name)

    console.print(
        f"Moving contents of new volume to {cluster_volume_name}", style="bold"
    )
    replace_docker_volume(client, cluster_new_name, cluster_volume_name)
    client.volumes.get(cluster_new_name).remove(force=True)

    console.print("Done!", style="bold green")
