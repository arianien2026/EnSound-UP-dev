# Email-ZeroCost-1A: Development candidate, not deployed

Baseline: arianien2026/EnSound-UP-dev main, 902207c4658dc6740c0b3a2817d645376432aca5.
Render verified Free/auto-deploy off; live dep-dbnuoejrjlhs739ufm5g at same SHA.
No commit, push, OAuth grant, configuration mutation, live send, database write,
order, payment or Production operation was performed for this candidate.

## Decision and limits

Use Gmail API over HTTPS 443 with the existing consumer Gmail sender
uptools.support@gmail.com. No custom sending domain, Workspace subscription,
SMTP App Password, paid email service or new dependency is required by this
transport. Ordinary recipients do not authorize OAuth and are not constrained by
Resend's sandbox recipient rule. Only send transactional mail they requested.
Google spam/abuse controls still apply; inbox placement is never guaranteed.
Render Free blocks SMTP 25/465/587, so SMTP is not the fallback.

Gmail send-only scope is sensitive, not an inbox-reading scope:
https://www.googleapis.com/auth/gmail.send
Only the sender authorizes access. Do not request Gmail read/modify/full-mail
scopes and do not reuse ChatGPT connector credentials. A consumer Gmail mailbox
is not a service account and must not be presented as the service-owned-data
verification exception. The single-sender setup may fit Google's limited-user
exception; that is a setup/release condition to confirm, not a claim of Google
approval for a publicly distributed OAuth app. Buyers never see this OAuth app.
If Google requires brand/scope verification for the chosen setup, stop release
and resolve that requirement; do not pretend Testing is a permanent solution.

External OAuth Testing refresh tokens with Gmail scope expire after seven days.
A short Development acceptance can use Testing with only the sender as test
user; ongoing operations require an appropriately published, policy-compliant
OAuth project. Publishing status and verification are separate concepts.
Password changes, revoked consent or Google's other token expiry rules can also
require reauthorization. None is silently bypassed by this code.

Consumer Gmail may block sending above 500 messages/day; this is an upper limit,
not a reserved transactional quota. API rate limits also apply. Google's current
quota docs list a free daily threshold of 80,000,000 units/project and send=100
units. Low-volume mail is far below that, but no linked billing account, trial,
quota purchase or payment method is authorized. If Console requires billing,
stop. Set restrictive available request quotas rather than relying on billing
alerts; recheck provider pricing before eventual public release.

## Implementation and safety

EMAIL_TRANSPORT defaults to resend, preserving existing deployments. Explicit
gmail_api selects the Google transport. Both verification, claim and release
messages use the same config. OAuth secrets are backend environment-only and
redacted from EmailConfig repr. Gmail mode does not read the Resend key.
Recipients must be single valid addresses; CR/LF injection is rejected before
network use. MIME uses Python's standard library and base64url encoding.
Fixed Google HTTPS endpoints use default TLS certificate/hostname verification.
Refresh and send each have 10-second timeouts. Access token is invocation-local.
Provider errors and malformed responses produce only a generic safe error.
A message id means provider acceptance, NOT confirmed recipient delivery.

No automatic send retry or cross-provider fallback is added: a timeout after
submission is ambiguous, so repeating it may send twice. Existing database-backed
claim cooldown/rate limits, one-use tokens and verification expiry remain intact.
No new durable delivery ledger/exactly-once guarantee is claimed. Follow existing
user-initiated claim retry/cooldown; do not blindly repeat create-order POSTs.
A failed order email remains pending/unverified; a failed Claim send does not
activate access. Claim endpoint's existing generic HTTP202 is not delivery proof.

E2E fixed-mailbox mode still requires exact sender pin, recipient pin, operator
key, expiry, Development database/TLS and Stage settings. Public buyer requests
cannot use this test mailbox mode to initiate email. No payment callback,
paid/entitlement/device rule, schema or frontend source was changed.
Claim email remains initiated by the existing /access/email-links workflow;
this candidate does not add an email side effect to the ECPay callback.

## Required setup (not performed; separate authorization)

1. Use/create a separate Development Google Cloud project with no billing/trial.
Enable Gmail API and configure OAuth consent accurately. Sender is the only
Google OAuth user; recipients need no OAuth account or Gmail address.
2. Register a supported OAuth client, obtain the sender's explicit offline
consent for gmail.send only, using the standard authorization-code flow with
state and an exact registered redirect (or installed-app loopback flow).
Do not use deprecated out-of-band grants. Only user performs any Google consent
or account security challenge. Never paste code, refresh token or secret in chat.
3. Securely provision Render backend-only values:
EMAIL_TRANSPORT=gmail_api
GMAIL_FROM_EMAIL=uptools.support@gmail.com
GMAIL_CLIENT_ID (secret value not supplied)
GMAIL_CLIENT_SECRET (secret)
GMAIL_REFRESH_TOKEN (secret)
EMAIL_VERIFY_BASE_URL=https://ensound-up-dev.onrender.com
Obtain the grant from that exact Gmail sender. Preserve all unrelated env values.
Do not put any of these credentials in VITE variables or static assets.
4. For a short authorized controlled-write acceptance, preserve the existing
startup safety gate and exact single-mailbox pins:
ENSOUND_DEV_E2E_EMAIL=uptools.support@gmail.com
ENSOUND_DEV_E2E_RESEND_OWNER_EMAIL=uptools.support@gmail.com
ENSOUND_DEV_E2E_APPROVED_FROM_EMAIL=uptools.support@gmail.com
The legacy RESEND_OWNER name is a recipient lock, not Gmail ownership proof.
Use existing short-lived key/expiry and authorized Development host. Do not
merely remove PGOPTIONS/read-only protection. No settings are enabled by ZIP.

## Minimal next acceptance and recovery

Needs one new authorization covering one Development commit/push, necessary
backend deployment/config, dedicated Google OAuth setup/consent, actual delivery
of one verification and one Claim email to the fixed mailbox, and short scoped
synthetic writes. One new pending synthetic order is needed ONLY to test actual
create-order automatic verification delivery; never submit it to ECPay. Reuse the
existing paid entitlement for Claim delivery; no new paid order or payment.
Do not register a third device (two already exist); if validating final device
redemption requires replacing/releasing an existing device, request that specific
action separately. Existing offline tests cover one-use redemption and device
registration. Check received mail and real proof redemption without printing
links/proofs. Do not call this full live acceptance until performed.

Before enabling writes, snapshot nonsensitive safety settings and exact rollback.
After testing restore read-only startup, PGOPTIONS read-only, E2E/buyer entry off,
operation key cleared/revoked and health endpoints healthy. Preserve records.
Transport rollback: EMAIL_TRANSPORT=resend or revert candidate release; Resend's
sandbox remains unable to deliver general-buyer mail, so rollback is safety-only.
No Production operation, new ECPay transaction or schema mutation is permitted.

## Official references (checked 2026-11-08)

- https://render.com/docs/free
- https://developers.google.com/workspace/gmail/api/guides/sending
- https://developers.google.com/workspace/gmail/api/auth/scopes
- https://developers.google.com/workspace/gmail/api/reference/quota
- https://support.google.com/mail/answer/22839
- https://developers.google.com/identity/protocols/oauth2
- https://developers.google.com/identity/protocols/oauth2/production-readiness/sensitive-scope-verification
- https://support.google.com/cloud/answer/13464323
