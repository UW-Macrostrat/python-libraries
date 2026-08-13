/*
Cleanup only — this schema is tables and indexes, nothing else.

Raster selection used to live in stored functions here. It moved into
`macrostrat.raster_index` as query text (see `queries.py`): a stored function is
a second deployment artifact that has to be version-matched with its callers,
and the schema ships with the *released* package while a running service may
still be on the previous one. Keeping the SQL in the wheel means one artifact
carries both the logic and the code that uses it.

These drops let a database that ran an earlier version clean itself up. They can
be deleted once no deployment predates the change.
*/

DROP FUNCTION IF EXISTS raster_layers.get_rasters(integer, integer, integer, text[], integer);
DROP FUNCTION IF EXISTS raster_layers.layer_footprints(text[]);
DROP FUNCTION IF EXISTS raster_layers.select_rasters(geometry, text[], integer, integer);
DROP FUNCTION IF EXISTS raster_layers.should_generate_tile(integer, integer, integer, text[]);
DROP FUNCTION IF EXISTS raster_layers.footprint_tile(integer, integer, integer, text[]);
DROP FUNCTION IF EXISTS raster_layers.tile_envelope(integer, integer, integer);
