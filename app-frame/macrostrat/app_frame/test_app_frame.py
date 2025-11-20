from typer.testing import CliRunner

from macrostrat.app_frame import Application

runner = CliRunner()

def test_app_frame_setup():
    main = _setup_app()
    assert main.app.name == "Test App"


def test_app_frame_cli_invocation():
    main = _setup_app()
    result = runner.invoke(main)
    assert result.exit_code == 0
    assert "Test App" in result.output


def _setup_app():
    app = Application(
        "Test App",
        log_modules=["test_app"],
    )
    return app.control_command()
