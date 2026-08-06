"""Serve indexed cloud-optimized rasters as mosaicked tile layers.

Reads assets from a `macrostrat.raster_index` database and composites them into
tiles, as FastAPI routes that can be mounted into any application.
"""

from .backend import NoAssetFoundError, OverscaledAssetsError, PGRasterMosaic
from .factory import LayerListParams, RasterMosaicFactory, fixed_layers
from .layers import RasterLayerConfig, register_raster_layers

__all__ = [
    "PGRasterMosaic",
    "RasterMosaicFactory",
    "RasterLayerConfig",
    "register_raster_layers",
    "fixed_layers",
    "LayerListParams",
    "NoAssetFoundError",
    "OverscaledAssetsError",
]
