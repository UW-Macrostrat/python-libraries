"""Command-line surface for the raster index.

A plain module-level Typer app. The database is a parameter of the app itself —
`--database`, or the `RASTER_INDEX_DATABASE`/`DATABASE_URL` environment
variables — resolved once in the callback and handed to commands through the
Click context.

A host application that already knows its own connection (Macrostrat mounts
these commands as `macrostrat raster`) calls `set_default_connection()` to supply
it, and its users never have to pass `--database`.
"""

from dataclasses import dataclass
from typing import Callable, Optional, Union

from rich import print
from rich.table import Table
from typer import Argument, BadParameter, Context, Exit, Option, Typer

from .defs import LayerDefinition
from .index import Connectable, RasterIndex
from .scan import scan_prefix

__all__ = ["cli", "set_default_connection", "index_for"]

# A connection, or something that produces one when a command actually needs it.
ConnectionSource = Union[Connectable, Callable[[], Connectable]]

# Set by a host application to supply its own database. Lower priority than an
# explicit `--database` so a user can always point the CLI somewhere else.
_default_connection: Optional[ConnectionSource] = None


def set_default_connection(source: Optional[ConnectionSource]) -> None:
    """Supply the connection to use when `--database` isn't given.

    Accepts a URL, an engine or `Database`, or a zero-argument callable
    returning one — a callable defers reading the host's configuration until a
    command actually runs.
    """
    global _default_connection
    _default_connection = source


@dataclass
class RasterIndexContext:
    """CLI state, attached to the Click context by the callback.

    The index is built on first use rather than in the callback, so `--help` and
    argument errors don't require a working database.
    """

    connection: Optional[ConnectionSource] = None
    _index: Optional[RasterIndex] = None

    @property
    def index(self) -> RasterIndex:
        if self._index is not None:
            return self._index

        source = self.connection
        if callable(source) and not isinstance(source, str):
            source = source()
        if source is None:
            raise BadParameter(
                "No database configured. Pass --database, or set "
                "RASTER_INDEX_DATABASE (or DATABASE_URL).",
                param_hint="--database",
            )

        self._index = RasterIndex(source)
        return self._index


def index_for(ctx: Context, *, require_schema: bool = True) -> RasterIndex:
    """The index for the running command.

    The schema check turns the common first-run mistake — a database that's
    never been provisioned — into a pointed message rather than an
    `UndefinedTable` traceback from whichever query happened to run first.
    """
    index = ctx.find_object(RasterIndexContext).index
    if require_schema and not index.schema_exists():
        print(
            "[yellow]No [bold]raster_layers[/bold] schema in this database.\n"
            "[dim]Apply it through your schema-management system, or run "
            "`create-schema` for a scratch database."
        )
        raise Exit(1)
    return index


cli = Typer(no_args_is_help=True, short_help="Manage indexed raster datasets")


@cli.callback()
def main(
    ctx: Context,
    database: Optional[str] = Option(
        None,
        "--database",
        envvar=["RASTER_INDEX_DATABASE", "DATABASE_URL"],
        help="PostgreSQL connection string for the raster index",
        show_default=False,
    ),
):
    """Manage indexed raster datasets."""
    ctx.obj = RasterIndexContext(connection=database or _default_connection)


@cli.command(name="create-schema")
def create_schema(ctx: Context):
    """Apply the `raster_layers` schema directly.

    For scratch databases. Managed deployments should build the schema through
    their own schema-management system.
    """
    index_for(ctx, require_schema=False).create_schema()
    print("[green]Created [bold]raster_layers[/bold] schema")


@cli.command(name="layers")
def list_layers(ctx: Context):
    """List indexed raster layers."""
    index = index_for(ctx)

    counts = {}
    for raster in index.rasters():
        counts[raster["layer"]] = counts.get(raster["layer"], 0) + 1

    table = Table("Layer", "Name", "Rasters", "Zooms", box=None)
    for layer in index.layers():
        table.add_row(
            layer.slug,
            layer.name or "",
            str(counts.get(layer.slug, 0)),
            _zoom_range(layer.minzoom, layer.maxzoom),
        )
    print(table)


@cli.command(name="rasters")
def list_rasters(ctx: Context, layer: Optional[str] = Argument(None)):
    """List indexed rasters, optionally within a single layer."""
    table = Table("Layer", "Raster", "Zooms", "Type", "Href", box=None)
    for raster in index_for(ctx).rasters(layer):
        table.add_row(
            raster["layer"],
            raster["slug"],
            _zoom_range(raster["minzoom"], raster["maxzoom"]),
            f"{raster['dtype']}×{raster['nbands']}",
            raster["href"],
        )
    print(table)


@cli.command(name="define-layer")
def define_layer(
    ctx: Context,
    slug: str,
    name: Optional[str] = Option(None),
    description: Optional[str] = Option(None),
    minzoom: Optional[int] = Option(None),
    maxzoom: Optional[int] = Option(None),
):
    """Create or update a layer definition."""
    index_for(ctx).register_layer(
        LayerDefinition(
            slug=slug,
            name=name,
            description=description,
            minzoom=minzoom,
            maxzoom=maxzoom,
        )
    )
    print(f"[green]Defined layer [bold]{slug}[/bold]")


@cli.command(name="set-colormap")
def set_colormap(
    ctx: Context,
    layer: str,
    source: str = Option(
        ...,
        "--from",
        help="Raster whose embedded palette should become the layer's colormap",
    ),
):
    """Copy a raster's embedded color table onto its layer.

    Categorical rasters (classification maps) carry their palette in the file.
    Serving them means having that palette at render time, which is what the
    layer's colormap is for.
    """
    from .footprints import get_raster_info

    colormap = get_raster_info(source).colormap
    if not colormap:
        print(f"[yellow]{source} has no embedded color table")
        raise Exit(1)

    index = index_for(ctx)
    existing = {l.slug: l for l in index.layers()}
    if layer not in existing:
        print(f"[yellow]Layer [bold]{layer}[/bold] is not defined")
        raise Exit(1)

    # Stored as JSON, so keys become strings and tuples become lists; the
    # serving side normalizes them back.
    index.register_layer(
        existing[layer],
        colormap={str(k): list(v) for k, v in colormap.items()},
    )
    print(f"[green]Set a {len(colormap)}-entry colormap on [bold]{layer}[/bold]")


@cli.command(name="add")
def add(
    ctx: Context,
    hrefs: list[str] = Argument(..., help="Raster URLs or paths"),
    layer: str = Option(..., "--layer", "-l", help="Layer to add rasters to"),
):
    """Register rasters in a layer, reading footprints and zoom ranges."""
    results = index_for(ctx).add_rasters(hrefs, layer)
    print(f"[green]Registered {len(results)}/{len(hrefs)} rasters in {layer}")


@cli.command(name="scan")
def scan(
    ctx: Context,
    url: str = Argument(..., help="Prefix to scan, e.g. s3://bucket/prefix/"),
    layer: str = Option(..., "--layer", "-l", help="Layer to add rasters to"),
    endpoint_url: Optional[str] = Option(
        None, help="S3-compatible endpoint to list against"
    ),
    public_url: Optional[str] = Option(
        None, help="Rewrite hrefs onto this HTTPS origin"
    ),
    anonymous: bool = Option(False, help="List without credentials"),
    dry_run: bool = Option(False, help="Show what would be registered"),
):
    """Register every raster under an object-store prefix."""
    objects = list(
        scan_prefix(
            url,
            endpoint_url=endpoint_url,
            public_url=public_url,
            anonymous=anonymous,
        )
    )
    if not objects:
        print(f"[yellow]No rasters found under {url}")
        return

    if dry_run:
        for obj in objects:
            print(f"{obj.href} [dim]({obj.size / 1e6:.1f} MB)")
        print(f"[dim]{len(objects)} rasters (dry run — nothing registered)")
        return

    results = index_for(ctx).add_rasters([o.href for o in objects], layer)
    print(f"[green]Registered {len(results)}/{len(objects)} rasters in {layer}")


@cli.command(name="remove")
def remove(ctx: Context, href: str):
    """Remove a raster from the index."""
    count = index_for(ctx).remove_raster(href)
    if count:
        print(f"[green]Removed {href}")
    else:
        print(f"[yellow]{href} is not in the index")


@cli.command(name="info")
def info(href: str):
    """Show the metadata that would be indexed for a raster.

    Reads the raster directly, so it needs no database.
    """
    from .footprints import get_raster_info

    print(get_raster_info(href).model_dump(exclude={"metadata"}))


@cli.command(name="assets")
def assets(
    ctx: Context,
    z: int,
    x: int,
    y: int,
    layers: list[str] = Option(..., "--layer", "-l"),
):
    """Show which rasters would be read for a tile."""
    index = index_for(ctx)

    table = Table("Layer", "Raster", "Zooms", "Overscaled", box=None)
    for asset in index.assets_for_tile(x, y, z, layers):
        table.add_row(
            asset.layer,
            asset.slug or "",
            _zoom_range(asset.minzoom, asset.maxzoom),
            "yes" if asset.overscaled else "",
        )
    print(table)

    if not index.should_generate_tile(x, y, z, layers):
        print("[yellow]No usable assets — this tile should not be generated")


def _zoom_range(minzoom, maxzoom) -> str:
    if minzoom is None and maxzoom is None:
        return ""
    low = minzoom if minzoom is not None else "?"
    high = maxzoom if maxzoom is not None else "?"
    return f"{low}–{high}"
