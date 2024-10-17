# Changelog

## [3.4.0] - 2024-10-17

- Add capability to instantiate the `Database` wrapper class from an engine as
  well as a URL.

## [3.3.0] - 2024-03-30

This release focuses on nicer semantics for applying database fixtures.

- Mark `Database.reflect_table` function as deprecated.
- Add a `Database.instance_params` dictionary to store common parameters that
  can be applied to any queries

## [3.2.0] - 2024-03-13

Add `macrostrat.database.transfer` module to incorporate functions for
streamlining `pg_dump`, `pg_restore` and similar operations.

## [3.0.0] - 2024-01-04

- Switch to sqlalchemy v2
- Improve database automapping

## [2.1.2] - 2023-05-16

- Added a `has_server_binds` method to the `run_sql` utility function to allow
  parameter-style inference to be disabled.
- Added some tests and documentation for the `run_sql` function.
