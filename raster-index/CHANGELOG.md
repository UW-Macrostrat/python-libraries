# Changelog

## [0.2.0] - 2026-08-22

- Layers can carry a class vocabulary — named classes for a categorical raster's
  integer values — stored in the existing `metadata` column, so no schema change
- Derive vocabularies from GDAL band metadata, a `.qml` sidecar or a JSON file
  (`categories_from_info`, `categories_from_qml`, `categories_from_json`)
- New `set-categories` command; `info --metadata` shows band metadata and the
  class vocabularies it holds
- Selection returns each layer's `categories`, so a tile read resolves what its
  values mean in the same query that picks the assets
- `register_layer` no longer nulls out `metadata` when it isn't supplied
- `RasterIndex.layer()` and `raster_info()` for reading one layer or one
  raster's stored reader metadata

## [0.1.1] - 2026-08-12

- Move raster selection out of stored functions into query text owned by the
  package (`queries.py`), leaving the schema as tables and indexes only
- Selection returns each layer's `colormap`, so a raster carries how to draw it
  as well as where it is; `RasterAsset` gained the matching field

## [0.1.0] - 2026-08-12

- Initial release: the `raster_layers` schema, `RasterIndex`, footprint
  extraction through rio-tiler, bucket scanning, and a mountable Typer CLI
