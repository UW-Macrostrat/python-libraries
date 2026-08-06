"""A mosaic reader whose assets come from the raster index.

`cogeo_mosaic.backends.BaseBackend` is deliberately *not* the base class: it
requires a `MosaicJSON` document, and the whole point of the index is that the
document is a live spatial query instead of a file. What this class does keep is
the shape titiler's `MosaicTilerFactory` expects — constructed as
`reader(input, tms=…, reader=…, reader_options=…)` and read via
`.tile(x, y, z, …)` — so the factory is reusable without reimplementing tiling.
"""

from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple, Type

import attr
from cogeo_mosaic.errors import NoAssetFoundError
from morecantile import TileMatrixSet
from rasterio.crs import CRS
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.errors import PointOutsideBounds
from rio_tiler.io import BaseReader, Reader
from rio_tiler.models import ImageData
from rio_tiler.mosaic import mosaic_reader
from rio_tiler.tasks import multi_values

from macrostrat.raster_index import RasterAsset, RasterIndex
from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["PGRasterMosaic", "NoAssetFoundError", "OverscaledAssetsError"]

# `NoAssetFoundError` is cogeo-mosaic's, re-exported: apps that already install
# titiler's `MOSAIC_STATUS_CODES` then get 404s for empty tiles without any extra
# exception handlers.

# The zoom at which a point query is resolved to a tile for asset lookup. Deep
# enough that a point in a small raster doesn't pull in every raster nearby.
POINT_LOOKUP_ZOOM = 14


class OverscaledAssetsError(NoAssetFoundError):
    """Assets exist, but the request is zoomed in past what any of them resolve.

    Distinguished from "nothing here" because the two want different responses:
    an empty region is a 404 forever, while an overscaled one becomes valid again
    when the client zooms out.
    """


@attr.s
class PGRasterMosaic(BaseReader):
    """Read a tile by compositing every indexed raster that covers it.

    `input` is the list of layer slugs to composite, in priority order — the
    mosaic's identity is the layer list, not a path.
    """

    input: List[str] = attr.ib()
    index: Optional[RasterIndex] = attr.ib(default=None)

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    minzoom: int = attr.ib(default=None)
    maxzoom: int = attr.ib(default=None)

    reader: Type[BaseReader] = attr.ib(default=Reader)
    reader_options: Dict = attr.ib(factory=dict)

    # Forwarded to `raster_layers.get_rasters`: how far below a raster's own
    # minzoom it may still be read.
    zoom_tolerance: int = attr.ib(default=3)
    # When true, serve a magnified tile rather than raising OverscaledAssetsError.
    allow_overscaled: bool = attr.ib(default=False)

    bounds: Tuple[float, float, float, float] = attr.ib(
        init=False, default=(-180, -90, 180, 90)
    )
    crs: CRS = attr.ib(init=False, default=WGS84_CRS)
    geographic_crs: CRS = attr.ib(init=False, default=WGS84_CRS)

    def __attrs_post_init__(self):
        if self.index is None:
            raise ValueError("PGRasterMosaic requires a RasterIndex")
        if isinstance(self.input, str):
            self.input = [self.input]

        bounds = self.index.layer_bounds(self.input)
        if bounds is not None:
            self.bounds = bounds

        # Without a mosaic document, the grid is the only authority on zoom range.
        if self.minzoom is None:
            self.minzoom = self.tms.minzoom
        if self.maxzoom is None:
            self.maxzoom = self.tms.maxzoom

    @property
    def mosaic_def(self):
        """Just enough of a `MosaicJSON` for titiler's TileJSON route.

        A shim, not a mosaic: it exists so the `/tilejson.json` endpoint can be
        inherited rather than reimplemented.
        """
        west, south, east, north = self.bounds
        center = ((west + east) / 2, (south + north) / 2, self.minzoom)
        return SimpleNamespace(
            bounds=self.bounds,
            center=center,
            minzoom=self.minzoom,
            maxzoom=self.maxzoom,
        )

    # -- Asset lookup ------------------------------------------------------

    def get_assets(self, x: int, y: int, z: int) -> List[RasterAsset]:
        return self.index.assets_for_tile(
            x, y, z, self.input, zoom_tolerance=self.zoom_tolerance
        )

    def assets_for_tile(self, x: int, y: int, z: int, **kwargs) -> List[RasterAsset]:
        assets = self.get_assets(x, y, z)
        if not assets:
            return assets
        if not self.allow_overscaled and all(a.overscaled for a in assets):
            raise OverscaledAssetsError(
                f"All assets for tile {z}/{x}/{y} are overscaled"
            )
        return assets

    def assets_for_point(
        self, lng: float, lat: float, coord_crs: CRS = WGS84_CRS, **kwargs
    ) -> List[RasterAsset]:
        if coord_crs != WGS84_CRS:
            raise NotImplementedError(
                "Point lookups against the raster index are WGS84-only"
            )
        tile = self.tms.tile(lng, lat, POINT_LOOKUP_ZOOM)
        return self.get_assets(tile.x, tile.y, tile.z)

    # -- Reading -----------------------------------------------------------

    def _open(self, href: str):
        return self.reader(href, tms=self.tms, **self.reader_options)

    def tile(
        self,
        tile_x: int,
        tile_y: int,
        tile_z: int,
        reverse: bool = False,
        assets: Optional[List[RasterAsset]] = None,
        **kwargs: Any,
    ) -> Tuple[ImageData, List[str]]:
        """Composite a tile from every covering raster.

        Assets are handed to `mosaic_reader` as hrefs rather than `RasterAsset`s:
        they come back out as the returned asset list, which callers (including
        titiler's `X-Assets` header) expect to be strings.
        """
        if assets is None:
            assets = self.assets_for_tile(tile_x, tile_y, tile_z)
        if not assets:
            raise NoAssetFoundError(
                f"No assets found for tile {tile_z}/{tile_x}/{tile_y}"
            )

        if reverse:
            assets = list(reversed(assets))

        def _reader(href: str, x: int, y: int, z: int, **kw) -> ImageData:
            with self._open(href) as src:
                return src.tile(x, y, z, **kw)

        hrefs = [a.href for a in assets]
        return mosaic_reader(hrefs, _reader, tile_x, tile_y, tile_z, **kwargs)

    def point(
        self,
        lon: float,
        lat: float,
        coord_crs: CRS = WGS84_CRS,
        reverse: bool = False,
        **kwargs: Any,
    ) -> List:
        """Read the value at a point from every covering raster."""
        assets = self.assets_for_point(lon, lat, coord_crs=coord_crs)
        if not assets:
            raise NoAssetFoundError(f"No assets found for point ({lon}, {lat})")

        if reverse:
            assets = list(reversed(assets))

        def _reader(href: str, lon: float, lat: float, **kw):
            with self._open(href) as src:
                return src.point(lon, lat, **kw)

        kwargs.setdefault("allowed_exceptions", (PointOutsideBounds,))
        hrefs = [a.href for a in assets]
        return list(multi_values(hrefs, _reader, lon, lat, **kwargs).items())

    # -- Unsupported -------------------------------------------------------
    #
    # These are whole-mosaic operations. They would each mean reading every
    # raster in a layer, which is not something a tile server should offer;
    # `macrostrat.raster_index` is where layer-wide metadata belongs.

    def info(self):
        raise NotImplementedError("Use the raster index for layer metadata")

    def statistics(self):
        raise NotImplementedError

    def preview(self):
        raise NotImplementedError

    def part(self):
        raise NotImplementedError

    def feature(self):
        raise NotImplementedError
