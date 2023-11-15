all:
	poetry run mono install

publish:
	poetry run mono publish

test:
	poetry run pytest -s -x --failed-first