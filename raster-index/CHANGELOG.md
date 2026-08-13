# Changelog

## [0.1.1] - 2026-08-12

- Move raster selection out of stored functions into query text owned by the
  package (`queries.py`), leaving the schema as tables and indexes only
- Selection returns each layer's `colormap`, so a raster carries how to draw it
  as well as where it is; `RasterAsset` gained the matching field

## [0.1.0] - 2026-08-12

- Initial release: the `raster_layers` schema, `RasterIndex`, footprint
  extraction through rio-tiler, bucket scanning, and a mountable Typer CLI
