"""End-to-end tile serving from an index-backed mosaic."""

from io import BytesIO

from morecantile import tms

web_mercator = tms.get("WebMercatorQuad")

# Inside both test rasters.
OVERLAP = (-104.95, 40.05)
# Well outside every test raster.
EMPTY = (0.0, 0.0)


def tile_path(lon: float, lat: float, zoom: int, suffix: str = ".png") -> str:
    tile = web_mercator.tile(lon, lat, zoom)
    return f"/rasters/minerals/tiles/{tile.z}/{tile.x}/{tile.y}{suffix}"


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
        """204, titiler's convention for a tile with no assets behind it."""
        response = client.get(tile_path(*EMPTY, 12))
        assert response.status_code == 204

    def test_overscaled_tile_is_empty(self, client):
        """Zoomed past the data's resolution is 'nothing to serve', not a blur."""
        response = client.get(tile_path(*OVERLAP, 22))
        assert response.status_code == 204

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
        response = client.get("/rasters/minerals/tilejson.json")
        assert response.status_code == 200
        data = response.json()
        assert data["minzoom"] == 0
        assert "{z}" in data["tiles"][0]
        # Bounds come from the indexed footprints, not the whole world.
        assert data["bounds"][0] > -110

    def test_bounds(self, client):
        response = client.get("/rasters/minerals/bounds")
        assert response.status_code == 200
        west, south, east, north = response.json()["bounds"]
        assert -106 < west < -104
        assert 39 < south < 41

    def test_assets_for_tile(self, client):
        tile = web_mercator.tile(*OVERLAP, 12)
        response = client.get(f"/rasters/minerals/{tile.z}/{tile.x}/{tile.y}/assets")
        assert response.status_code == 200
        data = response.json()
        assert data["should_generate"] is True
        assert [a["slug"] for a in data["assets"]] == ["fine", "coarse"]

    def test_footprints(self, client):
        response = client.get("/rasters/minerals/assets")
        assert response.status_code == 200
        data = response.json()
        assert data["type"] == "FeatureCollection"
        assert len(data["features"]) == 2

    def test_point_query(self, client):
        lon, lat = OVERLAP
        response = client.get(f"/rasters/minerals/point/{lon},{lat}")
        assert response.status_code == 200
        values = response.json()["values"]
        assert len(values) == 2
