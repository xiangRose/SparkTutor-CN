"""Persistent accounts and revocable browser sessions for the web workspace."""
from __future__ import annotations

from collections import defaultdict, deque
from contextlib import contextmanager
import hashlib
import hmac
from http.cookies import SimpleCookie, CookieError
import json
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
import uuid


COOKIE = 'sparktutor_login'
SESSION_SECONDS = 7 * 24 * 3600
ROUNDS = 600_000


class AccountError(ValueError):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


def password_hash(password, salt):
    return hashlib.pbkdf2_hmac('sha256', password.encode(), bytes.fromhex(salt), ROUNDS).hex()


class AccountStore:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'accounts.sqlite3'
        self.lock = threading.RLock()
        self.attempts = defaultdict(deque)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE,
                    display_name TEXT NOT NULL, salt TEXT NOT NULL, password_hash TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'student', enabled INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL, last_login INTEGER
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS sessions_user ON sessions(user_id);
            ''')
            if not db.execute('SELECT 1 FROM users LIMIT 1').fetchone():
                password = secrets.token_urlsafe(18)
                self._insert(db, 'admin', '管理员', password, 'admin', must_change=True)
                (self.directory / 'initial-admin.json').write_text(
                    json.dumps({'username':'admin', 'password':password}, ensure_ascii=False), encoding='utf-8')

    @contextmanager
    def connection(self):
        with self.lock:
            db = sqlite3.connect(self.path, timeout=10)
            db.row_factory = sqlite3.Row
            db.execute('PRAGMA foreign_keys = ON')
            try:
                with db:
                    yield db
            finally:
                db.close()

    @staticmethod
    def public(row):
        return {'id':row['id'], 'username':row['username'], 'displayName':row['display_name'],
                'role':row['role'], 'enabled':bool(row['enabled']),
                'mustChangePassword':bool(row['must_change_password']),
                'createdAt':row['created_at'], 'lastLogin':row['last_login']}

    @staticmethod
    def validate(username, display_name, password):
        if not isinstance(username, str) or not re.fullmatch(r'[a-zA-Z0-9_]{3,32}', username):
            raise AccountError('账号需为 3–32 位英文字母、数字或下划线。')
        if not isinstance(display_name, str) or not 1 <= len(display_name.strip()) <= 40:
            raise AccountError('学习昵称需为 1–40 个字符。')
        if not isinstance(password, str) or not 10 <= len(password) <= 128:
            raise AccountError('密码需为 10–128 个字符。')

    def _insert(self, db, username, display_name, password, role, must_change=False):
        self.validate(username, display_name, password)
        if role not in {'admin', 'student'}:
            raise AccountError('账号角色无效。')
        identity, salt = uuid.uuid4().hex, secrets.token_hex(16)
        try:
            db.execute('INSERT INTO users VALUES (?,?,?,?,?,?,1,?,?,NULL)',
                       (identity, username.lower(), display_name.strip(), salt, password_hash(password, salt),
                        role, int(must_change), int(time.time())))
        except sqlite3.IntegrityError:
            raise AccountError('该账号已存在，请换一个账号。', 409) from None
        return self.public(db.execute('SELECT * FROM users WHERE id=?', (identity,)).fetchone())

    def throttle(self, key, limit=10, window=600):
        now = time.monotonic()
        with self.lock:
            queue = self.attempts[key]
            while queue and queue[0] < now - window:
                queue.popleft()
            if len(queue) >= limit:
                raise AccountError('尝试次数过多，请稍后再试。', 429)
            queue.append(now)
            if len(self.attempts) > 5000:
                self.attempts = defaultdict(deque, {k:v for k,v in self.attempts.items() if v and v[-1] >= now-window})

    def register(self, values, *, admin=False):
        if not isinstance(values, dict):
            raise AccountError('账号信息格式无效。')
        with self.connection() as db:
            return self._insert(db, values.get('username'), values.get('displayName') or values.get('username'),
                                values.get('password'), values.get('role', 'student') if admin else 'student', must_change=admin)

    def login(self, username, password, client='local'):
        if not isinstance(username, str) or len(username)>32 or not isinstance(password, str) or len(password)>128:
            raise AccountError('账号或密码不正确。', 401)
        self.throttle(('login', client, username.lower()))
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE username=?', (username.lower(),)).fetchone()
            # Compare a real-length hash for unknown users as well.
            digest = password_hash(password, row['salt'] if row else '0' * 32)
            if row is None or not hmac.compare_digest(digest, row['password_hash']) or not row['enabled']:
                raise AccountError('账号或密码不正确，或账号已停用。', 401)
            token = secrets.token_urlsafe(32)
            db.execute('DELETE FROM sessions WHERE expires_at<=?', (int(time.time()),))
            db.execute('INSERT INTO sessions VALUES (?,?,?)',
                       (hashlib.sha256(token.encode()).hexdigest(), row['id'], int(time.time())+SESSION_SECONDS))
            db.execute('UPDATE users SET last_login=? WHERE id=?', (int(time.time()), row['id']))
            self.attempts.pop(('login', client, username.lower()), None)
            return token, self.public(row)

    @staticmethod
    def cookie_token(header):
        try:
            cookie = SimpleCookie(header or '')
            token = cookie[COOKIE].value if COOKIE in cookie else ''
            return token if re.fullmatch(r'[a-zA-Z0-9_-]{40,80}', token) else ''
        except CookieError:
            return ''

    def authenticate(self, cookie):
        token = self.cookie_token(cookie)
        if not token:
            return None
        with self.connection() as db:
            row = db.execute('SELECT u.* FROM users u JOIN sessions s ON u.id=s.user_id '
                             'WHERE s.token_hash=? AND s.expires_at>? AND u.enabled=1',
                             (hashlib.sha256(token.encode()).hexdigest(), int(time.time()))).fetchone()
            return self.public(row) if row else None

    def logout(self, cookie):
        token = self.cookie_token(cookie)
        with self.connection() as db:
            db.execute('DELETE FROM sessions WHERE token_hash=?', (hashlib.sha256(token.encode()).hexdigest(),))

    def users(self):
        with self.connection() as db:
            return [self.public(row) for row in db.execute('SELECT * FROM users ORDER BY created_at,id')]

    def change_password(self, identity, old_password, new_password, *, administrator=False):
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise AccountError('账号不存在。', 404)
            self.validate(row['username'], row['display_name'], new_password)
            if not administrator and (not isinstance(old_password, str) or len(old_password)>128 or
                                      not hmac.compare_digest(password_hash(old_password, row['salt']), row['password_hash'])):
                raise AccountError('原密码不正确。', 403)
            salt = secrets.token_hex(16)
            db.execute('UPDATE users SET salt=?,password_hash=?,must_change_password=? WHERE id=?',
                       (salt, password_hash(new_password,salt), int(administrator), identity))
            db.execute('DELETE FROM sessions WHERE user_id=?', (identity,))

    def set_enabled(self, identity, enabled, actor):
        if not isinstance(enabled, bool):
            raise AccountError('账号状态无效。')
        if identity == actor and not enabled:
            raise AccountError('不能停用当前登录的管理员。')
        with self.connection() as db:
            row = db.execute('SELECT * FROM users WHERE id=?', (identity,)).fetchone()
            if row is None:
                raise AccountError('账号不存在。', 404)
            if not enabled and row['role']=='admin' and db.execute(
                    "SELECT count(*) FROM users WHERE role='admin' AND enabled=1").fetchone()[0] <= 1:
                raise AccountError('至少需要保留一个可用管理员。')
            db.execute('UPDATE users SET enabled=? WHERE id=?', (int(enabled), identity))
            if not enabled:
                db.execute('DELETE FROM sessions WHERE user_id=?', (identity,))
