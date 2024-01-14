import socket
import time
from contextlib import contextmanager
from typing import Mapping, Optional

import docker
from docker.client import DockerClient
from docker.models.containers import Container
from sqlalchemy import create_engine
from sqlalchemy.exc import OperationalError

from macrostrat.utils import get_logger

log = get_logger(__name__)


@contextmanager
def database_cluster(
    client: DockerClient,
    image: str,
    data_volume: str = None,
    remove=True,
    environment: Optional[Mapping[str, str]] = None,
    port=None,
):
    """
    Start a database cluster in a Docker volume
    under a managed installation of Sparrow.
    """
    print("Starting database cluster...")
    if environment is None:
        environment = {}
    environment.setdefault("POSTGRES_HOST_AUTH_METHOD", "trust")
    environment.setdefault("POSTGRES_DB", "postgres")
    environment.setdefault("PGUSER", "postgres")
    ports = None
    if port is not None:
        ports = {f"5432/tcp": port}

    volumes = None
    if data_volume is not None:
        volumes = {data_volume: {"bind": "/var/lib/postgresql/data", "mode": "rw"}}

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
    log.info(f"Starting container {container.name} ({image})")

    url = f"postgresql://postgres@localhost:{port}/postgres"
    try:
        wait_for_cluster(container, url)

        log.info(f"Started container {container.name} ({image})")

        yield container
    finally:
        # Dump all container logs
        log.debug(container.logs().decode("utf-8"))
        log.info(f"Stopping container {container.name} ({image})...")
        container.stop()
        container.remove()


def wait_for_ready(engine, timeout=5):
    """
    Wait for a database to be ready.
    """
    is_ready = False
    elapsed = 0
    error = None
    start_time = time.time()
    while not is_ready:
        try:
            engine.connect()
        except OperationalError as err:
            error = err
        else:
            is_ready = True
        time.sleep(0.1)
        elapsed = time.time() - start_time
        if elapsed > timeout:
            if error is not None:
                raise error
            raise TimeoutError("Database was not found within timeout period")


def wait_for_cluster(container: Container, url: str):
    """
    Wait for a database to be ready.
    """
    log_text = "Waiting for database %s to be ready..." % url
    print(log_text)
    log.info(log_text)

    # Wait half a second to ensure that the container is stable
    stability_timeout = 0.5

    is_running = False
    time_running = 0
    start_time = time.time()
    while time_running < stability_timeout:
        container.reload()
        time.sleep(0.1)
        is_running = container.status == "running"
        if is_running:
            time_running = time.time() - start_time
        else:
            time_running = 0
        if container.status == "exited":
            raise RuntimeError(
                "Container exited unexpectedly:\n" + container.logs().decode("utf-8")
            )

    wait_for_ready(create_engine(url))
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
        volumes={
            from_volume: {"bind": "/from-volume"},
            to_volume: {"bind": "/to-volume"},
        },
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
    sock = None
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("", 0))
        sock = s.getsockname()[1]
    return sock
