"""A mosaic reader whose assets come from the raster index.

rio-tiler 8 owns the mosaic backend contract (`rio_tiler.mosaic.backend`), so all
this class supplies is the three asset-lookup methods — "which rasters cover
this tile / point / bbox?" — and the answer is a spatial query rather than a
MosaicJSON document. Reading, compositing and pixel selection are inherited.

Assets are hrefs (plain strings), which is what the base class expects: it
passes them straight to the reader and reports them back as the asset list.
"""

from typing import Any, Optional

import attr
from morecantile import TileMatrixSet
from rasterio.crs import CRS
from rasterio.warp import transform_bounds
from rio_tiler.constants import WEB_MERCATOR_TMS, WGS84_CRS
from rio_tiler.io import BaseReader, Reader
from rio_tiler.mosaic.backend import BaseBackend
from rio_tiler.types import BBox

from macrostrat.raster_index import RasterIndex
from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["PGRasterMosaic"]

# The zoom at which a point query is resolved to a tile for asset lookup. Deep
# enough that a point in a small raster doesn't pull in every raster nearby.
POINT_LOOKUP_ZOOM = 14


@attr.s
class PGRasterMosaic(BaseBackend):
    """Composite every indexed raster covering the requested area.

    `input` is the list of layer slugs to composite, in priority order — the
    mosaic's identity is the layer list, not a path.
    """

    input: list[str] = attr.ib()
    index: Optional[RasterIndex] = attr.ib(default=None)

    tms: TileMatrixSet = attr.ib(default=WEB_MERCATOR_TMS)
    reader: type[BaseReader] = attr.ib(default=Reader)
    reader_options: dict = attr.ib(factory=dict)

    # Forwarded to `raster_layers.get_rasters`: how far below a raster's own
    # minzoom it may still be read.
    zoom_tolerance: int = attr.ib(default=3)
    # Whether to keep serving data past its native resolution. On by default: a
    # layer that vanishes when you zoom in reads as a bug, and a magnified tile
    # is what every other raster service gives you. `should_generate_tile` in
    # the index is the right tool for deciding what to *cache*.
    allow_overscaled: bool = attr.ib(default=True)

    # Assets resolved for this request, kept so the colormap that came back with
    # them can reach rendering. The backend is constructed per request by the
    # route, so this is request-scoped state, not shared.
    resolved_assets: list = attr.ib(init=False, factory=list)

    bounds: BBox = attr.ib(init=False, default=(-180, -90, 180, 90))
    crs: CRS = attr.ib(init=False, default=WGS84_CRS)
    minzoom: int = attr.ib(init=False, default=None)
    maxzoom: int = attr.ib(init=False, default=None)

    def __attrs_post_init__(self):
        if self.index is None:
            raise ValueError("PGRasterMosaic requires a RasterIndex")
        if isinstance(self.input, str):
            self.input = [self.input]

        bounds = self.index.layer_bounds(self.input)
        if bounds is not None:
            self.bounds = bounds

        # Without a mosaic document, the grid is the only authority on zoom range.
        self.minzoom = self.tms.minzoom
        self.maxzoom = self.tms.maxzoom

    # -- Asset lookup ------------------------------------------------------
    #
    # Each returns hrefs, and an empty list where there's no coverage. The base
    # class turns "empty" into `NoAssetFoundError` *before* opening any reader,
    # so a miss costs one indexed query and nothing more.

    def assets_for_tile(self, x: int, y: int, z: int, **kwargs: Any) -> list[str]:
        assets = self.index.assets_for_tile(
            x, y, z, self.input, zoom_tolerance=self.zoom_tolerance
        )
        if not self.allow_overscaled:
            assets = [a for a in assets if not a.overscaled]
        self.resolved_assets = assets
        return [a.href for a in assets]

    @property
    def colormap(self) -> Optional[dict]:
        """The colormap for the assets resolved on this request, if any.

        First asset that declares one wins, which is the same precedence
        compositing uses.
        """
        for asset in self.resolved_assets:
            if asset.colormap:
                return asset.colormap
        return None

    @property
    def categories(self) -> Optional[list]:
        """The class vocabulary for the assets resolved on this request, if any.

        Same precedence as the colormap, and it came back on the same query.
        """
        for asset in self.resolved_assets:
            if asset.categories:
                return [c.model_dump(mode="json") for c in asset.categories]
        return None

    def tile(self, *args: Any, **kwargs: Any):
        """Read a tile, tagging it with what its assets said about themselves.

        Rendering (and any post-processing) happens back in the route, after the
        reader is closed, so the colormap and class vocabulary ride along on the
        image rather than being looked up again. This is what lets a request that
        filters by class name still cost one database query.
        """
        image, assets = super().tile(*args, **kwargs)
        metadata = {}
        colormap = self.colormap
        if colormap is not None:
            metadata["colormap"] = colormap
        categories = self.categories
        if categories is not None:
            metadata["categories"] = categories
        if metadata:
            image.metadata = {**(image.metadata or {}), **metadata}
        return image, assets

    def assets_for_point(
        self,
        lng: float,
        lat: float,
        coord_crs: Optional[CRS] = None,
        **kwargs: Any,
    ) -> list[str]:
        if coord_crs is not None and coord_crs != WGS84_CRS:
            lng, lat, _, _ = transform_bounds(coord_crs, WGS84_CRS, lng, lat, lng, lat)
        tile = self.tms.tile(lng, lat, POINT_LOOKUP_ZOOM)
        return self.assets_for_tile(tile.x, tile.y, tile.z)

    def assets_for_bbox(
        self,
        xmin: float,
        ymin: float,
        xmax: float,
        ymax: float,
        coord_crs: Optional[CRS] = None,
        **kwargs: Any,
    ) -> list[str]:
        if coord_crs is not None and coord_crs != WGS84_CRS:
            xmin, ymin, xmax, ymax = transform_bounds(
                coord_crs, WGS84_CRS, xmin, ymin, xmax, ymax
            )
        assets = self.index.assets_for_bbox(xmin, ymin, xmax, ymax, self.input)
        return [a.href for a in assets]
