.PHONY: install publish test

all: install

install:
	uv sync
	uv run mono install

publish:
	uv run mono publish

format:
	uv run isort .
	uv run black .

test:
	uv run pytest -s -x
