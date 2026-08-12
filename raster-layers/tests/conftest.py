from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pytest import fixture
from sqlalchemy.engine import make_url
from titiler.core.errors import DEFAULT_STATUS_CODES, add_exception_handlers

from macrostrat.database.utils import temporary_database
from macrostrat.raster_index import RasterIndex
from macrostrat.raster_index.testing import CATEGORICAL_COLORMAP, create_test_rasters
from macrostrat.raster_layers import RasterLayerConfig, register_raster_layers


@fixture(scope="session")
def raster_files(tmp_path_factory) -> dict[str, Path]:
    return create_test_rasters(tmp_path_factory.mktemp("rasters"))


@fixture(scope="session")
def index(database_url, pytestconfig, raster_files):
    """A raster index holding the test rasters, in its own database."""
    url = make_url(str(database_url)).set(database="raster_layers_test")
    with temporary_database(
        url, drop=pytestconfig.option.teardown, ensure_empty=True, force_drop=True
    ) as engine:
        index = RasterIndex(engine)
        index.create_schema()
        index.register_layer(
            "minerals",
            name="Test mineral maps",
            # Stored on the layer so the server can render categorical data
            # without the client having to send a palette.
            colormap={str(k): list(v) for k, v in CATEGORICAL_COLORMAP.items()},
        )
        for slug in ("fine", "coarse"):
            index.add_raster(raster_files[slug], layer="minerals", slug=slug)
        yield index


@fixture(scope="session")
def app(index) -> FastAPI:
    """A minimal application serving the test layer, as a host app would."""
    app = FastAPI()
    register_raster_layers(
        app,
        index,
        [RasterLayerConfig(slug="minerals", title="Test mineral maps")],
        prefix="/rasters",
    )
    # Deliberately *not* registering MOSAIC_STATUS_CODES: `register_raster_layers`
    # installs its own no-coverage handler, and re-registering titiler's after
    # mounting would clobber it (a real hazard for host applications too).
    add_exception_handlers(app, DEFAULT_STATUS_CODES)
    return app


@fixture(scope="session")
def client(app) -> TestClient:
    return TestClient(app)
