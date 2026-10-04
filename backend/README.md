# EnSound UP payment backend skeleton

This separate FastAPI service exposes `GET /health`, `GET /health/db`, and `POST /orders`. It does not process payments or grant access. Render PostgreSQL is the planned authoritative store. The first migration creates the `orders` table.

## Run locally (Windows PowerShell)

From the repository root:

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Visit `http://127.0.0.1:8000/health`. The response is HTTP 200 with:

```json
{"status":"ok","service":"ensound-up-payment"}
```

`/health` works without a database. For database operations, set `DATABASE_URL` in the **backend process environment** to a real PostgreSQL connection URL using the `postgresql+psycopg://` driver (or `postgresql://`, which the app normalizes to psycopg 3). The values in `.env.example` are placeholders; the app does not load that file automatically. Do not commit actual credentials or place them in the frontend. Render should supply `DATABASE_URL` as a server-side secret environment variable.

For a local PostgreSQL database, set the variable in the same PowerShell session before starting the service, replacing each placeholder with your own values:

```powershell
$env:DATABASE_URL = 'postgresql+psycopg://USER:PASSWORD@HOST:5432/DBNAME'
```

With PostgreSQL reachable, visit `http://127.0.0.1:8000/health/db`. It runs `SELECT 1` and returns HTTP 200:

```json
{"status":"ok","database":"reachable"}
```

If the variable is absent or the database cannot be reached, `/health/db` returns HTTP 503 with only `{"detail":"Database unavailable"}`. It never returns the connection string or a raw database error; `/health` remains available.

## Pending orders (no checkout yet)

After applying the `orders` migration, send only the entitlement email:

```powershell
$body = @{ email = 'learner@example.com' } | ConvertTo-Json
Invoke-RestMethod -Uri 'http://127.0.0.1:8000/orders' -Method Post -ContentType 'application/json' -Body $body
```

Successful creation returns HTTP 201 with `order_id`, `entitlement_email`, `product_code`, `amount`, `currency`, `pricing_code`, `status` (`pending`), `created_at`, and `expires_at`. The backend fixes the product to `ensound_up_web_full_access`, prices in TWD using its own UTC clock, and expires an unpaid pending order two hours after creation. The email is checked for plausible structure, surrounding spaces are trimmed, and its domain is normalized; the mailbox portion, including dots and plus addressing, is preserved. Additional request fields such as amount, status, or product are rejected. Invalid input returns HTTP 422. Missing/unavailable database returns HTTP 503 without exposing credentials or SQL errors; no order is returned. This response does **not** grant Full Access.

Regular price is NT$199 (`regular`). To enable NT$99 (`launch_promo`), set both `LAUNCH_PROMO_START_AT` and `LAUNCH_PROMO_END_AT` in the **backend environment** to timezone-aware ISO 8601 timestamps exactly seven days apart. The interval includes the start and excludes the end. Missing, malformed, naive, reversed, or non-seven-day configuration safely uses NT$199. `.env.example` lists placeholder names only; the app does not automatically load it. Set and verify the real launch dates operationally; never take pricing from the browser.

For local automated checks, from `backend/` run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` in PowerShell. A successful persistence test requires a real **Development** PostgreSQL `DATABASE_URL`; mocked tests do not prove that a database commit worked. Repeated POST requests may create separate pending orders at this stage. Duplicate-submit/idempotency protection is required before Production payment initiation; this endpoint does not charge money.

## Migrations

From `backend/` with `DATABASE_URL` set, apply the reviewed `orders` migration and check the database revision using the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
```

The first revision is `1c_pending_orders`. Its downgrade drops the `orders` table and its data, so run `alembic downgrade base` only on a disposable database after reviewing the effect. Future schema changes can use `alembic revision --autogenerate -m "describe schema change"` with a reachable database; review the generated migration before applying it. `alembic.ini` contains no credentials; migration execution reads `DATABASE_URL` from the backend environment.

## Render configuration (future deployment)

Set the Render service root directory to `backend`. Use `pip install -r requirements.txt` as the build command and the following start command:

```sh
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set the health-check path to `/health`. This task does not deploy the service or configure credentials. CORS is not enabled; add only the specific frontend origin when a later browser API actually needs it.
