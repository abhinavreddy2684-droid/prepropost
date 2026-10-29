.PHONY: up debug down migrate seed test lint fmt shell schema schema-check
# Test settings need no database, env vars or Docker, so the spec is identical everywhere.
SPECTACULAR = DJANGO_SETTINGS_MODULE=config.settings.test python manage.py spectacular --validate --fail-on-warn

up:       ; docker compose up --build
debug:    ; docker compose -f docker-compose.yml -f docker-compose.debug.yml up --build
down:     ; docker compose down
migrate:  ; docker compose run --rm api python manage.py migrate
seed:     ; docker compose run --rm api python manage.py seed_reference
test:     ; docker compose run --rm api pytest
lint:     ; ruff check . && ruff format --check .
fmt:      ; ruff format . && ruff check . --fix
shell:    ; docker compose run --rm api python manage.py shell
schema:   ; $(SPECTACULAR) --file openapi.yaml
schema-check: ; @tmp=$$(mktemp) && $(SPECTACULAR) --file $$tmp && \
	{ diff -u openapi.yaml $$tmp || { echo "openapi.yaml is out of date: run 'make schema' and commit it."; exit 1; }; }
