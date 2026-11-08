# Development Gmail smoke test — temporary release

Baseline: arianien2026/EnSound-UP-dev main 8fa76719c7a9660454cb194fed672ae393a5dfc7.
Only srv-db33dsm7bikc73bhukug; Free, auto-deploy off, existing read-only startup.
No database calls, new orders, valid claim/verification tokens or payment calls.

## Single-attempt proof across processes

An isolated issuer generates a fresh Ed25519 private key IN RAM and exposes only
its public verification key. It waits for the deployed service's 256-bit random
boot nonce, signs exactly one (protocol, run-id, boot-nonce) message, writes the
signed capability outside Git with mode 0600, and terminates. Never serialize,
back up or retain the private key. Verify issuer process exit before invoking
POST. Do not restart an issuer under the same configuration or issue another key.

Render receives ONLY the public verification key, not the private key. After an
instance restart or on another worker, a new random boot nonce makes the old
capability cryptographically invalid. No fresh valid signature can be created
for that nonce after the issuer terminates. Concurrent replay in the original
worker is rejected by a lock consumed BEFORE calling any provider, including
failures/timeouts. Thus the memory flag is NOT the sole restriction: it works
with a boot-bound capability and destruction of the only signing key. No
persistent Render disk, mutable DB record, or Gmail deduplication assumption is
used. This is a controlled test protocol, not a reusable production mail queue.

The private signed proof is authentication material: never print it, put it in
Git/logs/URLs or include it in a report. A lost response must NOT be retried.
HTTP GET challenge is non-sensitive and cannot send. HTTP POST accepts only the
signature, never recipient/content. Default disabled, expiring activation,
exact Render service, Gmail transport and sender/base URL checks fail closed.

## Temporary environment changes

Merge only ENSOUND_GMAIL_SMOKE_ENABLED, ENSOUND_GMAIL_SMOKE_EXPIRES_AT,
ENSOUND_GMAIL_SMOKE_RUN_ID and ENSOUND_GMAIL_SMOKE_PUBLIC_KEY. Public key is not
secret; the signing key never enters Render. No Gmail OAuth secret is read,
changed, downloaded or logged by the operator. Existing app uses its config.
Use a <=30 minute test window; code refuses >60 minutes. Keep read-only startup
and PGOPTIONS untouched. Update may deploy automatically: capture its deployment
and do not also trigger another deployment. Verify exact commit before POST.

Only one POST will be made. Mail recipient and sender are fixed to
uptools.support@gmail.com. Subject: EnSound UP Development Gmail Test.
Body is fixed, has no proof, payment information, customer data or valid token.
Safe result includes phase, HTTP status, attempt count, Gmail Message ID only.
Existing verification/claim templates and authorization rules are unchanged.

## Cleanup regardless of result

Delete the four temporary environment settings (OAuth secrets untouched),
remove the temporary router/issuer/test dependency and revert temporary transport
diagnostics. Deploy the confirmed cleanup commit; verify both route methods are
unavailable, health endpoints 200, auto-deploy off, read-only startup PASS and
unchanged business row counts. Destroy the local proof after recording only
non-sensitive results. Never claim inbox delivery from API acceptance alone.

## Local checks

Default disabled; wrong key/target/expiry; arbitrary recipient/content rejected;
concurrent replay; replay on a new boot; failed attempt consumed; exact fixed
content; HTTP status redaction; isolated issuer process exit with no private-key
file; no database/payment/token-issuer imports; existing mail regression suite.
