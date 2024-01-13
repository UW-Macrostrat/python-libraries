from macrostrat.app_frame import ApplicationError


class SubsystemError(ApplicationError):
    pass


class Subsystem:
    """A base subsystem

    app_version can be set to a specifier of valid versions of the hosting application.
    """

    dependencies = []
    app_version = None
    name = None

    def __init__(self, app):
        self.app = app
        self.db = self.app.db

    def should_enable(self):
        return True
