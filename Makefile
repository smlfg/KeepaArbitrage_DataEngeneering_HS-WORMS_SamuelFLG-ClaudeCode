.PHONY: test test-fast test-cov lint format format-check check

test:
	pytest

test-fast:
	pytest --no-cov -q

test-cov:
	pytest --cov=src --cov-report=term-missing --cov-report=html:reports/coverage

lint:
	mypy src/ --ignore-missing-imports
	isort --check-only src/ tests/
	black --check src/ tests/

format:
	isort src/ tests/
	black src/ tests/

format-check:
	isort --check-only --diff src/ tests/
	black --check --diff src/ tests/

check: format-check lint test
