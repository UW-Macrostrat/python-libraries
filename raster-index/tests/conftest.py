from pathlib import Path

from pytest import fixture
from sqlalchemy.engine import make_url

from macrostrat.database.utils import temporary_database
from macrostrat.raster_index import RasterIndex
from macrostrat.raster_index.testing import create_test_rasters


@fixture(scope="session")
def raster_files(tmp_path_factory) -> dict[str, Path]:
    """Generated test rasters, keyed by slug."""
    return create_test_rasters(tmp_path_factory.mktemp("rasters"))


@fixture(scope="session")
def raster_index(database_url, pytestconfig):
    """An empty raster index in its own database.

    Its own database, rather than the shared one from the root fixture, so it
    can't be confused by (or confuse) the other packages' test schemas.
    """
    url = make_url(str(database_url)).set(database="raster_index_test")
    with temporary_database(
        url, drop=pytestconfig.option.teardown, ensure_empty=True, force_drop=True
    ) as engine:
        index = RasterIndex(engine)
        index.create_schema()
        yield index


@fixture(scope="session")
def populated_index(raster_index, raster_files):
    """An index holding the test rasters, in two layers."""
    raster_index.register_layer(
        "minerals", name="Test mineral maps", description="Overlapping test rasters"
    )
    raster_index.register_layer("other", name="Somewhere else")

    for slug in ("fine", "coarse"):
        raster_index.add_raster(raster_files[slug], layer="minerals", slug=slug)
    raster_index.add_raster(raster_files["elsewhere"], layer="other", slug="elsewhere")
    return raster_index
