"""Canned layer definitions.

A `RasterLayerConfig` is the serving-side counterpart to a row in
`raster_layers.layer`: the index says what a layer *is*, this says how it should
be *rendered and mounted*. Written as data so a host application declares its
raster layers in a list, the way the tileserver already declares vector layers.
"""

import re
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Optional

from fastapi import APIRouter, FastAPI, Query, Request
from rio_tiler.types import RIOResampling
from starlette.responses import Response
from titiler.core.dependencies import DatasetParams
from titiler.core.errors import add_exception_handlers
from titiler.core.resources.enums import OptionalHeader
from titiler.mosaic.errors import MOSAIC_STATUS_CODES
from typing_extensions import Annotated

from macrostrat.raster_index import RasterIndex

from .factory import RasterMosaicFactory, fixed_layers

__all__ = [
    "RasterLayerConfig",
    "register_raster_layers",
    "install_exception_handlers",
]


@dataclass
class RasterLayerConfig:
    """One mounted raster layer.

    `layers` may name several indexed layers, composited in the order given —
    that is how a "unified" layer over several sub-collections is built.
    """

    slug: str
    layers: Optional[list[str]] = None
    title: Optional[str] = None
    # Applies to how pixels are sampled when a tile doesn't line up with the
    # raster grid. Categorical rasters (classification maps) must stay `nearest`;
    # continuous ones (elevation, geophysics) look far better bilinear.
    resampling: str = "nearest"
    colormap: Optional[dict] = None
    # Look the colormap up from the index when the request doesn't supply one.
    use_index_colormap: bool = True
    # Whether the layer advertises `?algorithm=classes` for filtering a
    # classification map down to named classes. Harmless on a continuous layer,
    # but meaningless there, so it can be turned off.
    class_filtering: bool = True
    # Forwarded to the backend — notably `allow_overscaled` and `zoom_tolerance`.
    backend_options: dict = field(default_factory=dict)
    optional_headers: list[OptionalHeader] = field(
        default_factory=lambda: [OptionalHeader.x_assets]
    )

    @property
    def layer_slugs(self) -> list[str]:
        return self.layers or [self.slug]

    def router(self, index: RasterIndex) -> APIRouter:
        factory = RasterMosaicFactory(
            index=index,
            path_dependency=fixed_layers(*self.layer_slugs),
            dataset_dependency=_dataset_params(self.resampling),
            default_colormap=self.colormap,
            use_index_colormap=self.use_index_colormap,
            class_filtering=self.class_filtering,
            backend_options=self.backend_options,
            optional_headers=self.optional_headers,
        )
        return factory.router


# A tile route's path tail: `/tiles/{tms}/{z}/{x}/{y}`, optionally `@{scale}x`
# and a format extension. Used only to tell a tile request apart from a `/point`
# or `/assets` one, and to size the empty image.
_TILE_PATH = re.compile(
    r"/tiles/[^/]+/\d+/\d+/\d+(?:@(?P<scale>\d+)x)?(?:\.(?P<format>\w+))?$"
)

# Every tile grid served here is 256-based; `@2x` doubles it, which is what a
# tile *with* data comes back as.
TILE_SIZE = 256

# Formats an empty tile can be drawn in. Anything else (JPEG, which has no alpha
# channel, or a data format like NPY) keeps the bodyless 204.
TRANSPARENT_FORMATS = {None, "png"}


@lru_cache(maxsize=8)
def _transparent_png(size: int) -> bytes:
    """A fully transparent, fully masked PNG of the requested size.

    Rendered the same way a real tile is, rather than hand-rolled, so it is a
    valid single-band-plus-alpha PNG by construction. Cached: there is one per
    tile size, and no-coverage requests are constant while panning.
    """
    import numpy
    from rio_tiler.utils import render

    data = numpy.zeros((1, size, size), dtype="uint8")
    mask = numpy.zeros((size, size), dtype="uint8")
    return render(data, mask, img_format="PNG")


def _no_content() -> Response:
    """A bodyless, explicitly zero-length 204.

    An explicit `Content-Length: 0` matters: Starlette omits the header entirely
    for a 204, which leaves the response close-delimited; Varnish then tries to
    read a body it will never get and fails the fetch with a 503.
    """
    return Response(status_code=204, headers={"content-length": "0"})


async def _empty_tile(request: Request, exc: Exception) -> Response:
    """No coverage: a transparent tile for image requests, a 204 otherwise.

    Running off the edge of coverage is routine and must never reach the client
    as an error. A 204 looked like the honest answer, but **mapbox-gl cannot
    consume it**: 204 satisfies `response.ok`, so it reads the body into a
    zero-length `ArrayBuffer`, which is truthy, and hands it to
    `createImageBitmap` — surfacing as "The image could not be decoded" on every
    tile past the edge of the data. The status was never the problem; asking a
    raster client to interpret a status instead of an image was.

    So an image request gets a real, fully transparent image, which every client
    already knows how to draw. Non-image endpoints (`/point`, `/assets`) keep the
    204, where nothing ever had trouble with it.
    """
    match = _TILE_PATH.search(request.url.path)
    if match is None:
        return _no_content()

    # The extension-less routes take the format as `?f=`; no format at all means
    # titiler would have chosen PNG for masked data anyway.
    fmt = match.group("format") or request.query_params.get("f")
    if fmt is not None:
        fmt = fmt.lower()
    if fmt not in TRANSPARENT_FORMATS:
        return _no_content()

    scale = int(match.group("scale") or 1)
    return Response(
        _transparent_png(scale * TILE_SIZE),
        media_type="image/png",
    )


def install_exception_handlers(app: FastAPI) -> None:
    """Map "no coverage" onto an empty tile rather than a server error.

    A tile request outside a layer's rasters is *normal operation* — panning to
    the edge of coverage does it constantly — so it must not surface as a 5xx or
    a logged traceback. Registering these with the routes means a host
    application gets the behavior by mounting the layers, without having to know
    these exceptions exist.
    """
    for exc, status in MOSAIC_STATUS_CODES.items():
        if status == 204:
            app.add_exception_handler(exc, _empty_tile)
        else:
            add_exception_handlers(app, {exc: status})


def register_raster_layers(
    app: FastAPI,
    index: RasterIndex,
    configs: list[RasterLayerConfig],
    *,
    prefix: str = "",
    tags: Optional[list[str]] = None,
) -> None:
    """Mount each layer at `<prefix>/<slug>`."""
    install_exception_handlers(app)
    for config in configs:
        app.include_router(
            config.router(index),
            prefix=f"{prefix}/{config.slug}",
            tags=tags or ["Rasters"],
        )


def _dataset_params(resampling: str) -> type[DatasetParams]:
    """A `DatasetParams` whose resampling default matches the layer's data.

    The `resampling` query parameter still overrides it; this only changes what a
    plain tile request gets.
    """

    @dataclass
    class LayerDatasetParams(DatasetParams):
        resampling_method: Annotated[
            RIOResampling,
            Query(
                alias="resampling",
                description=f"RasterIO resampling algorithm. Defaults to `{resampling}`.",
            ),
        ] = resampling

    return LayerDatasetParams
