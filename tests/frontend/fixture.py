"""Loopback-only ASGI adapter with synthetic provider/email fixture controls.

Never run this fixture as a deployed service. Uses temporary SQLite only.
"""
import json
import os
from pathlib import Path
import socket
import signal
import sys
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest.mock import patch
from sqlalchemy import select, func

sys.path[:0] = [str(Path(__file__).resolve().parents[2] / 'backend'),
                str(Path(__file__).resolve().parents[2] / 'backend/tests')]
from test_buyer_frontend_api import BuyerHttpTests
from app.models import Order, Entitlement, AuthorizedDevice
from datetime import datetime, timedelta, timezone

fixture = None

def reset():
    global fixture
    if fixture: fixture.doCleanups()
    fixture = BuyerHttpTests(); fixture.setUp()
    return {'email': fixture.email}

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self): self.route()
    def do_POST(self): self.route()
    def reply(self, status, data):
        body = json.dumps(data).encode()
        self.send_response(status); self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(body))); self.end_headers(); self.wfile.write(body)
    def route(self):
        raw = self.rfile.read(int(self.headers.get('Content-Length','0')))
        if self.path == '/__fixture__/reset': self.reply(200,reset()); return
        if self.path == '/__fixture__/email': self.reply(200,{'token': fixture.delivered_token()}); return
        if self.path == '/__fixture__/pay':
            data=json.loads(raw);result=fixture.callback(data['fields'], **data.get('changes',{}))
            self.reply(result.status_code, {'ack': result.text}); return
        if self.path == '/__fixture__/counts':
            with fixture.factory() as s:
                self.reply(200,{name:s.scalar(select(func.count()).select_from(model))
                    for name,model in [('orders',Order),('entitlements',Entitlement),('devices',AuthorizedDevice)]})
            return
        if self.path == '/__fixture__/state':
            data=json.loads(raw)
            with fixture.factory.begin() as s:
                if data.get('refund'):
                    order=s.scalar(select(Order));order.status=data['refund']
                    if data['refund']=='refunded':
                        entitlement=s.scalar(select(Entitlement));entitlement.status='revoked'
                        entitlement.revoked_at=datetime.now(timezone.utc);entitlement.revocation_reason='synthetic_confirmed_refund'
                if data.get('expire_device'):
                    s.scalar(select(AuthorizedDevice)).credential_expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)
                if data.get('advance_claim'):
                    # Advance fixture time only, preserving real rate-limit rules.
                    if not hasattr(fixture,'advance_patch'):
                        fixture.advance_patch=fixture.enterContext(patch('app.api.access.datetime'))
                    fixture.advance_patch.now.return_value=datetime.now(timezone.utc)+timedelta(hours=int(data['advance_claim']))
            self.reply(200,{});return
        headers={k:v for k,v in self.headers.items() if k.lower() in ('origin','content-type','authorization','x-device-id')}
        result=fixture.client.request(self.command,self.path,content=raw or None,headers=headers)
        self.send_response(result.status_code)
        for key in ('content-type','cache-control','referrer-policy'):
            if key in result.headers:self.send_header(key,result.headers[key])
        self.send_header('Content-Length',str(len(result.content)));self.end_headers();self.wfile.write(result.content)

if __name__ == '__main__':
    def stop_fixture(*args):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop_fixture)
    reset()
    # TestClient is in-process; only Node's incoming loopback requests use sockets.
    def blocked(*a,**kw): raise AssertionError('Outbound access forbidden in buyer fixture')
    import psycopg,urllib.request
    with patch.object(psycopg,'connect',blocked),patch.object(socket.socket,'connect',blocked),patch.object(urllib.request,'urlopen',blocked):
        server=HTTPServer(('127.0.0.1',0),Handler)
        print(json.dumps({'port':server.server_port}),flush=True)
        try:server.serve_forever()
        finally:fixture.doCleanups();server.server_close()
