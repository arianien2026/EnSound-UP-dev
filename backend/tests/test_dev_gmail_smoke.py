"""Offline admission/replay tests. No Gmail network or database access."""
import base64
from datetime import datetime, timedelta, timezone
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from app.api import dev_gmail_smoke as s
from app.services.email_delivery import EmailConfig, EmailDeliveryError, _deliver

class SmokeTests(unittest.TestCase):
    def setUp(self):
        self.private=Ed25519PrivateKey.generate()
        self.run='a'*32
        self.pub=self.private.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex()
        self.env={'ENSOUND_GMAIL_SMOKE_ENABLED':'1','ENSOUND_GMAIL_SMOKE_PUBLIC_KEY':self.pub,
                  'ENSOUND_GMAIL_SMOKE_RUN_ID':self.run,
                  'ENSOUND_GMAIL_SMOKE_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(minutes=15)).isoformat(),
                  'RENDER_SERVICE_ID':'srv-db33dsm7bikc73bhukug','EMAIL_TRANSPORT':'gmail_api'}
        self.enterContext(patch.dict(os.environ,self.env,clear=True))
        self.cap=s.OneBootCapability();self.enterContext(patch.object(s,'capability',self.cap))
        app=FastAPI();app.include_router(s.router);self.client=TestClient(app)
        self.config=EmailConfig('',s.RECIPIENT,'https://ensound-up-dev.onrender.com','gmail_api','client','secret','refresh')
        self.config_mock=self.enterContext(patch.object(s,'get_email_config',return_value=self.config))
        self.delivery=self.enterContext(patch.object(s,'_deliver',return_value={'http_status':200,'message_id':'abc123'}))

    def proof(self, private=None):
        return {'signature':base64.b64encode((private or self.private).sign(self.cap.message(self.run))).decode()}

    def test_default_disabled_and_all_target_checks_fail_closed(self):
        for change in ({'ENSOUND_GMAIL_SMOKE_ENABLED':'0'},{'ENSOUND_GMAIL_SMOKE_PUBLIC_KEY':''},{'RENDER_SERVICE_ID':'other'}, {'EMAIL_TRANSPORT':'resend'},{'ENSOUND_GMAIL_SMOKE_EXPIRES_AT':'2000-01-01T00:00:00+00:00'}):
            with patch.dict(os.environ,change):
                self.assertEqual(self.client.get(s.PATH).status_code,404)
                self.assertEqual(self.client.post(s.PATH,json=self.proof()).status_code,404)
        self.delivery.assert_not_called()

    def test_wrong_key_cannot_send(self):
        self.assertEqual(self.client.post(s.PATH,json=self.proof(Ed25519PrivateKey.generate())).status_code,403)
        self.delivery.assert_not_called()

    def test_receiver_or_arbitrary_content_not_accepted(self):
        for extra in ({'recipient':'other@example.com'},{'text':'custom'},{'subject':'custom'}):
            self.assertEqual(self.client.post(s.PATH,json={**self.proof(),**extra}).status_code,422)
        self.delivery.assert_not_called()

    def test_fixed_content_and_exactly_one_attempt(self):
        payload=self.proof()
        self.assertEqual(self.client.post(s.PATH,json=payload).json()['result'],'SEND_PASS')
        self.assertEqual(self.client.post(s.PATH,json=payload).status_code,409)
        self.delivery.assert_called_once()
        body=self.delivery.call_args.args[0]
        self.assertEqual(body,{'from':s.RECIPIENT,'to':[s.RECIPIENT],'subject':s.SUBJECT,'text':s.TEXT})
        self.assertNotIn('token=',body['text'])

    def test_restart_replay_fails_even_with_same_verification_key(self):
        payload=self.proof();self.cap.consume(payload['signature'])
        restarted=s.OneBootCapability()
        self.assertNotEqual(restarted.boot,self.cap.boot)
        with self.assertRaises(HTTPException) as e:restarted.consume(payload['signature'])
        self.assertEqual(e.exception.status_code,403)

    def test_concurrent_requests_consume_before_delivery(self):
        signature=self.proof()['signature'];accepted=[]
        def attempt():
            try:self.cap.consume(signature);accepted.append(True)
            except HTTPException:pass
        threads=[threading.Thread(target=attempt) for _ in range(10)]
        for t in threads:t.start()
        for t in threads:t.join()
        self.assertEqual(len(accepted),1)

    def test_failure_does_not_retry_or_allow_second_request(self):
        self.delivery.side_effect=EmailDeliveryError(phase='gmail_send',http_status=403)
        payload=self.proof();result=self.client.post(s.PATH,json=payload).json()
        self.assertEqual(result,{'result':'FAIL','phase':'gmail_send','http_status':403,'message_id':None,'send_attempts':1})
        self.assertEqual(self.client.post(s.PATH,json=payload).status_code,409)
        self.delivery.assert_called_once()

    def test_wrong_sender_consumes_proof_without_send(self):
        self.config_mock.return_value=EmailConfig('','other@gmail.com',self.config.base_url,'gmail_api')
        self.assertEqual(self.client.post(s.PATH,json=self.proof()).json()['phase'],'configuration')
        self.delivery.assert_not_called()
        self.assertTrue(self.cap.used)

    def test_real_transport_errors_expose_only_phase_and_status(self):
        payload={'from':s.RECIPIENT,'to':[s.RECIPIENT],'subject':s.SUBJECT,'text':s.TEXT}
        for code in (400,403,429):
            with patch('app.services.email_delivery.urlopen',side_effect=HTTPError('https://oauth2.googleapis.com/token',code,'secret',{},io.BytesIO(b'refresh-secret'))):
                with self.assertRaises(EmailDeliveryError) as e:_deliver(payload,self.config)
                self.assertEqual(e.exception.phase,'oauth_refresh');self.assertEqual(e.exception.http_status,code)
                self.assertNotIn('secret',str(e.exception))

    def test_issuer_exits_without_persisting_private_key(self):
        script=Path(__file__).resolve().parents[1]/'scripts/issue_gmail_smoke_capability.py'
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d)
            process=subprocess.Popen([sys.executable,str(script)],cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while not (folder/'public.json').exists() and time.monotonic()<deadline:time.sleep(.02)
                public=json.loads((folder/'public.json').read_text())
                challenge={'run_id':public['run_id'],'boot_nonce':'b'*64}
                (folder/'challenge.json').write_text(json.dumps(challenge))
                out,err=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0);self.assertEqual(out,b'');self.assertEqual(err,b'')
                proof=json.loads((folder/'proof.json').read_text())
                message=('EnSound-Gmail-Smoke-v1\n'+public['run_id']+'\n'+'b'*64).encode()
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(public['public_key'])).verify(base64.b64decode(proof['signature']),message)
                self.assertEqual(set(p.name for p in folder.iterdir()),{'public.json','challenge.json','proof.json'})
                self.assertEqual((folder/'proof.json').stat().st_mode & 0o777,0o600)
            finally:
                if process.poll() is None:process.kill();process.communicate()

    def test_no_database_payment_or_proof_issuer_imports(self):
        source=Path(s.__file__).read_text()
        self.assertNotIn('app.database',source);self.assertNotIn('app.models',source)
        self.assertNotIn('send_claim_email',source);self.assertNotIn('ecpay',source.lower())

if __name__=='__main__':unittest.main()
