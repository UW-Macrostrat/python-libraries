# `macrostrat.raster_layers`

Serve the layers in a [`macrostrat.raster_index`](../raster-index) database as
mosaicked map tiles, as FastAPI routes mountable in any application.

Given a tile, the index says which COGs cover it and in what order; this package
reads them, composites them, and renders the result. It is titiler's
`MosaicTilerFactory` with the assets coming from a live spatial query instead of
a MosaicJSON document, so tiling, rescaling, colormaps and output formats are all
inherited rather than reimplemented.

## Usage

```python
from fastapi import FastAPI
from macrostrat.raster_index import RasterIndex
from macrostrat.raster_layers import RasterLayerConfig, register_raster_layers

app = FastAPI()
index = RasterIndex("postgresql://localhost:5432/macrostrat")

register_raster_layers(
    app,
    index,
    [
        # A unified layer over several indexed collections, composited in order.
        RasterLayerConfig(
            slug="mineral-maps",
            layers=["emit-minerals", "aviris-minerals"],
            resampling="nearest",  # categorical data
        ),
        RasterLayerConfig(slug="elevation", resampling="bilinear"),
    ],
    prefix="/rasters",
)
```

Each layer gets `/tiles/{z}/{x}/{y}[@{scale}x][.{format}]`, `/tilejson.json`,
`/bounds`, `/point/{lon},{lat}`, `/{z}/{x}/{y}/assets`, and `/assets` (footprints
as GeoJSON).

Install titiler's exception handlers (`MOSAIC_STATUS_CODES`) so a tile with no
coverage comes back as `204 No Content` — `NoAssetFoundError` is cogeo-mosaic's,
re-exported for exactly that reason.

## Behavior worth knowing

- **Overscaled tiles are empty by default.** When every covering raster is
  coarser than the requested zoom, the response is "nothing to serve" (204)
  rather than a magnified blur. Set `allow_overscaled=True` in `backend_options`
  to serve them anyway.
- **Colormaps default per layer.** Categorical rasters are unreadable without
  their palette, so when a request doesn't send one, the layer's `colormap` from
  the index is used. An explicit `colormap` / `colormap_name` query parameter
  always wins.
- **Whole-mosaic operations are unsupported.** `/info`, `/statistics`, `/preview`
  and friends would mean reading every raster in a layer; layer-wide metadata
  belongs to the index.
- **No tile caching.** Left to the host application's caching layer.
