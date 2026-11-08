"""Sender migration tests with synthetic secrets and no outbound delivery."""
import io
import json
import os
import secrets
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from app.services.email_delivery import (
    EmailDeliveryError, get_email_config, send_verification_email,
    send_claim_email, send_release_email,
)


class SenderConfigTests(unittest.TestCase):
    def setUp(self):
        self.synthetic_key = secrets.token_urlsafe(32)
        self.enterContext(patch.dict(os.environ, {
            "RESEND_API_KEY": self.synthetic_key,
            "RESEND_FROM_EMAIL": "access@mail.example.com",
            "EMAIL_VERIFY_BASE_URL": "https://ensound-up-dev.onrender.com",
        }, clear=True))

    def test_verification_claim_and_release_share_the_approved_sender(self):
        config = get_email_config()
        token = secrets.token_urlsafe(32)
        cases = ((send_verification_email, "/orders/verify-email"),
                 (send_claim_email, "/access/claim"),
                 (send_release_email, "/access/release"))
        for send, path in cases:
            with self.subTest(template=send.__name__), patch(
                "app.services.email_delivery.urlopen",
                return_value=io.BytesIO(b'{"id":"synthetic-accepted"}'),
            ) as provider:
                send("uptools.support@gmail.com", token, config)
                request = provider.call_args.args[0]
                payload = json.loads(request.data)
                self.assertEqual(request.full_url, "https://api.resend.com/emails")
                self.assertEqual(request.get_method(), "POST")
                self.assertEqual(payload["from"], "access@mail.example.com")
                self.assertEqual(payload["to"], ["uptools.support@gmail.com"])
                self.assertIn(path + "#token=" + token, payload["text"])
                self.assertEqual(provider.call_args.kwargs["timeout"], 10)

    def test_provider_403_never_becomes_delivery_success_or_exposes_response(self):
        config = get_email_config()
        token = secrets.token_urlsafe(32)
        for send in (send_verification_email, send_claim_email, send_release_email):
            error = HTTPError("https://api.resend.com/emails", 403, "Forbidden", {},
                             io.BytesIO((self.synthetic_key + token).encode()))
            with self.subTest(template=send.__name__), patch(
                    "app.services.email_delivery.urlopen", side_effect=error):
                with self.assertRaises(EmailDeliveryError) as caught:
                    send("uptools.support@gmail.com", token, config)
                self.assertEqual(str(caught.exception), "Email delivery unavailable")
                self.assertNotIn(self.synthetic_key, repr(caught.exception))
                self.assertNotIn(token, repr(caught.exception))

    def test_config_representation_does_not_reveal_credentials(self):
        config = get_email_config()
        self.assertNotIn(self.synthetic_key, repr(config))
        self.assertNotIn(self.synthetic_key, str(config))


if __name__ == "__main__":
    unittest.main()
