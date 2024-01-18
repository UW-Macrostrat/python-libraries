# Macrostrat application frame

This is a CLI toolset for managing data and services for a Dockerized application.
It was originally developed for the [Sparrow](https://sparrow-data.org) application, but it has been generalized
for use with other projects including [Macrostrat](https://macrostrat.org), Mapboard and other systems.

It is designed to work well with projects managed using Docker Compose, but may eventually
acquire features for managing Kubernetes-based applications.

# Basic usage

```python
app = Application(
    "Mapboard",
    restart_commands={"gateway": "caddy reload --config /etc/caddy/Caddyfile"},
    log_modules=["mapboard.server"],
    compose_files=[MAPBOARD_ROOT / "system" / "docker-compose.yaml"],
)
cli = app.control_command()
```

This creates a [typer](https://typer.tiangolo.com/) CLI app that can be used to control the application,
with abilities to start, stop, and restart services. It provides easy extension points to add new functionality.
