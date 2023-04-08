import time
from contextlib import contextmanager
import socket

import docker
from docker.client import DockerClient
from docker.models.containers import Container


@contextmanager
def database_cluster(
    client: DockerClient,
    image: str,
    data_volume: str,
    remove=True,
    environment=None,
    port=5432,
) -> Container:
    """
    Start a database cluster in a Docker volume
    under a managed installation of Sparrow.
    """
    print("Starting database cluster...")
    try:
        container = client.containers.run(
            image,
            detach=True,
            remove=remove,
            environment=environment,
            volumes={data_volume: {"bind": "/var/lib/postgresql/data", "mode": "rw"}},
            user="postgres",
            ports={f"5432/tcp": port},
        )
        wait_for_cluster(container)
        yield container
    finally:
        print(f"Stopping database cluster {image}...")
        container.stop()


def wait_for_cluster(container: Container):
    """
    Wait for a database to be ready.
    """
    print("Waiting for database to be ready...")
    # is_running = False
    # while not is_running:
    #     print(container.status)
    #     time.sleep(0.1)
    #     is_running = container.status == "created"

    is_ready = False
    while not is_ready:
        time.sleep(0.1)
        # log_step(container)
        res = container.exec_run("pg_isready", user="postgres")
        is_ready = res.exit_code == 0
    time.sleep(1)
    # log_step(container)
    return


def replace_docker_volume(client: DockerClient, old_name: str, new_name: str):
    """
    Replace the contents of a Docker volume.
    """
    print(f"Moving contents of volume {old_name} to {new_name}")
    client.containers.run(
        "bash",
        '-c "cd /old ; cp -av . /new"',
        volumes={old_name: {"bind": "/old"}, new_name: {"bind": "/new"}},
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
