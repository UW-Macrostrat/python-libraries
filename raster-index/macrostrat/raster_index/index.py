"""The index itself: reading and writing the `raster_layers` schema.

Deliberately built on SQLAlchemy Core rather than ORM models. The index has two
very different consumers — a CLI that registers rasters, and a tile server that
asks one hot question per tile — and neither benefits from reflection. Keeping to
Core also means the serving side can hold a plain engine and run inside
FastAPI's threadpool without dragging session state along.
"""

import json
from pathlib import Path
from typing import Any, Iterable, Optional, Union

from sqlalchemy import create_engine, text
from sqlalchemy.engine import Connection, Engine

from macrostrat.utils import get_logger

from .defs import LayerDefinition, RasterAsset, RasterInfo
from .footprints import get_raster_info
from .queries import TILE_ENVELOPE, bbox_envelope, selection

log = get_logger(__name__)

__all__ = ["RasterIndex", "schema_files"]

__here__ = Path(__file__).parent

# Anything that can be turned into an engine: a URL, a SQLAlchemy engine or
# connection, or an object exposing `.engine` (such as `macrostrat.database`'s
# `Database`, duck-typed so this package doesn't pin a version of it).
Connectable = Union[Engine, Connection, str, Any]


def schema_files() -> list[Path]:
    """The SQL files defining the `raster_layers` schema, in application order.

    Exposed so a host application can fold them into its own schema-management
    system (Macrostrat wraps them in a `SchemaDefinition` chunk) instead of this
    package having to know anything about how schemas get applied.
    """
    return sorted((__here__ / "schema").glob("*.sql"))


def _resolve_engine(conn: Connectable) -> Engine:
    if isinstance(conn, Engine):
        return conn
    if isinstance(conn, Connection):
        return conn.engine
    engine = getattr(conn, "engine", None)
    if engine is not None:
        return engine
    return create_engine(str(conn))


class RasterIndex:
    """An index of cloud-optimized rasters, grouped into named layers."""

    engine: Engine

    def __init__(self, connection: Connectable):
        self.engine = _resolve_engine(connection)

    # -- Schema ------------------------------------------------------------

    def create_schema(self) -> None:
        """Apply the `raster_layers` schema.

        Standalone use (tests, a scratch database) only — in Macrostrat the
        schema is applied by the schema-management system, from the same files.
        PostGIS is enabled here rather than in the SQL, because installing an
        extension needs privileges the role that owns an application schema
        generally doesn't have.
        """
        # Imported lazily: applying multi-statement SQL files is the only thing
        # `macrostrat.database` is needed for, and nothing on the serving path
        # should pay for importing it.
        from macrostrat.database import Database

        with self.engine.begin() as conn:
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS postgis"))
        db = Database(self.engine.url)
        for file in schema_files():
            db.run_sql(file)

    def schema_exists(self) -> bool:
        """Whether the `raster_layers` schema has been applied."""
        sql = text(
            "SELECT EXISTS (SELECT 1 FROM information_schema.schemata "
            "WHERE schema_name = 'raster_layers')"
        )
        with self.engine.connect() as conn:
            return bool(conn.execute(sql).scalar())

    # -- Layers ------------------------------------------------------------

    def register_layer(
        self, layer: Union[LayerDefinition, str], **kwargs
    ) -> LayerDefinition:
        """Create or update a layer definition.

        Idempotent: re-registering a layer overwrites the fields it specifies.
        """
        if isinstance(layer, str):
            layer = LayerDefinition(slug=layer, **kwargs)
        elif kwargs:
            layer = layer.model_copy(update=kwargs)

        sql = text("""
            INSERT INTO raster_layers.layer
              (slug, name, description, minzoom, maxzoom, rescale_range,
               colormap, metadata)
            VALUES
              (:slug, :name, :description, :minzoom, :maxzoom, CAST(:rescale_range AS numeric[]),
               CAST(:colormap AS jsonb), CAST(:metadata AS jsonb))
            ON CONFLICT (slug) DO UPDATE SET
              name = excluded.name,
              description = excluded.description,
              minzoom = excluded.minzoom,
              maxzoom = excluded.maxzoom,
              rescale_range = excluded.rescale_range,
              colormap = excluded.colormap,
              metadata = excluded.metadata
            """)
        params = dict(
            slug=layer.slug,
            name=layer.name or layer.slug,
            description=layer.description,
            minzoom=layer.minzoom,
            maxzoom=layer.maxzoom,
            rescale_range=layer.rescale_range,
            colormap=_json(layer.colormap),
            metadata=_json(layer.metadata),
        )
        with self.engine.begin() as conn:
            conn.execute(sql, params)
        log.info("Registered layer %s", layer.slug)
        return layer

    def layers(self) -> list[LayerDefinition]:
        sql = text("""
            SELECT slug, name, description, minzoom, maxzoom, rescale_range,
                   colormap, metadata
            FROM raster_layers.layer
            ORDER BY slug
            """)
        with self.engine.connect() as conn:
            rows = conn.execute(sql).mappings().all()
        return [
            LayerDefinition(
                **{
                    **dict(row),
                    "rescale_range": _floats(row["rescale_range"]),
                }
            )
            for row in rows
        ]

    def remove_layer(self, slug: str, *, cascade: bool = False) -> int:
        """Delete a layer. Its rasters must be gone first unless `cascade`."""
        with self.engine.begin() as conn:
            if cascade:
                conn.execute(
                    text("DELETE FROM raster_layers.raster WHERE layer = :slug"),
                    dict(slug=slug),
                )
            res = conn.execute(
                text("DELETE FROM raster_layers.layer WHERE slug = :slug"),
                dict(slug=slug),
            )
        return res.rowcount

    # -- Rasters -----------------------------------------------------------

    def add_raster(
        self,
        href: Union[str, Path],
        layer: str,
        *,
        slug: Optional[str] = None,
        info: Optional[RasterInfo] = None,
        rescale_range: Optional[list[float]] = None,
        minzoom: Optional[int] = None,
        maxzoom: Optional[int] = None,
        ensure_layer: bool = True,
        reader_options: Optional[dict] = None,
    ) -> RasterInfo:
        """Register a raster, reading its metadata if not supplied.

        Keyed on `href`, so this is an upsert: pointing the CLI at the same
        bucket twice refreshes the index rather than duplicating it. One
        consequence worth knowing — a raster belongs to exactly one layer, so
        registering the same href under a different layer *moves* it rather than
        adding a second copy.
        """
        href = str(href)
        if info is None:
            info = get_raster_info(href, **(reader_options or {}))
        if slug is None:
            slug = default_slug(href)

        if ensure_layer:
            self._ensure_layer(layer)

        sql = text("""
            INSERT INTO raster_layers.raster
              (layer, slug, href, footprint, minzoom, maxzoom, dtype, nbands,
               nodata, crs, rescale_range, info)
            VALUES (
              :layer, :slug, :href,
              ST_SetSRID(ST_GeomFromGeoJSON(:geometry), 4326),
              :minzoom, :maxzoom, :dtype, :nbands, :nodata, :crs,
              CAST(:rescale_range AS numeric[]), CAST(:info AS jsonb)
            )
            ON CONFLICT (href) DO UPDATE SET
              layer = excluded.layer,
              slug = excluded.slug,
              footprint = excluded.footprint,
              minzoom = excluded.minzoom,
              maxzoom = excluded.maxzoom,
              dtype = excluded.dtype,
              nbands = excluded.nbands,
              nodata = excluded.nodata,
              crs = excluded.crs,
              rescale_range = excluded.rescale_range,
              info = excluded.info,
              updated_at = now()
            """)
        params = dict(
            layer=layer,
            slug=slug,
            href=href,
            geometry=json.dumps(info.geometry),
            minzoom=minzoom if minzoom is not None else info.minzoom,
            maxzoom=maxzoom if maxzoom is not None else info.maxzoom,
            dtype=info.dtype,
            nbands=info.nbands,
            nodata=info.nodata,
            crs=info.crs,
            rescale_range=rescale_range,
            info=_json(info.metadata),
        )
        with self.engine.begin() as conn:
            conn.execute(sql, params)
        log.info("Registered raster %s in layer %s", slug, layer)
        return info

    def add_rasters(
        self, hrefs: Iterable[Union[str, Path]], layer: str, **kwargs
    ) -> list[RasterInfo]:
        """Register several rasters, skipping (but reporting) any that fail.

        A bad or unreadable object in a bucket shouldn't abort the rest of a
        scan; the log line is the record of what was left out.
        """
        results = []
        for href in hrefs:
            try:
                results.append(self.add_raster(href, layer, **kwargs))
            except Exception as err:
                log.error("Could not register %s: %s", href, err)
        return results

    def rasters(self, layer: Optional[str] = None) -> list[dict[str, Any]]:
        sql = """
            SELECT id, layer, slug, href, minzoom, maxzoom, dtype, nbands,
                   nodata, crs, ST_AsGeoJSON(footprint) footprint
            FROM raster_layers.raster
        """
        params: dict[str, Any] = {}
        if layer is not None:
            sql += " WHERE layer = :layer"
            params["layer"] = layer
        sql += " ORDER BY layer, slug"
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(text(sql), params).mappings()]

    def remove_raster(self, href: str) -> int:
        with self.engine.begin() as conn:
            res = conn.execute(
                text("DELETE FROM raster_layers.raster WHERE href = :href"),
                dict(href=href),
            )
        return res.rowcount

    # -- Raster selection --------------------------------------------------
    #
    # Every lookup composes `queries.selection`, so the ordering and zoom rules
    # live in exactly one place. These methods differ only in the area they ask
    # about and what they do with the rows.

    # Columns needed to build a `RasterAsset`.
    _ASSET_COLUMNS = (
        "href, layer, slug, minzoom, maxzoom, rescale_range, colormap, overscaled"
    )

    def _select_assets(self, geometry: str, params: dict) -> list[RasterAsset]:
        """Run the selection over an area and return assets in read order."""
        sql = text(
            f"SELECT {self._ASSET_COLUMNS} FROM ({selection(geometry)}) selected"
        )
        with self.engine.connect() as conn:
            rows = conn.execute(sql, params).mappings().all()
        return [
            RasterAsset(**{**dict(row), "rescale_range": _floats(row["rescale_range"])})
            for row in rows
        ]

    def assets_for_tile(
        self,
        x: int,
        y: int,
        z: int,
        layers: list[str],
        *,
        zoom_tolerance: int = 3,
    ) -> list[RasterAsset]:
        """The rasters to composite for a tile, in compositing order."""
        return self._select_assets(
            TILE_ENVELOPE,
            dict(x=x, y=y, z=z, layers=layers, zoom=z, tolerance=zoom_tolerance),
        )

    def assets_for_bbox(
        self,
        west: float,
        south: float,
        east: float,
        north: float,
        layers: list[str],
    ) -> list[RasterAsset]:
        """The rasters intersecting a WGS84 bounding box, in compositing order.

        No zoom is involved, so nothing is filtered or flagged as overscaled.
        """
        return self._select_assets(
            bbox_envelope,
            dict(
                west=west,
                south=south,
                east=east,
                north=north,
                layers=layers,
                zoom=None,
                tolerance=None,
            ),
        )

    def should_generate_tile(self, x: int, y: int, z: int, layers: list[str]) -> bool:
        """Whether a tile has any asset that isn't overscaled.

        Used to avoid caching (or even rendering) tiles that would only ever be a
        blurry magnification of data already served at a lower zoom.
        """
        sql = text(f"""
            SELECT EXISTS (
              SELECT 1 FROM ({selection(TILE_ENVELOPE)}) selected
              WHERE NOT overscaled
            )
            """)
        params = dict(x=x, y=y, z=z, layers=layers, zoom=z, tolerance=3)
        with self.engine.connect() as conn:
            return bool(conn.execute(sql, params).scalar())

    def layer_bounds(
        self, layers: list[str]
    ) -> Optional[tuple[float, float, float, float]]:
        """The combined extent of a set of layers, in EPSG:4326.

        `None` when no rasters are indexed for them — the caller decides whether
        that is an empty layer or a typo.
        """
        sql = text(f"""
            SELECT ST_XMin(e) west, ST_YMin(e) south,
                   ST_XMax(e) east, ST_YMax(e) north
            FROM (
              SELECT ST_Extent(footprint) e FROM ({selection()}) selected
            ) a
            WHERE e IS NOT NULL
            """)
        params = dict(layers=layers, zoom=None, tolerance=None)
        with self.engine.connect() as conn:
            row = conn.execute(sql, params).first()
        if row is None:
            return None
        return (row.west, row.south, row.east, row.north)

    def footprints(self, layers: list[str]) -> dict[str, Any]:
        """The footprints of a set of layers, as a GeoJSON FeatureCollection."""
        sql = text(f"""
            SELECT jsonb_build_object(
              'type', 'Feature',
              'geometry', ST_AsGeoJSON(footprint)::jsonb,
              'properties', jsonb_build_object(
                'id', id, 'layer', layer, 'slug', slug, 'href', href,
                'minzoom', minzoom, 'maxzoom', maxzoom, 'dtype', dtype
              )
            )
            FROM ({selection()}) selected
            """)
        params = dict(layers=layers, zoom=None, tolerance=None)
        with self.engine.connect() as conn:
            features = [r[0] for r in conn.execute(sql, params)]
        return {"type": "FeatureCollection", "features": features}

    def footprint_tile(
        self, x: int, y: int, z: int, layers: Optional[list[str]] = None
    ) -> bytes:
        """Raster footprints for a tile, as Mapbox Vector Tile bytes.

        The raster-side counterpart to Macrostrat's map-footprints layer: an
        index of *where coverage is*, cheap enough to draw at any zoom, without
        touching a single COG. No zoom filter — coverage should be visible even
        where the rasters themselves would be overscaled.

        The MVT layer name `raster_footprints` is a cross-repo contract: it must
        match the `source-layer` used by any client style.

        Empty (zero-length) where nothing intersects, which is a valid empty
        tile as far as a client is concerned.
        """
        sql = text(f"""
            WITH selected AS ({selection(TILE_ENVELOPE)}),
            footprints AS (
              SELECT
                id, layer, slug, href, minzoom, maxzoom, dtype,
                ST_AsMVTGeom(
                  ST_Transform(ST_Intersection(footprint, {TILE_ENVELOPE}), 3857),
                  ST_TileEnvelope(:z, :x, :y),
                  4096, 8, true
                ) AS geom
              FROM selected
            )
            SELECT ST_AsMVT(footprints, 'raster_footprints', 4096, 'geom')
            FROM footprints
            WHERE geom IS NOT NULL
            """)
        params = dict(x=x, y=y, z=z, layers=layers, zoom=None, tolerance=None)
        with self.engine.connect() as conn:
            data = conn.execute(sql, params).scalar()
        return bytes(data) if data is not None else b""

    # -- Internals ---------------------------------------------------------

    def _ensure_layer(self, slug: str) -> None:
        """Create a bare layer if it doesn't exist, without clobbering one that does."""
        sql = text("""
            INSERT INTO raster_layers.layer (slug, name)
            VALUES (:slug, :slug)
            ON CONFLICT (slug) DO NOTHING
            """)
        with self.engine.begin() as conn:
            conn.execute(sql, dict(slug=slug))


def default_slug(href: str) -> str:
    """A raster's identifier within its layer, from the tail of its href."""
    return Path(href.split("?")[0]).stem


def _json(value: Optional[dict]) -> Optional[str]:
    """Serialize a dict for a `jsonb` column, preserving SQL NULL."""
    if value is None:
        return None
    return json.dumps(value)


def _floats(value: Optional[Iterable]) -> Optional[list[float]]:
    """Coerce a `numeric[]` column (which comes back as Decimals) to floats."""
    if value is None:
        return None
    return [float(v) for v in value]
