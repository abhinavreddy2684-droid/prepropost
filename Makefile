.PHONY: up down migrate seed test lint fmt shell
up:       ; docker compose up --build
down:     ; docker compose down
migrate:  ; docker compose run --rm api python manage.py migrate
seed:     ; docker compose run --rm api python manage.py seed_reference
test:     ; docker compose run --rm api pytest
lint:     ; ruff check . && ruff format --check .
fmt:      ; ruff format . && ruff check . --fix
shell:    ; docker compose run --rm api python manage.py shell
