/*
Asset selection for mosaicked tile serving.

The tile server asks a single question per tile — "which rasters, in which order,
should I read?" — and this is where it is answered. Keeping it in SQL means the
spatial index does the work, and means the same answer is available to cache
warmers and diagnostics that aren't running the Python reader.
*/

/* The tile's extent in EPSG:4326.

Only the WebMercatorQuad grid is supported for now; Mars-tiler's per-TMS bounds
table is deliberately left out until Macrostrat actually needs a second grid. */
CREATE OR REPLACE FUNCTION raster_layers.tile_envelope(
  _x integer,
  _y integer,
  _z integer
) RETURNS geometry(Polygon, 4326)
AS $$
  SELECT ST_Transform(ST_TileEnvelope(_z, _x, _y), 4326);
$$ LANGUAGE SQL IMMUTABLE;


/*
The rasters that should be composited for a tile, in compositing order.

Ordering is by position in `_layers` first, then by `maxzoom` descending, so a
caller that passes several layers gets them stacked in the order it asked for,
and within a layer the highest-resolution raster wins the pixel.

`_zoom_tolerance` admits rasters slightly coarser than the requested zoom: below
a raster's `minzoom` its pixels are still readable (just upsampled), and cutting
them off exactly at `minzoom` leaves visible holes when a mosaic mixes
resolutions. `overscaled` flags the opposite case — the tile is zoomed in past
what the raster actually resolves — which callers use to decide whether a tile is
worth generating at all.
*/
CREATE OR REPLACE FUNCTION raster_layers.get_rasters(
  _x integer,
  _y integer,
  _z integer,
  _layers text[],
  _zoom_tolerance integer DEFAULT 3
)
RETURNS TABLE (
  id integer,
  href text,
  layer text,
  slug text,
  minzoom integer,
  maxzoom integer,
  rescale_range numeric[],
  overscaled boolean
) AS $$
  SELECT
    r.id,
    r.href,
    r.layer,
    r.slug,
    coalesce(r.minzoom, l.minzoom) minzoom,
    coalesce(r.maxzoom, l.maxzoom) maxzoom,
    coalesce(r.rescale_range, l.rescale_range) rescale_range,
    _z > coalesce(r.maxzoom, l.maxzoom) overscaled
  FROM raster_layers.raster r
  JOIN raster_layers.layer l
    ON r.layer = l.slug
  WHERE ST_Intersects(
      r.footprint,
      raster_layers.tile_envelope(_x, _y, _z)
    )
    AND r.layer = ANY(_layers)
    AND _z >= coalesce(r.minzoom, l.minzoom, 0) - _zoom_tolerance
  ORDER BY array_position(_layers, r.layer), maxzoom DESC;
$$ LANGUAGE SQL STABLE;


/* Whether a tile has any asset that isn't overscaled.

Used to avoid caching (or even rendering) tiles that would only ever be a blurry
magnification of data we already serve at a lower zoom. */
CREATE OR REPLACE FUNCTION raster_layers.should_generate_tile(
  _x integer,
  _y integer,
  _z integer,
  _layers text[]
) RETURNS boolean AS $$
  SELECT EXISTS (
    SELECT 1
    FROM raster_layers.get_rasters(_x, _y, _z, _layers) r
    WHERE NOT r.overscaled
  );
$$ LANGUAGE SQL STABLE;


/* Every footprint in a layer, as GeoJSON features.

Backs the `/assets` diagnostic route: a quick way to see what a layer covers
without reading a single pixel. */
CREATE OR REPLACE FUNCTION raster_layers.layer_footprints(_layers text[])
RETURNS TABLE (feature jsonb) AS $$
  SELECT jsonb_build_object(
    'type', 'Feature',
    'geometry', ST_AsGeoJSON(r.footprint)::jsonb,
    'properties', jsonb_build_object(
      'id', r.id,
      'layer', r.layer,
      'slug', r.slug,
      'href', r.href,
      'minzoom', r.minzoom,
      'maxzoom', r.maxzoom,
      'dtype', r.dtype
    )
  )
  FROM raster_layers.raster r
  WHERE r.layer = ANY(_layers)
  ORDER BY r.layer, r.slug;
$$ LANGUAGE SQL STABLE;
