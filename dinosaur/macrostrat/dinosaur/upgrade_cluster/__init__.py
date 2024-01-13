from typing import List

from docker.client import DockerClient
from rich.console import Console

from macrostrat.utils import get_logger

from .describe import (
    check_database_cluster_version,
    check_database_exists,
    count_database_tables,
)
from .restore import pg_restore
from .utils import (
    database_cluster,
    ensure_empty_docker_volume,
    get_unused_port,
    replace_docker_volume,
)

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

    if current_version not in version_images:
        raise DatabaseUpgradeError("No upgrade path available")

    if target_version not in version_images:
        raise DatabaseUpgradeError("Target PostgreSQL version is not supported")

    if int(current_version) == int(target_version):
        console.print(
            f"[bold green]Database cluster is already at version {target_version}."
        )
        return

    # Create the volume for the new cluster
    dest_volume = ensure_empty_docker_volume(client, cluster_new_name)

    print(
        f"Upgrading database cluster from version {current_version} to {target_version}..."
    )

    source_port = get_unused_port()
    target_port = get_unused_port()

    with database_cluster(
        client, version_images[current_version], cluster_volume_name, port=source_port
    ) as source, database_cluster(
        client,
        version_images[target_version],
        dest_volume.name,
        port=target_port,
    ) as target:
        # Dump the database
        log.info("Dumping database...")

        # Run PG_Restore asynchronously
        for dbname in databases:
            if check_database_exists(source, dbname):
                log.info(f"Database {dbname} exists in source cluster")
            else:
                log.info(f"Database {dbname} does not exist in source, skipping dump.")
                return

            n_tables = count_database_tables(source, dbname)

            log.info("Creating database")

            target.exec_run(f"createdb -U postgres {dbname}", user="postgres")

            if not check_database_exists(target, dbname):
                raise DatabaseUpgradeError("Database not created")

        pg_restore(source, target, dbname)

        db_exists = check_database_exists(target, dbname)
        new_n_tables = count_database_tables(target, dbname)

        if db_exists:
            log.info(f"Database {dbname} exists in target cluster.")
        else:
            log.info(f"Database {dbname} does not exist in target, dump failed.")
            dest_volume.remove()
            return

        if new_n_tables >= n_tables:
            log.info(f"{new_n_tables} tables were restored.")
        else:
            dest_volume.remove()
            raise DatabaseUpgradeError(
                f"Expected {n_tables} tables, got {new_n_tables}"
            )

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


# In-place upgrade
