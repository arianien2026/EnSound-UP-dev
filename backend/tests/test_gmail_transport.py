"""Offline transport contract: synthetic credentials; never contact Google."""
import base64
import io
import json
import os
import unittest
from email import message_from_bytes, policy
from unittest.mock import patch
from urllib.error import HTTPError
from app.services.email_delivery import (get_email_config, EmailDeliveryError,
    send_verification_email, send_claim_email, send_release_email)

SCOPE = 'https://www.googleapis.com/auth/gmail.send'

class GmailTransportTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch.dict(os.environ, {
            'EMAIL_TRANSPORT':'gmail_api', 'GMAIL_FROM_EMAIL':'uptools.support@gmail.com',
            'GMAIL_CLIENT_ID':'synthetic.apps.googleusercontent.com',
            'GMAIL_CLIENT_SECRET':'synthetic-secret', 'GMAIL_REFRESH_TOKEN':'synthetic-refresh',
            'EMAIL_VERIFY_BASE_URL':'https://ensound-up-dev.onrender.com',
        }, clear=True))
        self.config = get_email_config()

    def grant(self, **override):
        return io.BytesIO(json.dumps(dict(access_token='synthetic-access', token_type='Bearer', scope=SCOPE, **override)).encode())

    def test_all_templates_use_google_https_and_one_recipient(self):
        for send, path in ((send_verification_email,'/orders/verify-email'),(send_claim_email,'/access/claim'),(send_release_email,'/access/release')):
            with self.subTest(template=send.__name__), patch('app.services.email_delivery.urlopen',side_effect=[self.grant(),io.BytesIO(b'{"id":"accepted"}')]) as net:
                send('buyer@example.com','synthetic-proof',self.config)
                refresh, delivery = [c.args[0] for c in net.call_args_list]
                self.assertEqual(refresh.full_url,'https://oauth2.googleapis.com/token')
                self.assertIn(b'grant_type=refresh_token',refresh.data)
                self.assertEqual(delivery.full_url,'https://gmail.googleapis.com/gmail/v1/users/me/messages/send')
                self.assertEqual(delivery.get_header('Authorization'),'Bearer synthetic-access')
                msg=message_from_bytes(base64.urlsafe_b64decode(json.loads(delivery.data)['raw']),policy=policy.default)
                self.assertEqual(msg['From'],self.config.sender)
                self.assertEqual(msg['To'],'buyer@example.com')
                self.assertIn(path+'#token=synthetic-proof',msg.get_content())
                self.assertNotIn('synthetic-secret',msg.as_string())
                self.assertNotIn('synthetic-refresh',msg.as_string())
                for call in net.call_args_list:
                    self.assertEqual(call.kwargs['timeout'],10)

    def test_invalid_recipient_fails_before_any_network(self):
        for recipient in ('bad', 'a@example.com\r\nBcc: b@example.com','a@example.com,b@example.com'):
            with self.subTest(recipient=recipient), patch('app.services.email_delivery.urlopen') as net:
                with self.assertRaises(EmailDeliveryError): send_claim_email(recipient,'proof',self.config)
                net.assert_not_called()

    def test_refresh_failure_stops_before_send_and_redacts(self):
        err=HTTPError('https://oauth2.googleapis.com/token',400,'synthetic-secret',{},io.BytesIO(b'synthetic-refresh'))
        with patch('app.services.email_delivery.urlopen',side_effect=err) as net:
            with self.assertRaises(EmailDeliveryError) as caught: send_claim_email('buyer@example.com','proof',self.config)
            self.assertEqual(str(caught.exception),'Email delivery unavailable')
            self.assertEqual(net.call_count,1)

    def test_send_failures_are_not_retried_or_fallback(self):
        for error in (TimeoutError('proof'),HTTPError('https://gmail.googleapis.com',403,'secret',{},None),HTTPError('https://gmail.googleapis.com',429,'quota',{},None)):
            with self.subTest(error=type(error).__name__), patch('app.services.email_delivery.urlopen',side_effect=[self.grant(),error]) as net:
                with self.assertRaises(EmailDeliveryError) as caught: send_verification_email('buyer@example.com','proof',self.config)
                self.assertEqual(str(caught.exception),'Email delivery unavailable')
                self.assertEqual(net.call_count,2)

    def test_malformed_acceptance_is_failure(self):
        with patch('app.services.email_delivery.urlopen',side_effect=[self.grant(),io.BytesIO(b'{}')]):
            with self.assertRaises(EmailDeliveryError): send_claim_email('buyer@example.com','proof',self.config)

    def test_broad_scope_and_bad_refresh_response_fail_closed(self):
        for grant in ({'access_token':'x','token_type':'Bearer','scope':SCOPE+' https://mail.google.com/'}, {}, {'access_token':'x\r\n','token_type':'Bearer'}):
            with patch('app.services.email_delivery.urlopen',return_value=io.BytesIO(json.dumps(grant).encode())) as net:
                with self.assertRaises(EmailDeliveryError): send_claim_email('buyer@example.com','proof',self.config)
                self.assertEqual(net.call_count,1)

    def test_configuration_is_redacted_and_resend_not_required(self):
        self.assertEqual(self.config.api_key,'')
        for value in ('synthetic-secret','synthetic-refresh'): self.assertNotIn(value,repr(self.config))

    def test_missing_or_injected_config_rejected(self):
        for name in ('GMAIL_CLIENT_ID','GMAIL_CLIENT_SECRET','GMAIL_REFRESH_TOKEN','GMAIL_FROM_EMAIL'):
            for value in ('','bad\r\ninjection'):
                with self.subTest(name=name,value=value),patch.dict(os.environ,{name:value}):
                    with self.assertRaises(EmailDeliveryError): get_email_config()

    def test_unknown_transport_fails_closed(self):
        with patch.dict(os.environ,{'EMAIL_TRANSPORT':'smtp'}):
            with self.assertRaises(EmailDeliveryError): get_email_config()
