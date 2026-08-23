"""Raster selection, as SQL owned by this package.

Every question anyone asks of the index is the same question — "which rasters,
in which order?" — differing only in the area asked about and whether a zoom
level is in play. `SELECTION` is that query; everything else composes it.

**Why this isn't a database function.** It was one, briefly, and the cost showed
up immediately: a stored function is a second deployment artifact that has to be
version-matched with the code calling it. Renaming it broke a running server;
adding a column meant a drop-and-recreate plus applying SQL out of band, because
the schema ships with the *released* package while the caller was still the old
one. Keeping the query in the wheel means one artifact carries both the logic
and its callers, and the schema is reduced to what genuinely needs migrating:
tables and indexes.

Nothing was lost by moving it. The MVT and GeoJSON shapes still run in the
database — they just arrive as query text rather than as a stored procedure —
and a plain subquery is inlined by the planner exactly as an inlinable SQL
function was, so the GIST index on `footprint` is still driven the same way.
"""

__all__ = ["SELECTION", "selection", "TILE_ENVELOPE", "bbox_envelope"]

# The tile's extent in EPSG:4326. Only WebMercatorQuad is supported; a second
# grid would mean carrying per-grid bounds, which Macrostrat doesn't need yet.
TILE_ENVELOPE = "ST_Transform(ST_TileEnvelope(:z, :x, :y), 4326)"

# A WGS84 bounding box, for area queries that aren't tile-shaped.
bbox_envelope = "ST_MakeEnvelope(:west, :south, :east, :north, 4326)"

# `{geometry}` is substituted with one of the expressions above (or `NULL` for
# "no spatial filter"). It is never user input — callers pass one of the
# constants in this module.
SELECTION = """
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
      -- Layer-level, but selected per row so a raster carries how to draw it
      -- and what its values mean, as well as where it is. A tile read then
      -- resolves all of it at once, and per-raster overrides (leveling) have
      -- somewhere to go.
      l.colormap,
      -- The class vocabulary for categorical layers, stored inside the layer's
      -- `metadata`. Selected here for the same reason as the colormap: a tile
      -- that filters by class name must not cost a second query to find out
      -- what the names mean.
      l.metadata -> 'categories' categories,
      coalesce(CAST(:zoom AS integer) > coalesce(r.maxzoom, l.maxzoom), false)
        overscaled
    FROM raster_layers.raster r
    JOIN raster_layers.layer l
      ON r.layer = l.slug
    -- Bind parameters are cast explicitly: Postgres can't infer a type for a
    -- bare parameter in `IS NULL`, and every one of these is optional.
    WHERE (
        CAST(:layers AS text[]) IS NULL
        OR r.layer = ANY(CAST(:layers AS text[]))
      )
      AND ({geometry} IS NULL OR ST_Intersects(r.footprint, {geometry}))
      AND (
        CAST(:zoom AS integer) IS NULL
        OR CAST(:zoom AS integer)
           >= coalesce(r.minzoom, l.minzoom, 0) - CAST(:tolerance AS integer)
      )
    ORDER BY
      coalesce(array_position(CAST(:layers AS text[]), r.layer), 0),
      coalesce(r.maxzoom, l.maxzoom) DESC,
      r.slug
"""


def selection(geometry: str = "NULL") -> str:
    """The selection query, filtered to an area.

    Ordering is by position in `:layers` first, then `maxzoom` descending, so a
    caller passing several layers gets them stacked in the order it asked for,
    and within a layer the highest-resolution raster wins the pixel. `slug`
    breaks remaining ties so results are stable.

    `:zoom` NULL disables zoom filtering — a bbox or footprint query has no
    zoom. When given, `:tolerance` admits rasters slightly coarser than the
    requested zoom: below a raster's `minzoom` its pixels are still readable
    (just upsampled), and cutting them off exactly at `minzoom` leaves visible
    holes when a mosaic mixes resolutions. `overscaled` flags the opposite case,
    which callers use to decide whether a tile is worth caching.
    """
    return SELECTION.format(geometry=geometry)
