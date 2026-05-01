.PHONY: install dev up down migrate test lint fmt clean

install:
	pip install -r requirements.txt -r requirements-dev.txt

up:
	docker compose up -d postgres redis

down:
	docker compose down

migrate:
	python -m app.database migrate

dev: up migrate
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

test:
	pytest -v --cov=app --cov-report=term-missing

test-unit:
	pytest -v -m "not integration"

lint:
	ruff check app tests

fmt:
	ruff format app tests

clean:
	docker compose down -v
	rm -rf .pytest_cache .coverage .ruff_cache .mypy_cache
