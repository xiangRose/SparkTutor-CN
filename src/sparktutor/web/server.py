"""Dependency-free local HTTP transport; learning rules stay in ServerHandler."""

from __future__ import annotations

import asyncio
import hmac
import html
import ipaddress
import json
import os
import re
import socket
import threading
from http.cookies import SimpleCookie
from concurrent.futures import CancelledError as FutureCancelled, TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sparktutor.config.settings import ExecutionMode, Settings
from sparktutor.engine.isolated_executor import IsolatedJobPool
from .accounts import AccountError, AccountStore, COOKIE, SESSION_SECONDS
from .runtime import SessionResetError, WebRuntime

ASSETS = Path(__file__).with_name("static")
MAX_BODY = 2 * 1024 * 1024
DISABLED_DATA_METHODS = frozenset({
    "getLearningDataOverview", "exportLearningSummary", "backupLearningDatabase", "clearLearningRecords",
})


def trusted_local_settings(*, host, data_dir=None, dry_run=False):
    """Validate an explicit, loopback-only execution workspace before opening it."""
    if dry_run:
        raise ValueError("--trusted-local 与 --dry-run 不能同时使用。")
    try:
        local_address = host == "localhost" or (
            ipaddress.ip_address(host).version == 4 and ipaddress.ip_address(host).is_loopback
        )
    except ValueError:
        local_address = False
    if not local_address:
        raise ValueError("可信本机实操模式只允许 127.0.0.1 等 IPv4 回环地址或 localhost。")
    directory = Path(data_dir) if data_dir else Path.cwd() / ".sparktutor-trusted-local"
    directory = directory.resolve()
    account_roots = [directory, *directory.parents]
    if directory == (Path.cwd() / ".sparktutor-web").resolve() or any(
            (directory / name).exists() for name in ("accounts", "users")) or any(
            (parent / "accounts" / "accounts.sqlite3").exists() for parent in account_roots):
        raise ValueError("可信本机实操模式需要独立数据目录，不能使用账号数据目录。")
    return directory


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # Windows SO_REUSEADDR can let two servers bind the same port, making
        # clients hang instead of reporting the occupied port at startup.
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, settings, *, accounts=False, trusted_proxy=False, trusted_local=False):
        if trusted_local:
            if accounts:
                raise ValueError("可信本机实操模式不能与多用户账号同时启用。")
            trusted_local_settings(host=address[0], data_dir=settings.data_dir,
                                   dry_run=settings.execution_mode == ExecutionMode.DRY_RUN)
        super().__init__(address, WebRequestHandler)
        self.settings = settings
        self.trusted_local = trusted_local
        self.trusted_proxy = trusted_proxy
        self.accounts = AccountStore(settings.data_dir / 'accounts') if accounts else None
        self.isolated_pool = (IsolatedJobPool(settings.isolated_execution, settings.data_dir / "isolated-jobs")
                              if accounts and settings.isolated_execution.enabled else None)
        self.user_runtimes = {}
        self.runtime_lock = threading.RLock()
        self.runtime = WebRuntime(settings)

    def runtime_for(self, user):
        if not self.accounts:
            return self.runtime
        with self.runtime_lock:
            if user['id'] not in self.user_runtimes:
                # Each account owns its database, files, navigation, and model settings.
                settings = Settings(data_dir=self.settings.data_dir / 'users' / user['id'],
                                    execution_mode=ExecutionMode.ISOLATED if self.isolated_pool else ExecutionMode.DRY_RUN,
                                    isolated_execution=self.settings.isolated_execution.model_copy(deep=True),
                                    timeout_seconds=self.settings.isolated_execution.timeout_seconds if self.isolated_pool
                                    else self.settings.timeout_seconds)
                self.user_runtimes[user['id']] = WebRuntime(
                    settings, multi_user=True, isolated_pool=self.isolated_pool, account_id=user['id'])
            return self.user_runtimes[user['id']]

    def server_close(self):
        super().server_close()
        if hasattr(self, "runtime"):
            self.runtime.close()
        for runtime in getattr(self, 'user_runtimes', {}).values():
            runtime.close()


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = "SparkTutorWeb/1.0"

    def reply(self, status, body, content_type="application/json; charset=utf-8", extra_headers=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        for name, value in (extra_headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def current_user(self):
        return self.server.accounts.authenticate(self.headers.get('Cookie')) if self.server.accounts else None

    def login_cookie(self, token='', expire=False):
        cookie = SimpleCookie()
        cookie[COOKIE] = token
        cookie[COOKIE]['path'] = '/'
        cookie[COOKIE]['httponly'] = True
        cookie[COOKIE]['samesite'] = 'Strict'
        cookie[COOKIE]['max-age'] = 0 if expire else SESSION_SECONDS
        if self.server.trusted_proxy and self.headers.get('X-Forwarded-Proto') == 'https':
            cookie[COOKIE]['secure'] = True
        return cookie.output(header='').strip()

    def valid_host(self):
        # Accept the actual destination interface, including its LAN address,
        # while still rejecting arbitrary Host headers / DNS rebinding.
        destination = self.connection.getsockname()[0]
        return self.headers.get("Host", "").lower() in {
            f"localhost:{self.server.server_port}", f"127.0.0.1:{self.server.server_port}",
            f"{destination}:{self.server.server_port}",
        }

    def do_GET(self):
        if not self.valid_host():
            return self.reply(403, {"error": "请通过本机或局域网 IP 地址访问。"})
        path = urlsplit(self.path).path
        user = self.current_user()
        if path == '/api/auth/session' and self.server.accounts:
            return self.reply(200, {'user':user, 'accountsEnabled':True})
        if path == '/api/admin/users' and self.server.accounts:
            if not user:
                return self.reply(401, {'error':'请先登录。'})
            if user['role'] != 'admin':
                return self.reply(403, {'error':'需要管理员账号。'})
            if user['mustChangePassword']:
                return self.reply(403, {'error':'请先修改初始密码。'})
            return self.reply(200, {'users':self.server.accounts.users()})
        runtime = self.server.runtime_for(user) if user or not self.server.accounts else None
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/favicon.svg": ("favicon.svg", "image/svg+xml"),
                  "/assets/guofeng-hero.png": ("assets/guofeng-hero.png", "image/png"),
                  "/assets/pet-idle.png": ("assets/pet-idle.png", "image/png"),
                  "/assets/pet-cheer.png": ("assets/pet-cheer.png", "image/png"),
                  "/assets/pet-sleep.png": ("assets/pet-sleep.png", "image/png")}
        if path == "/api/health":
            return self.reply(200, {"status": "ok", "port": self.server.server_port,
                                    "accessMode": "trusted_local" if self.server.trusted_local else
                                    "accounts" if self.server.accounts else "local",
                                    "executionMode": ("isolated" if self.server.isolated_pool else "dry_run") if self.server.accounts else
                                    self.server.settings.execution_mode.value})
        extra = {"/features.js", "/features.css", "/settings.js", "/settings.css", "/appearance.js", "/appearance.css", "/guofeng.css", "/pet.css", "/pet.js",
                 '/account.js', '/account.css', '/login.js', '/login.css', '/checkin.js', '/checkin.css',
                 "/vendor/editor.bundle.js", "/vendor/EDITOR-LICENSES.txt"}
        if path in assets:
            filename, mime = assets[path]
            if path == '/' and self.server.accounts and not user:
                filename = 'login.html'
        elif path in extra:
            filename = path.lstrip("/")
            mime = ("text/javascript" if filename.endswith(".js") else
                    "text/css" if filename.endswith(".css") else "text/plain") + "; charset=utf-8"
        else:
            return self.reply(404, {"error": "页面不存在。"})
        resource = (ASSETS / filename).resolve()
        if not resource.is_relative_to(ASSETS.resolve()) or not resource.is_file():
            return self.reply(404, {"error": "页面不存在。"})
        content = resource.read_bytes()
        if filename == "index.html":
            content = content.replace(b"__API_TOKEN__", runtime.token.encode())
            content = content.replace(b'__USER_ID__', (user['id'] if user else '').encode())
            content = content.replace(b'__USER_ROLE__', (user['role'] if user else '').encode())
            content = content.replace(b'__USER_NAME__', html.escape(user['displayName'] if user else '自主学习者', quote=True).encode())
            content = content.replace(b'__MUST_CHANGE_PASSWORD__', str(bool(user and user['mustChangePassword'])).lower().encode())
            if self.server.trusted_local:
                content = content.replace(
                    b'<div class="header-right">',
                    '<div class="header-right"><span class="local-tag"><i></i> 本机实操</span>'.encode(), 1)
        self.reply(200, content, mime)

    def do_POST(self):
        # Drain a bounded request body even when rejecting credentials; closing
        # a socket with unread data can reset the connection on Windows.
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > MAX_BODY:
                return self.reply(413, {"error": "请求过大或为空。"})
            self.connection.settimeout(15)
            body = self.rfile.read(length)
        except (ValueError, OSError):
            return self.reply(400, {"error": "请求正文无效或读取超时。"})
        if not self.valid_host():
            return self.reply(403, {'error':'访问地址不受支持。'})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host', '').lower()}":
            return self.reply(403, {"error": "请求来源不受支持。"})
        if self.server.accounts and self.path.startswith(('/api/auth/', '/api/admin/')):
            return self.account_post(body)
        user = self.current_user()
        if self.server.accounts and not user:
            return self.reply(401, {'error':'登录已过期，请重新登录。', 'loginRequired':True})
        runtime = self.server.runtime_for(user)
        if not hmac.compare_digest(self.headers.get('X-SparkTutor-Token', ''), runtime.token):
            return self.reply(403, {'error':'登录或连接已更新，请刷新页面。'})
        if user and user['mustChangePassword']:
            return self.reply(403, {'error':'请先在账号设置中修改初始密码。', 'passwordChangeRequired':True})
        if self.path != "/api/rpc":
            return self.reply(404, {"error": "接口不存在。"})
        session_id = self.headers.get("X-SparkTutor-Session", "")
        if not re.fullmatch(r"[a-zA-Z0-9_-]{16,100}", session_id):
            return self.reply(400, {"error": "缺少有效的学习会话。"})
        future = None
        try:
            message = json.loads(body)
            if not isinstance(message, dict) or not isinstance(message.get("method"), str) or not isinstance(message.get("params", {}), dict):
                raise ValueError("无效的请求格式。")
            if message["method"] in DISABLED_DATA_METHODS:
                return self.reply(404, {"error": "网页端数据管理接口已移除。"})
            future = asyncio.run_coroutine_threadsafe(runtime.dispatch(
                session_id, message["method"], message.get("params", {})), runtime.loop)
            self.reply(200, {"result": future.result(timeout=max(180, runtime.settings.timeout_seconds + 60))})
        except FutureTimeout:
            if future:
                future.cancel()
            self.reply(504, {"error": "运行超时，请检查执行环境后重试。"})
        except FutureCancelled:
            self.reply(409, {"error": "运行已取消，课程测试未完成，没有记录为通过。", "cancelled": True})
        except SessionResetError as error:
            self.reply(409, {"error": str(error), "sessionReset": True, "sessionId": error.session_id})
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception:
            self.reply(500, {"error": "操作未完成，请查看服务日志或重试。"})

    def account_post(self, body):
        try:
            values = json.loads(body)
            if not isinstance(values, dict):
                raise AccountError('请求格式无效。')
            if self.headers.get('X-SparkTutor-Auth') != '1':
                raise AccountError('请从登录或账号页面提交。', 403)
            store = self.server.accounts
            user = self.current_user()
            if self.path == '/api/auth/register':
                store.throttle(('register', self.client_address[0]), limit=12, window=3600)
                created = store.register(values)
                return self.reply(201, {'user':created})
            if self.path == '/api/auth/login':
                token, current = store.login(values.get('username'), values.get('password'), self.client_address[0])
                return self.reply(200, {'user':current}, extra_headers={'Set-Cookie':self.login_cookie(token)})
            if not user:
                raise AccountError('请先登录。', 401)
            runtime = self.server.runtime_for(user)
            if not hmac.compare_digest(self.headers.get('X-SparkTutor-Token',''), runtime.token):
                raise AccountError('登录已更新，请刷新后再试。', 403)
            if self.path == '/api/auth/logout':
                store.logout(self.headers.get('Cookie'))
                return self.reply(200, {'ok':True}, extra_headers={'Set-Cookie':self.login_cookie(expire=True)})
            if self.path == '/api/auth/password':
                store.change_password(user['id'],values.get('oldPassword'),values.get('newPassword'))
                return self.reply(200, {'ok':True}, extra_headers={'Set-Cookie':self.login_cookie(expire=True)})
            if user['mustChangePassword']:
                raise AccountError('请先修改初始密码。', 403)
            if self.path.startswith('/api/admin/'):
                if user['role'] != 'admin':
                    raise AccountError('需要管理员账号。', 403)
                if self.path == '/api/admin/create':
                    return self.reply(201, {'user':store.register(values,admin=True)})
                if self.path == '/api/admin/password':
                    store.change_password(values.get('userId'),None,values.get('newPassword'),administrator=True)
                    return self.reply(200, {'ok':True})
                if self.path == '/api/admin/status':
                    store.set_enabled(values.get('userId'),values.get('enabled'),user['id'])
                    return self.reply(200, {'ok':True})
            raise AccountError('接口不存在。',404)
        except AccountError as error:
            self.reply(error.status, {'error':str(error)})
        except (ValueError, TypeError, UnicodeError):
            self.reply(400, {'error':'请求格式无效。'})


def serve(port=8888, data_dir=None, dry_run=False, host="0.0.0.0", trusted_local=False, isolated_execution=False):
    if isolated_execution and (trusted_local or dry_run):
        raise ValueError("账号隔离执行不能与可信单人或仅语法检查参数同时使用。")
    if trusted_local:
        # Validate before Settings.load or WebRuntime can create/read learning files.
        data_dir = trusted_local_settings(host=host, data_dir=data_dir, dry_run=dry_run)
    settings = Settings.load()
    settings.data_dir = Path(data_dir) if data_dir else Path.cwd() / ".sparktutor-web"
    if isolated_execution:
        settings.isolated_execution.enabled = True
    if trusted_local:
        settings.execution_mode = ExecutionMode.LOCAL
    elif dry_run:
        settings.execution_mode = ExecutionMode.DRY_RUN
        settings.isolated_execution.enabled = False
    server = WebServer((host, port), settings, accounts=not trusted_local, trusted_local=trusted_local)
    print(f"SparkTutor 网页端已启动：http://localhost:{server.server_port}", flush=True)
    print(f"监听地址：{host}:{server.server_port}", flush=True)
    print(f"学习数据：{settings.data_dir.resolve()}", flush=True)
    if trusted_local:
        print("可信本机实操：无需登录，仅限本机使用。代码在当前电脑真实执行，请只运行可信练习。", flush=True)
        print("真实课程测试成功后才记录通过；缺少 Spark、错误或超时不会降级为通过。", flush=True)
    else:
        print(f"首次管理员密码：{(settings.data_dir / 'accounts' / 'initial-admin.json').resolve()}（首次登录后修改）", flush=True)
        if server.isolated_pool:
            print("账号实操：每次作业独立容器执行；服务或镜像未就绪时明确失败，不降级为宿主执行。", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


def main():
    import argparse
    parser = argparse.ArgumentParser(description="SparkTutor 本地网页端")
    parser.add_argument("--port", type=int, default=8888)
    parser.add_argument("--host", help="账号模式默认 0.0.0.0；可信本机实操默认 127.0.0.1。")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--trusted-local", action="store_true", help="可信单人本机实操，不启用账号，仅允许回环地址。")
    parser.add_argument("--isolated-execution", action="store_true", help="启用账号独立容器执行。")
    args = parser.parse_args()
    host = args.host or ("127.0.0.1" if args.trusted_local else "0.0.0.0")
    try:
        serve(args.port, args.data_dir, args.dry_run, host, args.trusted_local, args.isolated_execution)
    except ValueError as error:
        parser.error(str(error))
