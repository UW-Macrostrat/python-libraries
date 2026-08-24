"""Footprints that follow the data, not the file's corners.

A raster's bounding box is a cheap footprint and a poor one.
This derives the footprint from the raster's own validity mask instead:
polygonize the valid-data region, generalize it, and store that. Selection then
rejects a raster without valid data before a reader is ever opened.

Two properties shape the approach:

- **It must be affordable.** The mask is read decimated, through the raster's
  overviews where it has them, so this is a small read of a pyramid level rather
  than a pass over full-resolution pixels.
- **The result must stay small.** A footprint traced at full fidelity can carry
  tens of thousands of vertices, and every tile query would then pay for an exact
  intersection against it. Generalization is applied until the geometry is under
  a vertex budget, so geometry-processing time is kept to a minimum.
"""

from typing import Any, Optional, Union

import numpy
import rasterio
from rasterio import features
from rasterio.crs import CRS
from rasterio.warp import transform_geom
from rio_tiler.constants import WGS84_CRS

from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["mask_footprint", "MaskFootprint"]

# Longest edge, in pixels, of the decimated mask read. 1024 resolves a swath
# edge to well under a tile at the zooms that matter while keeping the read (and
# the number of traced vertices) small.
DEFAULT_MAX_SIZE = 1024

# Vertex budget for the stored geometry. Selection runs an exact intersection
# against this on every tile, so it is a query-cost ceiling, not an aesthetic
# choice. Generous: a swath traced at 1024px and generalized comes out in the
# tens of vertices, so this only binds on genuinely fragmented coverage.
DEFAULT_MAX_VERTICES = 500

# Mitre joins when growing the footprint (see `_generalize`). Named because the
# integer form is what every shapely 2.x accepts; the string spellings are newer.
_MITRE = 2

# Regions smaller than this share of the decimated image are dropped. Isolated
# valid pixels are usually artifacts, and each one would otherwise become its own
# polygon in the footprint.
DEFAULT_MIN_AREA_FRACTION = 1e-5


class MaskFootprint:
    """A mask-derived footprint, with the numbers that justify it."""

    def __init__(
        self,
        geometry: dict[str, Any],
        *,
        bbox_area: float,
        footprint_area: float,
        vertices: int,
        parts: int,
        decimated_shape: tuple[int, int],
    ):
        self.geometry = geometry
        self.bbox_area = bbox_area
        self.footprint_area = footprint_area
        self.vertices = vertices
        self.parts = parts
        self.decimated_shape = decimated_shape

    @property
    def area_fraction(self) -> float:
        """Footprint area as a share of the bounding box.

        The headline number: how much of every bbox-selected read was wasted.
        """
        if self.bbox_area == 0:
            return 1.0
        return self.footprint_area / self.bbox_area


def mask_footprint(
    href: Union[str, Any],
    *,
    max_size: int = DEFAULT_MAX_SIZE,
    max_vertices: int = DEFAULT_MAX_VERTICES,
    min_area_fraction: float = DEFAULT_MIN_AREA_FRACTION,
    **open_options,
) -> Optional[MaskFootprint]:
    """The valid-data footprint of a raster, in EPSG:4326.

    Returns None when the mask says everything is valid — a raster that fills its
    own bounding box has nothing to gain, and storing a traced rectangle would
    only add vertices for the selection query to chew on.

    Needs `shapely` (`macrostrat.raster_index[footprints]`); it is an optional
    dependency because only the indexing side does this, and the tile server that
    consumes the result shouldn't have to carry it.
    """
    shapely = _require_shapely()
    from shapely.geometry import mapping, shape
    from shapely.ops import unary_union

    with rasterio.open(str(href), **open_options) as src:
        mask, transform = _read_mask(src, max_size)
        crs = src.crs
        bbox_bounds = src.bounds

    valid = mask > 0
    if not valid.any():
        raise ValueError(f"{href} has no valid data")

    total = float(valid.size)
    if valid.all():
        log.info("%s fills its bounding box; keeping the bbox footprint", href)
        return None

    min_pixels = max(1.0, min_area_fraction * total)
    polygons = []
    for geom, value in features.shapes(
        valid.astype("uint8"), mask=valid, transform=transform
    ):
        if not value:
            continue
        polygon = shape(geom)
        if polygon.is_empty:
            continue
        polygons.append(polygon)

    if not polygons:
        raise ValueError(f"Could not trace a footprint for {href}")

    # Pixel area in CRS units, for turning the fractional threshold into an area.
    pixel_area = abs(transform.a * transform.e)
    keep = [p for p in polygons if p.area >= min_pixels * pixel_area]
    if not keep:
        # Everything was below threshold: keep the largest rather than nothing.
        keep = [max(polygons, key=lambda p: p.area)]

    geometry = unary_union(keep)

    # Generalize in the source CRS, where a tolerance is expressed in the
    # raster's own units and a pixel is a natural starting point.
    pixel = max(abs(transform.a), abs(transform.e))
    traced = geometry
    geometry = _generalize(geometry, pixel, max_vertices)

    # The outward buffer can push past the raster's own edge, which would claim
    # ground the file cannot possibly cover. Clipping to the bounding box can't
    # lose any data — the data is inside the box by definition.
    from shapely.geometry import box

    geometry = geometry.intersection(box(*bbox_bounds))

    # A footprint must never be *smaller* than the data: a corner cut off the
    # inside means tiles along that edge stop selecting the raster and the mosaic
    # grows holes. `_generalize` grows the geometry to guarantee this, and it is
    # cheap enough to assert rather than trust.
    if not geometry.covers(traced):
        log.warning("Generalized footprint for %s did not cover the traced data", href)
        geometry = geometry.union(traced)

    wgs84 = transform_geom(crs, WGS84_CRS, mapping(geometry))
    footprint = shape(wgs84)

    bbox_area = _bbox_area_wgs84(crs, bbox_bounds)
    return MaskFootprint(
        geometry=mapping(footprint),
        bbox_area=bbox_area,
        footprint_area=footprint.area,
        vertices=_count_vertices(footprint),
        parts=_count_parts(footprint),
        decimated_shape=mask.shape,
    )


def _read_mask(src, max_size: int) -> tuple[numpy.ndarray, Any]:
    """The dataset mask, decimated so the longest edge is at most `max_size`.

    `out_shape` lets GDAL serve the read from an overview when the raster has
    them, which is what keeps this affordable on a large COG.
    """
    scale = max(src.height, src.width) / max_size
    if scale <= 1:
        height, width = src.height, src.width
    else:
        height = max(1, int(round(src.height / scale)))
        width = max(1, int(round(src.width / scale)))

    mask = src.dataset_mask(out_shape=(height, width))
    # The transform has to describe the decimated grid, not the full-resolution
    # one, or every traced vertex lands in the wrong place.
    transform = src.transform * src.transform.scale(
        src.width / width, src.height / height
    )
    return mask, transform


def _generalize(geometry, pixel: float, max_vertices: int):
    """Simplify to the vertex budget, growing the result so it still covers.

    Two things happen here, and the order matters:

    - **Simplify**, starting at one pixel of the decimated mask. That is where
      the win is: the staircase a diagonal swath edge traces is pixel-scale
      noise, and removing it took one test case from 1891 vertices to 9 for a
      1.2% change in area.
    - **Grow by the tolerance**, with mitre joins. `simplify` cuts corners
      *inward*, so a simplified footprint does not contain its own data — which
      would punch holes in the mosaic along every diagonal edge. Buffering
      outward by the tolerance restores containment, and on an
      already-simplified polygon a mitre buffer adds no vertices at all.

    The tolerance doubles only if the budget is still not met, because area
    overshoot grows with it — the same test case is 5% larger than the data at
    one pixel and 109% larger at 32.
    """
    tolerance = pixel
    for _ in range(24):
        simplified = geometry.simplify(tolerance, preserve_topology=True)
        if not simplified.is_empty:
            grown = simplified.buffer(tolerance, join_style=_MITRE, mitre_limit=2.0)
            if not grown.is_empty and _count_vertices(grown) <= max_vertices:
                return grown
        tolerance *= 2

    # Rather than return something unbounded in size. Still far tighter than a
    # bounding box for a diagonal swath, and it covers by construction.
    log.warning("Falling back to a convex hull to meet the vertex budget")
    return geometry.convex_hull


def _bbox_area_wgs84(crs: CRS, bounds) -> float:
    """Area of the raster's bounding box in EPSG:4326, for comparison."""
    from shapely.geometry import box, mapping, shape

    geom = transform_geom(crs, WGS84_CRS, mapping(box(*bounds)))
    return shape(geom).area


def _count_vertices(geometry) -> int:
    if geometry.is_empty:
        return 0
    if (
        geometry.geom_type.startswith("Multi")
        or geometry.geom_type == "GeometryCollection"
    ):
        return sum(_count_vertices(part) for part in geometry.geoms)
    exterior = len(geometry.exterior.coords)
    return exterior + sum(len(ring.coords) for ring in geometry.interiors)


def _count_parts(geometry) -> int:
    if geometry.is_empty:
        return 0
    if (
        geometry.geom_type.startswith("Multi")
        or geometry.geom_type == "GeometryCollection"
    ):
        return len(geometry.geoms)
    return 1


def _require_shapely():
    try:
        import shapely
    except ImportError as err:  # pragma: no cover - depends on the environment
        raise ImportError(
            "Mask-derived footprints need shapely. Install "
            "`macrostrat.raster_index[footprints]`."
        ) from err
    return shapely
