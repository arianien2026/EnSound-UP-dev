# EnSound UP Web Full Access — Payment and Entitlement Architecture

Status: authoritative v1 specification for the Development repository. This document defines the payment and entitlement boundary; it does not describe an implemented checkout. Learning functionality and the existing FREE three-question quota are frozen.

## 1. Purpose and scope

Sell EnSound UP Web Full Access without a traditional member/account center. Associate a verified purchase with an entitlement email and authorize up to three devices. Payment, entitlement, device authorization, and refund handling require a backend; a browser redirect or local setting cannot establish ownership. Production implementation and release are separate future tasks.

## 2. Frozen commercial decisions

| Item | v1 decision |
| --- | --- |
| Provider | ECPay / 綠界科技; NewebPay is only a possible future fallback |
| Product | EnSound UP Web Full Access |
| Regular price | NT$199 |
| Launch promotion | NT$99 for a seven-day time window, evaluated on the backend |
| Promotion type | Time-limited, never a first-N-customers or quantity-based offer |
| Purchase scope | The purchaser retains the Full Access scope included with the purchased version; future major products, major expansions, and unrelated features are not promised free |
| Refund request target | Within seven days under the published customer-facing refund policy |
| Support | uptools.support@gmail.com |

The current Development product-info UI still says “first 50 purchases NT$99.” It is stale relative to this specification. Correct and verify that copy during payment implementation before taking real orders; do not treat it as the pricing rule.

Frozen v1 implementation choices: the trusted backend runs on **Render**, with **Render PostgreSQL** as the authoritative persistent store for orders, entitlements, and authorized devices. **Resend** delivers system-generated transactional email; `uptools.support@gmail.com` remains the support contact. A pending unpaid order expires after **two hours**. Device identity uses a random browser-generated Device ID, and offline Full Access uses a backend-signed entitlement token. ECPay integration starts in its **Test** environment and moves to Production configuration only after end-to-end validation passes.

## 3. Trust boundaries

- The backend owns product identity, price, promotion window, order status, entitlement, device slots, and revocation. The browser can request a purchase and display server-reported status, but cannot grant Full Access or submit an authoritative price.
- ECPay's **validated server-to-server notification** is the payment-success signal. A browser return URL, success page, screenshot, or client assertion is not sufficient.
- An authenticated, server-issued device credential may cache authorization temporarily. An editable frontend boolean or permanent localStorage value is never proof of purchase.
- Persist the order's authoritative charged price and promotion decision. Do not recompute an existing order's price from the browser clock or a later page load.

## 4. Purchase flow

1. Customer enters an entitlement email and repeats it in a confirmation field. A mismatch must be corrected before order creation; the customer may edit the email before payment.
2. Backend validates the request, determines the product and current server-side promotion eligibility, creates a pending order tied to the confirmed email, stores its price and unique order identifier, and prepares the ECPay transaction.
3. Browser follows the backend-provided ECPay checkout flow. The backend verifies ECPay's server notification and payment details against its pending order.
4. After verified payment, backend marks the order paid and grants the server-side Full Access entitlement. The browser looks up entitlement status; a return page may show a pending state while notification processing completes.
5. An authorized device receives a bounded credential and periodically revalidates. The client enables paid learning access only from valid server-backed entitlement state.

The current Paywall's “解鎖完整版 / Unlock Full Version” primary action is a placeholder. A future payment task must connect it to this flow while preserving the existing Paywall close behavior and frozen learning limits.

## 5. Pending order model

Store at least: unique internal order ID, provider transaction reference(s), product/version scope, confirmed entitlement email, currency (TWD), authoritative charged amount, regular or promotion price basis, creation and expiration timestamps, promotion eligibility decision, provider/payment status, and transition/audit timestamps. Keep enough information to reconcile a notification, a delayed browser return, and support cases.

An order starts pending and expires **two hours after creation if unpaid**. Its backend-stored price remains fixed during that window; a subsequent promotion expiry must not silently recalculate it. An expired unpaid order cannot simply be reused: a later purchase attempt creates a new order and re-evaluates promotion eligibility and price. New orders created after the promotion ends use NT$199. A successfully verified paid order is not invalidated merely because its original two-hour pending window later passes. The handling of an order still unpaid at expiration that later receives a purported paid notification requires an explicit reconciliation rule before implementation; never grant access from an unverified or mismatched payment. Use unique references and idempotent transitions so duplicate notifications cannot create duplicate entitlements or charges in local records.

Render PostgreSQL stores the authoritative order, entitlement, and authorized-device records. Exact schema, migrations, and reconciliation fields are implementation details.

Record the seven-day promotion's start and end as authoritative server timestamps with an explicit time zone or UTC representation. The server checks whether an order qualifies at creation. Operations must set and verify the actual launch window before enabling the offer; the browser clock and submitted amount have no authority.

## 6. ECPay integration boundary

The trusted backend runs on Render and holds provider credentials; it creates the provider request using the stored order amount and identifier. Integrate against the **ECPay Test environment first**, complete end-to-end payment validation, record PASS, and only then configure Production. Do not use real Production transactions for initial development. Keep Test and Production endpoints, credentials, and callback/return URLs separate and outside client bundles. Exact ECPay SDK/library versus direct protocol implementation and deployment configuration remain implementation decisions; validate the chosen integration against current provider documentation before writing code. Merchant credentials, HashKey, HashIV, or equivalent secrets must stay in secure server-side environment/secrets configuration, never in Git or React bundles and never supplied by the browser.

The callback handler must authenticate/validate the notification according to the selected ECPay mode, match the merchant/order and provider references, verify amount and currency, and accept only a genuine successful payment result. Log enough to investigate rejected or repeated notifications without exposing credentials or full sensitive payment data. A browser redirect is informational and should query backend order status.

## 7. Payment success verification

Make payment recording and entitlement grant an idempotent, auditable server-side operation. On verified success, move the order to paid and create or activate the purchased product-scope entitlement for its email. A duplicate valid notification should return the already established outcome. An invalid, incomplete, mismatched, or unconfirmed result must not activate access and should enter a support/reconciliation path. Never call `setAccessLevel('full')` from a success redirect as a purchase grant.

## 8. Entitlement model

Represent an entitlement server-side with a stable identifier, email, purchased product/version scope, source paid order, state, activation/revocation timestamps, and reason/audit reference. At minimum distinguish **pending** (verified payment/activation reconciliation, if needed), **active**, and **revoked**. An order has its own pending/paid/refunded status; do not conflate order and entitlement states. Optional internal states must not broaden the purchase promise.

Entitlement lookup and device authorization must be authenticated to the entitlement email through a mechanism chosen before implementation. Do not expose an entitlement merely because someone types a known email. Revoke only on a confirmed completed refund or another verified support decision, with an audit trail. Server responses and any cached credential must reflect the purchased scope rather than all future products by default.

## 9. Email rules

Use two email fields before payment and require confirmation. The backend binds the confirmed email to the pending order; do not accept a client-only display email as entitlement evidence. **Resend** delivers transactional email; the exact verified sender domain/address and DNS setup, plus the proof-of-email-ownership flow without accounts, remain to be specified before implementation. The customer support mailbox is not the transactional delivery service.

Before payment, the customer can correct the email normally. After payment but before activation, support may correct it after verifying the relevant order/payment information. After activation there is no self-service email change; support may handle verified exceptional cases such as an obvious typo, a permanently unavailable mailbox, or an unusable work/school account. Use only the minimum necessary verification. Never ask for a full card number, password, unnecessary identity documents, or unrelated sensitive information. Preserve the order-to-entitlement audit trail when a correction is approved.

## 10. Device authorization

One active entitlement authorizes at most **three** devices. On first activation, the browser generates a random Device ID and stores it locally for future recognition. The backend associates this ID with the entitlement, registers and counts authorized devices, and refuses a fourth unless a slot is released. Do not derive identity from screen size, installed fonts, hardware characteristics, or any other browser fingerprinting signals. Clearing browser/site storage may create a new Device ID; device release and verified support exceptions provide recovery. Exact local storage mechanism, server-side representation/hashing, registration authentication, and recovery proof remain implementation details. A locally editable Device ID identifies a request; it never grants access by itself.

The customer/support v1 release flow can release one device. After a normal release, record a **30-day cooldown** before another normal release. A verified exceptional support case may override this manually with a reason and audit entry. Registration, release, and the cooldown check must be server-enforced; a client refresh must not reset them.

## 11. Offline entitlement and revalidation

Only the trusted backend may issue a **backend-signed offline entitlement token** to an authorized device. It must bind to the entitlement, purchased product scope, authorized device, and expiry, and be verifiable without treating editable browser state such as `full=true` as authority. It may remain valid offline for at most **seven days**; the v1 target is seven days, with a shorter lifetime allowed if implementation constraints demand it. The local cache is not permanent ownership proof. The exact signed-token format (JWT or otherwise), signing algorithm, key storage/rotation, claims, and local storage mechanism belong to implementation/security design.

After expiry, reconnect and revalidate with the backend before continuing Full Access. Revocation/refund must be reflected at online revalidation, and an offline credential can remain usable only until its bounded expiry. Define issuance/replay protection, clock handling, and storage security before implementation.

## 12. Refund and revocation rules

Customers may submit a refund request within seven days according to the published refund policy. **Requested** or **pending** refund is not a completed refund: continue Full Access while the money has not actually been returned. Once the refund is confirmed/completed, record that outcome and revoke the associated entitlement, including subsequent device authorization; cached offline access expires within the bounded TTL. Escalate ambiguous or exceptional cases to manual support rather than automatically revoking access. Reconcile duplicate charges and partial/failed provider events against authoritative payment records before acting.

## 13. Customer support and manual exceptions

Use `uptools.support@gmail.com` for v1 support; Resend is the separate transactional email delivery service. No full admin dashboard is required for initial MVP. Support may handle email correction, exceptional device release, paid-but-missing access, duplicate charges, refund issues, and entitlement repair/recovery after verification.

Future tooling or a controlled manual procedure needs minimum audit information: order and provider reference, paid amount/currency and timestamp, masked/confirmed entitlement email, entitlement state and scope, device slot/release history and cooldown, refund status, actor, reason, timestamp, and before/after state for manual changes. Restrict access to this information and avoid collecting full card numbers or unrelated documents.

## 14. Development versus Production

`DevAccessSwitch` is a Development test mechanism and must not ship as a Production paid-unlock route. Production must not accept `setAccessLevel('full')` or an equivalent client-side switch as proof of payment; use server-verified entitlement and device authorization instead. Remove or disable the global `🧪 EnSound UP · Development` label during Production release preparation. Keep ECPay Test credentials and development callback/configuration separate from Production. The backend on Render and its PostgreSQL records are trusted; the frontend remains separate and untrusted for paid access decisions. Never commit ECPay or token-signing secrets.

Before Production release, explicitly verify the removal of development unlock paths and label, real checkout/callback and entitlement lookup, provider environment configuration, the current seven-day promotion copy, the refund/support copy, and Paywall action/close behavior. Repository separation alone does not perform these checks.

## 15. Security invariants

- Client-provided price, promotion status, paid status, entitlement status, device count, and browser time are never authoritative.
- Only a validated ECPay server notification can make an order paid and grant Full Access; redirects cannot.
- Match provider notification to an existing pending order's merchant/order reference, currency, and stored amount; handle retries idempotently.
- Keep provider secrets, credential signing material, and authoritative state on the backend; issue only bounded credentials to authorized devices.
- Reject access on revoked, expired, mismatched, or unauthorized credentials; log state changes needed for reconciliation and support.
- Do not change the frozen FREE learning quota or rely on `DevAccessSwitch` in Production.

## 16. Implementation phases

1. Resolve the remaining lower-level decisions below. Design the Render backend, PostgreSQL schema/migrations, Resend sender setup, and order/entitlement state transitions.
2. Implement double-entry email intake and server-owned pending orders with a two-hour unpaid expiry, fixed price, and backend-controlled seven-day promotion window.
3. Integrate ECPay **Test** checkout and validated server notification; reconcile paid orders and issue persistent entitlements. Make retries idempotent and complete end-to-end validation before Production configuration.
4. Implement entitlement lookup and three-device authorization using random browser-generated Device IDs, release cooldown, backend-signed offline token, and revalidation. Connect the Paywall purchase action without changing learning quota semantics.
5. Establish manual support/refund/revocation procedure, verify Production separation and commercial copy, and test success, failure, retry, refund, expiry, and device-limit paths before accepting payments.

## 17. Open decisions

| Decision | Type | Required before |
| --- | --- | --- |
| Handling an expired unpaid order with a delayed purported payment notification | Implementation / operational | Callback and reconciliation |
| Exact promotion start/end timestamps and time zone (seven-day duration is frozen) | Launch operations | Enabling promotion |
| Exact framework/language and Render service/callback configuration | Implementation | Backend work |
| PostgreSQL schema/migrations and backup/reconciliation procedure | Implementation | Orders and entitlements |
| Resend verified sender domain/address, DNS, and proof-of-email-ownership flow without accounts | Implementation / product UX | Entitlement activation/recovery |
| Device ID local storage/server representation, registration authentication, and recovery proof | Implementation / product UX | Device authorization |
| Signed-token format, algorithm, key storage/rotation, exact claims, storage, and clock handling | Implementation / security | Offline access |
| ECPay SDK/library versus direct protocol, exact Test/Production credentials and deployment configuration | Implementation / deployment | Provider integration |
| Entitlement API endpoints, environment-variable names, and logging/audit record details | Implementation | Backend and operations |
| Exact manual support procedure and published refund wording | Operations / product copy | Accepting payments |

The two-hour order expiry, Render hosting and PostgreSQL, Resend delivery, random Device ID, backend-signed offline token, and ECPay Test-first progression are frozen. The remaining entries specify implementation/security or launch-operational details, not alternative product/vendor choices.
