"""Footprints traced from a raster's validity mask.

The point of these is selectivity: a bbox footprint makes every tile in the box
open the raster, so what matters is that the derived geometry is much smaller
than the box, cheap enough to test against on every query, and never smaller
than the data itself.
"""

import numpy
from pytest import approx, fixture, raises

from macrostrat.raster_index.mask_footprint import mask_footprint
from macrostrat.raster_index.testing import FINE_BOUNDS, create_test_raster


def diagonal_swath(size: int, half_width: int) -> numpy.ndarray:
    """A diagonal band of data inside a square bbox — the EMIT situation."""
    values = numpy.zeros((size, size), dtype="uint8")
    for row in range(size):
        values[row, max(0, row - half_width) : min(size, row + half_width)] = 2
    return values


@fixture(scope="module")
def swath(tmp_path_factory):
    directory = tmp_path_factory.mktemp("mask-footprints")
    return create_test_raster(
        directory / "swath.tif", FINE_BOUNDS, size=512, values=diagonal_swath(512, 40)
    )


class TestSelectivity:
    def test_footprint_is_far_smaller_than_the_bbox(self, swath):
        """The whole reason this exists."""
        result = mask_footprint(str(swath))
        assert result is not None
        assert result.area_fraction < 0.25

    def test_geometry_stays_cheap_to_query(self, swath):
        """Selection tests every tile against this, so vertex count is a cost."""
        result = mask_footprint(str(swath))
        assert result.vertices < 100

    def test_vertex_budget_is_honored(self, swath):
        result = mask_footprint(str(swath), max_vertices=20)
        assert result.vertices <= 20

    def test_full_coverage_raster_keeps_its_bbox(self, tmp_path):
        """No nodata means nothing to gain, and a traced rectangle would only add
        vertices for the selection query to chew on."""
        values = numpy.full((128, 128), 2, dtype="uint8")
        path = create_test_raster(
            tmp_path / "full.tif", FINE_BOUNDS, size=128, values=values
        )
        assert mask_footprint(str(path)) is None

    def test_all_nodata_is_an_error(self, tmp_path):
        """Rather than an empty footprint, which would silently hide the raster."""
        values = numpy.zeros((64, 64), dtype="uint8")
        path = create_test_raster(
            tmp_path / "empty.tif", FINE_BOUNDS, size=64, values=values
        )
        with raises(ValueError, match="no valid data"):
            mask_footprint(str(path))


class TestCorrectness:
    def test_footprint_covers_the_data(self, swath):
        """A footprint smaller than its data punches holes in the mosaic.

        `simplify` cuts corners *inward*, so this is the property the outward
        buffer in `_generalize` exists to restore — and the one that would fail
        silently, as missing tiles along a diagonal edge.
        """
        from rasterio import features
        from rasterio import open as rio_open
        from shapely.geometry import shape
        from shapely.ops import unary_union

        result = mask_footprint(str(swath))
        footprint = shape(result.geometry)

        with rio_open(str(swath)) as src:
            mask = src.dataset_mask()
            transform = src.transform
        valid = mask > 0
        traced = unary_union(
            [
                shape(geom)
                for geom, value in features.shapes(
                    valid.astype("uint8"), mask=valid, transform=transform
                )
                if value
            ]
        )
        # The fixtures are EPSG:4326, so traced coordinates are already lon/lat.
        assert footprint.covers(traced)

    def test_disjoint_data_becomes_multipart(self, tmp_path):
        """Two strips shouldn't be bridged into one blob covering the gap."""
        values = numpy.zeros((256, 256), dtype="uint8")
        values[:, :48] = 1
        values[:, 208:] = 3
        path = create_test_raster(
            tmp_path / "strips.tif", FINE_BOUNDS, size=256, values=values
        )
        result = mask_footprint(str(path))
        assert result.parts == 2
        assert result.geometry["type"] == "MultiPolygon"

    def test_large_raster_is_read_decimated(self, tmp_path):
        """Affordability: the mask read is bounded regardless of raster size."""
        path = create_test_raster(
            tmp_path / "big.tif",
            FINE_BOUNDS,
            size=2048,
            values=diagonal_swath(2048, 150),
        )
        result = mask_footprint(str(path), max_size=256)
        assert max(result.decimated_shape) == 256

    def test_footprint_lands_in_the_right_place(self, swath):
        """A decimated read needs a rescaled transform, or every vertex is off."""
        from shapely.geometry import box, shape

        result = mask_footprint(str(swath))
        assert shape(result.geometry).within(box(*FINE_BOUNDS).buffer(1e-9))

    def test_footprint_stays_inside_the_raster(self, swath):
        """The outward safety buffer must not claim ground outside the file."""
        from shapely.geometry import box, shape

        result = mask_footprint(str(swath))
        assert shape(result.geometry).within(box(*FINE_BOUNDS))


@fixture
def scratch_layer(raster_index):
    """A layer that exists only for one test.

    `raster_index` is session-scoped and shared, and other tests assert on the
    exact set of layers in it — so anything registered here has to be removed
    again.
    """
    slug = "mask-footprint-scratch"
    raster_index.register_layer(slug)
    try:
        yield slug
    finally:
        raster_index.remove_layer(slug, cascade=True)


class TestIndexIntegration:
    def test_refined_footprint_changes_asset_selection(
        self, raster_index, scratch_layer, tmp_path
    ):
        """The payoff: a tile off the data no longer selects the raster.

        A bbox footprint answers "yes" for the whole box; the traced one answers
        "no" for the corners, which is what stops the mosaic opening a file over
        the network to read nothing.
        """
        from macrostrat.raster_index.mask_footprint import mask_footprint

        path = create_test_raster(
            tmp_path / "corner-swath.tif",
            FINE_BOUNDS,
            size=512,
            values=diagonal_swath(512, 20),
        )
        raster_index.add_raster(path, layer=scratch_layer, slug="swath")

        west, south, east, north = FINE_BOUNDS
        # The band runs NW->SE in this raster (array row 0 is north), so the
        # south-west corner is the one it never reaches.
        corner = (west, south, west + (east - west) / 10, south + (north - south) / 10)

        before = raster_index.assets_for_bbox(*corner, [scratch_layer])
        assert len(before) == 1, "the bounding box selects it"

        result = mask_footprint(str(path))
        raster_index.update_footprint(str(path), result.geometry)

        after = raster_index.assets_for_bbox(*corner, [scratch_layer])
        assert after == [], "the traced footprint does not"

        # Still selected where the data actually is.
        middle = (west, south, east, north)
        assert len(raster_index.assets_for_bbox(*middle, [scratch_layer])) == 1

    def test_add_raster_can_trace_on_registration(
        self, raster_index, scratch_layer, tmp_path
    ):
        path = create_test_raster(
            tmp_path / "on-add.tif",
            FINE_BOUNDS,
            size=256,
            values=diagonal_swath(256, 20),
        )
        raster_index.add_raster(
            path, layer=scratch_layer, slug="on-add", mask_footprint=True
        )
        footprint = raster_index.rasters(scratch_layer)[0]["footprint"]
        # A traced footprint has more than the five corners of a box.
        assert footprint.count("[") > 6


class TestRasterFilter:
    """`rasters=` narrows a layer to specific slugs."""

    def test_selection_can_be_narrowed(self, populated_index):
        both = populated_index.assets_for_bbox(-105.0, 40.0, -104.9, 40.1, ["minerals"])
        assert len(both) == 2

        one = populated_index.assets_for_bbox(
            -105.0, 40.0, -104.9, 40.1, ["minerals"], rasters=["fine"]
        )
        assert [a.slug for a in one] == ["fine"]

    def test_unknown_slug_selects_nothing(self, populated_index):
        assets = populated_index.assets_for_bbox(
            -105.0, 40.0, -104.9, 40.1, ["minerals"], rasters=["nope"]
        )
        assert assets == []

    def test_footprints_respect_the_filter(self, populated_index):
        data = populated_index.footprints(["minerals"], rasters=["coarse"])
        slugs = [f["properties"]["slug"] for f in data["features"]]
        assert slugs == ["coarse"]

    def test_bounds_respect_the_filter(self, populated_index):
        whole = populated_index.layer_bounds(["minerals"])
        narrowed = populated_index.layer_bounds(["minerals"], rasters=["fine"])
        assert narrowed is not None
        # `fine` is the smaller of the two overlapping test rasters.
        assert narrowed[0] > whole[0] or narrowed[2] < whole[2]
