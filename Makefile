.PHONY: up down logs test be fe lint typecheck

up:
	docker compose up --build

down:
	docker compose down -v

logs:
	docker compose logs -f --tail=200

test:
	cd backend && pytest -q

be:
	cd backend && uvicorn app.main:app --reload --port 8000

fe:
	cd frontend && npm run dev

lint:
	cd backend && ruff check .
	cd frontend && npm run lint

typecheck:
	cd backend && mypy app
	cd frontend && npm run typecheck
