# `macrostrat.raster_index`

An index of cloud-optimized rasters (COGs), grouped into named **layers** that a
tile server can serve as single mosaics.

Nothing here stores pixels. A row in `raster_layers.raster` is a reference to a
COG in object storage plus the metadata needed to decide whether reading it is
worthwhile for a given tile: its footprint, its native zoom range, and its data
type. The schema name is `raster_layers` rather than `raster`/`rasters` to stay
clear of PostGIS Raster's vocabulary.

Serving these layers is [`macrostrat.raster_layers`](https://github.com/UW-Macrostrat/python-libraries/tree/main/raster-layers).

## Usage

```python
from macrostrat.raster_index import RasterIndex, LayerDefinition

index = RasterIndex("postgresql://localhost:5432/macrostrat")
index.create_schema()  # or apply `schema_files()` through your own system

index.register_layer(
    LayerDefinition(slug="emit-minerals", name="EMIT mineral maps", maxzoom=14)
)
index.add_raster(
    "https://storage.example.org/rasters/nevada.tif", layer="emit-minerals"
)

index.assets_for_tile(x=180, y=411, z=10, layers=["emit-minerals"])
```

## Schema

`schema_files()` returns the SQL defining the schema, in application order, so a
host application can fold it into its own schema management rather than calling
`create_schema()`. Two tables and a few functions:

- `raster_layers.layer` — a named mosaic, and the defaults its rasters inherit
  (zoom range, rescale range, colormap).
- `raster_layers.raster` — one COG: `href`, EPSG:4326 `footprint`, zoom range,
  `dtype`/`nbands`/`nodata`, and the full reader metadata as `info`.
- `raster_layers.get_rasters(x, y, z, layers[])` — asset selection, ordered by
  layer priority then resolution. The core of the whole package.
- `raster_layers.should_generate_tile(...)` — whether any asset actually resolves
  at this zoom, for cache warmers and render short-circuits.
- `raster_layers.layer_footprints(layers[])` — footprints as GeoJSON features.

## CLI

`raster-index` reads `RASTER_INDEX_DATABASE` (or `DATABASE_URL`):

```sh
raster-index define-layer emit-minerals --name "EMIT mineral maps"
raster-index scan https://storage.example.org/remote-sensing-data/emit-mineral-maps/ \
  --layer emit-minerals
raster-index set-colormap emit-minerals --from https://storage.example.org/.../nevada.tif
raster-index assets 10 180 411 --layer emit-minerals
```

The connection is a parameter of the app itself: `--database`, or those
environment variables. A host application that already knows its own connection
(Macrostrat mounts these as `macrostrat raster`) calls
`set_default_connection(url_or_callable)` once, and its users never pass
`--database` — though it still works, and still wins. Scanning object stores
needs the `s3` extra (boto3), whichever URL form you use — an `https://` bucket
URL is *rewritten* into an endpoint/bucket/prefix and listed through the same
S3 API, rather than being a second code path.

## Known limitations

- Footprints are bounding boxes, so rasters crossing the antimeridian are
  indexed incorrectly. The column is typed `geometry`, not `polygon`, so a
  mask-derived footprint can replace them without a migration.
- WebMercatorQuad only. Alternate tile grids (and non-Earth bodies, as in
  [mars-tiler](https://github.com/davenquinn/mars-tiler)) would need a per-grid
  bounds table.
