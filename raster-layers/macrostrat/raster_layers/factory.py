"""FastAPI routes for serving indexed raster layers.

`RasterMosaicFactory` is titiler's `MosaicTilerFactory` with the index bound in
as its backend. Since rio-tiler 8 moved the mosaic backend contract into
rio-tiler itself, every route titiler registers — tiles, tilejson, info, point,
assets — works against an index-backed mosaic unchanged. What's added here is
the layer's own default colormap and a footprints layer.
"""

from collections.abc import Callable
from functools import partial
from typing import Any, Optional

from attrs import define, field
from fastapi import Depends, Path, Query
from starlette.responses import JSONResponse, Response
from titiler.core.dependencies import ColorMapParams
from titiler.mosaic.factory import MosaicTilerFactory
from typing_extensions import Annotated

from macrostrat.raster_index import RasterIndex
from macrostrat.utils import get_logger

from .backend import PGRasterMosaic

log = get_logger(__name__)

__all__ = ["RasterMosaicFactory", "fixed_layers", "LayerListParams", "MVT_MEDIA_TYPE"]

MVT_MEDIA_TYPE = "application/vnd.mapbox-vector-tile"


def fixed_layers(*slugs: str) -> Callable[[], list[str]]:
    """A path dependency serving a fixed set of layers.

    The usual case: a route that *is* a layer, with the layer list baked in
    rather than accepted from the client.
    """

    def dependency() -> list[str]:
        return list(slugs)

    return dependency


def LayerListParams(
    layers: Annotated[
        str, Query(description="Comma-delimited raster layers to composite")
    ] = "",
) -> list[str]:
    """A path dependency letting the client choose and order layers."""
    return [slug for slug in layers.split(",") if slug]


@define(kw_only=True)
class RasterMosaicFactory(MosaicTilerFactory):
    """Tile routes for one or more layers in a raster index."""

    index: Optional[RasterIndex] = field(default=None)

    # Required by the parent, but we always supply it ourselves in
    # `__attrs_post_init__` — the backend has to carry the index.
    backend: Any = field(default=None)

    # Colormap used when the request doesn't specify one. Falls back to the
    # layer's own colormap in the index (categorical rasters are unreadable
    # without it, and clients shouldn't have to know a 40-entry palette).
    default_colormap: Optional[dict] = field(default=None)
    use_index_colormap: bool = field(default=True)

    # Forwarded to `PGRasterMosaic` (e.g. `allow_overscaled`, `zoom_tolerance`).
    backend_options: dict = field(factory=dict)

    # The viewer template pulls in remote assets; not useful for these layers.
    add_viewer: bool = field(default=False)

    _colormap_cache: dict = field(factory=dict, init=False)

    def __attrs_post_init__(self):
        if self.index is None:
            raise ValueError("RasterMosaicFactory requires a RasterIndex")
        # titiler constructs the backend itself, and `backend_dependency` can't
        # carry a non-request-scoped object, so bind the index with a partial.
        self.backend = partial(PGRasterMosaic, index=self.index, **self.backend_options)
        self.colormap_dependency = self._colormap_dependency()
        super().__attrs_post_init__()

    def register_routes(self):
        super().register_routes()
        self.footprints()

    def footprints(self):
        """Coverage of these layers, without reading any pixels."""

        @self.router.get(
            "/footprints/{z}/{x}/{y}",
            responses={
                200: {
                    "content": {MVT_MEDIA_TYPE: {}},
                    "description": "Raster footprints as a vector tile",
                }
            },
            response_class=Response,
        )
        def footprint_tile(
            z: Annotated[int, Path(description="Tile zoom level")],
            x: Annotated[int, Path(description="Tile column")],
            y: Annotated[int, Path(description="Tile row")],
            src_path=Depends(self.path_dependency),
        ):
            """Raster footprints as a vector tile (layer `raster_footprints`)."""
            data = self.index.footprint_tile(x, y, z, src_path)
            return Response(data, media_type=MVT_MEDIA_TYPE)

        @self.router.get(
            "/footprints",
            responses={200: {"description": "Raster footprints as GeoJSON"}},
        )
        def footprints(src_path=Depends(self.path_dependency)):
            """Every footprint in these layers, as a GeoJSON FeatureCollection."""
            return JSONResponse(self.index.footprints(src_path))

    # -- Colormap resolution -----------------------------------------------

    def _colormap_dependency(self) -> Callable[..., Optional[Any]]:
        """Wrap titiler's colormap params with a per-layer default."""

        def dependency(
            colormap=Depends(ColorMapParams),
            src_path=Depends(self.path_dependency),
        ) -> Optional[Any]:
            if colormap:
                return colormap
            return self._layer_colormap(tuple(src_path))

        return dependency

    def _layer_colormap(self, layers: tuple[str, ...]) -> Optional[dict]:
        if self.default_colormap is not None:
            return self.default_colormap
        if not self.use_index_colormap:
            return None
        if layers in self._colormap_cache:
            return self._colormap_cache[layers]

        colormap = None
        try:
            # First layer that defines one wins, matching compositing priority.
            by_slug = {l.slug: l.colormap for l in self.index.layers()}
            for slug in layers:
                if by_slug.get(slug):
                    colormap = normalize_colormap(by_slug[slug])
                    break
        except Exception as err:  # pragma: no cover - degraded, not fatal
            log.warning("Could not resolve colormap for %s: %s", layers, err)
            return None

        self._colormap_cache[layers] = colormap
        return colormap


def normalize_colormap(colormap: dict) -> dict:
    """Coerce a stored colormap into rio-tiler's `{int: (r, g, b, a)}` form.

    JSON object keys are strings, and colormaps captured from a GDAL color table
    may be RGB triples rather than RGBA.
    """
    result = {}
    for key, value in colormap.items():
        color = list(value)
        if len(color) == 3:
            color.append(255)
        result[int(key)] = tuple(color[:4])
    return result
