# Pre Pro Post: backend

Django 5 + DRF, PostgreSQL, Redis, Celery. See `ARCHITECTURE.md` for design.

## Run

    cp .env.example .env
    make up            # api on :8000 (migrates and seeds crafts/locations on start)

## Develop

    make test          # pytest (needs Postgres)
    make fmt           # ruff format + fix
    pytest -k offers   # subset

Without Docker: create a Postgres DB, `pip install -r requirements/dev.txt`,
set `DATABASE_URL` and `DJANGO_SECRET_KEY`, then `python manage.py migrate && python manage.py seed_reference`.

## Endpoints available now

* `GET /api/bootstrap`: crafts, locations, enums (ETag + cache headers)
* `GET /healthz`, `GET /readyz`
* `GET /api/docs/`: OpenAPI UI
