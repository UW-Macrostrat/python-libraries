CREATE EXTENSION IF NOT EXISTS postgis;

CREATE TABLE IF NOT EXISTS sample (
  id serial NOT NULL PRIMARY KEY,
  geom geometry(Point, 4326) NOT NULL,
  name text NOT NULL
);

INSERT INTO sample (geom, name) VALUES
  (ST_GeomFromText('POINT(0 0)', 4326), 'A'),
  (ST_GeomFromText('POINT(1 1)', 4326), 'B'),
  (ST_GeomFromText('POINT(2 2)', 4326), 'C'),
  (ST_GeomFromText('POINT(3 3)', 4326), 'D'),
  (ST_GeomFromText('POINT(4 4)', 4326), 'E');
