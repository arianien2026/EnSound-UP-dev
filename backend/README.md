# EnSound UP payment backend skeleton

This separate FastAPI service exposes `GET /health` and `GET /health/db`. It does not process payments or grant access. Render PostgreSQL is the planned authoritative store. The first migration creates the `orders` table; no order API or payment flow exists yet.

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
