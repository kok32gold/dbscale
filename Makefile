.PHONY: install lint format format-check typecheck test test-unit test-integration coverage

install:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff format src tests

format-check:
	ruff format --check src tests

typecheck:
	mypy src

test:
	pytest

test-unit:
	pytest tests/unit tests/stress

test-integration:
	pytest tests/integration

coverage:
	pytest tests/unit tests/stress --cov=dbscale --cov-branch --cov-report=term-missing
