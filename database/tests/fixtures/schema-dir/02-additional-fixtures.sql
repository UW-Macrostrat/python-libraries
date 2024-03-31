CREATE TABLE IF NOT EXISTS test1.additional_table (
  id serial PRIMARY KEY,
  name text,
  description text,
  table1 integer REFERENCES test1.table1(id)
);