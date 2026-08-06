"""FastAPI routes for serving indexed raster layers.

`RasterMosaicFactory` is titiler's `MosaicTilerFactory` with the mosaic-document
routes removed and the index bound in. Everything about tiling, rendering,
rescaling and colormaps is inherited — the only thing this adds is where assets
come from and how a layer supplies its own default colormap.
"""

from dataclasses import dataclass, field
from functools import partial
from typing import Any, Callable, Optional

from fastapi import Depends, Path, Query
from starlette.responses import JSONResponse
from titiler.core.dependencies import ColorMapParams
from titiler.core.resources.enums import OptionalHeader
from titiler.mosaic.factory import MosaicTilerFactory
from typing_extensions import Annotated

from macrostrat.raster_index import RasterIndex
from macrostrat.utils import get_logger

from .backend import PGRasterMosaic

log = get_logger(__name__)

__all__ = ["RasterMosaicFactory", "fixed_layers", "LayerListParams"]


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


@dataclass
class RasterMosaicFactory(MosaicTilerFactory):
    """Tile routes for one or more layers in a raster index."""

    index: Optional[RasterIndex] = None

    # Colormap used when the request doesn't specify one. Falls back to the
    # layer's own colormap in the index (categorical rasters are unreadable
    # without it, and clients shouldn't have to know a 40-entry palette).
    default_colormap: Optional[dict] = None
    use_index_colormap: bool = True

    # Reader options forwarded to rio-tiler (e.g. `{"options": {...}}`).
    backend_options: dict = field(default_factory=dict)

    def __post_init__(self):
        if self.index is None:
            raise ValueError("RasterMosaicFactory requires a RasterIndex")
        # Bind the index into the backend: titiler constructs the reader itself,
        # and `backend_dependency` can't carry a non-request-scoped object.
        self.reader = partial(PGRasterMosaic, index=self.index, **self.backend_options)
        self.colormap_dependency = self._colormap_dependency()
        super().__post_init__()

    def register_routes(self):
        """Register only the routes that make sense without a mosaic document.

        Dropped: `/`, `/info`, `/validate`, `/map` — all of which describe or
        validate a MosaicJSON. `/assets` is replaced with index-backed versions.
        """
        self.tile()
        self.tilejson()
        self.bounds()
        self.point()
        self.assets()

    def assets(self):
        """Register asset-listing routes, answered from the index."""

        @self.router.get(
            "/{z}/{x}/{y}/assets",
            responses={200: {"description": "Rasters overlapping a tile"}},
        )
        def assets_for_tile(
            z: Annotated[int, Path(description="Tile zoom level")],
            x: Annotated[int, Path(description="Tile column")],
            y: Annotated[int, Path(description="Tile row")],
            src_path=Depends(self.path_dependency),
        ):
            """The rasters that would be read for a tile, in compositing order."""
            assets = self.index.assets_for_tile(x, y, z, src_path)
            return JSONResponse(
                {
                    "layers": src_path,
                    "should_generate": self.index.should_generate_tile(
                        x, y, z, src_path
                    ),
                    "assets": [a.model_dump() for a in assets],
                }
            )

        @self.router.get(
            "/assets",
            responses={200: {"description": "Footprints of every indexed raster"}},
        )
        def assets(src_path=Depends(self.path_dependency)):
            """Every footprint in these layers, as GeoJSON."""
            return JSONResponse(self.index.footprints(src_path))

    # -- Colormap resolution -----------------------------------------------

    def _colormap_dependency(self) -> Callable[..., Optional[dict]]:
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

    @property
    def _colormap_cache(self) -> dict:
        # Lazily attached so the dataclass doesn't need a mutable default.
        cache = getattr(self, "_cmap_cache", None)
        if cache is None:
            cache = {}
            self._cmap_cache = cache
        return cache


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
