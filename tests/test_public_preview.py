"""Temporary sharing authenticates assets and RPC before reaching the local app."""
import base64
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
from pathlib import Path
import threading
import secrets

import pytest

spec = importlib.util.spec_from_file_location('preview', Path(__file__).parents[1] / 'tools/public_preview.py')
preview = importlib.util.module_from_spec(spec)
spec.loader.exec_module(preview)


class Echo(BaseHTTPRequestHandler):
    def do_POST(self):
        body = self.rfile.read(int(self.headers.get('Content-Length', '0')))
        result = json.dumps({'headers':dict(self.headers), 'body':body.decode()}).encode()
        self.send_response(200)
        self.send_header('Content-Length', str(len(result)))
        self.end_headers()
        self.wfile.write(result)

    def log_message(self, *_):
        pass


@pytest.fixture
def gateway():
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Echo)
    public = ThreadingHTTPServer(('127.0.0.1', 0), preview.PreviewGateway)
    public.authorization = 'Basic ' + base64.b64encode(b'student:test-only-password').decode()
    public.access_token = secrets.token_urlsafe(32)
    public.require_auth = True
    public.public_url = 'https://test.trycloudflare.com'
    public.upstream_port = upstream.server_port
    for server in (upstream, public):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    yield public
    for server in (public, upstream):
        server.shutdown()
        server.server_close()


@pytest.mark.parametrize('path', ['/', '/app.js', '/api/rpc'])
def test_every_route_requires_authentication(gateway, path):
    connection = HTTPConnection('127.0.0.1', gateway.server_port)
    connection.request('GET', path)
    response = connection.getresponse()
    assert response.status == 401
    assert response.getheader('WWW-Authenticate').startswith('Basic ')
    connection.close()


def test_public_rpc_rewrites_only_valid_origin_and_does_not_leak_access_password(gateway):
    headers = {'Host':'test.trycloudflare.com', 'Origin':gateway.public_url,
               'Authorization':gateway.authorization, 'Content-Type':'application/json',
               'X-SparkTutor-Session':'a' * 32, 'X-SparkTutor-Token':'test-token'}
    connection = HTTPConnection('127.0.0.1', gateway.server_port)
    connection.request('POST', '/api/rpc', '{}', headers)
    response = connection.getresponse()
    assert response.status == 200
    result = json.loads(response.read())
    assert result['headers']['Origin'] == f'http://127.0.0.1:{gateway.upstream_port}'
    assert result['headers']['X-SparkTutor-Session'] == 'a' * 32
    assert 'Authorization' not in result['headers']
    connection.close()
    headers['Origin'] = 'https://unexpected.example'
    connection = HTTPConnection('127.0.0.1', gateway.server_port)
    connection.request('POST', '/api/rpc', '{}', headers)
    assert connection.getresponse().status == 403
    connection.close()


def test_password_login_issues_a_session_so_subresources_can_authenticate_without_basic_header(gateway):
    connection=HTTPConnection('127.0.0.1',gateway.server_port)
    connection.request('POST','/api/rpc','{}',{'Authorization':gateway.authorization})
    response=connection.getresponse()
    assert response.status==200
    cookie=response.getheader('Set-Cookie')
    assert 'HttpOnly' in cookie and 'SameSite=Strict' in cookie
    response.read();connection.close()
    # A webview may send the Basic header only on the navigation, then use cookies.
    connection=HTTPConnection('127.0.0.1',gateway.server_port)
    connection.request('POST','/api/rpc','{}',{'Cookie':cookie.split(';',1)[0]+'; sparktutor_login=application-session'})
    response=connection.getresponse()
    assert response.status==200
    result=json.loads(response.read())
    assert 'Authorization' not in result['headers']
    assert preview.ACCESS_COOKIE not in result['headers'].get('Cookie','')
    assert 'sparktutor_login=application-session' in result['headers']['Cookie']
    connection.close()


def test_forged_or_previous_gateway_session_cannot_bypass_password(gateway):
    for value in ('fake','A'*43):
        connection=HTTPConnection('127.0.0.1',gateway.server_port)
        connection.request('GET','/style.css',headers={'Cookie':preview.ACCESS_COOKIE+'='+value})
        assert connection.getresponse().status==401
        connection.close()


def test_public_gateway_allows_access_without_password(gateway):
    gateway.require_auth = False
    connection = HTTPConnection('127.0.0.1', gateway.server_port)
    connection.request('POST', '/api/rpc', '{}')
    response = connection.getresponse()
    assert response.status == 200
    assert response.getheader('Set-Cookie') is None
    connection.close()
