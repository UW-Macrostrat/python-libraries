"""End-to-end tile serving from an index-backed mosaic."""

from io import BytesIO

from morecantile import tms

from macrostrat.raster_index.testing import CATEGORICAL_COLORMAP

web_mercator = tms.get("WebMercatorQuad")

# Inside both test rasters.
OVERLAP = (-104.95, 40.05)
# Well outside every test raster.
EMPTY = (0.0, 0.0)

# titiler 1.x makes the tile matrix set explicit in every tile path.
TMS = "WebMercatorQuad"

# PNG colour types, read straight from the IHDR chunk: a colormapped tile is
# rendered to RGBA, an un-colormapped single-band one to grey+alpha.
GRAY_ALPHA = 4
RGBA = 6


def _png_color_type(data: bytes) -> int:
    return data[data.index(b"IHDR") + 13]


def tile_path(lon: float, lat: float, zoom: int, suffix: str = ".png") -> str:
    tile = web_mercator.tile(lon, lat, zoom)
    return f"/rasters/minerals/tiles/{TMS}/{tile.z}/{tile.x}/{tile.y}{suffix}"


class TestTiles:
    def test_tile_over_data(self, client):
        response = client.get(tile_path(*OVERLAP, 12))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_tile_composites_both_rasters(self, client):
        response = client.get(tile_path(*OVERLAP, 12))
        assert response.status_code == 200
        assets = response.headers["X-Assets"].split(",")
        assert len(assets) == 2

    def test_tile_outside_coverage_is_empty(self, client):
        """204, not a 500.

        Panning past the edge of coverage is normal operation, so it must never
        surface as a server error — the whole point of installing the mosaic
        exception handlers with the routes.
        """
        response = client.get(tile_path(*EMPTY, 12))
        assert response.status_code == 204

    def test_overscaled_tile_still_renders(self, client):
        """Zooming past native resolution magnifies rather than disappearing."""
        response = client.get(tile_path(*OVERLAP, 22))
        assert response.status_code == 200

    def test_layer_colormap_is_applied(self, client):
        """A categorical layer renders in color without the client asking."""
        from PIL import Image

        response = client.get(tile_path(*OVERLAP, 12))
        image = Image.open(BytesIO(response.content))
        colors = {c for _, c in image.convert("RGBA").getcolors(maxcolors=1 << 16)}
        # The magenta class from CATEGORICAL_COLORMAP survives rendering.
        assert (218, 50, 132, 255) in colors


class TestMetadata:
    def test_tilejson(self, client):
        response = client.get(f"/rasters/minerals/{TMS}/tilejson.json")
        assert response.status_code == 200
        data = response.json()
        assert "{z}" in data["tiles"][0]
        # Bounds come from the indexed footprints, not the whole world.
        assert data["bounds"][0] > -110

    def test_info_reports_bounds(self, client):
        response = client.get("/rasters/minerals/info")
        assert response.status_code == 200
        west, south, east, north = response.json()["bounds"]
        assert -106 < west < -104
        assert 39 < south < 41

    def test_assets_for_tile(self, client):
        tile = web_mercator.tile(*OVERLAP, 12)
        response = client.get(
            f"/rasters/minerals/tiles/{TMS}/{tile.z}/{tile.x}/{tile.y}/assets"
        )
        assert response.status_code == 200
        assets = response.json()
        assert len(assets) == 2
        # Finest raster first, as the index ordered them.
        assert assets[0].endswith("fine.tif")

    def test_point_query(self, client):
        lon, lat = OVERLAP
        response = client.get(f"/rasters/minerals/point/{lon},{lat}")
        assert response.status_code == 200
        # 1.x reports one entry per contributing asset, each with its values.
        assert len(response.json()["assets"]) == 2

    def test_point_outside_coverage_is_empty(self, client):
        response = client.get(f"/rasters/minerals/point/{EMPTY[0]},{EMPTY[1]}")
        assert response.status_code == 204


class TestFootprints:
    """The raster-side counterpart to the map-footprints layer."""

    def test_geojson_footprints(self, client):
        response = client.get("/rasters/minerals/footprints")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2

    def test_footprint_vector_tile(self, client):
        tile = web_mercator.tile(*OVERLAP, 8)
        response = client.get(
            f"/rasters/minerals/footprints/{tile.z}/{tile.x}/{tile.y}"
        )
        assert response.status_code == 200
        assert response.headers["content-type"] == "application/vnd.mapbox-vector-tile"
        assert len(response.content) > 0

    def test_footprint_tile_layer_name(self, client):
        """`raster_footprints` is a cross-repo contract with client styles."""
        import mapbox_vector_tile

        tile = web_mercator.tile(*OVERLAP, 8)
        response = client.get(
            f"/rasters/minerals/footprints/{tile.z}/{tile.x}/{tile.y}"
        )
        decoded = mapbox_vector_tile.decode(response.content)
        assert "raster_footprints" in decoded
        features = decoded["raster_footprints"]["features"]
        assert {f["properties"]["slug"] for f in features} == {"fine", "coarse"}

    def test_empty_footprint_tile(self, client):
        tile = web_mercator.tile(*EMPTY, 8)
        response = client.get(
            f"/rasters/minerals/footprints/{tile.z}/{tile.x}/{tile.y}"
        )
        assert response.status_code == 200
        assert response.content == b""


def test_empty_tile_has_no_body(client):
    """A 204 carrying a body is malformed and breaks caching proxies."""
    response = client.get(tile_path(*EMPTY, 12))
    assert response.status_code == 204
    assert response.content == b""
    assert "content-type" not in response.headers
    # Explicit zero length: without it the response is close-delimited and
    # caching proxies fail the fetch.
    assert response.headers["content-length"] == "0"


class TestColormapVisibility:
    """A colormap set after serving begins must take effect without a restart.

    The normal workflow registers rasters, looks at them, and *then* runs
    `set-colormap`. Caching the "no colormap" answer made that palette invisible
    until the process was restarted, which is how this regressed in production.
    """

    def test_colormap_set_after_first_request_is_picked_up(self, index):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from macrostrat.raster_index.testing import CATEGORICAL_COLORMAP
        from macrostrat.raster_layers import RasterLayerConfig, register_raster_layers

        # A second layer over the same rasters, deliberately with no colormap.
        # `add_raster` is keyed on href, so this *moves* them out of `minerals`
        # rather than copying — they get put back at the end.
        originals = index.rasters("minerals")
        index.register_layer("uncolored")
        for raster in originals:
            index.add_raster(raster["href"], layer="uncolored", slug=raster["slug"])

        try:
            self._assert_colormap_appears(index)
        finally:
            for raster in originals:
                index.add_raster(raster["href"], layer="minerals", slug=raster["slug"])

    def _assert_colormap_appears(self, index):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        from macrostrat.raster_index.testing import CATEGORICAL_COLORMAP
        from macrostrat.raster_layers import RasterLayerConfig, register_raster_layers

        app = FastAPI()
        register_raster_layers(
            app, index, [RasterLayerConfig(slug="uncolored")], prefix="/rasters"
        )
        client = TestClient(app)
        path = tile_path(*OVERLAP, 12).replace("/minerals/", "/uncolored/")

        # Served before a colormap exists: single band, no palette.
        first = client.get(path)
        assert first.status_code == 200
        assert _png_color_type(first.content) == GRAY_ALPHA

        index.register_layer(
            "uncolored",
            colormap={str(k): list(v) for k, v in CATEGORICAL_COLORMAP.items()},
        )

        # Same long-lived app: the next tile must be colored.
        second = client.get(path)
        assert second.status_code == 200
        assert _png_color_type(second.content) == RGBA

    def test_edited_colormap_takes_effect_immediately(self, client, index):
        """No cache anywhere: an edited palette shows up on the next tile."""
        from macrostrat.raster_index.testing import CATEGORICAL_COLORMAP

        def colors_of(response):
            from PIL import Image

            image = Image.open(BytesIO(response.content)).convert("RGBA")
            return {c for _, c in image.getcolors(maxcolors=1 << 16)}

        assert (218, 50, 132, 255) in colors_of(client.get(tile_path(*OVERLAP, 12)))

        index.register_layer(
            "minerals",
            name="Test mineral maps",
            colormap={"2": [1, 2, 3, 255]},
        )
        try:
            after = colors_of(client.get(tile_path(*OVERLAP, 12)))
            assert (1, 2, 3, 255) in after
            assert (218, 50, 132, 255) not in after
        finally:
            index.register_layer(
                "minerals",
                name="Test mineral maps",
                colormap={str(k): list(v) for k, v in CATEGORICAL_COLORMAP.items()},
            )

    def test_one_query_serves_assets_and_colormap(self, index):
        """The colormap rides along with the assets rather than being re-queried."""
        from macrostrat.raster_layers.backend import PGRasterMosaic

        backend = PGRasterMosaic(input=["minerals"], index=index)
        queries = []
        original = index.assets_for_tile
        index.assets_for_tile = lambda *a, **k: (
            queries.append(1),
            original(*a, **k),
        )[1]
        try:
            tile = web_mercator.tile(*OVERLAP, 12)
            backend.assets_for_tile(tile.x, tile.y, tile.z)
            assert backend.colormap is not None, "colormap came back with the assets"
            assert len(queries) == 1, "one lookup, not one per concern"
        finally:
            index.assets_for_tile = original
