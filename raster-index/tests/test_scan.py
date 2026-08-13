"""Bucket listing.

The interesting logic is the URL rewrite — turning the URL you'd paste in a
browser into an endpoint, a bucket and a prefix — so most of this is pure and
needs no network. The listing loop itself is checked against a stub client.
"""

from pytest import fixture, raises

from macrostrat.raster_index import scan_prefix
from macrostrat.raster_index.scan import parse_bucket_url


class TestHTTPSUrls:
    """An https:// URL has to imply everything: endpoint, bucket, prefix, hrefs."""

    def test_origin_becomes_the_endpoint(self):
        loc = parse_bucket_url("https://storage.example.org/rasters/minerals/")
        assert loc.endpoint_url == "https://storage.example.org"

    def test_first_path_segment_is_the_bucket(self):
        loc = parse_bucket_url("https://storage.example.org/rasters/minerals/")
        assert loc.bucket == "rasters"
        assert loc.prefix == "minerals/"

    def test_prefix_may_be_empty(self):
        loc = parse_bucket_url("https://storage.example.org/rasters")
        assert loc.bucket == "rasters"
        assert loc.prefix == ""

    def test_hrefs_point_back_at_the_same_url(self):
        """The whole point: no extra configuration to make hrefs readable."""
        loc = parse_bucket_url("https://storage.example.org/rasters/minerals/")
        assert (
            loc.href_for("minerals/nevada.tif")
            == "https://storage.example.org/rasters/minerals/nevada.tif"
        )

    def test_keys_are_percent_encoded(self):
        loc = parse_bucket_url("https://storage.example.org/rasters/")
        assert loc.href_for("south bolivia.tif").endswith("south%20bolivia.tif")

    def test_explicit_overrides_win(self):
        loc = parse_bucket_url(
            "https://storage.example.org/rasters/minerals/",
            endpoint_url="http://minio.internal:9000",
            public_url="https://cdn.example.org",
        )
        assert loc.endpoint_url == "http://minio.internal:9000"
        assert loc.href_for("a.tif") == "https://cdn.example.org/rasters/a.tif"

    def test_missing_bucket(self):
        with raises(ValueError, match="No bucket"):
            parse_bucket_url("https://storage.example.org/")


class TestS3Urls:
    def test_bucket_and_prefix(self):
        loc = parse_bucket_url("s3://rasters/minerals/")
        assert (loc.bucket, loc.prefix) == ("rasters", "minerals/")

    def test_endpoint_is_not_inferred(self):
        """Nothing in an s3:// URL says where the bucket lives."""
        loc = parse_bucket_url("s3://rasters/minerals/")
        assert loc.endpoint_url is None

    def test_hrefs_stay_s3_without_a_public_url(self):
        loc = parse_bucket_url("s3://rasters/minerals/")
        assert loc.href_for("minerals/a.tif") == "s3://rasters/minerals/a.tif"

    def test_public_url_rewrites_hrefs(self):
        loc = parse_bucket_url(
            "s3://rasters/minerals/", public_url="https://cdn.example.org"
        )
        assert loc.href_for("minerals/a.tif") == (
            "https://cdn.example.org/rasters/minerals/a.tif"
        )


def test_unsupported_scheme():
    with raises(ValueError, match="https:// or s3://"):
        parse_bucket_url("ftp://example.org/rasters/")


class StubClient:
    """Just enough of an S3 client to drive the listing loop."""

    def __init__(self, pages):
        self.pages = pages
        self.calls = []

    def get_paginator(self, name):
        assert name == "list_objects_v2"
        return self

    def paginate(self, **kwargs):
        self.calls.append(kwargs)
        return iter(self.pages)


@fixture
def stub(monkeypatch):
    """Replace the client factory; records how it was asked to list."""
    created = {}
    pages = [
        {
            "Contents": [
                {"Key": "minerals/", "Size": 0},
                {"Key": "minerals/nevada.tif", "Size": 1048576},
                {"Key": "minerals/nevada.tif.aux.xml", "Size": 3601},
                {"Key": "minerals/nevada.qml", "Size": 7522},
            ]
        },
        {"Contents": [{"Key": "minerals/utah.TIF", "Size": 2048}]},
    ]
    client = StubClient(pages)

    def factory(*, endpoint_url=None, credentials=False):
        created["endpoint_url"] = endpoint_url
        created["credentials"] = credentials
        return client

    monkeypatch.setattr("macrostrat.raster_index.scan.s3_client", factory)
    client.created = created
    return client


class TestListing:
    def test_rasters_are_found_and_sidecars_skipped(self, stub):
        """Buckets are full of .aux.xml and .qml files; only rasters are indexed."""
        objects = list(scan_prefix("https://storage.example.org/rasters/minerals/"))
        assert [o.key for o in objects] == [
            "minerals/nevada.tif",
            "minerals/utah.TIF",  # extension match is case-insensitive
        ]
        assert objects[0].size == 1048576

    def test_pages_are_all_consumed(self, stub):
        """Pagination is boto3's problem now — but it has to actually be used."""
        objects = list(scan_prefix("https://storage.example.org/rasters/minerals/"))
        assert len(objects) == 2

    def test_bucket_and_prefix_are_passed_through(self, stub):
        list(scan_prefix("https://storage.example.org/rasters/minerals/"))
        assert stub.calls == [{"Bucket": "rasters", "Prefix": "minerals/"}]

    def test_endpoint_is_derived_and_unsigned_by_default(self, stub):
        list(scan_prefix("https://storage.example.org/rasters/minerals/"))
        assert stub.created["endpoint_url"] == "https://storage.example.org"
        assert stub.created["credentials"] is False

    def test_credentials_are_opt_in(self, stub):
        list(scan_prefix("s3://rasters/minerals/", credentials=True))
        assert stub.created["credentials"] is True
