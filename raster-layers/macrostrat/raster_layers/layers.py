"""Canned layer definitions.

A `RasterLayerConfig` is the serving-side counterpart to a row in
`raster_layers.layer`: the index says what a layer *is*, this says how it should
be *rendered and mounted*. Written as data so a host application declares its
raster layers in a list, the way the tileserver already declares vector layers.
"""

from dataclasses import dataclass, field
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


async def _empty_tile(request: Request, exc: Exception) -> Response:
    """An empty, explicitly zero-length 204.

    Two things have to be right here, because running off the edge of coverage
    is routine and must never reach the client as an error:

    - **No body.** titiler's own handler renders a JSON payload at this status,
      and a 204 carrying a body is malformed.
    - **An explicit `Content-Length: 0`.** Starlette omits the header entirely
      for a 204, which leaves the response close-delimited; Varnish then tries
      to read a body it will never get and fails the fetch with a 503.
    """
    return Response(status_code=204, headers={"content-length": "0"})


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
