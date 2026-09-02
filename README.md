# SlotBook

SlotBook is an API-only demonstrator for appointment and service-slot booking.

## Status

The S3 local acceptance surface is implemented. OpenAPI/Swagger and the local
readiness endpoint are available; external deployment remains deferred to later
phases.

The planned technical baseline is Django, Django REST Framework, PostgreSQL, JWT authentication, drf-spectacular, and pytest-django. The demonstrator is intended to show role and ownership controls, transaction-safe double-booking protection, OpenAPI documentation, critical-path tests, and synthetic demo data.

SlotBook is an internal demonstrator. It is not client work, a market-validated product, or evidence of users, revenue, conversion, or business outcomes.

## Repository boundary

This repository is reserved for deployable application source, migrations, tests,
configuration examples, synthetic demo data, and public-safe technical
documentation. It must not contain credentials, private strategy, employment
information, prospect data, unpublished claims, or internal acceptance reports.

## Local setup

The pinned baseline is Python 3.13.x, Django 5.2.17, Django REST Framework
3.17.2, djangorestframework-simplejwt 5.5.1, drf-spectacular 0.30.0,
psycopg[binary] 3.3.5, pytest 9.1.1, and pytest-django 4.14.0.

```bash
python3.13 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
# Replace the placeholder secret, then load local environment variables.
set -a
. .env
set +a
python manage.py migrate
python manage.py seed_demo
SLOTBOOK_TEST_DB=sqlite python -m pytest
DJANGO_SECRET_KEY=local-validation-secret-key-32bytes SLOTBOOK_TEST_DB=sqlite \
  python manage.py spectacular --validate
```

By default, Django expects PostgreSQL 18.x using the `DB_*` variables shown in
`.env.example`. For model-only tests when PostgreSQL is unavailable, use the
explicit lightweight fallback `SLOTBOOK_TEST_DB=sqlite python -m pytest`.
SQLite is not evidence for PostgreSQL locking or contention behavior; no such
evidence is claimed by the local S2 checks. The PostgreSQL contention harness is
`tests/test_postgres_contention.py`; run it only with a live PostgreSQL
database using `pytest -m postgres_contention`. It is skipped when PostgreSQL
is unavailable, and SQLite cannot substitute for row-locking evidence.

The seed command creates only deterministic synthetic identities
`demo-provider` and `demo-customer`, one service, and two future slots. It does
not create real contacts or credentials.

For a local Swagger demonstration, choose a temporary synthetic password and
pass it explicitly to the seed command:

```bash
python manage.py seed_demo --password 'choose-a-local-demo-password'
```

The option changes only the local synthetic users and does not create public
demo credentials. Do not use a real password.

## Local API acceptance

Run the complete local checks with the explicit SQLite fallback:

```bash
DJANGO_SECRET_KEY=local-validation-secret-key-32bytes SLOTBOOK_TEST_DB=sqlite \
  python manage.py check
DJANGO_SECRET_KEY=local-validation-secret-key-32bytes SLOTBOOK_TEST_DB=sqlite \
  python manage.py makemigrations --check --dry-run
DJANGO_SECRET_KEY=local-validation-secret-key-32bytes SLOTBOOK_TEST_DB=sqlite \
  python -m pytest -q
```

Start the local interface to inspect the generated documentation and readiness
status:

```bash
DJANGO_SECRET_KEY=local-validation-secret-key-32bytes SLOTBOOK_TEST_DB=sqlite \
  python manage.py runserver 127.0.0.1:8000
curl -fsS http://127.0.0.1:8000/api/health/
curl -fsS http://127.0.0.1:8000/api/schema/
# Open http://127.0.0.1:8000/api/docs/ in a browser.
```

`/api/health/` returns only `{"status":"ready"}` or
`{"status":"not_ready"}` and is intended for the local interface. It does not
require JWT authentication and never returns database diagnostics or secrets.

## S3 API boundary

This delivery contains the frozen JWT, Provider, Customer discovery, and
booking endpoints, generated OpenAPI at `/api/schema/`, Swagger UI at
`/api/docs/`, and local readiness at `/api/health/`. It does not include
registration, role management, booking mutation, notification delivery,
frontend, payments, multitenancy, and deployment.
