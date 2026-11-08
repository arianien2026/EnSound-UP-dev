# Gmail OAuth 1C — refresh-only temporary Development validation

Baseline: arianien2026/EnSound-UP-dev main ceed42dccabb7024e2a10f0e676eba060019376e.
Only srv-db33dsm7bikc73bhukug. Existing Google status In production is accepted
from the user's Console confirmation. No new OAuth consent or secret rotation.

## Execution and one-attempt control

There is NO new HTTP route. The app starts normally. When explicitly enabled
for this exact Render service, a daemon thread emits only a non-secret run ID
and cryptographically random boot nonce, then polls public approval metadata in
this same repository. Health/startup never waits for approval or token refresh.

The isolated issuer keeps a fresh Ed25519 private key only in RAM. Render gets
only its public verification key through temporary non-secret settings. The
issuer signs ONE run/boot challenge, produces public approval metadata, and exits
without serializing its private key. Verify issuer exit before publication.
The approval JSON contains identifiers and a public signature only; it contains
NO Google credentials, OAuth tokens, authorization headers or recoverable key
material. It is not accepted by any external HTTP endpoint. Publishing it in
Git authorizes only this exact already-running internal probe.

A new process/restart generates a different boot nonce, so it cannot use the old
approval. No second signature for the existing verification key can be produced
after the sole issuer exits. In-process locking consumes approval BEFORE the
Google request, including failure/timeouts. Memory is not the sole control.
The app cannot generate valid approvals itself. No durable Neon write or
Render filesystem counter is used or assumed. Repeated/restarted probes can
poll stale metadata but cannot repeat the OAuth exchange.

The OAuth code POSTs only to https://oauth2.googleapis.com/token, once, with no
redirects or retries and a 10-second timeout. Access token, client secret,
refresh token and raw response are never returned/logged/persisted. Only result,
HTTP status, safe category, token-format boolean and attempt count are emitted.
The existing Gmail transport is unchanged; no Gmail send endpoint, order,
claim/verification token issuer, payment, entitlement, device or DB API is used.
Normal existing read-only startup still performs its own non-mutating checks.

## Temporary settings and publication

Merge only ENSOUND_REFRESH_PROBE_ENABLED, ENSOUND_REFRESH_PROBE_EXPIRES_AT,
ENSOUND_REFRESH_PROBE_RUN_ID, ENSOUND_REFRESH_PROBE_PUBLIC_KEY. Exact current
Gmail environment variables must remain untouched. Default disabled; explicit
expiry <=60 minutes; operate within 30 minutes. Capture the automatic deployment
from an environment update and do not trigger an extra deploy for that change.
Confirm its exact code SHA before issuing approval.

After observing the public challenge in Render logs, sign it once using the
isolated issuer and confirm process exit. Publish docs/gmail-refresh-approval.json
in one additional commit, while auto-deploy stays OFF. This approval-only commit
must not trigger a redeploy (which would invalidate the boot-bound approval).
The background probe reads that public approval and makes at most one exchange.
If no result appears or it fails, DO NOT retry/sign again.

## Mandatory cleanup

Remove the four temporary settings with Dashboard Save only, preserving all
OAuth secrets. Remove public approval, temporary probe, issuer, tests and added
crypto dependency; restore app.main byte-for-byte to baseline, then deploy the
confirmed cleanup commit. Confirm normal health, read-only/TLS/ORM, unchanged
business counts, auto-deploy off and no refresh-probe import/config left.
Keep only non-sensitive execution evidence. No Production or other project use.

## Limits

PASS proves the current Render refresh token worked at the test time. It does
not prove immortality or retroactive extension of a Testing-issued token. A
format-valid Access Token is not a paid entitlement. Long-term reliability
remains PARTIAL; no token replacement is justified solely by uncertainty.
