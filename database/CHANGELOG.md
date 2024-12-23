# Changelog

## [3.5.2] - 2024-12-23

- Add the ability to print less with the `run_sql` function
- Add a `Database.refresh_schema` method

## [3.5.1] - 2024-12-21

- Add a `statement_filter` parameter to the `run_sql` function to allow for
  filtering of statements in a SQL file.
- Improved the consistency of the `Database.run_sql` function with the `run_sql`
  utility function.

## [3.5.0] - 2024-11-25

- Add database transfer utilities for asynchronous `pg_load` and `pg_dump`
  operations.

## [3.4.1] - 2024-10-28

- Update the underlying version of `sqlparse` and `geoalchemy2`.

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
