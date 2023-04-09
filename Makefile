all:
	poetry install

publish:
	poetry run publish.py

test:
	poetry run pytest -s