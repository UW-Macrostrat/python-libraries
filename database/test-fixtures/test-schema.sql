/* A PostgreSQL test schema to evaluate multi-schema mapping behavior */

CREATE SCHEMA IF NOT EXISTS geology;

CREATE TABLE IF NOT EXISTS geology.formation (
  id serial PRIMARY KEY,
  name text,
  description text
);

CREATE TABLE IF NOT EXISTS sample (
  id serial PRIMARY KEY,
  name text,
  description text,
  formation integer REFERENCES geology.formation(id)
);