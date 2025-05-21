.PHONY: install publish test

all: install

install:
	poetry install
	poetry run mono install

publish:
	poetry run mono publish

format:
	poetry run isort .
	poetry run black .

test:
	poetry run pytest -s -x
