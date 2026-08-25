"""Types shared between indexing and serving.

These are the contract between `macrostrat.raster_index` and
`macrostrat.raster_layers`: the serving side never touches the tables directly,
it consumes `RasterAsset`s.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

__all__ = [
    "RasterAsset",
    "RasterInfo",
    "LayerDefinition",
    "RasterCategory",
    "LayerExtent",
]


class RasterCategory(BaseModel):
    """One class in a categorical raster's vocabulary.

    Classification maps address their classes by integer, but people address
    them by name. This is the join between the two, resolved once at ingest
    (from GDAL band metadata and the color table) and stored on the layer, so
    neither the tile server nor a client has to re-derive it.
    """

    value: int
    label: str
    # From the raster's color table, where it has one. Carried alongside the
    # label so a client can draw a legend from a single request.
    color: Optional[tuple[int, int, int, int]] = None


class LayerExtent(BaseModel):
    """What a set of layers covers, and at what resolutions.

    Bounds and zoom range together because they are answered by one query, and a
    tile route asks for both on every request — see `RasterIndex.layer_extent`.
    """

    bounds: tuple[float, float, float, float]
    # Native zoom range across the selected rasters. `None` where no raster
    # records one, which is the caller's cue to fall back to the tile grid's.
    minzoom: Optional[int] = None
    maxzoom: Optional[int] = None


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
    # The layer's class vocabulary, for categorical rasters. Like `colormap`,
    # it travels with the asset so a tile read resolves both "which rasters" and
    # "what the values mean" in one query.
    categories: Optional[list[RasterCategory]] = None
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
    # Stored inside `metadata`, but modeled separately: the vocabulary is a
    # first-class part of what a categorical layer *is*, while `metadata` is the
    # jsonb column that happens to hold it (which is why this needed no schema
    # change). `RasterIndex` folds it in on write and lifts it back out on read.
    categories: Optional[list[RasterCategory]] = None
