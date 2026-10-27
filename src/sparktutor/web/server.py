"""Dependency-free local HTTP transport; learning rules stay in ServerHandler."""

from __future__ import annotations

import asyncio
import hmac
import json
import os
import re
import socket
from concurrent.futures import TimeoutError as FutureTimeout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from sparktutor.config.settings import Settings
from .runtime import SessionResetError, WebRuntime

ASSETS = Path(__file__).with_name("static")
MAX_BODY = 2 * 1024 * 1024


class WebServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"

    def server_bind(self):
        # Windows SO_REUSEADDR can let two servers bind the same port, making
        # clients hang instead of reporting the occupied port at startup.
        if os.name == "nt":
            self.socket.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        super().server_bind()

    def __init__(self, address, settings):
        super().__init__(address, WebRequestHandler)
        self.runtime = WebRuntime(settings)

    def server_close(self):
        super().server_close()
        if hasattr(self, "runtime"):
            self.runtime.close()


class WebRequestHandler(BaseHTTPRequestHandler):
    server_version = "SparkTutorWeb/1.0"

    def reply(self, status, body, content_type="application/json; charset=utf-8"):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'")
        self.end_headers()
        try:
            self.wfile.write(body)
        except (BrokenPipeError, ConnectionResetError):
            pass

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
        assets = {"/": ("index.html", "text/html; charset=utf-8"),
                  "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                  "/style.css": ("style.css", "text/css; charset=utf-8"),
                  "/favicon.svg": ("favicon.svg", "image/svg+xml")}
        if path == "/api/health":
            return self.reply(200, {"status": "ok", "port": self.server.server_port})
        extra = {"/features.js", "/features.css", "/settings.js", "/settings.css",
                 "/vendor/editor.bundle.js", "/vendor/EDITOR-LICENSES.txt"}
        if path in assets:
            filename, mime = assets[path]
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
            content = content.replace(b"__API_TOKEN__", self.server.runtime.token.encode())
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
        if not self.valid_host() or not hmac.compare_digest(self.headers.get("X-SparkTutor-Token", ""), self.server.runtime.token):
            return self.reply(403, {"error": "连接凭据已过期，请刷新页面。"})
        origin = self.headers.get("Origin")
        if origin and origin != f"http://{self.headers.get('Host', '').lower()}":
            return self.reply(403, {"error": "请求来源不受支持。"})
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
            future = asyncio.run_coroutine_threadsafe(self.server.runtime.dispatch(
                session_id, message["method"], message.get("params", {})), self.server.runtime.loop)
            self.reply(200, {"result": future.result(timeout=max(180, self.server.runtime.settings.timeout_seconds + 60))})
        except FutureTimeout:
            if future:
                future.cancel()
            self.reply(504, {"error": "运行超时，请检查执行环境后重试。"})
        except SessionResetError as error:
            self.reply(409, {"error": str(error), "sessionReset": True, "sessionId": error.session_id})
        except (ValueError, KeyError, TypeError, UnicodeError) as error:
            self.reply(400, {"error": str(error)})
        except Exception:
            self.reply(500, {"error": "操作未完成，请查看服务日志或重试。"})


def serve(port=8888, data_dir=None, dry_run=False, host="0.0.0.0"):
    settings = Settings.load()
    settings.data_dir = Path(data_dir) if data_dir else Path.cwd() / ".sparktutor-web"
    if dry_run:
        from sparktutor.config.settings import ExecutionMode
        settings.execution_mode = ExecutionMode.DRY_RUN
    server = WebServer((host, port), settings)
    print(f"SparkTutor 网页端已启动：http://localhost:{server.server_port}", flush=True)
    print(f"监听地址：{host}:{server.server_port}", flush=True)
    print(f"学习数据：{settings.data_dir.resolve()}", flush=True)
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
    parser.add_argument("--host", default="0.0.0.0", help="默认允许局域网访问；仅本机使用可选 127.0.0.1。")
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    serve(args.port, args.data_dir, args.dry_run, args.host)
