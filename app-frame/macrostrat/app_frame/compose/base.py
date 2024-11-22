from rich.console import Console

from macrostrat.utils import cmd, get_logger

console = Console()

log = get_logger(__name__)


def compose(*args, **kwargs):
    """Run docker compose commands in the appropriate context"""
    return cmd("docker", "compose", *args, **kwargs)


def check_status(app_name: str, command_name: str) -> list[str]:
    # Check if containers are running
    res = compose("ps --services --filter status=running", capture_output=True)
    running_containers = res.stdout.decode("utf-8").strip()
    if running_containers == "":
        return []

    containers = running_containers.split("\n")
    containers = [c.strip() for c in containers]

    console.print("[dim]Some containers are already running and up to date: ")
    console.print("  " + ", ".join(containers))
    console.print(
        f"[dim]To fully restart {app_name}, run [cyan]{command_name} restart[/cyan]"
        f" or [cyan]{command_name} up --force-recreate[/cyan]."
    )

    return containers
