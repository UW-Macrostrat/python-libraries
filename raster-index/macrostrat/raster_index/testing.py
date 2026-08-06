"""Helpers for building bite-sized test rasters.

Test rasters are *generated*, not committed: a few hundred pixels of synthetic
categorical data is enough to exercise footprints, asset selection, compositing
and rendering, and it keeps binary fixtures out of the repository. Shared here
rather than in a test file so `macrostrat.raster_layers` — and Macrostrat's own
integration tests — can use the same fixtures.
"""

from pathlib import Path
from typing import Optional

import numpy as np
import rasterio
from rasterio.transform import from_bounds

__all__ = [
    "create_test_raster",
    "create_test_rasters",
    "CATEGORICAL_COLORMAP",
    "FINE_BOUNDS",
    "COARSE_BOUNDS",
    "ELSEWHERE_BOUNDS",
]

# The standard arrangement: two overlapping rasters at different resolutions —
# what asset selection actually has to reason about — plus one far enough away
# that no tile over the others can touch it.
FINE_BOUNDS = (-105.0, 40.0, -104.9, 40.1)
COARSE_BOUNDS = (-105.05, 39.95, -104.8, 40.2)
ELSEWHERE_BOUNDS = (10.0, 10.0, 10.2, 10.2)

# A tiny paletted colormap, in the shape of the EMIT mineral-map palettes:
# value 0 is transparent nodata, the rest are opaque classes.
CATEGORICAL_COLORMAP = {
    0: (0, 0, 0, 0),
    1: (150, 150, 150, 255),
    2: (218, 50, 132, 255),
    3: (80, 164, 50, 255),
}


def create_test_raster(
    path: Path,
    bounds: tuple[float, float, float, float],
    *,
    size: int = 256,
    values: Optional[np.ndarray] = None,
    colormap: Optional[dict] = CATEGORICAL_COLORMAP,
    nodata: Optional[float] = 0,
    crs: str = "EPSG:4326",
) -> Path:
    """Write a single-band Byte raster covering `bounds`.

    Data is banded across the image so a composited tile visibly differs from
    either source, and the border row/column is left as nodata so masking is
    exercised too.
    """
    west, south, east, north = bounds
    if values is None:
        values = np.zeros((size, size), dtype="uint8")
        # Three horizontal bands of distinct classes, inset from the edges.
        inset = max(1, size // 32)
        band_height = (size - 2 * inset) // 3
        for i in range(3):
            start = inset + i * band_height
            values[start : start + band_height, inset : size - inset] = i + 1

    profile = {
        "driver": "GTiff",
        "dtype": "uint8",
        "count": 1,
        "height": size,
        "width": size,
        "crs": crs,
        "transform": from_bounds(west, south, east, north, size, size),
        "tiled": True,
        "blockxsize": 128,
        "blockysize": 128,
        "compress": "deflate",
    }
    if nodata is not None:
        profile["nodata"] = nodata

    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(values, 1)
        if colormap is not None:
            dst.write_colormap(1, {k: v for k, v in colormap.items()})
        dst.build_overviews([2, 4])

    return path


def create_test_rasters(directory: Path) -> dict[str, Path]:
    """The standard three test rasters, keyed by slug."""
    return {
        "fine": create_test_raster(directory / "fine.tif", FINE_BOUNDS, size=512),
        "coarse": create_test_raster(directory / "coarse.tif", COARSE_BOUNDS, size=128),
        "elsewhere": create_test_raster(
            directory / "elsewhere.tif", ELSEWHERE_BOUNDS, size=128
        ),
    }
