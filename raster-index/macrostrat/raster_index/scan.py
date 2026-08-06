"""Discovering rasters in an object store.

Registration is usually "index everything under this prefix", so the index needs
to be able to list a bucket. `boto3` is an optional dependency: a deployment that
only ever registers explicit hrefs shouldn't have to install it.
"""

from dataclasses import dataclass
from typing import Iterator, Optional
from urllib.parse import urlparse

from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["RasterObject", "scan_prefix"]

# GDAL can open more than this, but a raster index should only ever be pointed at
# cloud-optimized rasters, and buckets are full of sidecars (.aux.xml, .qml).
RASTER_EXTENSIONS = (".tif", ".tiff", ".vrt")


@dataclass
class RasterObject:
    """An object found in a bucket, and the href the index should record."""

    bucket: str
    key: str
    size: int
    href: str


def scan_prefix(
    url: str,
    *,
    endpoint_url: Optional[str] = None,
    anonymous: bool = False,
    public_url: Optional[str] = None,
    extensions: tuple[str, ...] = RASTER_EXTENSIONS,
) -> Iterator[RasterObject]:
    """List candidate rasters under an `s3://bucket/prefix` URL.

    `public_url` rewrites each object to an HTTPS href on that origin. For a
    public-read bucket that is what you want in the index: the tile server then
    reads rasters over plain HTTP range requests and needs no credentials.
    """
    parsed = urlparse(url)
    if parsed.scheme != "s3":
        raise ValueError(f"Expected an s3:// URL, got {url!r}")
    bucket = parsed.netloc
    prefix = parsed.path.lstrip("/")

    client = _s3_client(endpoint_url=endpoint_url, anonymous=anonymous)
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(extensions):
                continue
            yield RasterObject(
                bucket=bucket,
                key=key,
                size=obj["Size"],
                href=_href(bucket, key, public_url),
            )


def _href(bucket: str, key: str, public_url: Optional[str]) -> str:
    if public_url is None:
        return f"s3://{bucket}/{key}"
    return f"{public_url.rstrip('/')}/{bucket}/{key}"


def _s3_client(*, endpoint_url: Optional[str], anonymous: bool):
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.client import Config
    except ImportError as err:  # pragma: no cover - depends on install extras
        raise ImportError(
            "Scanning object stores requires boto3: "
            "install `macrostrat.raster_index[s3]`"
        ) from err

    kwargs = {}
    if endpoint_url is not None:
        kwargs["endpoint_url"] = endpoint_url
    if anonymous:
        kwargs["config"] = Config(signature_version=UNSIGNED)
    return boto3.client("s3", **kwargs)
