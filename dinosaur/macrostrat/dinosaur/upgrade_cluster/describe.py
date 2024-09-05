from pathlib import Path
from subprocess import CalledProcessError

from docker.client import DockerClient
from docker.errors import ContainerError
from docker.models.containers import Container

from macrostrat.utils import get_logger

log = get_logger(__name__)


def check_database_cluster_version(client: DockerClient, volume_name: str):
    """
    Check the version of a PostgreSQL cluster in a Docker volume.
    """
    cluster_dir = "/var/lib/postgresql/data"
    version_file = Path(cluster_dir) / "PG_VERSION"
    log.info(f"Checking version of database cluster in volume {volume_name}")
    try:
        stdout = client.containers.run(
            "bash",
            f"cat {version_file}",
            volumes={volume_name: {"bind": cluster_dir, "mode": "ro"}},
            remove=True,
            stdout=True,
        )
    except (ContainerError, CalledProcessError) as exc:
        log.error(exc)
        return None
    return int(stdout.decode("utf-8").strip())


def check_database_exists(container: Container, db_name: str) -> bool:
    res = container.exec_run(f"psql -U postgres -lqt", stdout=True, demux=True)
    if res.exit_code != 0:
        return False
    stdout = res.output[0].decode("utf-8")
    for line in stdout.splitlines():
        if line.split("|")[0].strip() == db_name:
            return True
    return False


def count_database_tables(container: Container, db_name: str) -> int:
    res = container.exec_run(
        f"psql -U postgres -d {db_name} -c 'SELECT COUNT(*) FROM information_schema.tables;'",
        stdout=True,
        demux=True,
        user="postgres",
    )
    stdout = res.output[0].decode("utf-8")
    return int(stdout.splitlines()[2].strip())
