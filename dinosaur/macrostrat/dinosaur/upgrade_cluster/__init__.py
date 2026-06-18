import asyncio
from typing import List

from docker.client import DockerClient
from macrostrat.database import Database
from rich.console import Console

from macrostrat.database.transfer import move_tables
from macrostrat.database.utils import get_database_url, database_exists, create_database
from macrostrat.utils import get_logger
from .describe import (
    check_database_cluster_version,
    check_database_exists,
    count_database_tables,
)
from .utils import (
    ensure_empty_docker_volume,
    get_unused_port,
    replace_docker_volume,
    database_cluster_legacy,
)
from ..cluster import database_cluster

log = get_logger(__name__)

console = Console()


class DatabaseUpgradeError(Exception):
    pass


default_version_images = {11: "mdillon/postgis:11", 14: "postgis/postgis:14-3.3"}


def upgrade_database_cluster(
    client: DockerClient,
    cluster_volume_name: str,
    target_version: int,
    databases: List[str],
    version_images: dict = default_version_images,
):
    """
    Upgrade a PostgreSQL cluster in a Docker volume
    under a managed installation of Sparrow.
    """

    cluster_new_name = cluster_volume_name + "_new"

    current_version = check_database_cluster_version(client, cluster_volume_name)

    print(
        f"Upgrading database cluster from version {current_version} to {target_version}..."
    )

    if current_version not in version_images:
        raise DatabaseUpgradeError("No upgrade path available")

    if target_version not in version_images:
        raise DatabaseUpgradeError("Target PostgreSQL version is not supported")

    if int(current_version) == int(target_version):
        console.print(
            f"[bold green]Database cluster is already at version {target_version}."
        )
        return

    from_image = version_images[current_version]
    to_image = version_images[target_version]

    print(f"Docker containers - from: {from_image}, to: {to_image}")

    # Create the volume for the new cluster
    ensure_empty_docker_volume(client, cluster_new_name)

    with (
        database_cluster(
            from_image,
            data_volume=cluster_volume_name,
            docker_client=client,
        ) as source,
        database_cluster(
            to_image,
            data_volume=cluster_new_name,
            docker_client=client,
        ) as target,
    ):
        _upgrade_cluster(source, target, databases)

    # Remove the old volume
    backup_volume_name = cluster_volume_name + "_backup"
    console.print(f"Backing up old volume to {backup_volume_name}", style="bold")
    ensure_empty_docker_volume(client, backup_volume_name)
    replace_docker_volume(client, cluster_volume_name, backup_volume_name)

    console.print(
        f"Moving contents of new volume to {cluster_volume_name}", style="bold"
    )
    # Bring down any containers using the current cluster volume
    containers = client.containers.list(filters={"volume": cluster_volume_name})
    # Filter to only running containers
    restart_containers = [c for c in containers if c.status == "running"]
    for container in containers:
        container.stop()

    replace_docker_volume(client, cluster_new_name, cluster_volume_name)
    client.volumes.get(cluster_new_name).remove(force=True)

    console.print("Restarting containers", style="bold")
    for container in restart_containers:
        container.start()

    console.print("Done!", style="bold green")


def _upgrade_cluster(source, target, databases):
    log.info("Dumping database...")

    for dbname in databases:
        source_url = get_database_url(source).set(database=dbname)
        target_url = get_database_url(target).set(database=dbname)

        if not database_exists(source_url):
            log.info(f"Database {dbname} does not exist in source, skipping.")
            continue

        log.info(f"Database {dbname} exists in source cluster")
        source_db = Database(source_url)
        n_tables = count_database_tables(source_db)

        log.info("Creating database")
        create_database(target_url)

        if not database_exists(target_url):
            raise DatabaseUpgradeError("Database not created")

        target_db = Database(target_url)
        asyncio.run(move_tables(source_db.engine, target_db.engine))

        if not database_exists(target_url):
            raise DatabaseUpgradeError(
                f"Database {dbname} does not exist in target after migration"
            )

        new_n_tables = count_database_tables(target_db)
        if new_n_tables >= n_tables:
            log.info(f"{new_n_tables} tables were restored.")
        else:
            raise DatabaseUpgradeError(
                f"Expected {n_tables} tables, got {new_n_tables}"
            )


# In-place upgrade
