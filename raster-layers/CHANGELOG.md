# Changelog

## [0.2.0] - 2026-08-22

- Filter a categorical mosaic to named classes:
  `?algorithm=classes&algorithm_params={"classes":["Kaolinite"]}`. Applied after
  compositing, so excluded classes are masked and survivors keep the layer's
  palette
- New `/layer` route reporting a layer's palette and class vocabulary, so a
  client can build a legend without reading a raster
- Requires `macrostrat.raster_index` 0.2.0, which carries `categories` on assets

## [0.1.1] - 2026-08-12

- Resolve colormaps from the assets a tile read already fetched, rather than
  caching them or querying separately: `set-colormap` and later edits take effect
  on the next tile, with one database query per tile instead of two
- Requires `macrostrat.raster_index` 0.1.1, whose selection moved out of stored
  functions into the package

## [0.1.0] - 2026-08-12

- Initial release: `PGRasterMosaic` (a rio-tiler mosaic backend reading assets
  from `macrostrat.raster_index`), `RasterMosaicFactory`, canned
  `RasterLayerConfig` layers, and a `raster_footprints` vector-tile layer
