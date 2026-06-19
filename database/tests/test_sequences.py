"""Tests for reset_sequence and serial_to_identity functionality."""

from pytest import fixture, raises

from macrostrat.database import Database
from macrostrat.database.sequences import (
    ConvertToIdentityResult,
    ResetSequenceResult,
    reset_sequence,
    serial_to_identity,
)
from macrostrat.database.utils import temporary_database
from sqlalchemy.engine import make_url


@fixture(scope="module")
def seq_db(database_url):
    url = make_url(str(database_url)).set(database="test_sequences")
    with temporary_database(url, drop=True, force_drop=True) as engine:
        db = Database(engine.url)
        db.run_sql(
            """
            CREATE TABLE items (
                id serial PRIMARY KEY,
                name text
            );
            CREATE TABLE no_seq (
                id integer PRIMARY KEY,
                name text
            );
            CREATE TABLE serial_to_convert (
                id serial PRIMARY KEY,
                name text
            );
            """,
            raise_errors=True,
        )
        yield db
        db.cleanup()


# --- reset_sequence ---


def test_reset_sequence_basic(seq_db):
    """Sequence resets to MAX(id)+1 after a manual high-ID insert."""
    seq_db.run_sql(
        "INSERT INTO items (id, name) VALUES (100, 'seed')",
        raise_errors=True,
    )
    result = reset_sequence(seq_db, "items")

    assert isinstance(result, ResetSequenceResult)
    assert result.table == "public.items"
    assert result.column == "id"
    assert result.new_value == 101


def test_reset_sequence_enables_insert(seq_db):
    """After reset, auto-generated IDs don't collide with existing rows."""
    new_id = seq_db.run_query(
        "INSERT INTO items (name) VALUES ('auto') RETURNING id"
    ).scalar()
    assert new_id >= 101


def test_reset_sequence_dotted_table(seq_db):
    result = reset_sequence(seq_db, "public.items")
    assert result.table == "public.items"


def test_reset_sequence_schema_kwarg(seq_db):
    result = reset_sequence(seq_db, "items", schema="public")
    assert result.table == "public.items"


def test_reset_sequence_start_val(seq_db):
    result = reset_sequence(seq_db, "items", start_val=500)
    assert result.new_value == 500


def test_reset_sequence_schema_conflict(seq_db):
    with raises(ValueError, match="Conflicting schema"):
        reset_sequence(seq_db, "public.items", schema="other")


def test_reset_sequence_missing_table(seq_db):
    with raises(ValueError, match="does not exist"):
        reset_sequence(seq_db, "no_such_table")


def test_reset_sequence_no_autoincrement(seq_db):
    with raises(ValueError, match="No autoincrement or identity columns"):
        reset_sequence(seq_db, "no_seq")


# --- serial_to_identity ---


def test_serial_to_identity(seq_db):
    """Convert a serial column to GENERATED ALWAYS AS IDENTITY."""
    seq_db.run_sql(
        "INSERT INTO serial_to_convert (id, name) VALUES (50, 'seed')",
        raise_errors=True,
    )

    result = serial_to_identity(seq_db, "serial_to_convert")

    assert isinstance(result, ConvertToIdentityResult)
    assert result.table == "public.serial_to_convert"
    assert result.column == "id"
    assert result.restart_value == 51


def test_serial_to_identity_generates_ids(seq_db):
    """After conversion, the column auto-generates IDs from the correct start."""
    new_id = seq_db.run_query(
        "INSERT INTO serial_to_convert (name) VALUES ('post-convert') RETURNING id"
    ).scalar()
    assert new_id >= 51


def test_serial_to_identity_already_converted(seq_db):
    """Re-running on an already-identity column raises a clear error."""
    with raises(ValueError, match="already has identity"):
        serial_to_identity(seq_db, "serial_to_convert")


def test_serial_to_identity_missing_table(seq_db):
    with raises(ValueError, match="does not exist"):
        serial_to_identity(seq_db, "no_such_table")


def test_serial_to_identity_no_serial_column(seq_db):
    with raises(ValueError, match="No serial"):
        serial_to_identity(seq_db, "no_seq")
