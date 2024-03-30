CREATE SCHEMA IF NOT EXISTS test1;

CREATE TABLE IF NOT EXISTS test1.table1 (
  id serial PRIMARY KEY,
  name text,
  description text
);

CREATE TABLE IF NOT EXISTS test1.table2 (
  id serial PRIMARY KEY,
  name text,
  description text,
  table1 integer REFERENCES test1.table1(id)
);