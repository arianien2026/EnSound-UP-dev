# Controlled Development release candidate — not deployed

Only `arianien2026/EnSound-UP-dev`, `main`, baseline
`ca920c011fb2bfbd48398598ee3d06dc8857d490` is in scope. The exact 1C input
was compared byte-for-byte before edits, and preexisting changes were preserved.
No frontend or GitHub workflow file is changed. ECPay is the only payment provider.

## Confirmed release defects addressed

1. The current Render build command runs `alembic upgrade head`. The proposed
   build removes this step. Online migrations now fail before engine creation
   for every target except the preexisting strictly named local PostgreSQL test
   fixture. This protects both the frozen Neon schema and any old remote target.
   The nine opt-in local PostgreSQL tests retain their own server-identity guard.
2. The previous probe used nonexistent psycopg 3 APIs (`ConnectionInfo.ssl_in_use`
   and `PGconn.ssl_attribute`). It now checks supported `driver.pgconn.ssl_in_use`
   and effective `sslmode=verify-full`. It does not invent a TLS protocol value.
   Real Neon TLS remains NOT RUN until an authorized deployment succeeds.

The probe's default still requires all six business tables to be empty. The
proposed startup command explicitly allows counting existing rows so a later
restart is not tied to an empty database, while requiring the connection's
default transaction mode to be read-only. It never performs DML or DDL.
Post-release acceptance separately requires ZERO rows at this stage.

## Initial connection-acceptance mode

See `render-development-1d.json` for exact before/after commands and ordered
remote operations. `PGOPTIONS=-c default_transaction_read_only=on` keeps all
application database sessions read-only during this first controlled release.
The official connection URI must not contain options that override this default.
The startup probe refuses to launch Uvicorn if that default, TLS verification,
target, PG18.6, schema inventory, revision or ORM checks fail.

This is intentionally a **connection acceptance release**, not permission to
start taking orders, issue real entitlements, contact ECPay or send mail.
Do not remove the read-only setting without a separate authorization for a
later sandbox stage. Existing payment/email secrets are preserved; no new ones
are requested or copied for this connection stage.

## Prevent unintended deployments

The service currently auto-deploys on commits to main. Turn auto-deploy Off
before publishing anything, save the approved settings without deploying, then
merge only the four named environment keys. API environment updates require a
separate deploy to take effect. Do not use replace-all environment updates.

The repository also has a push-triggered GitHub Pages workflow. The proposed
single Development commit includes `[skip ci]` to skip that workflow; no frontend
deployment or workflow dispatch is authorized. Trigger only one explicit Render
deploy after settings and source have been verified. Leave auto-deploy Off.
No automatic retries or rollbacks with database side effects are authorized.

## Secret handling

`DATABASE_URL` is not in this candidate. After separate authorization, fetch it
with the Neon connector for the exact locked project/branch/database, retain it
only in execution memory, verify its public target fields without logging the
URI/password, and pass it directly into the Render secret-environment connector
with merge semantics. Do not print the connector's secret-bearing response.
No secrets go in Git, artifacts, command-line arguments, screenshots or chat.

## Verified read-only release gates

Dashboard authentication completed. Existing variable names were verified as:
`DATABASE_URL`, `ECPAY_STAGE_HASH_IV`, `ECPAY_STAGE_HASH_KEY`,
`ECPAY_STAGE_MERCHANT_ID`, `ECPAY_STAGE_RETURN_URL`, `EMAIL_VERIFY_BASE_URL`,
`RESEND_API_KEY`, `RESEND_FROM_EMAIL`. All values remained hidden; no secret was
copied or exported. No linked environment groups or secret files were present.

Hobby workspace, no card on file, current/projected monthly charges US$0;
pipeline 0/500 included minutes with a US$0 monthly spend limit; Free instance
hours 0.27/750 and bandwidth 0 MB/5 GB. The existing Starter build tier stays
unchanged; no upgrade, card, paid resource or increased limit is proposed.
Recheck remaining usage immediately before an authorized deployment.

`CLAIM_RATE_LIMIT_KEY` and `ENSOUND_DEV_FRONTEND_URL` are absent. They are not
needed for this read-only connection-acceptance release; configure them only in
the later separately authorized write-based Email Claim/frontend sandbox stage.
Do not claim live Email Claim readiness from the isolated tests.

Local verification: `python -m scripts.run_isolated_regressions` from `backend/`:
137 tests PASS, 0 failures/errors; nine real PostgreSQL fixture tests NOT RUN
because no isolated local PostgreSQL server is available. Tests block external
socket/psycopg connections. Exact patch apply and byte comparison are included in
the candidate evidence. Real Neon connection/TLS remains NOT RUN in this stage.

No remote mutation is authorized or performed under this preparation task.
The candidate is ready for a separate, explicit Development-only authorization.
