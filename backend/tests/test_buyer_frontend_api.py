"""Real buyer HTTP contracts inside the controlled test scope; no provider calls."""
import os
import secrets
import importlib.util
from pathlib import Path
from fastapi.testclient import TestClient
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4

from sqlalchemy import select, func
from app.models import Order, Entitlement
import test_dev_e2e_scope as controlled


class BuyerHttpTests(controlled.existing.ECPayHttpChainTests):
    def setUp(self):
        self.run_id = 'E2E-' + uuid4().hex[:16]
        self.email = self.run_id.lower() + '@example.com'
        super().setUp()
        self.key = secrets.token_urlsafe(32)
        self.enterContext(patch.dict(os.environ, {
            'ENSOUND_DEV_E2E_ENABLED': '1', 'ENSOUND_DEV_E2E_KEY': self.key,
            'ENSOUND_DEV_E2E_RUN_ID': self.run_id, 'ENSOUND_DEV_E2E_EMAIL': self.email,
            'ENSOUND_DEV_E2E_EXPIRES_AT': (datetime.now(timezone.utc) + timedelta(hours=1)).isoformat(),
            'ENSOUND_DEV_DATABASE_HOST': controlled.DEV_HOST, 'ENSOUND_DEV_DATABASE_NAME': 'neondb',
            'ECPAY_STAGE_RETURN_URL': controlled.DEV_CALLBACK, 'ECPAY_STAGE_MERCHANT_ID': '3002607',
        }))
        self.enterContext(patch('app.dev_e2e.get_database_url', return_value=(
            f'postgresql+psycopg://synthetic:dummy@{controlled.DEV_HOST}/neondb?sslmode=verify-full&sslrootcert=system')))
        self.enterContext(patch.dict(os.environ, {
            'ENSOUND_DEV_E2E_BUYER_ENABLED': '1',
            'ENSOUND_DEV_FRONTEND_URL': 'http://localhost:5173/EnSound-UP-dev/',
        }))
        self.client.headers.pop('X-EnSound-E2E-Key', None)
        self.client.headers['Origin'] = 'http://localhost:5173'

    # Reuse the four payment HTTP-chain tests in this buyer-origin/no-key mode.
    test_order_email_checkout_callback_claim_device_full_access = controlled.existing.ECPayHttpChainTests.test_order_email_checkout_callback_claim_device_full_access
    test_invalid_signature_amount_and_simulated_result_never_issue_access = controlled.existing.ECPayHttpChainTests.test_invalid_signature_amount_and_simulated_result_never_issue_access
    test_interrupted_grant_keeps_paid_then_authenticated_retry_recovers = controlled.existing.ECPayHttpChainTests.test_interrupted_grant_keeps_paid_then_authenticated_retry_recovers
    test_pending_refund_preserves_access_completed_refund_denies_access = controlled.existing.ECPayHttpChainTests.test_pending_refund_preserves_access_completed_refund_denies_access

    def test_resend_owner_buyer_flow_still_requires_origin_email_and_payment_proof(self):
        self.email = 'owner@example.com'
        with patch.dict(os.environ, {'ENSOUND_DEV_E2E_EMAIL': self.email,
                'ENSOUND_DEV_E2E_RESEND_OWNER_EMAIL': self.email,
                'RESEND_FROM_EMAIL': 'onboarding@resend.dev'}):
            self.assertEqual(self.client.post('/orders', json={'email': 'other@example.com'}).status_code, 403)
            self.assertEqual(self.client.post('/orders', json={'email': self.email},
                headers={'Origin': 'https://other.invalid'}).status_code, 403)
            for path in ('/orders', '/access/email-links'):
                self.assertEqual(self.client.post(path, json={'email': self.email}).status_code, 403)
            self.delivery.assert_not_called()
            # Mail initiation is operator-only; verification, checkout, claim
            # redemption and devices remain the existing proof-based buyer flow.
            original_post = self.client.post
            def scoped_post(path, *args, **kwargs):
                if path in ('/orders', '/access/email-links'):
                    kwargs['headers'] = {**kwargs.get('headers', {}), 'X-EnSound-E2E-Key': self.key}
                return original_post(path, *args, **kwargs)
            with patch.object(self.client, 'post', side_effect=scoped_post):
                self.test_order_email_checkout_callback_claim_device_full_access()

    def test_buyer_order_status_is_minimal_and_never_proof_of_full_access(self):
        order_id, token = self.new_order()
        status = self.client.get(f'/orders/{order_id}/status')
        self.assertEqual(status.json(), {'status': 'pending', 'email_verified': False})
        self.assertEqual(status.headers['cache-control'], 'no-store')
        self.assertEqual(self.client.get('/access/status').status_code, 401)
        self.assertEqual(self.client.post('/orders/verify-email', json={'token': token},
                         headers={'Origin': 'https://ensound-up-dev.onrender.com'}).status_code, 200)
        self.assertTrue(self.client.get(f'/orders/{order_id}/status').json()['email_verified'])
        fields = self.client.post(f'/orders/{order_id}/checkout', json={}).json()['fields']
        self.assertEqual(fields['ClientBackURL'], 'http://localhost:5173/EnSound-UP-dev/')
        page = self.client.get('/orders/verify-email')
        self.assertIn('Return to EnSound UP', page.text)
        self.assertNotIn(token, page.text)
        self.assertEqual(self.client.get(f'/orders/{uuid4()}/status').status_code, 404)

    def test_buyer_cannot_exceed_order_mail_limit_or_use_other_origin(self):
        for origin in ('https://other.invalid', ''):
            self.assertEqual(self.client.post('/orders', json={'email': self.email},
                             headers={'Origin': origin}).status_code, 403)
        for _ in range(3):self.new_order()
        self.assertEqual(self.client.post('/orders', json={'email': self.email}).status_code, 429)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(Order)), 3)

    def test_scope_is_checked_for_verification_checkout_and_status(self):
        order_id, token = self.new_order()
        with self.factory.begin() as session:
            session.get(Order, order_id).entitlement_email = 'other@example.com'
        self.assertEqual(self.client.post('/orders/verify-email', json={'token': token}).status_code, 403)
        self.assertEqual(self.client.get(f'/orders/{order_id}/status').status_code, 403)
        with self.factory.begin() as session:session.get(Order, order_id).email_verified_at = datetime.now(timezone.utc)
        self.assertEqual(self.client.post(f'/orders/{order_id}/checkout', json={}).status_code, 403)

    def test_actual_startup_cors_accepts_buyer_device_headers_only_on_configured_origin(self):
        # Load a fresh app after the fixture config is set, just as Render does.
        spec = importlib.util.spec_from_file_location('buyer_cors_app',
            Path(__file__).resolve().parents[1] / 'app/main.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        with TestClient(module.app) as client:
            headers = {'Origin': 'http://localhost:5173', 'Access-Control-Request-Method': 'GET',
                       'Access-Control-Request-Headers': 'authorization,x-device-id'}
            reply = client.options('/access/status', headers=headers)
            self.assertEqual(reply.status_code, 200)
            self.assertEqual(reply.headers['access-control-allow-origin'], 'http://localhost:5173')
            self.assertEqual(client.options('/access/status', headers={
                **headers, 'Origin': 'https://other.invalid'}).status_code, 400)

    # Parent's operator-only tests are deliberately in its own class; use only
    # explicit buyer tests here rather than accidentally redefining their intent.
