from pathlib import Path

from macrostrat.app_frame import Application

APP_ROOT = Path(__file__).parent

app = Application(
    "Test app",
    restart_commands={"gateway": "caddy reload --config /etc/caddy/Caddyfile"},
    log_modules=["test_app"],
    compose_files=[APP_ROOT / "docker-compose.yaml"],
)
main = app.control_command()

@main.command()
def throw_error():
    """Command that throws an error for testing purposes"""
    raise RuntimeError("This is a test error")
