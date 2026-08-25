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

    def test_tile_outside_coverage_is_a_transparent_image(self, client):
        """A drawable empty tile, not a 500 and not a 204.

        Panning past the edge of coverage is normal operation, so it must never
        surface as a server error — the whole point of installing the mosaic
        exception handlers with the routes. It also can't be a bodyless 204:
        mapbox-gl treats that as a successful response and fails to decode the
        empty body (see `_empty_tile`).
        """
        from PIL import Image

        response = client.get(tile_path(*EMPTY, 12))
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"
        image = Image.open(BytesIO(response.content)).convert("RGBA")
        assert {c for _, c in image.getcolors(maxcolors=1 << 16)} == {(0, 0, 0, 0)}

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


def test_empty_tile_matches_the_requested_size(client):
    """`@2x` doubles the grid's 256, the same as a tile with data."""
    from PIL import Image

    response = client.get(tile_path(*EMPTY, 12, "@2x.png"))
    assert response.status_code == 200
    assert Image.open(BytesIO(response.content)).size == (512, 512)


def test_empty_tile_in_an_opaque_format_stays_a_204(client):
    """JPEG has no alpha channel, so there is no empty tile to draw."""
    response = client.get(tile_path(*EMPTY, 12, ".jpg"))
    assert response.status_code == 204
    assert response.content == b""


def test_empty_point_query_has_no_body(client):
    """A 204 carrying a body is malformed and breaks caching proxies.

    Point queries keep the 204 — the status is only a problem for clients that
    expect an image back.
    """
    response = client.get(f"/rasters/minerals/point/{EMPTY[0]},{EMPTY[1]}")
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


class TestLayerMetadata:
    """The vocabulary a client needs before it can filter by class name."""

    def test_layer_route_reports_categories(self, client):
        response = client.get("/rasters/minerals/layer")
        assert response.status_code == 200
        data = response.json()
        assert data["slug"] == "minerals"
        labels = [c["label"] for c in data["categories"]]
        assert labels == ["Kaolinite", "Alunite", "Chlorite"]

    def test_categories_carry_palette_colors(self, client):
        """Names and colors in one request, so a legend needs no raster."""
        response = client.get("/rasters/minerals/layer")
        by_label = {c["label"]: c["color"] for c in response.json()["categories"]}
        assert by_label["Alunite"] == list(CATEGORICAL_COLORMAP[2])

    def test_layer_route_reports_colormap(self, client):
        response = client.get("/rasters/minerals/layer")
        colormap = response.json()["colormap"]
        assert colormap["2"] == list(CATEGORICAL_COLORMAP[2])

    def test_class_filter_is_advertised(self, client):
        """`classes` shows up as an algorithm on the tile route."""
        response = client.get("/openapi.json")
        params = response.json()["paths"][
            "/rasters/minerals/tiles/{tileMatrixSetId}/{z}/{x}/{y}"
        ]["get"]["parameters"]
        algorithm = next(p for p in params if p["name"] == "algorithm")
        assert "classes" in str(algorithm["schema"])


class TestClassFiltering:
    """Isolating classes of a categorical mosaic by name.

    Post-merge masking, so compositing is untouched and excluded classes render
    transparent while survivors keep the layer's palette.
    """

    def colors_of(self, response):
        from PIL import Image

        image = Image.open(BytesIO(response.content)).convert("RGBA")
        return {c for _, c in image.getcolors(maxcolors=1 << 16)}

    # Only two of the three classes fall in a zoom-12 tile here; zoom 10 sees
    # all of them, which is what a multi-class assertion needs.
    ALL_CLASSES_ZOOM = 10

    def filtered(self, client, classes, zoom=12):
        import json

        return client.get(
            tile_path(*OVERLAP, zoom),
            params={
                "algorithm": "classes",
                "algorithm_params": json.dumps({"classes": classes}),
            },
        )

    def test_unfiltered_tile_draws_every_class(self, client):
        colors = self.colors_of(client.get(tile_path(*OVERLAP, self.ALL_CLASSES_ZOOM)))
        for value in (1, 2, 3):
            assert CATEGORICAL_COLORMAP[value] in colors

    def test_named_class_survives_and_others_do_not(self, client):
        response = self.filtered(client, ["Alunite"])
        assert response.status_code == 200
        colors = self.colors_of(response)
        assert CATEGORICAL_COLORMAP[2] in colors, "the requested class keeps its color"
        assert CATEGORICAL_COLORMAP[1] not in colors
        assert CATEGORICAL_COLORMAP[3] not in colors

    def test_excluded_pixels_are_transparent(self, client):
        """Masked, not recolored: nothing but the selection is opaque.

        Excluded pixels keep the palette color their value maps to and lose only
        their alpha, since the mask and the colormap are combined at render time.
        """
        colors = self.colors_of(self.filtered(client, ["Alunite"]))
        assert any(color[3] == 0 for color in colors)
        opaque = {c for c in colors if c[3] == 255}
        assert opaque == {CATEGORICAL_COLORMAP[2]}

    def test_several_classes_keep_distinct_colors(self, client):
        """A multi-class selection reads as a legend, not one highlight color."""
        colors = self.colors_of(
            self.filtered(client, ["Alunite", "Chlorite"], self.ALL_CLASSES_ZOOM)
        )
        assert {c for c in colors if c[3] == 255} == {
            CATEGORICAL_COLORMAP[2],
            CATEGORICAL_COLORMAP[3],
        }

    def test_labels_are_case_insensitive(self, client):
        colors = self.colors_of(self.filtered(client, ["alunite"]))
        assert CATEGORICAL_COLORMAP[2] in colors

    def test_integer_classes_also_work(self, client):
        """A layer with no vocabulary is still filterable by raw class value."""
        colors = self.colors_of(self.filtered(client, [2]))
        assert CATEGORICAL_COLORMAP[2] in colors
        assert CATEGORICAL_COLORMAP[1] not in colors

    def test_unknown_class_is_a_400(self, client):
        """Not an empty tile: a typo and an absent mineral must look different."""
        response = self.filtered(client, ["Unobtainium"])
        assert response.status_code == 400
        assert "Kaolinite" in response.text

    def test_empty_selection_is_a_passthrough(self, client):
        colors = self.colors_of(self.filtered(client, []))
        assert CATEGORICAL_COLORMAP[1] in colors

    def test_filtering_still_composites_both_rasters(self, client):
        """Filtering happens after the merge, so asset selection is unchanged."""
        response = self.filtered(client, ["Alunite"])
        assert len(response.headers["X-Assets"].split(",")) == 2

    def test_filtered_tile_costs_one_query(self, index):
        """The vocabulary rides along with the assets, like the colormap."""
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
            assert backend.categories is not None
            assert backend.colormap is not None
            assert len(queries) == 1, "one lookup, not one per concern"
        finally:
            index.assets_for_tile = original


class TestDatasetFilter:
    """`?datasets=` views one raster *through* the mosaic.

    The point is that a focused dataset is not a special case: it keeps the
    layer's palette, its class vocabulary, the transparent empty tile and the
    per-asset point query, because it is the same route with a narrower asset
    selection.
    """

    def test_narrows_the_assets_read(self, client):
        response = client.get(tile_path(*OVERLAP, 12), params={"datasets": "fine"})
        assert response.status_code == 200
        assert response.headers["X-Assets"].split(",") == [
            a for a in response.headers["X-Assets"].split(",") if a.endswith("fine.tif")
        ]

    def test_unfiltered_still_composites_both(self, client):
        response = client.get(tile_path(*OVERLAP, 12))
        assert len(response.headers["X-Assets"].split(",")) == 2

    def test_the_layer_palette_still_applies(self, client):
        """The whole reason for routing this through the mosaic."""
        from PIL import Image

        response = client.get(tile_path(*OVERLAP, 12), params={"datasets": "coarse"})
        image = Image.open(BytesIO(response.content)).convert("RGBA")
        colors = {c for _, c in image.getcolors(maxcolors=1 << 16)}
        assert (218, 50, 132, 255) in colors

    def test_class_filtering_composes_with_it(self, client):
        """A focused dataset can still be filtered to named classes."""
        import json

        from PIL import Image

        response = client.get(
            tile_path(*OVERLAP, 12),
            params={
                "datasets": "coarse",
                "algorithm": "classes",
                "algorithm_params": json.dumps({"classes": ["Alunite"]}),
            },
        )
        assert response.status_code == 200
        image = Image.open(BytesIO(response.content)).convert("RGBA")
        colors = {c for _, c in image.getcolors(maxcolors=1 << 16)}
        assert {c for c in colors if c[3] == 255} == {CATEGORICAL_COLORMAP[2]}

    def test_comma_separated_slugs(self, client):
        response = client.get(
            tile_path(*OVERLAP, 12), params={"datasets": "fine,coarse"}
        )
        assert len(response.headers["X-Assets"].split(",")) == 2

    def test_whitespace_is_tolerated(self, client):
        response = client.get(
            tile_path(*OVERLAP, 12), params={"datasets": " fine , coarse "}
        )
        assert response.status_code == 200
        assert len(response.headers["X-Assets"].split(",")) == 2

    def test_a_dataset_with_no_coverage_here_is_an_empty_tile(self, client):
        """Same transparent PNG as running off the edge of the whole layer."""
        response = client.get(tile_path(*OVERLAP, 12), params={"datasets": "elsewhere"})
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_point_query_narrows_too(self, client):
        lon, lat = OVERLAP
        response = client.get(
            f"/rasters/minerals/point/{lon},{lat}", params={"datasets": "fine"}
        )
        assert response.status_code == 200
        assert len(response.json()["assets"]) == 1

    def test_point_query_unfiltered_reports_every_raster(self, client):
        """What the client's dataset picker is built from: the overlap itself."""
        lon, lat = OVERLAP
        response = client.get(f"/rasters/minerals/point/{lon},{lat}")
        assert len(response.json()["assets"]) == 2

    def test_footprints_narrow_too(self, client):
        response = client.get(
            "/rasters/minerals/footprints", params={"datasets": "coarse"}
        )
        slugs = [f["properties"]["slug"] for f in response.json()["features"]]
        assert slugs == ["coarse"]

    def test_datasets_is_advertised(self, client):
        response = client.get("/openapi.json")
        params = response.json()["paths"][
            "/rasters/minerals/tiles/{tileMatrixSetId}/{z}/{x}/{y}"
        ]["get"]["parameters"]
        assert "datasets" in [p["name"] for p in params]


class TestAdvertisedZoomRange:
    """What the mosaic says about itself.

    Reporting the tile grid's 0-24 instead of the layer's real range misleads
    every client that reads `/info` or `tilejson.json`, and inflates the WMTS
    capabilities document by a `TileMatrixLimits` block per claimed zoom per
    advertised layer.
    """

    def test_backend_reports_the_layers_range(self, index):
        from macrostrat.raster_layers.backend import PGRasterMosaic

        backend = PGRasterMosaic(input=["minerals"], index=index)
        rasters = index.rasters("minerals")
        assert backend.minzoom == min(r["minzoom"] for r in rasters)
        assert backend.maxzoom == max(r["maxzoom"] for r in rasters)
        assert backend.maxzoom < web_mercator.maxzoom

    def test_tilejson_advertises_it(self, client):
        response = client.get(f"/rasters/minerals/{TMS}/tilejson.json")
        data = response.json()
        assert data["maxzoom"] < 24
        assert data["minzoom"] >= 0

    def test_overscaled_tiles_still_render(self, client):
        """The advertised range is metadata, not a gate.

        Serving past native resolution is deliberate (`allow_overscaled`), and
        rio-tiler does no zoom validation in `tile()` — so narrowing what we
        advertise must not narrow what we serve.
        """
        response = client.get(tile_path(*OVERLAP, 22))
        assert response.status_code == 200

    def test_one_query_for_bounds_and_zooms(self, index):
        """Backend construction happens per request, on every route."""
        queries = []
        original = index.layer_extent
        index.layer_extent = lambda *a, **k: (queries.append(1), original(*a, **k))[1]
        try:
            from macrostrat.raster_layers.backend import PGRasterMosaic

            PGRasterMosaic(input=["minerals"], index=index)
            assert len(queries) == 1
        finally:
            index.layer_extent = original


class TestAdvertisedURLsResolve:
    """A layer must not advertise URLs that 404.

    Including a router does not tell it where it is mounted, and titiler builds
    absolute URLs from `router_prefix`. Left empty, TileJSON advertised
    `/tiles/...` instead of `/rasters/minerals/tiles/...` — broken for every
    consumer that follows the document, and invisible to any client that builds
    tile URLs itself.
    """

    def test_tilejson_tiles_are_fetchable(self, client):
        tilejson = client.get(f"/rasters/minerals/{TMS}/tilejson.json").json()
        url = tilejson["tiles"][0]
        assert "/rasters/minerals/" in url

        path = url.split("testserver", 1)[-1]
        for placeholder, value in (("{z}", "12"), ("{x}", "873"), ("{y}", "1587")):
            path = path.replace(placeholder, value)
        assert client.get(path).status_code in (200, 204)
