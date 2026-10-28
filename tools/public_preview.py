"""Temporary public sharing of an isolated local workspace."""
from __future__ import annotations

import argparse
import base64
import hmac
from http.client import HTTPConnection
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from http.cookies import SimpleCookie, CookieError
import json
import os
from pathlib import Path
import re
import secrets
import subprocess
import threading
from urllib.parse import urlsplit

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.web.server import MAX_BODY, WebServer

ACCESS_COOKIE = 'sparktutor_preview_access'


class PreviewGateway(BaseHTTPRequestHandler):
    """Authenticate every route before forwarding to the unchanged local server."""

    def reject(self, status, message):
        body = message.encode('utf-8')
        self.send_response(status)
        if status == 401:
            self.send_header('WWW-Authenticate', 'Basic realm="SparkTutor Preview", charset="UTF-8"')
        self.send_header('Content-Type', 'text/plain; charset=utf-8')
        self.send_header('Content-Length', str(len(body)))
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(body)

    def forward(self):
        supplied = self.headers.get('Authorization', '')
        basic_authenticated = hmac.compare_digest(supplied, self.server.authorization)
        try:
            cookies = SimpleCookie(self.headers.get('Cookie', ''))
            access = cookies[ACCESS_COOKIE].value if ACCESS_COOKIE in cookies else ''
        except CookieError:
            cookies, access = SimpleCookie(), ''
        cookie_authenticated = bool(re.fullmatch(r'[A-Za-z0-9_-]{40,80}',access) and
                                    hmac.compare_digest(access,getattr(self.server,'access_token','')))
        # Private previews require proof of the outer sharing password. Public
        # previews skip only this gate; the application still owns account login.
        if self.server.require_auth and not basic_authenticated and not cookie_authenticated:
            return self.reject(401, '请输入分享账号和访问密码。')
        host = self.headers.get('Host', '')
        public_host = urlsplit(self.server.public_url).netloc
        local_host = f'127.0.0.1:{self.server.server_port}'
        if host not in {local_host, public_host} or not host:
            return self.reject(403, '访问地址不受支持。')
        origin = self.headers.get('Origin')
        expected = f'http://{local_host}' if host == local_host else self.server.public_url
        if origin and origin != expected:
            return self.reject(403, '请求来源不受支持。')
        if self.headers.get('Transfer-Encoding'):
            return self.reject(400, '请使用固定长度请求。')
        try:
            length = int(self.headers.get('Content-Length', '0'))
            if length < 0 or length > MAX_BODY:
                return self.reject(413, '请求过大。')
            self.connection.settimeout(20)
            body = self.rfile.read(length) if length else None
        except (ValueError, OSError):
            return self.reject(400, '请求正文无效。')
        upstream_host = f'127.0.0.1:{self.server.upstream_port}'
        headers = {'Host': upstream_host, 'Content-Length': str(length)}
        for name in ('Content-Type', 'X-SparkTutor-Token', 'X-SparkTutor-Session', 'X-SparkTutor-Auth', 'Cookie'):
            if name in self.headers:
                headers[name] = self.headers[name]
        if ACCESS_COOKIE in cookies:
            del cookies[ACCESS_COOKIE]
            headers['Cookie'] = '; '.join(item.OutputString() for item in cookies.values())
        headers['X-Forwarded-Proto'] = 'https' if host==public_host and expected.startswith('https:') else 'http'
        if origin:
            headers['Origin'] = f'http://{upstream_host}'
        connection = HTTPConnection('127.0.0.1', self.server.upstream_port, timeout=300)
        try:
            connection.request(self.command, self.path, body, headers)
            response = connection.getresponse()
            content = response.read()
            self.send_response(response.status)
            for name, value in response.getheaders():
                if name.lower() not in {'connection', 'transfer-encoding', 'content-length', 'server', 'date'}:
                    self.send_header(name, value)
            self.send_header('Content-Length', str(len(content)))
            self.send_header('Referrer-Policy', 'no-referrer')
            if self.server.require_auth and basic_authenticated and not cookie_authenticated:
                cookie = SimpleCookie()
                cookie[ACCESS_COOKIE] = self.server.access_token
                cookie[ACCESS_COOKIE]['path'] = '/'
                cookie[ACCESS_COOKIE]['httponly'] = True
                cookie[ACCESS_COOKIE]['samesite'] = 'Strict'
                cookie[ACCESS_COOKIE]['max-age'] = 8 * 3600
                if expected.startswith('https:'):
                    cookie[ACCESS_COOKIE]['secure'] = True
                self.send_header('Set-Cookie',cookie.output(header='').strip())
            self.end_headers()
            self.wfile.write(content)
        except (OSError, ConnectionError):
            self.close_connection = True
        finally:
            connection.close()

    do_GET = forward
    do_POST = forward

    def log_message(self, *_):
        pass


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--provider', choices=('cloudflare', 'localhost-run'), default='localhost-run')
    parser.add_argument('--cloudflared', type=Path)
    parser.add_argument('--state-dir', type=Path, required=True)
    parser.add_argument('--public', action='store_true', help='Allow access without the outer preview password')
    parser.add_argument('--reuse-tunnel', action='store_true', help='Restart the gateway while retaining the running tunnel and URL')
    args = parser.parse_args()
    directory = args.state_dir.resolve()
    directory.mkdir(parents=True, exist_ok=True)
    status_path = directory / 'status.json'
    previous = {}
    if status_path.is_file():
        try:
            previous = json.loads(status_path.read_text(encoding='utf-8'))
        except (ValueError,OSError):
            pass
    # Start with new model settings and learning records, rather than the owner's data.
    settings = Settings(data_dir=directory / 'learning')
    if args.public:
        # An anonymous internet endpoint must never execute submitted learner code.
        settings.execution_mode = ExecutionMode.DRY_RUN
    backend = WebServer(('127.0.0.1', 0), settings, accounts=True, trusted_proxy=True)
    gateway_port = int(previous.get('gatewayPort',0)) if args.reuse_tunnel else 0
    if args.reuse_tunnel and (not 1024<=gateway_port<=65535 or
                            not re.fullmatch(r'https://[a-z0-9-]+\.(trycloudflare\.com|lhr\.life)',previous.get('url',''))):
        parser.error('A valid running tunnel status is required for --reuse-tunnel')
    gateway = ThreadingHTTPServer(('127.0.0.1', gateway_port), PreviewGateway)
    gateway.daemon_threads = True
    gateway.upstream_port = backend.server_port
    gateway.public_url = previous['url'] if args.reuse_tunnel else ''
    gateway.require_auth = not args.public
    password = previous.get('password')
    if not isinstance(password,str) or not 20<=len(password)<=128:
        password = secrets.token_urlsafe(18)
    gateway.authorization = 'Basic ' + base64.b64encode(f'student:{password}'.encode()).decode()
    gateway.access_token = secrets.token_urlsafe(32)
    for server in (backend, gateway):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    if args.reuse_tunnel:
        previous.update(pid=os.getpid(),backendPort=backend.server_port,state='connected')
        status_path.write_text(json.dumps(previous,ensure_ascii=False,indent=2),encoding='utf-8')
        try:
            threading.Event().wait()
        finally:
            for server in (gateway,backend):
                server.shutdown();server.server_close()
        return
    if args.provider == 'cloudflare':
        if not args.cloudflared:
            parser.error('--cloudflared is required for the cloudflare provider')
        command = [str(args.cloudflared.resolve()), 'tunnel', '--no-autoupdate', '--protocol', 'http2',
                   '--url', f'http://127.0.0.1:{gateway.server_port}']
    else:
        command = ['ssh', '-T', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=20',
                   '-o', 'ServerAliveInterval=30', '-o', 'ServerAliveCountMax=3',
                   '-o', 'IdentityAgent=none', '-o', 'IdentityFile=none', '-o', 'IdentitiesOnly=yes',
                   '-o', 'StrictHostKeyChecking=accept-new', '-o', f'UserKnownHostsFile={directory / "known-hosts"}',
                   '-R', f'80:127.0.0.1:{gateway.server_port}', 'nokey@localhost.run']
    child = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, encoding='utf-8', errors='replace',
                             creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    status = {'pid': os.getpid(), 'tunnelPid': child.pid, 'access': 'public' if args.public else 'password',
              'username': None if args.public else 'student', 'password': None if args.public else password,
              'gatewayPort': gateway.server_port, 'backendPort': backend.server_port, 'url': '', 'state': 'starting'}
    def save():
        status_path.write_text(json.dumps(status, ensure_ascii=False, indent=2), encoding='utf-8')
    save()
    try:
        with (directory / 'tunnel.log').open('w', encoding='utf-8') as log:
            for line in child.stdout:
                log.write(line); log.flush()
                match = re.search(r'https://(?!api\.)[a-z0-9-]+\.(?:trycloudflare\.com|lhr\.life|localhost\.run)', line)
                if match and (args.provider == 'cloudflare' or 'tunneled with tls termination' in line):
                    gateway.public_url = match.group(0)
                    status.update(url=gateway.public_url, state='connected' if args.provider == 'localhost-run' else 'connecting'); save()
                if 'Registered tunnel connection' in line:
                    status['state'] = 'connected'; save()
        status['state'] = 'stopped'; save()
    finally:
        child.terminate()
        for server in (gateway, backend):
            server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
