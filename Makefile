.PHONY: install publish test

all: install

install:
	poetry install
	poetry run mono install

publish:
	poetry run mono publish

test:
	poetry run pytest -s -x --failed-first