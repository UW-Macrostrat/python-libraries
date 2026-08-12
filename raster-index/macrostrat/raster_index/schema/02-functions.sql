/*
Raster selection.

Every question anyone asks of this index is the same question — "which rasters,
in which order?" — differing only in the area asked about and whether a zoom
level is in play. So there is exactly one function that answers it,
`raster_layers.select_rasters`, and everything else is a thin wrapper that builds
a geometry or reshapes the result.

Keeping it in SQL (rather than PL/pgSQL) matters: a plain SQL function is
inlinable, so the planner folds the body into the caller and still drives the
GIST index on `footprint`. A PL/pgSQL body would be an opaque call boundary and
would defeat that.
*/

/* Removed in favor of `select_rasters`, which subsumes all three. Dropped here so
   re-applying this schema cleans up an older deployment; the diff-based
   migration path would otherwise be the only thing that noticed. */
DROP FUNCTION IF EXISTS raster_layers.get_rasters(integer, integer, integer, text[], integer);
DROP FUNCTION IF EXISTS raster_layers.layer_footprints(text[]);


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
The rasters covering an area, in compositing order. The one place that knows how
raster selection works.

`_geometry` is in EPSG:4326; NULL means "no spatial filter" (every raster in the
layers), which is what the footprint and extent queries want.

`_layers` NULL means every layer. Ordering is by position in `_layers` first,
then `maxzoom` descending, so a caller that passes several layers gets them
stacked in the order it asked for, and within a layer the highest-resolution
raster wins the pixel. `slug` breaks remaining ties so results are stable.

`_zoom` NULL disables zoom filtering entirely — a bbox or footprint query has no
zoom. When given, `_zoom_tolerance` admits rasters slightly coarser than the
requested zoom: below a raster's `minzoom` its pixels are still readable (just
upsampled), and cutting them off exactly at `minzoom` leaves visible holes when a
mosaic mixes resolutions. `overscaled` flags the opposite case — zoomed in past
what the raster resolves — which callers use to decide whether a tile is worth
caching.
*/
CREATE OR REPLACE FUNCTION raster_layers.select_rasters(
  _geometry geometry DEFAULT NULL,
  _layers text[] DEFAULT NULL,
  _zoom integer DEFAULT NULL,
  _zoom_tolerance integer DEFAULT 3
)
RETURNS TABLE (
  id integer,
  layer text,
  slug text,
  href text,
  footprint geometry(Geometry, 4326),
  minzoom integer,
  maxzoom integer,
  dtype text,
  rescale_range numeric[],
  overscaled boolean
) AS $$
  SELECT
    r.id,
    r.layer,
    r.slug,
    r.href,
    r.footprint,
    coalesce(r.minzoom, l.minzoom) minzoom,
    coalesce(r.maxzoom, l.maxzoom) maxzoom,
    r.dtype,
    coalesce(r.rescale_range, l.rescale_range) rescale_range,
    coalesce(_zoom > coalesce(r.maxzoom, l.maxzoom), false) overscaled
  FROM raster_layers.raster r
  JOIN raster_layers.layer l
    ON r.layer = l.slug
  WHERE (_layers IS NULL OR r.layer = ANY(_layers))
    AND (_geometry IS NULL OR ST_Intersects(r.footprint, _geometry))
    AND (
      _zoom IS NULL
      OR _zoom >= coalesce(r.minzoom, l.minzoom, 0) - _zoom_tolerance
    )
  ORDER BY
    coalesce(array_position(_layers, r.layer), 0),
    coalesce(r.maxzoom, l.maxzoom) DESC,
    r.slug;
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
    FROM raster_layers.select_rasters(
      raster_layers.tile_envelope(_x, _y, _z), _layers, _z
    ) r
    WHERE NOT r.overscaled
  );
$$ LANGUAGE SQL STABLE;


/* Raster footprints as a vector tile.

The raster-side counterpart to Macrostrat's map-footprints layer: an index of
*where coverage is*, cheap enough to draw at any zoom, without touching a single
COG. Selection is `select_rasters` with no zoom filter — coverage should be
visible even where the rasters themselves are overscaled.

The MVT layer name is `raster_footprints` — a cross-repo contract, and it must
match the `source-layer` used by any client style. */
CREATE OR REPLACE FUNCTION raster_layers.footprint_tile(
  _x integer,
  _y integer,
  _z integer,
  _layers text[] DEFAULT NULL
) RETURNS bytea AS $$
  WITH footprints AS (
    SELECT
      r.id,
      r.layer,
      r.slug,
      r.href,
      r.minzoom,
      r.maxzoom,
      r.dtype,
      ST_AsMVTGeom(
        ST_Transform(
          ST_Intersection(r.footprint, raster_layers.tile_envelope(_x, _y, _z)),
          3857
        ),
        ST_TileEnvelope(_z, _x, _y),
        4096,
        8,
        true
      ) AS geom
    FROM raster_layers.select_rasters(
      raster_layers.tile_envelope(_x, _y, _z), _layers
    ) r
  )
  SELECT ST_AsMVT(footprints, 'raster_footprints', 4096, 'geom')
  FROM footprints
  WHERE geom IS NOT NULL;
$$ LANGUAGE SQL STABLE;
