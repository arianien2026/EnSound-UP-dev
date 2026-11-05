# EnSound UP Development backend

Recovered existing entitlement, Email Claim, device authorization/release and guarded recovery implementation, with the 1A Neon connection security fixes retained. **ECPay is the only approved payment provider.** The recovered checkout and callback currently use **ECPay Stage only**; no live payment or Production callback is authorized in this integration stage.

For controlled release preparation, read [release/RELEASE-1D.md](release/RELEASE-1D.md). Remote online migrations are disabled; only the existing isolated localhost PostgreSQL fixture is permitted.

The local migration head now matches the initialized Neon revision `3c3b_device_release`. **Do not run any upgrade, downgrade, stamp, autogenerate or create/drop-table operation against the initialized Neon database.** Read [NEON-DEVELOPMENT.md](NEON-DEVELOPMENT.md) for current execution results and the read-only application probe. Historical setup examples below are not authorization to change the Neon schema or send payments/emails.

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

Manual email delivery is outside this connection stage; automated delivery tests use mocks. The initialized Neon database must remain unchanged and empty. Reopening the link must not produce another verification. Do not use the real Production database or credentials for this check. Automated tests mock the mail service and do not send email.

Regular price is NT$199 (`regular`). To enable NT$99 (`launch_promo`), set both `LAUNCH_PROMO_START_AT` and `LAUNCH_PROMO_END_AT` in the **backend environment** to timezone-aware ISO 8601 timestamps exactly seven days apart. The interval includes the start and excludes the end. Missing, malformed, naive, reversed, or non-seven-day configuration safely uses NT$199. `.env.example` lists placeholder names only; the app does not automatically load it. Set and verify the real launch dates operationally; never take pricing from the browser.

For local automated checks, from `backend/` run `.\.venv\Scripts\python.exe -m unittest discover -s tests -v` in PowerShell. A successful persistence test requires a real **Development** PostgreSQL `DATABASE_URL`; mocked tests do not prove that a database commit worked. Repeated POST requests may create separate pending orders at this stage. Duplicate-submit/idempotency protection is required before Production payment initiation; this endpoint does not charge money.

## ECPay Stage checkout foundation

Set `ECPAY_STAGE_MERCHANT_ID`, `ECPAY_STAGE_HASH_KEY`, `ECPAY_STAGE_HASH_IV`, and `ECPAY_STAGE_RETURN_URL` in the **backend process environment**. The return URL must be a public HTTPS backend notification URL. Keep the Stage credentials server-side; do not put them in Git or the frontend. The checkout endpoint is fixed to ECPay's Stage URL and cannot be configured to use Production. Missing or invalid configuration fails closed. The sample values in the automated checksum vector are publicly published ECPay examples, not EnSound UP credentials.

`POST /orders/{order_id}/checkout` accepts an existing order UUID in the path and no payment fields in the body (an empty JSON object is also accepted). It rejects extra fields such as `amount`, `product_code`, `ReturnURL`, or `HashKey`. A pending, unexpired order with confirmed email is required. On success it returns `checkout_url` and signed `fields` for a browser to submit as an `application/x-www-form-urlencoded` POST form to ECPay Stage. The order's stored amount is used as an integer TWD amount. The `provider_order_id` stores one ECPay trade number per order, protected by its existing unique database constraint; repeated checkout-data requests for the same order reuse that trade number. If ECPay has already accepted a transaction with it, do not submit the same number as a new transaction; a new verified order is needed for another checkout attempt.

`POST /payments/ecpay/return` is the **server-to-server** ECPay Stage `ReturnURL`; configure `ECPAY_STAGE_RETURN_URL` to this public HTTPS backend path. It accepts form-encoded notifications, verifies their CheckMacValue with Stage credentials and matches MerchantID, MerchantTradeNo, and the stored TWD amount before updating an order. Only a genuine `RtnCode=1` notification with `SimulatePaid` absent or `0` can mark a pending or expired order paid. It stores ECPay's TradeNo, payment method and payment time, and replies with exactly `1|OK` after safe handling. A matching repeat is idempotent; simulation and failure notifications do not mark the order paid. Rejected/conflicting notifications and database failures do not receive that acknowledgement. There is no browser success callback on this path.

The two-hour `expires_at` blocks **new checkout**, not a verified payment from an existing checkout: a delayed authentic notification can mark the order paid without changing its original expiry. The recovered Stage callback commits verified payment state before attempting an idempotent entitlement grant. A failed grant is handled by the guarded recovery mechanism; a payment response alone is not Full Access proof. Do not invoke this flow against live endpoints during connection testing. Run the automated tests and validate the Stage callback end to end before any Production configuration; no real PostgreSQL or ECPay call is exercised by the unit tests. ECPay's All-in-One form specification, payment result and checksum documentation: <https://developers.ecpay.com.tw/2862/>, <https://developers.ecpay.com.tw/2878/>, and <https://developers.ecpay.com.tw/2902/>.

## Historical migration setup — not for initialized Neon

From `backend/` with `DATABASE_URL` set, apply the reviewed `orders` migration and check the database revision using the same virtual environment:

```powershell
.\.venv\Scripts\python.exe -m alembic upgrade head
.\.venv\Scripts\python.exe -m alembic current
.\.venv\Scripts\python.exe -m alembic heads
```

The first revision is `1c_pending_orders`; `1e_email_verification` adds verification fields; `2b_ecpay_stage_payment_result` adds nullable Stage payment identity fields and a unique provider TradeNo constraint. Do not downgrade a real Development database merely for testing: downgrades discard stored payment information, and `alembic downgrade base` drops the entire `orders` table. Future schema changes can use `alembic revision --autogenerate -m "describe schema change"` with a reachable database; review the generated migration before applying it. `alembic.ini` contains no credentials; migration execution reads `DATABASE_URL` from the backend environment.

## Render configuration (future deployment)

Set the Render service root directory to `backend`. Use `pip install -r requirements.txt` as the build command and the following start command:

```sh
python -m uvicorn app.main:app --host 0.0.0.0 --port $PORT
```

Set the health-check path to `/health`. This task does not deploy the service or configure credentials. CORS is not enabled; add only the specific frontend origin when a later browser API actually needs it.
