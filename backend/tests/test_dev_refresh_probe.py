"""Offline tests: never issue a real refresh, send mail or access a database."""
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
from unittest.mock import patch, Mock
from urllib.error import HTTPError
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
from app import dev_refresh_probe as p
from app.services.email_delivery import EmailConfig

class Response(io.BytesIO):
    status=200

class RefreshProbeTests(unittest.TestCase):
    def setUp(self):
        self.key=Ed25519PrivateKey.generate();self.run='a'*32
        self.env={'ENSOUND_REFRESH_PROBE_ENABLED':'1','ENSOUND_REFRESH_PROBE_PUBLIC_KEY':self.key.public_key().public_bytes(Encoding.Raw,PublicFormat.Raw).hex(),
                  'ENSOUND_REFRESH_PROBE_RUN_ID':self.run,'ENSOUND_REFRESH_PROBE_EXPIRES_AT':(datetime.now(timezone.utc)+timedelta(minutes=30)).isoformat(),
                  'RENDER_SERVICE_ID':'srv-db33dsm7bikc73bhukug','EMAIL_TRANSPORT':'gmail_api'}
        self.enterContext(patch.dict(os.environ,self.env,clear=True))
        self.probe=p.Probe()
        self.config=EmailConfig('','uptools.support@gmail.com','https://ensound-up-dev.onrender.com','gmail_api','client','client-secret','refresh-secret')

    def approval(self):
        message=(p.PREFIX+'\n'+self.run+'\n'+self.probe.boot).encode()
        return {'run_id':self.run,'boot_nonce':self.probe.boot,'signature':base64.b64encode(self.key.sign(message)).decode()}

    def test_mock_exchange_only_fixed_token_endpoint_and_redacted_result(self):
        reply={'access_token':'access-secret','token_type':'Bearer','expires_in':3599,'scope':'https://www.googleapis.com/auth/gmail.send'}
        capture=io.StringIO()
        with patch.object(p,'build_opener') as net,patch('sys.stdout',capture),patch('sys.stderr',capture):
            net.return_value.open.return_value=Response(json.dumps(reply).encode())
            result=p.exchange_only(self.config)
            net.return_value.open.assert_called_once()
            req=net.return_value.open.call_args.args[0]
            self.assertEqual(req.full_url,'https://oauth2.googleapis.com/token')
            self.assertEqual(req.get_method(),'POST');self.assertIn(b'grant_type=refresh_token',req.data)
            self.assertEqual(result['result'],'PASS');self.assertTrue(result['access_token_format_valid'])
            self.assertEqual(capture.getvalue(),'')
            for secret in ('access-secret','client-secret','refresh-secret'):self.assertNotIn(secret,json.dumps(result))

    def test_http_errors_do_not_read_or_emit_response_body(self):
        for code in (400,401,403,429,500):
            body=Mock()
            with patch.object(p,'build_opener') as net:
                net.return_value.open.side_effect=HTTPError(p.TOKEN_URL,code,'access-secret',{},body)
                result=p.exchange_only(self.config)
                self.assertEqual(result['http_status'],code);self.assertEqual(result['error_type'],'oauth_http_error')
                body.read.assert_not_called();net.return_value.open.assert_called_once()
                self.assertNotIn('secret',json.dumps(result))

    def test_timeout_does_not_retry(self):
        with patch.object(p,'build_opener') as net:
            net.return_value.open.side_effect=TimeoutError('refresh-secret')
            result=p.exchange_only(self.config)
            self.assertEqual(result['result'],'FAIL');self.assertIsNone(result['http_status'])
            net.return_value.open.assert_called_once();self.assertNotIn('secret',json.dumps(result))

    def test_invalid_token_response_rejected(self):
        base={'access_token':'synthetic-access','token_type':'Bearer','expires_in':3600}
        for update in ({'access_token':''},{'access_token':'bad\r\nsecret'},{'token_type':'other'},{'expires_in':0},{'scope':'https://mail.google.com/'}):
            with patch.object(p,'build_opener') as net:
                net.return_value.open.return_value=Response(json.dumps({**base,**update}).encode())
                self.assertFalse(p.exchange_only(self.config)['access_token_format_valid'])

    def test_redirect_is_never_followed(self):
        with self.assertRaises(ValueError):p.NoRedirect().redirect_request(None,None,302,'',{},'https://other.invalid')

    def test_default_disabled_no_thread_no_network(self):
        for settings in ({'ENSOUND_REFRESH_PROBE_ENABLED':'0'},{'ENSOUND_REFRESH_PROBE_ENABLED':''}):
            with patch.dict(os.environ,settings),patch.object(p.threading,'Thread') as thread,patch.object(p,'exchange_only') as exchange:
                self.probe.start();self.probe.run();thread.assert_not_called();exchange.assert_not_called()

    def test_invalid_service_expiry_public_key_or_run_fail_closed(self):
        for changes in ({'RENDER_SERVICE_ID':'other'},{'ENSOUND_REFRESH_PROBE_EXPIRES_AT':'2000-01-01T00:00:00+00:00'}, {'ENSOUND_REFRESH_PROBE_PUBLIC_KEY':''},{'ENSOUND_REFRESH_PROBE_RUN_ID':'bad'},{'EMAIL_TRANSPORT':'resend'}):
            with patch.dict(os.environ,changes):self.assertIsNone(self.probe.settings())

    def test_start_never_waits_and_starts_only_one_daemon(self):
        with patch.object(p.threading,'Thread') as thread:
            self.probe.start();self.probe.start()
            thread.assert_called_once();self.assertTrue(thread.call_args.kwargs['daemon'])
            thread.return_value.start.assert_called_once()

    def test_approval_bound_to_run_boot_and_signing_key(self):
        approval=self.approval()
        for change in ({'run_id':'b'*32},{'boot_nonce':'b'*64},{'signature':base64.b64encode(b'bad').decode()}):
            self.assertFalse(self.probe.consume({**approval,**change}))
        self.assertTrue(self.probe.consume(approval));self.assertFalse(self.probe.consume(approval))

    def test_restart_same_settings_cannot_replay_prior_approval(self):
        approval=self.approval();self.assertTrue(self.probe.consume(approval))
        restarted=p.Probe();self.assertNotEqual(restarted.boot,self.probe.boot)
        self.assertFalse(restarted.consume(approval))

    def test_concurrent_approval_consumed_once(self):
        approval=self.approval();results=[]
        threads=[threading.Thread(target=lambda:results.append(self.probe.consume(approval))) for _ in range(10)]
        for thread in threads:thread.start()
        for thread in threads:thread.join()
        self.assertEqual(results.count(True),1)

    def test_run_refreshes_once_even_after_failure(self):
        approval=self.approval();logs=[]
        with patch.object(self.probe,'read_approval',return_value=approval),patch.object(p,'get_email_config',return_value=self.config),patch.object(p,'exchange_only',return_value={'result':'FAIL','http_status':400}) as exchange,patch.object(p,'emit',side_effect=logs.append):
            self.probe.run();self.probe.run()
            exchange.assert_called_once();self.assertTrue(self.probe.consumed)
            self.assertEqual(logs[-1]['result'],'FAIL')
            self.assertFalse(self.probe.consume(approval))

    def test_no_public_routes_send_calls_or_database_access(self):
        source=Path(p.__file__).read_text()
        for text in ('@router','@app','send_verification_email','send_claim_email','messages/send','app.database','app.models','ecpay'):
            self.assertNotIn(text,source)

    def test_public_approval_metadata_parsed_without_oauth_secret(self):
        approval=self.approval()
        with patch.object(p,'build_opener') as net:
            net.return_value.open.return_value=Response(json.dumps({'content':base64.b64encode(json.dumps(approval).encode()).decode()}).encode())
            self.assertEqual(self.probe.read_approval(),approval)
            request=net.return_value.open.call_args.args[0]
            self.assertEqual(request.full_url,p.APPROVAL_URL);self.assertIsNone(request.get_header('Authorization'))

    def test_single_signature_issuer_exits_no_private_key_file(self):
        script=Path(__file__).resolve().parents[1]/'scripts/issue_gmail_refresh_approval.py'
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);process=subprocess.Popen([sys.executable,str(script)],cwd=d,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            try:
                deadline=time.monotonic()+5
                while not (folder/'public.json').exists() and time.monotonic()<deadline:time.sleep(.02)
                data=json.loads((folder/'public.json').read_text())
                (folder/'challenge.json').write_text(json.dumps({'run_id':data['run_id'],'boot_nonce':'b'*64}))
                out,err=process.communicate(timeout=5)
                self.assertEqual(process.returncode,0);self.assertEqual(out,b'');self.assertEqual(err,b'')
                approval=json.loads((folder/'approval.json').read_text())
                message=(p.PREFIX+'\n'+data['run_id']+'\n'+'b'*64).encode()
                Ed25519PublicKey.from_public_bytes(bytes.fromhex(data['public_key'])).verify(base64.b64decode(approval['signature']),message)
                self.assertEqual(set(x.name for x in folder.iterdir()),{'public.json','challenge.json','approval.json'})
                self.assertEqual(set(approval),{'run_id','boot_nonce','signature'})
            finally:
                if process.poll() is None:process.kill();process.communicate()

if __name__=='__main__':unittest.main()
