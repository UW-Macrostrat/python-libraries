from typing import Optional

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import Version
from toposort import toposort_flatten

from macrostrat.utils.logs import get_logger

from ..core import ApplicationBase
from .defs import Subsystem, SubsystemError

log = get_logger(__name__)


class SubsystemManager:
    """
    Storage class for plugins. Currently, we enforce a single
    phase of plugin loading, ended by a call to `finished_loading_plugins`.
    Hooks can be run afterwards. This removes the risk of some parts of the
    application performing actions before all plugins are initialized.
    """

    _hooks_fired = []
    _app: Optional[ApplicationBase] = None
    _subsystem_cls: Subsystem = Subsystem

    def __init__(self, subsystem_cls: Subsystem = Subsystem):
        self._app = None
        self.__init_store = []
        self.__store = None

        # Ensure that the plugin class is a subclass of Subsystem
        assert issubclass(subsystem_cls, Subsystem) or subsystem_cls is Subsystem
        self._subsystem_cls = subsystem_cls

    def __iter__(self):
        try:
            yield from self.__store
        except TypeError:
            raise SubsystemError("Cannot list subsystems until loading is finished.")

    @property
    def is_ready(self):
        return self.__store is not None

    def _is_compatible(self, sub: Subsystem):
        """Assess package compatibility: https://packaging.pypa.io/en/latest/specifiers.html"""
        if sub.app_version is None:
            return True
        try:
            spec = SpecifierSet(sub.app_version, prereleases=True)
        except InvalidSpecifier:
            raise SubsystemError(
                f"Subsystem '{sub.name}' specifies an invalid {self.app.name} compatibility range '{sub.app_version}'"
            )
        return Version() in spec

    def add(self, plugin):
        if not plugin.should_enable(self):
            return
        if not self._is_compatible(plugin):
            _raise_compat_error(plugin)
            return

        try:
            self.__init_store.append(plugin)
        except AttributeError:
            raise SubsystemError(
                f"Cannot add subsystems after {self.app.name} is finished loading."
            )
        except Exception as err:
            _raise_load_error(plugin, err)

    def add_module(self, module):
        for _, obj in module.__dict__.items():
            try:
                assert issubclass(obj, Subsystem)
            except (TypeError, AssertionError):
                continue

            if obj is Subsystem:
                continue

            self.add(obj)

    def add_all(self, *plugins):
        for plugin in plugins:
            self.add(plugin)

    def order_plugins(self, store=None):
        store = store or self.__store
        for p in store:
            if getattr(p, "name") is None:
                raise SubsystemError(
                    f"{self.app.name} subsystem '{p}' must have a name attribute."
                )
        struct = {p.name: set(p.dependencies) for p in store}
        map_ = {p.name: p for p in store}
        res = toposort_flatten(struct, sort=True)
        return {map_[k] for k in res}

    def __load_plugin(self, plugin, app: ApplicationBase):
        if isinstance(plugin, Subsystem):
            log.info(f"Loading subsystem: {plugin.__class__.name}")
            return plugin

        if issubclass(plugin, Subsystem):
            return self.__load_plugin(plugin(app))

        raise SubsystemError(
            f"{app.name} subsystems must be an instance or subclass of Subsystem"
        )

    def finalize(self, app: ApplicationBase):
        candidate_store = self.order_plugins(self.__init_store)

        self.__store = []
        for plugin in candidate_store:
            self.__store.append(self.__load_plugin(plugin, app))

        self.__init_store = None

    def get(self, name: str) -> Subsystem:
        """Get a plugin object, given its name."""
        for plugin in self.__store:
            if plugin.name == name:
                return plugin
        raise AttributeError(f"Subsystem {name} not found")

    def _iter_hooks(self, hook_name):
        method_name = "on_" + hook_name.replace("-", "_")
        for plugin in self.__store:
            method = getattr(plugin, method_name, None)
            if method is None:
                continue
            log.info("  subsystem: " + plugin.name)
            yield plugin, method

    def run_hook(self, hook_name, *args, **kwargs):
        self._hooks_fired.append(hook_name)
        for _, method in self._iter_hooks(hook_name):
            method(*args, **kwargs)


def _raise_compat_error(sub: Subsystem, app: ApplicationBase):
    _error = (
        f"Subsystem '{sub.name}' is incompatible with {app.name} "
        f"version {app.version} (expected {sub.app_version})"
    )
    log.error(_error)
    raise SubsystemError(_error)


def _raise_load_error(sub, err):
    _error = f"Could not load subsystem '{sub.name}': {err}"
    log.error(_error)
    raise SubsystemError(_error)
