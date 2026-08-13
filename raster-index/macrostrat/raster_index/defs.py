"""Types shared between indexing and serving.

These are the contract between `macrostrat.raster_index` and
`macrostrat.raster_layers`: the serving side never touches the tables directly,
it consumes `RasterAsset`s.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

__all__ = ["RasterAsset", "RasterInfo", "LayerDefinition"]


class RasterAsset(BaseModel):
    """A raster selected for a specific tile.

    Produced by `raster_layers.get_rasters`; consumed by the mosaic reader.
    """

    href: str
    layer: str
    slug: Optional[str] = None
    minzoom: Optional[int] = None
    maxzoom: Optional[int] = None
    rescale_range: Optional[list[float]] = None
    # Layer-level today, per-raster once leveling exists. Travels with the asset
    # so a tile read resolves both "which rasters" and "how to draw them".
    colormap: Optional[dict[str, Any]] = None
    # True when the requested tile is zoomed in past what this raster resolves.
    overscaled: bool = False


class RasterInfo(BaseModel):
    """Metadata derived by opening a raster, before it is written to the index."""

    href: str
    # Bounding box in EPSG:4326, as (west, south, east, north).
    bounds: tuple[float, float, float, float]
    # Footprint as a GeoJSON geometry dict, in EPSG:4326.
    geometry: dict[str, Any]
    minzoom: int
    maxzoom: int
    dtype: str
    nbands: int
    nodata: Optional[float] = None
    crs: Optional[str] = None
    # Colormap embedded in the raster itself (a GDAL color table), if any.
    colormap: Optional[dict[int, tuple[int, int, int, int]]] = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class LayerDefinition(BaseModel):
    """A named mosaic, and the defaults its rasters inherit."""

    slug: str
    name: Optional[str] = None
    description: Optional[str] = None
    minzoom: Optional[int] = None
    maxzoom: Optional[int] = None
    rescale_range: Optional[list[float]] = None
    colormap: Optional[dict[str, Any]] = None
    metadata: Optional[dict[str, Any]] = None
