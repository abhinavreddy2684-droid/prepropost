.PHONY: up down migrate seed test lint fmt shell schema schema-check
up:       ; docker compose up --build
down:     ; docker compose down
migrate:  ; docker compose run --rm api python manage.py migrate
seed:     ; docker compose run --rm api python manage.py seed_reference
test:     ; docker compose run --rm api pytest
lint:     ; ruff check . && ruff format --check .
fmt:      ; ruff format . && ruff check . --fix
shell:    ; docker compose run --rm api python manage.py shell
# OpenAPI contract. `schema` regenerates openapi.yaml; `schema-check` fails if it is stale or invalid.
# Both use test settings so the output does not depend on a local .env or a running database.
SCHEMA_ENV = DJANGO_SETTINGS_MODULE=config.settings.test
schema:       ; $(SCHEMA_ENV) python manage.py spectacular --validate --fail-on-warn --file openapi.yaml
schema-check: ; f=$$(mktemp) && $(SCHEMA_ENV) python manage.py spectacular --validate --fail-on-warn --file $$f && diff -u openapi.yaml $$f; rc=$$?; rm -f $$f; exit $$rc
