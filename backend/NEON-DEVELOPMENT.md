# Neon application integration — Development only

Use the existing initialized `ensound-up-payment-dev` project, branch `br-sparkling-grass-b8kgshc7`, database `neondb`. The branch display name `production` is a Neon branch name only. No application deployment or environment cutover is included in this patch.

## Configuration

Supply `DATABASE_URL` privately in the backend process environment. Never put it in frontend files, Git, command-line arguments, reports, screenshots, or test logs. The application does not load `.env.example` automatically.

For Neon hosts, `get_database_url()` enforces `sslmode=verify-full` and defaults `sslrootcert=system`. A supplied `require` or `verify-ca` mode is strengthened; `disable`, `allow`, `prefer`, duplicate modes, and target-overriding libpq query parameters are rejected. An explicit CA path is preserved. Invalid certificates fail closed; there is no insecure fallback. The libpq build must support `sslrootcert=system`; use a supported psycopg binary build or explicitly provide your trusted CA file if necessary.

Set these non-secret target locks in this Development process:

```text
ENSOUND_DEV_DATABASE_HOST=ep-sparkling-smoke-b80uupgp.c-14.us-east-1.aws.neon.tech
ENSOUND_DEV_DATABASE_NAME=neondb
```

The Neon connection tool may return the `-pooler` hostname. That is a distinct connection hostname even when it belongs to the same endpoint. This stage selects the verified direct hostname above and preserves the role credentials. Do not remove the target lock to accept an unexpected host. The application never rewrites endpoint hosts automatically.

Production secrets and all payment/email credentials must remain absent from the integration process. Database logging hides SQL parameters. Health checks return a generic 503 on connection failure.

## Read-only runtime check

From `backend/`, with the secret already injected into the process:

```text
python -m scripts.check_neon_readonly --expected-host ep-sparkling-smoke-b80uupgp.c-14.us-east-1.aws.neon.tech
```

The checker uses the actual application's SQLAlchemy engine, psycopg driver, session factory, and all six business models. It verifies the effective host/database and client TLS mode, uses a READ ONLY transaction, checks PG18.6, table inventory and revision, issues an ORM SELECT for each model, counts the six business tables, and rolls back. It never invokes Alembic migrations or creates rows. It emits only controlled JSON fields; failure details and secrets are withheld. Exit 0 means PASS; exit 2 means incomplete/failed integration. A session SAVEPOINT only supports read-only session cleanup; it is not authorization for synthetic writes.

The checker also reports missing local Alembic revisions and missing entitlement/device models. It also requires all six business tables to remain empty. Database success alone does not validate external payments or email delivery.

## Backend recovery and actual 1B results

Source repository: `arianien2026/EnSound-UP-dev`, `main`, `ca920c011fb2bfbd48398598ee3d06dc8857d490`. Remote main was freshly checked and remains at this commit. The previously modified 1A workspace was preserved before integration and its files matched the exact `EnSound-Neon-App-Integration-1A(1).zip` input.

Recovery source: existing `EnSound-Neon-Rebuild-1B-validation.zip`, nested `inputs/candidate.zip`, SHA-256 `7dbfde6d6f43ab927761795fec820f0657a21247d1d507322fe97731fd8f78ac`. Its manifest identifies the existing ECPay-1D Decision B / R3-6E-5D permit-aware fallback. The original Stage-2G-11 package remains unrecovered; this is not that original package. Backend source and tests were recovered byte-for-byte without changing entitlement, refund or device-limit rules. No frontend files were integrated.

Recovered modules include entitlement grant and lookup, Email Claim, online Full Access verification, device registration, the three-device limit, 30-day device release cooldown, and explicit permit-guarded recovery. ECPay is the only approved provider. The current implementation retains its ECPay Stage payment eligibility checks; live ECPay checkout/callback validation is not part of this stage. A 30-day online credential lifetime is not an offline Full Access cache TTL; this stage does not implement or extend offline access.

Actual results:

- 128 local regression tests PASS (125 recovered/1A regressions plus 3 new real ASGI/SQLite integration tests); one PostgreSQL test class (9 tests) skipped because there is no isolated local PostgreSQL server. SQLite and fake-session tests do not establish PostgreSQL locking/concurrency correctness.
- Tests were rerun with sockets and psycopg connections blocked, and inherited database/payment/email configuration removed from the test process. Temporary SQLite fixtures contain synthetic data only. Email deliveries and provider interactions are mocked.
- Actual Uvicorn loopback startup PASS; `/health` HTTP 200; `/health/db` HTTP 503 with generic `Database unavailable`.
- Approved Neon direct hostname DNS lookup failed with `EAI_AGAIN` (errno -3). TCP, TLS handshake/certificate validation and PostgreSQL authentication could not run. The probe used dummy credentials after DNS failed; no real secret was fetched, logged or packaged in this stage. No other configured and authorized backend execution environment was available through the connected capabilities; the SQL connector cannot host a Python backend.
- The recovered application's six actual ORM-generated PostgreSQL SELECT statements were executed through Neon SQL tools successfully, returning zero rows. All 56 ORM columns matched the actual Neon columns, types and nullability. This is server-side SQL/model compatibility evidence; direct backend engine/session integration is still NOT RUN.
- Local Alembic revision graph loads successfully and has the sole head `3c3b_device_release`, matching the actual Neon revision. No migration was executed against Neon.
- Read-only Neon inspection confirmed `neondb`, server version `180006`, and zero records in orders, entitlements, authorized_devices, email_claim_tokens, email_claim_sessions and email_claim_rate_limits.
- Schema changes, Neon business writes, real payments, emails, deployment, commit/push and Production access: NONE.

Run the isolated local regressions from `backend/`:

```text
python -m pip install -r requirements-dev.txt
python -m scripts.run_isolated_regressions
```

The implementation is integrated in an isolated Development clone only. The remaining connection action is to run the existing read-only probe in an authorized Development runtime whose network permits DNS and TCP 5432 to the exact endpoint. Supply the connection secret through that runtime's existing secret mechanism; do not put it in chat, arguments, logs or Git. Do not bypass certificate validation, hardcode IP addresses, or alter the schema to work around the network limitation. No deployment or paid resource is authorized by this package.

## Integration 1C execution update

The exact 1B input was compared byte-for-byte with this working backend before any edits. Backend recovery and schema checks were reused, not repeated. The approved payment provider is ECPay only.

Four added actual ASGI/SQLite tests exercise the entire order -> email verification -> checkout -> signed synthetic ECPay callback -> committed paid state -> entitlement -> Email Claim -> device -> online Full Access chain. The successful path mocks only the outbound email transport; routes, order/payment persistence, signature checking, grant transactions and access decisions execute the existing code. It never follows the returned checkout URL or calls an external payment endpoint. Interrupted entitlement delivery is injected in one failure test, followed by an authenticated synthetic callback retry. Bad MAC, wrong amount, simulated/failed payment, proof replay and client Full Access flags fail closed. Pending refunds preserve access; a local confirmed-refund fixture prevents access and regrant. No external refund API is invoked or verified.

SQLite loses timezone offsets; the test fixture restores its known UTC/order and Asia/Taipei/provider timestamps. This fixture adaptation is not a PostgreSQL implementation fix and does not establish PostgreSQL row-lock or concurrency correctness.

Render read-only inventory verified the existing Development Python web service `srv-db33dsm7bikc73bhukug`, linked to `arianien2026/EnSound-UP-dev`, `main`, root `backend`, Free plan, and automatic deploy enabled. Its latest live deploy is `dep-db3omdjncjis73b8gjdg` at `ca920c011fb2bfbd48398598ee3d06dc8857d490`; it does not contain these local recovered changes. Current connected Render tools expose no remote command execution. Official documentation confirms Free web services do not support SSH/Dashboard shell or one-off jobs (https://render.com/docs/free and https://render.com/docs/ssh, checked 2026-11-03). An SSH address field in service metadata does not prove eligibility on Free. No SSH attempt, deploy, environment-variable update or Render database query was performed.

Current Work DNS lookup for the exact approved Neon hostname still fails with EAI_AGAIN before TCP/TLS/authentication. Using Render for this backend would therefore require separate authorization to publish the reviewed Development source and configure only that service's Neon secret and target locks; no such mutation was performed. An ordinary redeploy of the old GitHub commit would not deploy this recovered implementation. Free-plan budget/usage must remain within the strict zero-cost boundary before any future deploy is authorized/executed.

1C final combined regression run: 132 tests PASS, 0 failures/errors; the existing local PostgreSQL class containing 9 tests remains skipped because no isolated PostgreSQL server is available. The socket/psycopg-blocked runner was used. Actual Uvicorn /health returned 200; /health/db for the approved Neon target returned 503. Final read-only Neon counts remained zero in all six business tables.

## 1D release preparation

See [release/RELEASE-1D.md](release/RELEASE-1D.md) and the exact proposed configuration JSON. The actual Render build command was found to include `alembic upgrade head`; removing it is mandatory before publishing the candidate. Online migration execution is now blocked before connection except for the existing strictly named local PostgreSQL test fixture. No Neon migration ran.

The probe's psycopg TLS API usage was corrected to `driver.pgconn.ssl_in_use` after direct inspection of installed psycopg 3.3.7 showed the earlier attributes do not exist. Effective `verify-full`, target locking and credential redaction remain required. No live TLS PASS is inferred from this API correction.

Two explicit startup switches are available: `--allow-existing-business-rows` retains row counts without failing only because rows exist, and `--require-read-only-default` additionally requires `default_transaction_read_only=on`. The default standalone acceptance probe still requires zero rows. The initial proposed Render acceptance configuration sets `PGOPTIONS=-c default_transaction_read_only=on`, runs the probe, then starts Uvicorn; it never runs migrations. Final acceptance still checks that all six business tables remain empty. This read-only deployment mode does not authorize live payment or email traffic.

The service has automatic deploys on commits, and the repo has a GitHub Pages push workflow. The exact proposed release therefore pauses Render auto-deploy, uses a single `[skip ci]` Development commit, and requests one manual Render deploy after source/settings verification. No remote mutation has occurred. Dashboard authentication is now complete; existing environment key names and zero-cost usage/limits were verified read-only. No values were revealed. The exact observations and proposed operations are in release/render-development-1d.json. The 1D suite passed 137 tests with no failures; nine local PostgreSQL tests remain NOT RUN. No release was executed.
