.PHONY: help install install-dev lint format type-check test test-unit test-integration test-cov clean format-check lint-check docker-lint docker-format docker-type-check docker-test docker-test-unit docker-test-cov docker-check-all docker-shell

help:
	@echo "SummarAIzer Development Commands"
	@echo "================================"
	@echo ""
	@echo "LOCAL COMMANDS (requires local venv with dev dependencies):"
	@echo "  make install           Install runtime dependencies"
	@echo "  make install-dev       Install runtime + development dependencies"
	@echo ""
	@echo "LOCAL Code Quality:"
	@echo "  make lint              Run linter (ruff) - auto-fixes"
	@echo "  make lint-check        Check code quality (no fixes)"
	@echo "  make lint-safe         Auto-fix SAFE issues only (imports, code upgrades)"
	@echo "  make lint-full         Auto-fix ALL issues (verify tests after!)"
	@echo "  make format            Format code with Black and fix imports with Ruff"
	@echo "  make format-check      Check if code is formatted (no changes)"
	@echo "  make type-check        Run static type checking (mypy)"
	@echo "  make check-all         Run all checks (lint, format, type)"
	@echo ""
	@echo "LOCAL Testing:"
	@echo "  make test              Run all tests"
	@echo "  make test-unit         Run unit tests only"
	@echo "  make test-integration  Run integration tests only"
	@echo "  make test-cov          Run tests with coverage report"
	@echo "  make test-watch        Run tests in watch mode (requires pytest-watch)"
	@echo ""
	@echo "DOCKER COMMANDS (runs inside running 'summaraizer' container):"
	@echo "  Docker Setup:"
	@echo "    make docker-install-dev  Install dev tools in container (first time!)"
	@echo "    make docker-shell        Open bash shell in container"
	@echo ""
	@echo "  Docker Code Quality:"
	@echo "    make docker-format       Format code with Black (inside container)"
	@echo "    make docker-lint         Run linter (ruff) with fixes (inside container)"
	@echo "    make docker-lint-check   Check code quality (inside container, no fixes)"
	@echo "    make docker-lint-safe    Auto-fix SAFE issues only (imports, upgrades)"
	@echo "    make docker-lint-full    Auto-fix ALL issues (verify tests after!)"
	@echo "    make docker-type-check   Run static type checking (inside container)"
	@echo "    make docker-check-all    Run all checks (inside container)"
	@echo ""
	@echo "  Docker Testing:"
	@echo "    make docker-test-unit    Run unit tests (inside container)"
	@echo "    make docker-test-cov     Run tests with coverage (inside container)"
	@echo "    make docker-pre-commit   Full pre-commit check (inside container)"
	@echo ""
	@echo "Development:"
	@echo "  make dev               Start development server (hot reload)"
	@echo "  make logs              Show Docker Compose logs"
	@echo "  make clean             Clean up cache files and build artifacts"
	@echo "  make pre-commit        Run full pre-commit check (ideal before git push)"
	@echo ""

install:
	pip install -r requirements.txt

install-dev:
	pip install -r requirements.txt -r requirements-dev.txt

# Docker installation commands
docker-install:
	docker exec summaraizer pip install -r requirements.txt
	@echo "✅ Runtime dependencies installed in container"

docker-install-dev:
	docker exec summaraizer pip install -r requirements.txt -r requirements-dev.txt
	@echo "✅ All dependencies installed in container (first time setup!)"

# Linting with Ruff
lint:
	ruff check app tests --fix

lint-check:
	ruff check app tests

# Safe fixes (import sorting, code modernization)
lint-safe:
	ruff check app tests --fix --select I,UP,F
	@echo "✅ Safe linting applied (imports, upgrades, undefined names)"

# Unsafe fixes (may affect logic) - review tests after!
lint-full:
	ruff check app tests --fix
	@echo "⚠️  Full linting applied - VERIFY TESTS: make docker-test-unit"

# Formatting with Black and Ruff
format:
	ruff check app tests --fix --select I
	black app tests

format-check:
	black --check app tests
	ruff check app tests

# Type checking with mypy
type-check:
	mypy app

# Combined checks
check-all: lint-check format-check type-check
	@echo "✅ All checks passed!"

# Testing
test:
	pytest tests/ -v

test-unit:
	pytest tests/unit/ -v --strict-markers

test-integration:
	pytest tests/integration/ -v --strict-markers

test-cov:
	pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

test-watch:
	pytest-watch tests/ -- -v

# Development server
dev:
	docker-compose up -d
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

logs:
	docker-compose logs -f

# Cleanup
clean:
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .pytest_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .mypy_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name .ruff_cache -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name htmlcov -exec rm -rf {} + 2>/dev/null || true
	find . -name .coverage -delete 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✨ Cleaned up cache and build artifacts"

# Pre-commit hook (run before pushing)
pre-commit: clean format-check type-check lint-check test-unit
	@echo "✅ Pre-commit checks passed! Ready to push."

# Alternative: if you want strict mode with -c flag
pre-commit-strict: clean
	black --check app tests || { echo "❌ Code formatting issues found. Run 'make format'"; exit 1; }
	ruff check app tests || { echo "❌ Linting issues found. Run 'make lint'"; exit 1; }
	mypy app || { echo "❌ Type checking failed"; exit 1; }
	pytest tests/unit/ -v || { echo "❌ Unit tests failed"; exit 1; }
	@echo "✅ All pre-commit checks passed!"

# Quick format & check
quick-fix: format lint test-unit
	@echo "✅ Quick fix complete!"

# ============================================================================
# DOCKER COMMANDS - Run inside the 'summaraizer' container
# ============================================================================
# These commands run inside the Docker container
# The venv is mounted as a volume, so dependencies are shared

docker-shell:
	docker exec -it summaraizer /bin/bash

docker-format:
	docker exec summaraizer bash -c "ruff check app tests --fix --select I && black app tests"
	@echo "✅ Code formatted inside container"

docker-lint:
	docker exec summaraizer ruff check app tests --fix

docker-lint-check:
	docker exec summaraizer ruff check app tests

# Safe linting only (imports, code upgrades, undefined names)
docker-lint-safe:
	docker exec summaraizer ruff check app tests --fix --select I,UP,F
	@echo "✅ Safe linting applied in container (imports, upgrades, undefined names)"

# Full linting with safety check
docker-lint-full:
	docker exec summaraizer ruff check app tests --fix
	@echo "⚠️  Full linting applied - VERIFY TESTS!"
	@echo "Run: make docker-test-unit"

docker-type-check:
	docker exec summaraizer mypy app

docker-check-all: docker-lint-check docker-format docker-type-check
	@echo "✅ All checks passed inside container!"

docker-test:
	docker exec summaraizer pytest tests/ -v

docker-test-unit:
	docker exec summaraizer pytest tests/unit/ -v --strict-markers

docker-test-integration:
	docker exec summaraizer pytest tests/integration/ -v --strict-markers

docker-test-cov:
	docker exec summaraizer pytest tests/ -v --cov=app --cov-report=term-missing --cov-report=html
	@echo "Coverage report generated in htmlcov/index.html"

docker-pre-commit: docker-format docker-lint-check docker-type-check docker-test-unit
	@echo "✅ Pre-commit checks passed inside container! Ready to push."
