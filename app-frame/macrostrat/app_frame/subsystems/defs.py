from ..exc import ApplicationError


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

    def should_enable(self, mgr: "SubsystemManager"):
        return True
