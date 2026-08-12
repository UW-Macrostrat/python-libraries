"""Discovering rasters in an object store.

Registration is usually "index everything under this prefix", so the index needs
to be able to list a bucket. There is one listing implementation — `ListObjectsV2`
through boto3 — and two ways to address it:

- **an `https://` URL**, the one you'd paste into a browser. Its origin becomes
  the endpoint and its first path segment the bucket, so nothing else has to be
  configured, and the hrefs recorded point back at that same origin.
- **an `s3://` URL**, for buckets whose endpoint isn't implied by a public URL.

Requests are unsigned unless credentials are asked for: these buckets are
public-read, and signing an anonymous listing just fails.
"""

from dataclasses import dataclass
from typing import Iterator, Optional
from urllib.parse import quote, urlparse

from macrostrat.utils import get_logger

log = get_logger(__name__)

__all__ = ["RasterObject", "BucketPrefix", "parse_bucket_url", "scan_prefix"]

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


@dataclass
class BucketPrefix:
    """Where to list, and how to name what's found there."""

    bucket: str
    prefix: str
    # Endpoint to talk to; None means "wherever boto3 resolves AWS to".
    endpoint_url: Optional[str] = None
    # Origin to build hrefs on; None records `s3://` URLs instead.
    public_url: Optional[str] = None

    def href_for(self, key: str) -> str:
        if self.public_url is None:
            return f"s3://{self.bucket}/{key}"
        # Percent-encode the key so spaces and other awkward characters survive
        # into a URL the tile server can actually open.
        return f"{self.public_url.rstrip('/')}/{self.bucket}/{quote(key)}"


def parse_bucket_url(
    url: str,
    *,
    endpoint_url: Optional[str] = None,
    public_url: Optional[str] = None,
) -> BucketPrefix:
    """Resolve a bucket URL into everything needed to list it.

    An `https://` URL is rewritten rather than handled separately: the origin is
    the endpoint and the first path segment is the bucket, which is how every
    path-style S3 endpoint is laid out. Explicit `endpoint_url`/`public_url`
    always win, so an unusual deployment can still be described exactly.
    """
    parsed = urlparse(url)

    if parsed.scheme in ("http", "https"):
        origin = f"{parsed.scheme}://{parsed.netloc}"
        segments = parsed.path.lstrip("/").split("/", 1)
        bucket = segments[0]
        if not bucket:
            raise ValueError(
                f"No bucket in {url!r} — expected https://host/bucket/prefix/"
            )
        return BucketPrefix(
            bucket=bucket,
            prefix=segments[1] if len(segments) > 1 else "",
            endpoint_url=endpoint_url or origin,
            # Rasters are indexed at the URL they were found at, so the tile
            # server reads them over plain HTTP range requests.
            public_url=public_url or origin,
        )

    if parsed.scheme == "s3":
        return BucketPrefix(
            bucket=parsed.netloc,
            prefix=parsed.path.lstrip("/"),
            endpoint_url=endpoint_url,
            public_url=public_url,
        )

    raise ValueError(f"Expected an https:// or s3:// URL, got {url!r}")


def scan_prefix(
    url: str,
    *,
    endpoint_url: Optional[str] = None,
    credentials: bool = False,
    public_url: Optional[str] = None,
    extensions: tuple[str, ...] = RASTER_EXTENSIONS,
) -> Iterator[RasterObject]:
    """List candidate rasters under a bucket prefix."""
    location = parse_bucket_url(url, endpoint_url=endpoint_url, public_url=public_url)
    client = s3_client(endpoint_url=location.endpoint_url, credentials=credentials)

    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=location.bucket, Prefix=location.prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            if not key.lower().endswith(extensions):
                continue
            yield RasterObject(
                bucket=location.bucket,
                key=key,
                size=obj["Size"],
                href=location.href_for(key),
            )


def s3_client(*, endpoint_url: Optional[str] = None, credentials: bool = False):
    """An S3 client for listing a bucket.

    Path-style addressing is forced only when an endpoint was given: that's the
    layout a rewritten `https://host/bucket/...` URL implies. Against AWS itself,
    boto3's own choice (virtual-hosted) is left alone.
    """
    try:
        import boto3
        from botocore import UNSIGNED
        from botocore.client import Config
    except ImportError as err:  # pragma: no cover - depends on install extras
        raise ImportError(
            "Listing buckets requires boto3: install `macrostrat.raster_index[s3]`"
        ) from err

    config_args = {}
    if not credentials:
        # Public-read is the common case; signing is opt-in.
        config_args["signature_version"] = UNSIGNED
    if endpoint_url is not None:
        config_args["s3"] = {"addressing_style": "path"}

    kwargs = {"config": Config(**config_args)} if config_args else {}
    if endpoint_url is not None:
        kwargs["endpoint_url"] = endpoint_url
    return boto3.client("s3", **kwargs)
