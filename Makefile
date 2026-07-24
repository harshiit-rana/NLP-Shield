.PHONY: install download-models test evaluate fixtures

install:
	pip install -e ".[dev]"

download-models:
	python scripts/download_models.py $(if $(GLINER),--gliner,)

fixtures:
	python scripts/make_fixtures.py

test:
	pytest -q

evaluate:
	python scripts/evaluate.py
