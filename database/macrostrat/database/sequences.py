import re
from dataclasses import dataclass
from typing import Optional

from psycopg.sql import Identifier, Literal, SQL
from sqlalchemy import inspect as sa_inspect

from macrostrat.database import Database


@dataclass
class ResetSequenceResult:
    table: str
    column: str
    sequence: str
    new_value: int


@dataclass
class ConvertToIdentityResult:
    table: str
    column: str
    old_sequence: str
    restart_value: int


def reset_sequence(
    db: Database,
    table: str,
    column: Optional[str] = None,
    *,
    schema: Optional[str] = None,
    start_val: Optional[int] = None,
) -> ResetSequenceResult:
    """Reset a primary key or identity sequence for a table to MAX(id)+1.

    Ensures that auto-generated primary keys won't collide with manually
    inserted rows.
    """
    schema, table = _resolve_schema_table(schema, table)

    insp = db.inspector
    if not insp.has_table(table, schema=schema):
        raise ValueError(f"Table {table} does not exist in schema {schema}")

    columns = insp.get_columns(table, schema=schema)

    increment_cols = [col for col in columns if is_autoincrementing_column(col)]
    if len(increment_cols) == 0:
        raise ValueError(
            f"No autoincrement or identity columns found in table {schema}.{table}"
        )

    if column is not None:
        increment_cols = [col for col in increment_cols if col["name"] == column]
        if len(increment_cols) == 0:
            raise ValueError(
                f"No autoincrement or identity column named {column!r} found in table {schema}.{table}"
            )

    if len(increment_cols) > 1:
        col_names = ", ".join(col["name"] for col in increment_cols)
        raise ValueError(
            f"Multiple autoincrement columns found in table {schema}.{table} ({col_names}). Please specify a column."
        )

    identity_col = increment_cols[0]
    col_ident_name = identity_col["name"]
    sequence_name = _get_sequence_name(db, schema, table, identity_col)
    if sequence_name is None:
        raise ValueError(
            f"Could not determine sequence name for column {col_ident_name!r} in table {schema}.{table}"
        )

    col_name = Identifier(col_ident_name)
    table_name = Identifier(schema, table)

    sql = "SELECT setval(:sequence_name, COALESCE(:start_val, (SELECT MAX({col_name})+1 FROM {table_name}), 1), false)"
    res = db.run_query(
        sql,
        dict(
            sequence_name=sequence_name,
            table_name=table_name,
            col_name=col_name,
            start_val=start_val,
        ),
    ).scalar()

    return ResetSequenceResult(
        table=f"{schema}.{table}",
        column=col_ident_name,
        sequence=sequence_name,
        new_value=res,
    )


def serial_to_identity(
    db: Database,
    table: str,
    column: Optional[str] = None,
    *,
    schema: Optional[str] = None,
    always: bool = True,
) -> ConvertToIdentityResult:
    """Convert a serial (nextval-backed) column to GENERATED AS IDENTITY.

    Data is preserved and the new identity sequence starts at MAX(column)+1.
    The old sequence is dropped after the identity column is created.
    """
    schema, table = _resolve_schema_table(schema, table)

    # Use a fresh inspector — db.inspector is cached and won't reflect DDL
    # changes made in the same session (e.g. a prior serial_to_identity call).
    insp = sa_inspect(db.engine)
    if not insp.has_table(table, schema=schema):
        raise ValueError(f"Table {table} does not exist in schema {schema}")

    columns = insp.get_columns(table, schema=schema)

    serial_cols = [
        col for col in columns
        if col.get("default") and "nextval" in str(col["default"])
    ]

    if len(serial_cols) == 0:
        already_identity = [col for col in columns if col.get("identity") is not None]
        if already_identity:
            names = ", ".join(c["name"] for c in already_identity)
            raise ValueError(
                f"Table {schema}.{table} already has identity column(s): {names}"
            )
        raise ValueError(
            f"No serial (nextval-backed) columns found in table {schema}.{table}"
        )

    if column is not None:
        serial_cols = [col for col in serial_cols if col["name"] == column]
        if len(serial_cols) == 0:
            raise ValueError(
                f"No serial column named {column!r} in table {schema}.{table}"
            )

    if len(serial_cols) > 1:
        col_names = ", ".join(col["name"] for col in serial_cols)
        raise ValueError(
            f"Multiple serial columns in table {schema}.{table} ({col_names}). Please specify one."
        )

    target_col = serial_cols[0]
    col_ident_name = target_col["name"]
    old_sequence = sequence_name_for_column(target_col)
    if old_sequence is None:
        raise ValueError(
            f"Could not determine sequence name for column {col_ident_name!r} in table {schema}.{table}"
        )

    col_ident = Identifier(col_ident_name)
    table_ident = Identifier(schema, table)
    generated_clause = "ALWAYS" if always else "BY DEFAULT"

    restart_val = db.run_query(
        SQL("SELECT COALESCE(MAX({col_name}), 0) + 1 FROM {table_name}"),
        dict(col_name=col_ident, table_name=table_ident),
    ).scalar()

    db.run_sql(
        SQL("ALTER TABLE {table_name} ALTER COLUMN {col_name} DROP DEFAULT"),
        params=dict(table_name=table_ident, col_name=col_ident),
        raise_errors=True,
    )

    db.run_sql(
        SQL(
            "ALTER TABLE {table_name} ALTER COLUMN {col_name} "
            "ADD GENERATED {generated} AS IDENTITY (START WITH {restart_val})"
        ),
        params=dict(
            table_name=table_ident,
            col_name=col_ident,
            generated=SQL(generated_clause),
            restart_val=Literal(restart_val),
        ),
        raise_errors=True,
    )

    db.run_sql(
        SQL("DROP SEQUENCE IF EXISTS {seq}"),
        params=dict(seq=_sequence_identifier(old_sequence)),
        raise_errors=True,
    )

    return ConvertToIdentityResult(
        table=f"{schema}.{table}",
        column=col_ident_name,
        old_sequence=old_sequence,
        restart_value=restart_val,
    )


def _resolve_schema_table(schema: Optional[str], table: str):
    """Parse schema and table, detecting dotted notation and checking for conflicts."""
    if schema is None:
        schema = "public"
    if "." in table:
        dotted_schema, table = table.split(".", 1)
        if schema != "public" and schema != dotted_schema:
            raise ValueError(
                f"Conflicting schema: keyword argument {schema!r} vs table prefix {dotted_schema!r}"
            )
        schema = dotted_schema
    return schema, table


def _get_sequence_name(db: Database, schema: str, table: str, col: dict) -> Optional[str]:
    """Look up the sequence for a column via pg_get_serial_sequence.

    Handles both serial (nextval default) and identity columns. Falls back
    to parsing the nextval expression for unowned sequences.
    """
    seq = db.run_query(
        "SELECT pg_get_serial_sequence(:table, :column)",
        dict(table=f"{schema}.{table}", column=col["name"]),
    ).scalar()
    if seq is not None:
        return seq
    return sequence_name_for_column(col)


def _sequence_identifier(sequence_name: str):
    """Return a psycopg Identifier for a possibly schema-qualified sequence name."""
    if "." in sequence_name:
        seq_schema, seq_name = sequence_name.split(".", 1)
        return Identifier(seq_schema, seq_name)
    return Identifier(sequence_name)


def is_autoincrementing_column(col):
    return (
        col.get("autoincrement") is True
        or col.get("identity") is not None
        or (col.get("default") and "nextval" in str(col["default"]))
    )


def sequence_name_for_column(col) -> Optional[str]:
    """Extract the sequence name from a column's nextval default expression.

    Returns None for identity columns (no nextval default) or if the
    expression doesn't match the expected pattern.
    """
    if col.get("default") and "nextval" in str(col["default"]):
        match = re.search(r"nextval\('([^']+)'(::regclass)?\)", str(col["default"]))
        if match:
            return match.group(1)
    return None
