"""An index of cloud-optimized rasters, for mosaicked tile serving.

Rasters live in object storage; this package records *where* they are, *what*
they cover, and *which named layer* they belong to, so a tile server can answer
"which files do I read for this tile?" with a single spatial query.

Serving is a separate concern, handled by `macrostrat.raster_layers`.
"""

from .defs import LayerDefinition, RasterAsset, RasterInfo
from .footprints import get_raster_info
from .index import RasterIndex, schema_files
from .scan import RasterObject, scan_prefix

__all__ = [
    "RasterIndex",
    "schema_files",
    "get_raster_info",
    "scan_prefix",
    "RasterObject",
    "RasterAsset",
    "RasterInfo",
    "LayerDefinition",
]
