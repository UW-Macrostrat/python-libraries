from pathlib import Path

from macrostrat.app_frame import Application, DockerComposeManager

APP_ROOT = Path(__file__).parent

app = Application(
    "Test app",
    log_modules=["test_app"],
)

compose = DockerComposeManager(
    app,
    restart_commands={"gateway": "caddy reload --config /etc/caddy/Caddyfile"},
    compose_files=[APP_ROOT / "docker-compose.yaml"],
)

main = app.control_command()

compose.add_commands(main)

@main.command()
def throw_error():
    """Command that throws an error for testing purposes"""
    raise RuntimeError("This is a test error")
