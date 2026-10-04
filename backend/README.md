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

Successful creation returns HTTP 201 with `order_id`, `entitlement_email`, `product_code`, `amount`, `currency`, `pricing_code`, `status` (`pending`), `created_at`, and `expires_at`, after Resend accepts the verification email. The backend fixes the product to `ensound_up_web_full_access`, prices in TWD using its own UTC clock, and expires an unpaid pending order two hours after creation. The email is checked for plausible structure, surrounding spaces are trimmed, and its domain is normalized; the mailbox portion, including dots and plus addressing, is preserved. Additional request fields such as amount, status, or product are rejected. Invalid input returns HTTP 422. Missing/unavailable database returns HTTP 503 without exposing credentials or SQL errors; no order is returned. This response does **not** grant Full Access.

## Email verification (Development)

Before POST `/orders`, set `RESEND_API_KEY`, `RESEND_FROM_EMAIL`, and `EMAIL_VERIFY_BASE_URL` in the **backend process environment**. For local testing, the base URL can be `http://127.0.0.1:8000` when the email link will be opened on the same machine. Use a sender allowed by your Resend Development account (its test sender where permitted); a Production sender/domain has not been chosen. Never print or commit the API key. Non-local verification base URLs must use HTTPS. `.env.example` contains only names and safe placeholders and is not loaded automatically.

On order creation the backend stores a SHA-256 digest of a random 32-byte token and a 30-minute token expiry. It commits the pending, **unverified** order, then asks Resend to send the verification link. The link contains the token in a URL fragment, which is not sent in an ordinary GET request or access log. A minimal same-origin page immediately removes the fragment from the address bar and POSTs the token in the request body to `/orders/verify-email`. A valid one-time token marks `email_verified_at` while the order remains pending and within its original two-hour expiry. The endpoint returns `{"status":"email_verified"}`; invalid, expired, or reused links return a safe error. The backend helper `is_payment_eligible` requires an existing, pending, unexpired, verified order before a future payment-initiation flow can use it. No payment or entitlement is created here.

If the email configuration is missing or invalid, POST `/orders` returns 503 **before** creating an order. If Resend rejects delivery or its response is unavailable **after** the database commit, the API returns 503 rather than claiming email was sent; that order remains unverified and expires normally. The learner may submit a new order request. There is no background queue or resend endpoint in this stage. A provider acceptance is not proof that the recipient received the message.

For a real manual Development check: apply the new migration to Development PostgreSQL, confirm `alembic current` shows `1e_email_verification`, configure the three server environment variables, POST an order using an email allowed by Resend, receive and open its link, and confirm `email_verified_at` is stored while status remains `pending`. Reopening the link must not produce another verification. Do not use the real Production database or credentials for this check. Automated tests mock the mail service and do not send email.

Regular price is NT$199 (`regular`). To enable NT$99 (`launch_promo`), set both `LAUNCH_PROMO_START_AT` and `LAUNCH_PROMO_END_AT` in the **backend environment** to timezone-aware ISO 8601 timestamps exactly seven days apart. The interval includes the start and excludes the end. Missing, malformed, naive, reversed, or non-seven-day configuration safely uses NT$199. `.env.example` lists placeholder names only; the app does not automatically load it. Set and verify the real launch dates operationally; never take pricing from the browser.

For local automated checks, from `backend/` run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` in PowerShell. A successful persistence test requires a real **Development** PostgreSQL `DATABASE_URL`; mocked tests do not prove that a database commit worked. Repeated POST requests may create separate pending orders at this stage. Duplicate-submit/idempotency protection is required before Production payment initiation; this endpoint does not charge money.

## Migrations

From `backend/` with `DATABASE_URL` set, apply the reviewed `orders` migration and check the database revision using the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
```

The first revision is `1c_pending_orders`; `1e_email_verification` adds nullable verification fields and a unique digest constraint without rewriting 1C or existing order rows. A downgrade removes verification state; `alembic downgrade base` drops the `orders` table and its data. Do not downgrade a real Development database merely for testing. Future schema changes can use `alembic revision --autogenerate -m "describe schema change"` with a reachable database; review the generated migration before applying it. `alembic.ini` contains no credentials; migration execution reads `DATABASE_URL` from the backend environment.

## Render configuration (future deployment)

Set the Render service root directory to `backend`. Use `pip install -r requirements.txt` as the build command and the following start command:

```sh
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set the health-check path to `/health`. This task does not deploy the service or configure credentials. CORS is not enabled; add only the specific frontend origin when a later browser API actually needs it.
