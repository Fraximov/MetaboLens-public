# Development and validation

Run commands from the repository root. Python 3.10+ is recommended; application
images use Python 3.12. Do not use another checkout's database, runtime or SIRIUS
home for tests.

## Source checks

```bash
python3 scripts/check_release.py
python3 -m unittest discover -s tests -v
python3 scripts/check_context_docs.py
node frontend/tests/test_eic_search.js
```

The release scan is for a clean source export. It intentionally rejects private
`.env` and runtime files after an installation has been configured.

## Container tests

Build only the public images after creating a private local configuration:

```bash
python3 scripts/configure_local.py --email developer@example.org
docker compose --env-file .env -f infra/compose/docker-compose.yml build
```

If `.env` already exists, omit the configuration command. The following tests
mount only source read-only, have no network, and use synthetic test credentials.
They neither start the app nor mount the Docker socket or runtime volumes.

```bash
docker run --rm --network none -v "$PWD:/app:ro" -w /app/backend -e PYTHONPATH=/app/backend:/app -e PYTHONDONTWRITEBYTECODE=1 -e APP_ENV=dev -e AUTO_SEED=true -e APP_SECRET_KEY=ci-only-not-a-deployment-secret-00000000 -e WORKFLOW_PIPELINE_ROOT=/app/workflows/nextflow4ms-dial-msdial5-clean metabolens-public-backend:latest python -m pytest -q -p no:cacheprovider tests

docker run --rm --network none -v "$PWD/frontend:/app:ro" -v "$PWD/workflows:/workflows:ro" -e PYTHONDONTWRITEBYTECODE=1 metabolens-public-frontend:latest python -m pytest -q -p no:cacheprovider tests

docker run --rm --network none -v "$PWD:/app:ro" -e PYTHONPATH=/app/backend:/app/worker -e PYTHONDONTWRITEBYTECODE=1 -e APP_ENV=dev -e AUTO_SEED=true -e APP_SECRET_KEY=ci-only-not-a-deployment-secret-00000000 metabolens-public-worker:latest python -m unittest discover -s /app/worker/tests -v
```

CI runs these suites and applies all Alembic migrations against disposable
PostgreSQL 16. Real scientific processing is a separate acceptance test requiring
fresh scientific images and permitted input data; unit tests do not establish
scientific accuracy. Optional acceptance tests report skips when tools/data are
unavailable.

## Configuration changes

Production is the default. The signing key is required. Demo seeding or a test
bearer token requires explicit `APP_ENV=dev` configuration and must never be used
for a shared deployment. The UI has no test-token shortcut.

Use migrations for schema changes. Input checksums, frozen workflow source and
review revisions are part of provenance; do not silently rewrite an existing run
when changing a scientific image or parameter schema.
