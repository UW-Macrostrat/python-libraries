"""Derive index metadata by opening a raster.

This is the only place in the package that touches pixels (or, more precisely,
headers): everything the index stores about a raster comes from here, so
registering a raster and re-registering it later are guaranteed to agree.
"""

from typing import Any, Optional

import rasterio
from rio_tiler.constants import WGS84_CRS
from rio_tiler.io import Reader

from macrostrat.utils import get_logger

from .defs import RasterInfo

log = get_logger(__name__)

__all__ = ["get_raster_info", "bounds_to_geometry"]


def bounds_to_geometry(bounds: tuple[float, float, float, float]) -> dict[str, Any]:
    """A GeoJSON polygon for a (west, south, east, north) bounding box."""
    west, south, east, north = bounds
    return {
        "type": "Polygon",
        "coordinates": [
            [
                [west, north],
                [west, south],
                [east, south],
                [east, north],
                [west, north],
            ]
        ],
    }


def get_raster_info(href: str, **reader_options) -> RasterInfo:
    """Open a raster and collect everything the index needs to know about it.

    The footprint is the geographic bounding box. That is enough for asset
    selection — a false positive costs one wasted read, and rio-tiler masks the
    result anyway — and it avoids paying for a mask read at registration time.
    Rasters spanning the antimeridian are the known failure case: their
    bounding box wraps the wrong way around the globe.
    """
    with Reader(href, **reader_options) as src:
        bounds = tuple(_geographic_bounds(src))
        info = src.info()
        dataset = src.dataset

        crs = _crs_string(src.crs)
        # rio-tiler normalizes an absent color table to `{}`; keep it as None so
        # "no colormap" and "empty colormap" don't have to be distinguished
        # downstream.
        colormap = src.colormap or None

        return RasterInfo(
            href=href,
            bounds=bounds,
            geometry=bounds_to_geometry(bounds),
            minzoom=src.minzoom,
            maxzoom=src.maxzoom,
            dtype=dataset.meta["dtype"],
            nbands=dataset.count,
            nodata=_first_nodata(dataset),
            crs=crs,
            colormap=colormap,
            metadata=info.model_dump(mode="json", exclude={"colormap"}),
        )


def _geographic_bounds(src: Reader) -> tuple[float, float, float, float]:
    """The reader's bounds in WGS84, across rio-tiler versions.

    rio-tiler 6.4 introduced `get_geographic_bounds(crs)` and deprecated the
    `geographic_bounds` property, which is gone in 7.x. Macrostrat's tile server
    and its API run different major versions of the stack, so this package has
    to work with both.
    """
    getter = getattr(src, "get_geographic_bounds", None)
    if getter is not None:
        return getter(WGS84_CRS)
    return src.geographic_bounds


def _crs_string(crs: Optional[rasterio.crs.CRS]) -> Optional[str]:
    if crs is None:
        return None
    # Prefer an authority code; fall back to WKT for CRSs PROJ can't name
    # (planetary bodies, custom local grids).
    try:
        epsg = crs.to_epsg()
    except Exception:  # pragma: no cover - PROJ can raise on exotic CRSs
        epsg = None
    if epsg is not None:
        return f"EPSG:{epsg}"
    return crs.to_string()


def _first_nodata(dataset) -> Optional[float]:
    """The dataset's nodata value, if all bands agree on one."""
    values = {v for v in dataset.nodatavals if v is not None}
    if len(values) != 1:
        return None
    return float(values.pop())
