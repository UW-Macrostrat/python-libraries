from distutils.log import warn
from warnings import warn

# Drag in geographic types for database reflection
from geoalchemy2 import Geography, Geometry
from sqlalchemy.ext.automap import generate_relationship

from macrostrat.database.utils import reflect_table
from macrostrat.utils.logs import get_logger

from .cache import DatabaseModelCache
from .utils import (
    ModelCollection,
    TableCollection,
    _classname_for_table,
    classname_for_table,
    name_for_collection_relationship,
    name_for_scalar_relationship,
)

log = get_logger(__name__)


class AutomapError(Exception):
    pass


model_builder = DatabaseModelCache()
BaseModel = model_builder.automap_base()


class DatabaseMapper:
    automap_base = BaseModel
    automap_error = None
    _models = None
    _tables = None

    def __init__(self, db, **kwargs):
        # https://docs.sqlalchemy.org/en/13/orm/extensions/automap.html#sqlalchemy.ext.automap.AutomapBase.prepare
        # TODO: add the process flow described below:
        # https://docs.sqlalchemy.org/en/13/orm/extensions/automap.html#generating-mappings-from-an-existing-metadata
        self.db = db

        # This stuff should be placed outside of core (one likely extension point).
        self.reflection_kwargs = dict(
            name_for_scalar_relationship=kwargs.get(
                "name_for_scalar_relationship", name_for_scalar_relationship
            ),
            name_for_collection_relationship=kwargs.get(
                "name_for_collection_relationship", name_for_collection_relationship
            ),
            classname_for_table=kwargs.get("classname_for_table", _classname_for_table),
            generate_relationship=kwargs.get(
                "generate_relationship", generate_relationship
            ),
        )

        self._models = ModelCollection(self.automap_base.classes)
        self._tables = TableCollection(self._models)

    def reflect_database(self, schemas=["public"], use_cache=True):
        # This stuff should be placed outside of core (one likely extension point).

        for schema in schemas:
            self.reflect_schema(schema, use_cache=use_cache)

        self._cache_database_map()

        self._models = ModelCollection(self.automap_base.classes)
        self._tables = TableCollection(self._models)

    def _cache_database_map(self):
        if self.automap_base.loaded_from_cache:
            return
        self.automap_base.builder._cache_database_map(self.automap_base.metadata)

    def reflect_schema(self, schema, use_cache=True):
        if use_cache and self.automap_base.loaded_from_cache:
            log.info("Database models for %s have been loaded from cache", schema)
            self.automap_base.prepare(schema=schema, **self.reflection_kwargs)
            return
        log.info(f"Reflecting schema {schema}")
        if schema == "public":
            schema = None
        # Reflect tables in schemas we care about
        # Note: this will not reflect views because they don't have primary keys.
        self.automap_base.prepare(
            autoload_with=self.db.engine, schema=schema, **self.reflection_kwargs
        )
        self._models = ModelCollection(self.automap_base.classes)
        self._tables = TableCollection(self._models)

    def reflect_table(self, tablename, *column_args, **kwargs):
        # Warn that this method is deprecated
        warn(
            "DatabaseMapper.reflect_table is deprecated. Use Database.reflect_table instead",
            DeprecationWarning,
        )
        return reflect_table(self.db.engine, tablename, *column_args, **kwargs)

    def reflect_view(self, tablename, *column_args, **kwargs):
        pass
        # schema = kwargs.pop("schema", "public")
        # meta = MetaData(self.engine,schema=schema)
        # ##meta.reflect(view=True)
        # log.info(meta.tables)
        # return meta.tables[tablename]

    def register_models(self, *models):
        # Could allow overriding name functions etc.
        self._models.register(*models)

    def automap_view(self, table_name, *column_args, **kwargs):
        """
        Views cannot be directly automapped, because they don't have primary keys.
        So we have to use a workaround of specifying a primary key ourselves.
        """
        tbl = self.reflect_table(table_name, *column_args, **kwargs)
        name = classname_for_table(tbl)
        return type(name, (self.automap_base,), dict(__table__=tbl))
