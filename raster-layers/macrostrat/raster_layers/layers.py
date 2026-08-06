"""Canned layer definitions.

A `RasterLayerConfig` is the serving-side counterpart to a row in
`raster_layers.layer`: the index says what a layer *is*, this says how it should
be *rendered and mounted*. Written as data so a host application declares its
raster layers in a list, the way the tileserver already declares vector layers.
"""

from dataclasses import dataclass, field
from typing import Optional

from fastapi import APIRouter, FastAPI, Query
from rio_tiler.types import RIOResampling
from titiler.core.dependencies import DatasetParams
from titiler.core.resources.enums import OptionalHeader
from typing_extensions import Annotated

from macrostrat.raster_index import RasterIndex

from .factory import RasterMosaicFactory, fixed_layers

__all__ = ["RasterLayerConfig", "register_raster_layers"]


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
            optional_headers=self.optional_headers,
        )
        return factory.router


def register_raster_layers(
    app: FastAPI,
    index: RasterIndex,
    configs: list[RasterLayerConfig],
    *,
    prefix: str = "",
    tags: Optional[list[str]] = None,
) -> None:
    """Mount each layer at `<prefix>/<slug>`."""
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
