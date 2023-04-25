import time
from contextlib import contextmanager
import socket

import docker
from docker.client import DockerClient
from docker.models.containers import Container
from macrostrat.utils import get_logger
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

log = get_logger(__name__)


@contextmanager
def database_cluster(
    client: DockerClient,
    image: str,
    data_volume: str = None,
    remove=True,
    environment={},
    port=None,
):
    """
    Start a database cluster in a Docker volume
    under a managed installation of Sparrow.
    """
    print("Starting database cluster...")
    environment.setdefault("POSTGRES_HOST_AUTH_METHOD", "trust")
    environment.setdefault("POSTGRES_DB", "postgres")
    environment.setdefault("PGUSER", "postgres")
    ports = None
    if port is not None:
        ports = {f"5432/tcp": port}

    volumes = None
    if data_volume is not None:
        volumes = {data_volume: {"bind": "/var/lib/postgresql/data", "mode": "rw"}}

    try:
        container = client.containers.run(
            image,
            detach=True,
            remove=False,
            auto_remove=False,
            environment=environment,
            volumes=volumes,
            user="postgres",
            ports=ports,
        )
        log.info(f"Started container {container.name} ({image})")

        url = f"postgresql://postgres@localhost:{port}/postgres"

        wait_for_cluster(container, url)
        yield container
    finally:
        log.debug(container.logs().decode("utf-8"))
        log.info(f"Stopping container {container.name} ({image})...")
        container.stop()
        container.remove()


def wait_for_cluster(container: Container, url: str):
    """
    Wait for a database to be ready.
    """
    print("Waiting for database to be ready...")
    log.debug("Waiting for database to be ready...")

    # is_running = False
    # while not is_running:
    #     print(container.status)
    #     time.sleep(0.1)
    #     is_running = container.status == "created"

    is_ready = False
    engine = create_engine(url)
    while not is_ready:
        # log_step(container)
        try:
            engine.connect()
        except OperationalError:
            pass
        else:
            is_ready = True
        time.sleep(0.1)
    print("Database cluster is ready")
    log.debug("Database cluster is ready")
    # log_step(container)


def replace_docker_volume(client: DockerClient, from_volume: str, to_volume: str):
    """
    Replace the contents of a Docker volume.
    """
    print(f"Copying contents of volume {from_volume} to {to_volume}")
    client.containers.run(
        "bash",
        '-c "cd /from-volume ; cp -av . /to-volume"',
        volumes={from_volume: {"bind": "/from-volume"}, to_volume: {"bind": "/to-volume"}},
        remove=True,
    )


def ensure_empty_docker_volume(client: DockerClient, volume_name: str):
    """
    Ensure that a Docker volume does not exist.
    """
    try:
        client.volumes.get(volume_name).remove()
    except docker.errors.NotFound:
        pass
    return client.volumes.create(name=volume_name)


def get_unused_port():
    """
    Get an unused port on the host machine.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        return s.getsockname()[1]
