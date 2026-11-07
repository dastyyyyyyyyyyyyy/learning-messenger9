import base64
import binascii
import hashlib
import hmac
import json
import os
import re
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from messenger_updates import (
    REACTION_EMOJIS,
    load_accessible_message,
    migrate_update_schema,
    same_conversation,
    utc_timestamp,
    unique_digital_id,
)


HOST = os.environ.get("HOST", "127.0.0.1")
PORT = int(os.environ.get("PORT", "8000"))
DATABASE = Path(os.environ.get(
    "MESSENGER_DB", str(Path(__file__).with_name("messenger.db"))
))
TEMPLATE = Path(__file__).with_name("templates") / "index.html"
STATIC_FILES = {
    "/static/styles.css": (
        Path(__file__).with_name("static") / "styles.css",
        "text/css; charset=utf-8",
    ),
    "/static/app.js": (
        Path(__file__).with_name("static") / "app.js",
        "text/javascript; charset=utf-8",
    ),
}
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "").lower() in {"1", "true", "yes"}
SESSION_TTL = 12 * 60 * 60
PASSWORD_ITERATIONS = 310_000
MAX_MEDIA_BYTES = 20 * 1024 * 1024
MAX_MEDIA_REQUEST_BYTES = 28 * 1024 * 1024
MAX_AVATAR_BYTES = 512 * 1024
MAX_AVATAR_REQUEST_BYTES = 710 * 1024
MAX_TOTAL_MEDIA_BYTES = 100 * 1024 * 1024
MAX_SQLITE_ID = (1 << 63) - 1
RATE_LIMIT_WINDOW = 60
RATE_LIMITS = {}
rate_limits_lock = threading.Lock()
sessions = {}
sessions_lock = threading.Lock()



PAGE = TEMPLATE.read_text(encoding="utf-8")


@contextmanager
def connect_database():
    connection = sqlite3.connect(DATABASE, timeout=10)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def backup_database_before_update():
    if not DATABASE.exists():
        return
    uri = DATABASE.resolve().as_uri() + "?mode=ro"
    schema_connection = sqlite3.connect(uri, uri=True)
    schema_connection.row_factory = sqlite3.Row
    try:
        tables = {
            row["name"] for row in schema_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        if not {"users", "messages"} <= tables:
            return
        user_columns = {
            row["name"] for row in schema_connection.execute("PRAGMA table_info(users)")
        }
        message_columns = {
            row["name"] for row in schema_connection.execute("PRAGMA table_info(messages)")
        }
        indexes = {
            row["name"] for row in schema_connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'index'"
            )
        }
        required_message_columns = {"reply_to_id", "edited_at", "deleted_at"}
        required_update_tables = {
            "message_reactions", "message_pins", "typing_status", "user_presence"
        }
        if (
            "digital_id" in user_columns
            and "avatar_data" in user_columns
            and required_message_columns <= message_columns
            and required_update_tables <= tables
            and {
                "users_by_digital_id", "messages_by_reply", "typing_status_by_chat"
            } <= indexes
        ):
            return
    finally:
        schema_connection.close()

    backup_path = DATABASE.with_name(DATABASE.name + ".pre-update-0.2")
    if backup_path.exists():
        return
    source_connection = sqlite3.connect(DATABASE, timeout=10)
    backup_connection = None
    try:
        backup_connection = sqlite3.connect(backup_path, timeout=10)
        source_connection.backup(backup_connection)
    except sqlite3.Error:
        if backup_connection is not None:
            backup_connection.close()
            backup_connection = None
        if backup_path.exists():
            backup_path.unlink()
        raise
    finally:
        source_connection.close()
        if backup_connection is not None:
            backup_connection.close()


def initialize_database():
    backup_database_before_update()
    with connect_database() as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY,
                username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_salt BLOB NOT NULL,
                password_hash BLOB NOT NULL,
                display_name TEXT NOT NULL DEFAULT ''
            );
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                sender_id INTEGER NOT NULL REFERENCES users(id),
                recipient_id INTEGER NOT NULL REFERENCES users(id),
                text TEXT NOT NULL,
                created_at TEXT NOT NULL,
                kind TEXT NOT NULL DEFAULT 'text',
                media_name TEXT,
                media_type TEXT,
                media_data BLOB,
                group_id INTEGER REFERENCES chat_groups(id)
            );
            CREATE INDEX IF NOT EXISTS messages_between_users
                ON messages(sender_id, recipient_id, id);
            CREATE TABLE IF NOT EXISTS polls (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                message_id INTEGER NOT NULL UNIQUE REFERENCES messages(id) ON DELETE CASCADE,
                question TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poll_options (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                poll_id INTEGER NOT NULL REFERENCES polls(id) ON DELETE CASCADE,
                text TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS poll_votes (
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                option_id INTEGER NOT NULL REFERENCES poll_options(id) ON DELETE CASCADE,
                PRIMARY KEY (user_id, option_id)
            );
            CREATE TABLE IF NOT EXISTS chat_groups (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                invite_code TEXT NOT NULL UNIQUE,
                created_by INTEGER NOT NULL REFERENCES users(id),
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS group_members (
                group_id INTEGER NOT NULL REFERENCES chat_groups(id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                joined_at TEXT NOT NULL,
                PRIMARY KEY (group_id, user_id)
            );
            CREATE TABLE IF NOT EXISTS call_sessions (
                id TEXT PRIMARY KEY,
                caller_id INTEGER NOT NULL REFERENCES users(id),
                callee_id INTEGER NOT NULL REFERENCES users(id),
                mode TEXT NOT NULL CHECK (mode IN ('audio', 'video')),
                status TEXT NOT NULL CHECK (status IN ('ringing', 'accepted', 'rejected', 'ended')),
                created_at REAL NOT NULL,
                updated_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS call_signals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                call_id TEXT NOT NULL REFERENCES call_sessions(id) ON DELETE CASCADE,
                sender_id INTEGER NOT NULL REFERENCES users(id),
                kind TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS call_signals_by_call
                ON call_signals(call_id, id);
            """
        )
        message_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(messages)")
        }
        for column, definition in (
            ("kind", "TEXT NOT NULL DEFAULT 'text'"),
            ("media_name", "TEXT"),
            ("media_type", "TEXT"),
            ("media_data", "BLOB"),
            ("group_id", "INTEGER REFERENCES chat_groups(id)"),
        ):
            if column not in message_columns:
                connection.execute(
                    "ALTER TABLE messages ADD COLUMN %s %s" % (column, definition)
                )
        connection.execute(
            "CREATE INDEX IF NOT EXISTS group_messages_by_group ON messages(group_id, id)"
        )
        user_columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "avatar_data" not in user_columns:
            connection.execute("ALTER TABLE users ADD COLUMN avatar_data BLOB")
        if "display_name" not in user_columns:
            connection.execute(
                "ALTER TABLE users ADD COLUMN display_name TEXT NOT NULL DEFAULT ''"
            )
        connection.execute(
            "UPDATE users SET display_name = username WHERE display_name = ''"
        )
        migrate_update_schema(connection)


def make_password_hash(password, salt):
    return hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS
    )


def valid_origin(handler):
    origin = handler.headers.get("Origin")
    if origin is None:
        return False
    parsed_origin = urlparse(origin)
    expected_scheme = "https" if COOKIE_SECURE else "http"
    return (
        parsed_origin.scheme == expected_scheme
        and parsed_origin.netloc == handler.headers.get("Host")
    )


def allow_rate_limited_request(key, limit):
    now = time.monotonic()
    with rate_limits_lock:
        if key not in RATE_LIMITS and len(RATE_LIMITS) >= 10_000:
            expired_keys = [
                stored_key for stored_key, timestamps in RATE_LIMITS.items()
                if not timestamps or now - timestamps[-1] >= RATE_LIMIT_WINDOW
            ]
            for expired_key in expired_keys:
                del RATE_LIMITS[expired_key]
            if len(RATE_LIMITS) >= 10_000:
                return False
        recent_attempts = [
            timestamp for timestamp in RATE_LIMITS.get(key, [])
            if now - timestamp < RATE_LIMIT_WINDOW
        ]
        if len(recent_attempts) >= limit:
            RATE_LIMITS[key] = recent_attempts
            return False
        recent_attempts.append(now)
        RATE_LIMITS[key] = recent_attempts
        return True


def resolve_typing_chat(connection, recipient_name, user_id):
    if not isinstance(recipient_name, str):
        return None
    recipient_name = recipient_name.strip()
    group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
    if group_match:
        group_id = int(group_match.group(1))
        member = connection.execute(
            "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
            (group_id, user_id),
        ).fetchone()
        return recipient_name if member is not None else None

    recipient = connection.execute(
        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
        (recipient_name,),
    ).fetchone()
    if recipient is None or recipient["id"] == user_id:
        return None
    first_id, second_id = sorted((user_id, recipient["id"]))
    return "dm:%s:%s" % (first_id, second_id)


def describe_presence(last_seen_at):
    if last_seen_at is None:
        return {"online": False, "last_seen_at": None, "status": "Не в сети"}
    age = max(0, time.time() - last_seen_at)
    if age < 45:
        status = "В сети"
        online = True
    elif age < 24 * 60 * 60:
        status = "Был(а) недавно"
        online = False
    else:
        status = "Не в сети"
        online = False
    return {
        "online": online,
        "last_seen_at": last_seen_at,
        "status": status,
    }


def load_poll(connection, poll_id, user_id):
    poll = connection.execute(
        """
        SELECT p.id, p.question
        FROM polls p
        JOIN messages m ON m.id = p.message_id
        WHERE p.id = ? AND m.deleted_at IS NULL AND (
            (m.group_id IS NULL AND (m.sender_id = ? OR m.recipient_id = ?))
            OR (m.group_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM group_members gm
                WHERE gm.group_id = m.group_id AND gm.user_id = ?
            ))
        )
        """,
        (poll_id, user_id, user_id, user_id),
    ).fetchone()
    if poll is None:
        return None

    options = connection.execute(
        """
        SELECT o.id, o.text, COUNT(v.user_id) AS votes
        FROM poll_options o
        LEFT JOIN poll_votes v ON v.option_id = o.id
        WHERE o.poll_id = ?
        GROUP BY o.id
        ORDER BY o.id
        """,
        (poll_id,),
    ).fetchall()
    selected = connection.execute(
        """
        SELECT o.id
        FROM poll_options o
        JOIN poll_votes v ON v.option_id = o.id
        WHERE o.poll_id = ? AND v.user_id = ?
        """,
        (poll_id, user_id),
    ).fetchall()
    return {
        "id": poll["id"],
        "question": poll["question"],
        "options": [
            {"id": option["id"], "text": option["text"], "votes": option["votes"]}
            for option in options
        ],
        "selected": [option["id"] for option in selected],
    }


class ChatHandler(BaseHTTPRequestHandler):
    def send_json(self, status_code, data, extra_headers=None):
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status_code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if extra_headers:
            for name, value in extra_headers:
                self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def send_page(self):
        body = PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def send_static(self, static_path, content_type):
        try:
            body = static_path.read_bytes()
        except OSError:
            self.send_json(500, {"error": "Не удалось загрузить файл интерфейса."})
            return
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def read_json(self, max_bytes=8192):
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > max_bytes:
                raise ValueError("Размер запроса неправильный.")
            data = json.loads(self.rfile.read(content_length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError, json.JSONDecodeError):
            self.send_json(400, {"error": "Не удалось прочитать данные формы."})
            return None
        if not isinstance(data, dict):
            self.send_json(400, {"error": "Ожидались данные формы."})
            return None
        return data

    def get_session(self):
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
            token = cookie["session"].value
        except (KeyError, ValueError):
            return None

        now = time.time()
        with sessions_lock:
            session = sessions.get(token)
            if session is None:
                return None
            if session["expires"] < now:
                del sessions[token]
                return None
            session["expires"] = now + SESSION_TTL
            return session["user_id"]

    def set_session(self, user_id, revoke_existing=False):
        token = secrets.token_urlsafe(32)
        with sessions_lock:
            if revoke_existing:
                old_tokens = [
                    session_token for session_token, session in sessions.items()
                    if session["user_id"] == user_id
                ]
                for session_token in old_tokens:
                    del sessions[session_token]
            sessions[token] = {"user_id": user_id, "expires": time.time() + SESSION_TTL}
        cookie = "session=%s; Path=/; HttpOnly; SameSite=Strict; Max-Age=%s" % (
            token, SESSION_TTL
        )
        if COOKIE_SECURE:
            cookie += "; Secure"
        return [("Set-Cookie", cookie)]

    def clear_session(self):
        cookie = SimpleCookie()
        cookie.load(self.headers.get("Cookie", ""))
        token = cookie["session"].value if "session" in cookie else None
        if token:
            with sessions_lock:
                sessions.pop(token, None)
        return [("Set-Cookie", "session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0")]

    def do_GET(self):
        path = urlparse(self.path)
        if path.path == "/":
            self.send_page()
            return
        static_resource = STATIC_FILES.get(path.path)
        if static_resource is not None:
            self.send_static(*static_resource)
            return

        user_id = self.get_session()
        if user_id is None:
            self.send_json(401, {"error": "Нужно войти в аккаунт."})
            return

        if path.path == "/api/me":
            with connect_database() as connection:
                user = connection.execute(
                    "SELECT id, username, display_name, digital_id, "
                    "avatar_data IS NOT NULL AS has_avatar "
                    "FROM users WHERE id = ?", (user_id,)
                ).fetchone()
            if user is None:
                self.send_json(401, {"error": "Аккаунт не найден."})
            else:
                self.send_json(200, {
                    "id": user["id"],
                    "username": user["username"],
                    "display_name": user["display_name"] or user["username"],
                    "digital_id": user["digital_id"],
                    "has_avatar": bool(user["has_avatar"]),
                })
            return

        if path.path == "/api/typing":
            query = parse_qs(path.query)
            with connect_database() as connection:
                chat_key = resolve_typing_chat(
                    connection, query.get("with", [""])[0], user_id
                )
                if chat_key is None:
                    self.send_json(404, {"error": "Чат не найден."})
                    return
                rows = connection.execute(
                    """
                    SELECT u.id, u.username, u.display_name
                    FROM typing_status ts
                    JOIN users u ON u.id = ts.user_id
                    WHERE ts.chat_key = ? AND ts.user_id != ? AND ts.updated_at >= ?
                    ORDER BY ts.updated_at DESC
                    """,
                    (chat_key, user_id, time.time() - 5),
                ).fetchall()
            self.send_json(200, {
                "typing": [
                    {
                        "id": row["id"],
                        "username": row["username"],
                        "display_name": row["display_name"] or row["username"],
                    }
                    for row in rows
                ]
            })
            return

        if path.path == "/api/presence":
            query = parse_qs(path.query)
            username = query.get("with", [""])[0].strip()
            with connect_database() as connection:
                user = connection.execute(
                    """
                    SELECT u.id, p.last_seen_at
                    FROM users u
                    LEFT JOIN user_presence p ON p.user_id = u.id
                    WHERE u.username = ? COLLATE NOCASE AND u.id != ?
                    """,
                    (username, user_id),
                ).fetchone()
            if user is None:
                self.send_json(404, {"error": "Пользователь не найден."})
                return
            self.send_json(200, {
                "user_id": user["id"],
                **describe_presence(user["last_seen_at"]),
            })
            return

        if path.path == "/api/notifications":
            query = parse_qs(path.query)
            initial = query.get("initial", ["0"])[0] == "1"
            try:
                after_id = max(0, int(query.get("after", ["0"])[0]))
                if after_id > MAX_SQLITE_ID:
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "Неверный номер уведомления."})
                return
            with connect_database() as connection:
                access_clause = """
                    (
                        (m.group_id IS NULL AND (m.sender_id = ? OR m.recipient_id = ?))
                        OR (m.group_id IS NOT NULL AND EXISTS (
                            SELECT 1 FROM group_members gm
                            WHERE gm.group_id = m.group_id AND gm.user_id = ?
                        ))
                    )
                """
                if initial:
                    latest = connection.execute(
                        "SELECT COALESCE(MAX(m.id), 0) FROM messages m WHERE " + access_clause,
                        (user_id, user_id, user_id),
                    ).fetchone()[0]
                    self.send_json(200, {"messages": [], "cursor": latest})
                    return

                rows = connection.execute(
                    """
                    SELECT m.id, m.sender_id, m.text, m.kind, m.deleted_at,
                           sender.username AS sender, sender.display_name AS sender_display,
                           recipient.username AS recipient,
                           recipient.display_name AS recipient_display,
                           g.name AS group_name, m.group_id,
                           CASE WHEN m.group_id IS NOT NULL
                                THEN 'group:' || m.group_id
                                WHEN m.sender_id = ? THEN recipient.username
                                ELSE sender.username END AS conversation_id
                    FROM messages m
                    JOIN users sender ON sender.id = m.sender_id
                    LEFT JOIN users recipient ON recipient.id = CASE
                        WHEN m.sender_id = ? THEN m.recipient_id ELSE m.sender_id END
                    LEFT JOIN chat_groups g ON g.id = m.group_id
                    WHERE m.id > ? AND m.deleted_at IS NULL AND """ + access_clause + """
                    ORDER BY m.id
                    LIMIT 100
                    """,
                    (user_id, user_id, after_id, user_id, user_id, user_id),
                ).fetchall()
            next_cursor = rows[-1]["id"] if rows else after_id
            self.send_json(200, {
                "cursor": next_cursor,
                "messages": [
                    {
                        "id": row["id"],
                        "sender": row["sender"],
                        "sender_display": row["sender_display"] or row["sender"],
                        "conversation_id": row["conversation_id"],
                        "conversation_name": row["group_name"] or (
                            row["sender_display"] or row["sender"]
                            if row["sender_id"] != user_id
                            else row["recipient_display"] or row["recipient"]
                        ),
                        "text": "Сообщение удалено" if row["deleted_at"] else row["text"],
                        "kind": row["kind"],
                        "deleted": row["deleted_at"] is not None,
                    }
                    for row in rows
                ],
            })
            return

        if path.path == "/api/calls/incoming":
            now = time.time()
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE status = 'ringing' AND created_at < ?
                    """,
                    (now, now - 60),
                )
                calls = connection.execute(
                    """
                    SELECT c.id, c.mode, u.username, u.display_name
                    FROM call_sessions c
                    JOIN users u ON u.id = c.caller_id
                    WHERE c.callee_id = ? AND c.status = 'ringing'
                    ORDER BY c.created_at DESC LIMIT 5
                    """,
                    (user_id,),
                ).fetchall()
            self.send_json(200, [
                {"id": row["id"], "mode": row["mode"], "username": row["username"],
                 "display_name": row["display_name"] or row["username"]}
                for row in calls
            ])
            return

        call_match = re.fullmatch(r"/api/calls/([A-Za-z0-9_-]+)", path.path)
        if call_match:
            query = parse_qs(path.query)
            try:
                after_id = max(0, int(query.get("after", ["0"])[0]))
                if after_id > MAX_SQLITE_ID:
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "Неверный номер сигнала."})
                return
            call_id = call_match.group(1)
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE id = ? AND status = 'ringing' AND created_at < ?
                    """,
                    (time.time(), call_id, time.time() - 60),
                )
                call = connection.execute(
                    """
                    SELECT id, caller_id, callee_id, mode, status
                    FROM call_sessions
                    WHERE id = ? AND (caller_id = ? OR callee_id = ?)
                    """,
                    (call_id, user_id, user_id),
                ).fetchone()
                if call is None:
                    self.send_json(404, {"error": "Звонок не найден."})
                    return
                signals = connection.execute(
                    """
                    SELECT id, kind, payload FROM call_signals
                    WHERE call_id = ? AND sender_id != ? AND id > ?
                    ORDER BY id LIMIT 100
                    """,
                    (call_id, user_id, after_id),
                ).fetchall()
            self.send_json(200, {
                "id": call["id"],
                "caller_id": call["caller_id"],
                "callee_id": call["callee_id"],
                "mode": call["mode"],
                "status": call["status"],
                "signals": [
                    {"id": row["id"], "kind": row["kind"], "payload": json.loads(row["payload"])}
                    for row in signals
                ],
            })
            return

        if path.path == "/api/groups":
            with connect_database() as connection:
                groups = connection.execute(
                    """
                    SELECT g.id, g.name, g.invite_code, COUNT(members.user_id) AS member_count
                    FROM chat_groups g
                    JOIN group_members mine ON mine.group_id = g.id AND mine.user_id = ?
                    JOIN group_members members ON members.group_id = g.id
                    GROUP BY g.id
                    ORDER BY g.id DESC
                    """,
                    (user_id,),
                ).fetchall()
            self.send_json(200, [
                {"id": row["id"], "name": row["name"],
                 "invite_code": row["invite_code"], "member_count": row["member_count"]}
                for row in groups
            ])
            return

        if path.path == "/api/chats":
            with connect_database() as connection:
                rows = connection.execute(
                    """
                    SELECT u.id, u.username, u.display_name, u.digital_id,
                           u.avatar_data IS NOT NULL AS has_avatar,
                           p.last_seen_at,
                           m.text, m.kind, m.created_at, m.deleted_at
                    FROM (
                        SELECT CASE WHEN sender_id = ? THEN recipient_id ELSE sender_id END
                                   AS partner_id,
                               MAX(id) AS last_message_id
                        FROM messages
                        WHERE group_id IS NULL AND (sender_id = ? OR recipient_id = ?)
                        GROUP BY partner_id
                    ) recent
                    JOIN users u ON u.id = recent.partner_id
                    LEFT JOIN user_presence p ON p.user_id = u.id
                    JOIN messages m ON m.id = recent.last_message_id
                    ORDER BY recent.last_message_id DESC
                    LIMIT 100
                    """,
                    (user_id, user_id, user_id),
                ).fetchall()
            chats = []
            for row in rows:
                if row["deleted_at"]:
                    preview = "Сообщение удалено"
                elif row["kind"] == "media":
                    preview = "Фото или видео"
                elif row["kind"] == "voice":
                    preview = "Голосовое сообщение"
                elif row["kind"] == "poll":
                    preview = "Опрос: " + (row["text"] or "")
                else:
                    preview = row["text"] or ""
                chats.append({
                    "id": row["id"],
                    "username": row["username"],
                    "display_name": row["display_name"] or row["username"],
                    "digital_id": row["digital_id"],
                    "has_avatar": bool(row["has_avatar"]),
                    **describe_presence(row["last_seen_at"]),
                    "last_message": preview[:120],
                    "last_time": row["created_at"],
                })
            self.send_json(200, chats)
            return

        if path.path == "/api/pins":
            query = parse_qs(path.query)
            conversation = query.get("with", [""])[0]
            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", conversation)
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    scope = "m.group_id = ?"
                    params = (group_id,)
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (conversation,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    scope = """
                        m.group_id IS NULL AND (
                            (m.sender_id = ? AND m.recipient_id = ?)
                            OR (m.sender_id = ? AND m.recipient_id = ?)
                        )
                    """
                    params = (user_id, recipient["id"], recipient["id"], user_id)
                rows = connection.execute(
                    """
                    SELECT m.id, m.text, m.kind, m.deleted_at, m.media_name,
                           sender.username AS sender, sender.display_name AS sender_display,
                           p.pinned_at
                    FROM message_pins p
                    JOIN messages m ON m.id = p.message_id
                    JOIN users sender ON sender.id = m.sender_id
                    WHERE m.deleted_at IS NULL AND """ + scope + """
                    ORDER BY p.pinned_at DESC
                    LIMIT 20
                    """,
                    params,
                ).fetchall()
            self.send_json(200, [
                {
                    "id": row["id"],
                    "text": row["text"],
                    "kind": row["kind"],
                    "media_name": row["media_name"],
                    "sender": row["sender_display"] or row["sender"],
                    "pinned_at": row["pinned_at"],
                }
                for row in rows
            ])
            return

        if path.path == "/api/media":
            query = parse_qs(path.query)
            try:
                message_id = int(query.get("id", ["0"])[0])
            except ValueError:
                self.send_json(400, {"error": "Неверный номер файла."})
                return
            if not 0 <= message_id <= MAX_SQLITE_ID:
                self.send_json(400, {"error": "Неверный номер файла."})
                return
            with connect_database() as connection:
                media = connection.execute(
                    """
                    SELECT media_type, media_data
                    FROM messages
                    WHERE id = ? AND kind IN ('media', 'voice') AND deleted_at IS NULL
                        AND (
                            (group_id IS NULL AND (sender_id = ? OR recipient_id = ?))
                            OR (group_id IS NOT NULL AND EXISTS (
                                SELECT 1 FROM group_members gm
                                WHERE gm.group_id = messages.group_id AND gm.user_id = ?
                            ))
                        )
                    """,
                    (message_id, user_id, user_id, user_id),
                ).fetchone()
            if media is None:
                self.send_json(404, {"error": "Файл не найден."})
                return
            body = media["media_data"]
            self.send_response(200)
            self.send_header("Content-Type", media["media_type"])
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Content-Disposition", "inline; filename=\"media\"")
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.path == "/api/avatar":
            query = parse_qs(path.query)
            try:
                avatar_user_id = int(query.get("id", ["0"])[0])
            except ValueError:
                self.send_json(400, {"error": "Неверный номер пользователя."})
                return
            if not 0 < avatar_user_id <= MAX_SQLITE_ID:
                self.send_json(400, {"error": "Неверный номер пользователя."})
                return
            with connect_database() as connection:
                avatar = connection.execute(
                    "SELECT avatar_data FROM users WHERE id = ?",
                    (avatar_user_id,),
                ).fetchone()
            if avatar is None or avatar["avatar_data"] is None:
                self.send_json(404, {"error": "Аватар не найден."})
                return
            body = avatar["avatar_data"]
            self.send_response(200)
            self.send_header("Content-Type", "image/jpeg")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)
            return

        if path.path == "/api/users":
            query = parse_qs(path.query)
            search = query.get("search", [""])[0].strip()
            if len(search) < 2:
                self.send_json(200, [])
                return
            search = search[:30].casefold()
            with connect_database() as connection:
                rows = connection.execute(
                    """
                    SELECT u.id, u.username, u.display_name, u.digital_id,
                        u.avatar_data IS NOT NULL AS has_avatar,
                        p.last_seen_at,
                        (SELECT m.text FROM messages m
                         WHERE (m.sender_id = ? AND m.recipient_id = u.id)
                            OR (m.sender_id = u.id AND m.recipient_id = ?)
                         ORDER BY m.id DESC LIMIT 1) AS last_message
                    FROM users u
                    LEFT JOIN user_presence p ON p.user_id = u.id
                    WHERE u.id != ?
                    ORDER BY u.username COLLATE NOCASE
                    LIMIT 5000
                    """,
                    (user_id, user_id, user_id),
                ).fetchall()
            matching_rows = [
                row for row in rows
                if search in row["username"].casefold()
                or search in row["display_name"].casefold()
                or search in row["digital_id"].casefold()
            ][:50]
            self.send_json(200, [
                {"id": row["id"], "username": row["username"],
                 "display_name": row["display_name"] or row["username"],
                 "digital_id": row["digital_id"],
                 "has_avatar": bool(row["has_avatar"]),
                 **describe_presence(row["last_seen_at"]),
                 "last_message": row["last_message"] or ""}
                for row in matching_rows
            ])
            return

        if path.path == "/api/polls":
            query = parse_qs(path.query)
            recipient_name = query.get("with", [""])[0]
            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                if group_match:
                    group_id = int(group_match.group(1))
                    group = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    poll_rows = connection.execute(
                        """
                        SELECT p.id FROM polls p
                        JOIN messages m ON m.id = p.message_id
                        WHERE m.group_id = ? AND m.deleted_at IS NULL
                        """,
                        (group_id,),
                    ).fetchall()
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    poll_rows = connection.execute(
                        """
                        SELECT p.id
                        FROM polls p
                        JOIN messages m ON m.id = p.message_id
                        WHERE (
                            (m.sender_id = ? AND m.recipient_id = ?)
                            OR (m.sender_id = ? AND m.recipient_id = ?)
                        ) AND m.deleted_at IS NULL
                        """,
                        (user_id, recipient["id"], recipient["id"], user_id),
                    ).fetchall()
                result = [load_poll(connection, row["id"], user_id) for row in poll_rows]
            self.send_json(200, result)
            return

        if path.path == "/api/messages":
            query = parse_qs(path.query)
            recipient_name = query.get("with", [""])[0]
            try:
                after_id = max(0, int(query.get("after", ["0"])[0]))
                before_id = (
                    max(1, int(query["before"][0])) if "before" in query else None
                )
                if (after_id > MAX_SQLITE_ID
                        or before_id is not None and before_id > MAX_SQLITE_ID):
                    raise ValueError
            except ValueError:
                self.send_json(400, {"error": "Неверный номер сообщения."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                if group_match:
                    group_id = int(group_match.group(1))
                    group = connection.execute(
                        """
                        SELECT g.id, g.name FROM chat_groups g
                        JOIN group_members gm ON gm.group_id = g.id
                        WHERE g.id = ? AND gm.user_id = ?
                        """,
                        (group_id, user_id),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    message_query = """
                        SELECT m.id, m.sender_id, m.recipient_id, m.group_id,
                               sender.username AS sender,
                               sender.display_name AS sender_display, m.text, m.created_at,
                               m.kind, m.media_name, m.media_type, m.edited_at, m.deleted_at,
                               m.reply_to_id, p.id AS poll_id,
                               reply.text AS reply_text, reply.kind AS reply_kind,
                               reply.deleted_at AS reply_deleted_at,
                               reply_sender.username AS reply_sender,
                               reply_sender.display_name AS reply_sender_display
                        FROM messages m
                        JOIN users sender ON sender.id = m.sender_id
                        LEFT JOIN polls p ON p.message_id = m.id
                        LEFT JOIN messages reply ON reply.id = m.reply_to_id
                        LEFT JOIN users reply_sender ON reply_sender.id = reply.sender_id
                        WHERE m.group_id = ? AND m.id %s ?
                        ORDER BY m.id DESC
                        LIMIT 100
                    """ % (">" if before_id is None else "<")
                    params = (group_id, after_id if before_id is None else before_id)
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    message_query = """
                        SELECT m.id, m.sender_id, m.recipient_id, m.group_id,
                               sender.username AS sender,
                               sender.display_name AS sender_display, m.text, m.created_at,
                               m.kind, m.media_name, m.media_type, m.edited_at, m.deleted_at,
                               m.reply_to_id, p.id AS poll_id,
                               reply.text AS reply_text, reply.kind AS reply_kind,
                               reply.deleted_at AS reply_deleted_at,
                               reply_sender.username AS reply_sender,
                               reply_sender.display_name AS reply_sender_display
                        FROM messages m
                        JOIN users sender ON sender.id = m.sender_id
                        LEFT JOIN polls p ON p.message_id = m.id
                        LEFT JOIN messages reply ON reply.id = m.reply_to_id
                        LEFT JOIN users reply_sender ON reply_sender.id = reply.sender_id
                        WHERE m.id %s ? AND (
                            (m.sender_id = ? AND m.recipient_id = ?)
                            OR (m.sender_id = ? AND m.recipient_id = ?)
                        )
                        ORDER BY m.id DESC
                        LIMIT 100
                    """ % (">" if before_id is None else "<")
                    params = (
                        after_id if before_id is None else before_id,
                        user_id, recipient["id"], recipient["id"], user_id,
                    )
                rows = connection.execute(message_query, params).fetchall()
                result = []
                for row in reversed(rows):
                    deleted = row["deleted_at"] is not None
                    message = {
                        "id": row["id"],
                        "sender": row["sender"],
                        "sender_display": row["sender_display"] or row["sender"],
                        "text": "Сообщение удалено" if deleted else row["text"],
                        "time": row["created_at"],
                        "kind": row["kind"],
                        "media_name": row["media_name"],
                        "media_type": row["media_type"],
                        "edited": row["edited_at"] is not None,
                        "deleted": deleted,
                        "reply_to_id": row["reply_to_id"],
                    }
                    if row["reply_to_id"] is not None:
                        message["reply"] = {
                            "id": row["reply_to_id"],
                            "sender": row["reply_sender_display"] or row["reply_sender"],
                            "text": "Сообщение удалено"
                            if row["reply_deleted_at"] is not None
                            else row["reply_text"],
                            "kind": row["reply_kind"],
                        }
                    if row["poll_id"] is not None and not deleted:
                        message["poll"] = load_poll(connection, row["poll_id"], user_id)
                    result.append(message)

                message_ids = [message["id"] for message in result]
                reactions_by_message = {}
                pins_by_message = {}
                if message_ids:
                    placeholders = ",".join("?" for _ in message_ids)
                    reaction_rows = connection.execute(
                        """
                        SELECT message_id, emoji, COUNT(*) AS count,
                               MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS reacted
                        FROM message_reactions
                        WHERE message_id IN (""" + placeholders + """)
                        GROUP BY message_id, emoji
                        """,
                        (user_id, *message_ids),
                    ).fetchall()
                    reactions_by_message = {}
                    for reaction in reaction_rows:
                        reactions_by_message.setdefault(reaction["message_id"], []).append({
                            "emoji": reaction["emoji"],
                            "count": reaction["count"],
                            "reacted": bool(reaction["reacted"]),
                        })
                    pinned_rows = connection.execute(
                        "SELECT message_id, pinned_by FROM message_pins "
                        "WHERE message_id IN (" + placeholders + ")",
                        message_ids,
                    ).fetchall()
                    pins_by_message = {
                        row["message_id"]: row["pinned_by"] for row in pinned_rows
                    }
                    for message in result:
                        message["reactions"] = reactions_by_message.get(message["id"], [])
                        message["pinned"] = message["id"] in pins_by_message
            self.send_json(200, result)
            return

        self.send_json(404, {"error": "Страница не найдена."})

    def do_POST(self):
        if not valid_origin(self):
            self.send_json(403, {"error": "Запрос отклонён: неверный источник."})
            return

        path = urlparse(self.path).path
        if path == "/api/logout":
            self.send_json(200, {"ok": True}, self.clear_session())
            return

        if path == "/api/avatar":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            data = self.read_json(MAX_AVATAR_REQUEST_BYTES)
            if data is None:
                return

            if data.get("remove") is True:
                avatar_data = None
            else:
                encoded_data = data.get("data")
                if not isinstance(encoded_data, str):
                    self.send_json(400, {"error": "Выбери изображение JPEG."})
                    return
                try:
                    avatar_data = base64.b64decode(encoded_data, validate=True)
                except (binascii.Error, ValueError):
                    self.send_json(400, {"error": "Файл аватара повреждён."})
                    return
                if not avatar_data or len(avatar_data) > MAX_AVATAR_BYTES:
                    self.send_json(413, {"error": "Размер аватара должен быть не больше 512 КБ."})
                    return
                if not (
                    avatar_data.startswith(b"\xff\xd8\xff")
                    and avatar_data.endswith(b"\xff\xd9")
                ):
                    self.send_json(400, {"error": "Аватар должен быть изображением JPEG."})
                    return

            with connect_database() as connection:
                connection.execute(
                    "UPDATE users SET avatar_data = ? WHERE id = ?",
                    (avatar_data, user_id),
                )
            self.send_json(200, {"ok": True, "has_avatar": avatar_data is not None})
            return

        if path == "/api/media":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            data = self.read_json(MAX_MEDIA_REQUEST_BYTES)
            if data is None:
                return
            recipient_name = data.get("to")
            file_name = data.get("name")
            encoded_data = data.get("data")
            if not all(isinstance(value, str) for value in (recipient_name, file_name, encoded_data)):
                self.send_json(400, {"error": "Не удалось прочитать файл."})
                return
            try:
                media_data = base64.b64decode(encoded_data, validate=True)
            except (binascii.Error, ValueError):
                self.send_json(400, {"error": "Файл повреждён."})
                return
            if not media_data or len(media_data) > MAX_MEDIA_BYTES:
                self.send_json(413, {"error": "Размер файла должен быть не больше 20 МБ."})
                return

            media_signatures = (
                (b"\xff\xd8\xff", "image/jpeg", ".jpg"),
                (b"\x89PNG\r\n\x1a\n", "image/png", ".png"),
                (b"GIF87a", "image/gif", ".gif"),
                (b"GIF89a", "image/gif", ".gif"),
            )
            detected_media = next(
                ((mime, extension) for signature, mime, extension in media_signatures
                 if media_data.startswith(signature)),
                None,
            )
            if detected_media is None and len(media_data) >= 12:
                if media_data.startswith(b"RIFF") and media_data[8:12] == b"WEBP":
                    detected_media = ("image/webp", ".webp")
                elif media_data[4:8] == b"ftyp":
                    detected_media = ("video/mp4", ".mp4")
                elif media_data.startswith(b"\x1a\x45\xdf\xa3"):
                    detected_media = ("video/webm", ".webm")
            if detected_media is None:
                self.send_json(400, {"error": "Поддерживаются только фото JPEG/PNG/GIF/WebP и видео MP4/WebM."})
                return
            media_type, extension = detected_media
            safe_name = file_name.replace("\\", "/").split("/")[-1]
            safe_name = "".join(
                character for character in safe_name
                if character.isprintable() and character not in "\r\n"
            ).strip()[:120]
            if not safe_name:
                safe_name = "media" + extension

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name.strip())
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name.strip(),),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                stored_bytes = connection.execute(
                    "SELECT COALESCE(SUM(length(media_data)), 0) FROM messages "
                    "WHERE kind IN ('media', 'voice')"
                ).fetchone()[0]
                if stored_bytes + len(media_data) > MAX_TOTAL_MEDIA_BYTES:
                    self.send_json(507, {
                        "error": "Хранилище демо-чата заполнено. Сейчас общий лимит медиа — 100 МБ."
                    })
                    return
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, kind,
                         media_name, media_type, media_data, group_id)
                    VALUES (?, ?, '', ?, 'media', ?, ?, ?, ?)
                    """,
                    (user_id, recipient_id, datetime.now().strftime("%H:%M"),
                     safe_name, media_type, media_data, group_id),
                )
                message_id = cursor.lastrowid
            self.send_json(201, {"ok": True, "id": message_id})
            return

        if path == "/api/voice":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            data = self.read_json(MAX_MEDIA_REQUEST_BYTES)
            if data is None:
                return
            recipient_name = data.get("to")
            encoded_data = data.get("data")
            duration_ms = data.get("duration_ms")
            if (not isinstance(recipient_name, str) or not isinstance(encoded_data, str)
                    or type(duration_ms) is not int or not 1 <= duration_ms <= 120_000):
                self.send_json(400, {"error": "Голосовое сообщение должно длиться не больше 2 минут."})
                return
            try:
                media_data = base64.b64decode(encoded_data, validate=True)
            except (binascii.Error, ValueError):
                self.send_json(400, {"error": "Запись повреждена."})
                return
            if (not media_data or len(media_data) > MAX_MEDIA_BYTES
                    or not media_data.startswith(b"\x1a\x45\xdf\xa3")):
                self.send_json(400, {"error": "Поддерживается запись аудио WebM размером до 20 МБ."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name.strip())
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name.strip(),),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                stored_bytes = connection.execute(
                    "SELECT COALESCE(SUM(length(media_data)), 0) FROM messages "
                    "WHERE kind IN ('media', 'voice')"
                ).fetchone()[0]
                if stored_bytes + len(media_data) > MAX_TOTAL_MEDIA_BYTES:
                    self.send_json(507, {
                        "error": "Хранилище демо-чата заполнено. Общий лимит медиа — 100 МБ."
                    })
                    return
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, kind,
                         media_name, media_type, media_data, group_id)
                    VALUES (?, ?, '', ?, 'voice', ?, 'audio/webm', ?, ?)
                    """,
                    (
                        user_id, recipient_id, datetime.now().strftime("%H:%M"),
                        "Голосовое сообщение.webm", media_data, group_id,
                    ),
                )
                message_id = cursor.lastrowid
            self.send_json(201, {"ok": True, "id": message_id})
            return

        call_signal_match = re.fullmatch(r"/api/calls/([A-Za-z0-9_-]+)/signal", path)
        data = self.read_json(65536 if path == "/api/calls" or call_signal_match else 8192)
        if data is None:
            return

        if path == "/api/presence":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            if data.get("active") is not True:
                self.send_json(400, {"error": "Неверный сигнал активности."})
                return
            last_seen_at = time.time()
            with connect_database() as connection:
                connection.execute(
                    """
                    INSERT INTO user_presence (user_id, last_seen_at)
                    VALUES (?, ?)
                    ON CONFLICT(user_id)
                    DO UPDATE SET last_seen_at = excluded.last_seen_at
                    """,
                    (user_id, last_seen_at),
                )
            self.send_json(200, {
                "ok": True,
                **describe_presence(last_seen_at),
            })
            return

        if path == "/api/typing":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            typing = data.get("typing")
            if type(typing) is not bool:
                self.send_json(400, {"error": "Неверный статус набора текста."})
                return
            with connect_database() as connection:
                chat_key = resolve_typing_chat(connection, data.get("to"), user_id)
                if chat_key is None:
                    self.send_json(404, {"error": "Чат не найден."})
                    return
                now = time.time()
                if typing:
                    connection.execute(
                        """
                        INSERT INTO typing_status (user_id, chat_key, updated_at)
                        VALUES (?, ?, ?)
                        ON CONFLICT(user_id, chat_key)
                        DO UPDATE SET updated_at = excluded.updated_at
                        """,
                        (user_id, chat_key, now),
                    )
                    connection.execute(
                        "DELETE FROM typing_status WHERE updated_at < ?",
                        (now - 10,),
                    )
                else:
                    connection.execute(
                        "DELETE FROM typing_status WHERE user_id = ? AND chat_key = ?",
                        (user_id, chat_key),
                    )
            self.send_json(200, {"ok": True, "typing": typing})
            return

        message_action_match = re.fullmatch(
            r"/api/messages/(\d{1,12})/(edit|delete|reactions|pin)", path
        )
        if message_action_match:
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            message_id = int(message_action_match.group(1))
            action = message_action_match.group(2)
            with connect_database() as connection:
                message = load_accessible_message(connection, message_id, user_id)
                if message is None:
                    self.send_json(404, {"error": "Сообщение не найдено."})
                    return
                if message["deleted_at"] is not None:
                    self.send_json(404, {"error": "Сообщение не найдено."})
                    return

                if action == "edit":
                    text = data.get("text")
                    if message["sender_id"] != user_id or message["kind"] != "text":
                        self.send_json(403, {"error": "Можно редактировать только своё текстовое сообщение."})
                        return
                    if not isinstance(text, str) or not text.strip() or len(text.strip()) > 1000:
                        self.send_json(400, {"error": "Текст должен содержать от 1 до 1000 символов."})
                        return
                    connection.execute(
                        "UPDATE messages SET text = ?, edited_at = ? WHERE id = ?",
                        (text.strip(), utc_timestamp(), message_id),
                    )
                    self.send_json(200, {"ok": True})
                    return

                if action == "delete":
                    if message["sender_id"] != user_id:
                        self.send_json(403, {"error": "Удалить можно только своё сообщение."})
                        return
                    connection.execute(
                        """
                        UPDATE messages
                        SET text = '', media_name = NULL, media_type = NULL,
                            media_data = NULL, deleted_at = ?
                        WHERE id = ?
                        """,
                        (utc_timestamp(), message_id),
                    )
                    connection.execute("DELETE FROM polls WHERE message_id = ?", (message_id,))
                    connection.execute(
                        "DELETE FROM message_reactions WHERE message_id = ?", (message_id,)
                    )
                    connection.execute(
                        "DELETE FROM message_pins WHERE message_id = ?", (message_id,)
                    )
                    self.send_json(200, {"ok": True})
                    return

                if action == "reactions":
                    emoji = data.get("emoji")
                    if emoji not in REACTION_EMOJIS:
                        self.send_json(400, {"error": "Выбери реакцию из списка."})
                        return
                    existing = connection.execute(
                        """
                        SELECT 1 FROM message_reactions
                        WHERE message_id = ? AND user_id = ? AND emoji = ?
                        """,
                        (message_id, user_id, emoji),
                    ).fetchone()
                    if existing:
                        connection.execute(
                            """
                            DELETE FROM message_reactions
                            WHERE message_id = ? AND user_id = ? AND emoji = ?
                            """,
                            (message_id, user_id, emoji),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT OR IGNORE INTO message_reactions
                                (message_id, user_id, emoji, created_at)
                            VALUES (?, ?, ?, ?)
                            """,
                            (message_id, user_id, emoji, utc_timestamp()),
                        )
                    reactions = connection.execute(
                        """
                        SELECT emoji, COUNT(*) AS count,
                               MAX(CASE WHEN user_id = ? THEN 1 ELSE 0 END) AS reacted
                        FROM message_reactions
                        WHERE message_id = ?
                        GROUP BY emoji
                        ORDER BY emoji
                        """,
                        (user_id, message_id),
                    ).fetchall()
                    self.send_json(200, {
                        "reactions": [
                            {"emoji": row["emoji"], "count": row["count"],
                             "reacted": bool(row["reacted"])}
                            for row in reactions
                        ]
                    })
                    return

                pinned = data.get("pinned")
                if type(pinned) is not bool:
                    self.send_json(400, {"error": "Укажи новое состояние закрепления."})
                    return
                if pinned:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO message_pins (message_id, pinned_by, pinned_at)
                        VALUES (?, ?, ?)
                        """,
                        (message_id, user_id, utc_timestamp()),
                    )
                else:
                    connection.execute(
                        "DELETE FROM message_pins WHERE message_id = ?", (message_id,)
                    )
            self.send_json(200, {"pinned": pinned})
            return

        if path == "/api/calls":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            recipient_name = data.get("to")
            mode = data.get("mode")
            if not isinstance(recipient_name, str) or mode not in ("audio", "video"):
                self.send_json(400, {"error": "Укажи собеседника и тип звонка."})
                return
            now = time.time()
            with connect_database() as connection:
                connection.execute(
                    """
                    UPDATE call_sessions SET status = 'ended', updated_at = ?
                    WHERE (status = 'ringing' AND created_at < ?)
                       OR (status = 'accepted' AND updated_at < ?)
                    """,
                    (now, now - 60, now - 6 * 60 * 60),
                )
                connection.execute(
                    """
                    DELETE FROM call_sessions
                    WHERE status IN ('rejected', 'ended') AND updated_at < ?
                    """,
                    (now - 24 * 60 * 60,),
                )
                recipient = connection.execute(
                    "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                    (recipient_name.strip(),),
                ).fetchone()
                if recipient is None or recipient["id"] == user_id:
                    self.send_json(404, {"error": "Собеседник не найден."})
                    return
                busy = connection.execute(
                    """
                    SELECT 1 FROM call_sessions
                    WHERE status IN ('ringing', 'accepted')
                      AND (caller_id IN (?, ?) OR callee_id IN (?, ?))
                    LIMIT 1
                    """,
                    (user_id, recipient["id"], user_id, recipient["id"]),
                ).fetchone()
                if busy:
                    self.send_json(409, {"error": "Один из участников уже разговаривает."})
                    return
                call_id = secrets.token_urlsafe(18)
                connection.execute(
                    """
                    INSERT INTO call_sessions
                        (id, caller_id, callee_id, mode, status, created_at, updated_at)
                    VALUES (?, ?, ?, ?, 'ringing', ?, ?)
                    """,
                    (call_id, user_id, recipient["id"], mode, now, now),
                )
            self.send_json(201, {"id": call_id, "status": "ringing"})
            return

        if call_signal_match:
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            call_id = call_signal_match.group(1)
            kind = data.get("kind")
            payload = data.get("payload", {})
            if kind not in ("accept", "reject", "end", "offer", "answer", "ice"):
                self.send_json(400, {"error": "Неизвестный сигнал звонка."})
                return
            if not isinstance(payload, dict) or len(json.dumps(payload)) > 32000:
                self.send_json(400, {"error": "Данные звонка слишком большие или неверные."})
                return
            if kind in ("offer", "answer") and (
                not isinstance(payload.get("sdp"), str)
                or payload.get("type") not in ("offer", "answer")
            ):
                self.send_json(400, {"error": "Неверное описание соединения."})
                return
            if kind == "ice" and not isinstance(payload.get("candidate"), str):
                self.send_json(400, {"error": "Неверный ICE-кандидат."})
                return

            now = time.time()
            with connect_database() as connection:
                call = connection.execute(
                    """
                    SELECT caller_id, callee_id, status FROM call_sessions
                    WHERE id = ? AND (caller_id = ? OR callee_id = ?)
                    """,
                    (call_id, user_id, user_id),
                ).fetchone()
                if call is None:
                    self.send_json(404, {"error": "Звонок не найден."})
                    return
                is_caller = call["caller_id"] == user_id
                if kind in ("accept", "reject") and is_caller:
                    self.send_json(403, {"error": "Ответить на звонок может только получатель."})
                    return
                if kind == "accept":
                    if call["status"] == "ringing":
                        connection.execute(
                            "UPDATE call_sessions SET status = 'accepted', updated_at = ? WHERE id = ?",
                            (now, call_id),
                        )
                    elif call["status"] != "accepted":
                        self.send_json(409, {"error": "Звонок уже завершён."})
                        return
                elif kind == "reject":
                    if call["status"] != "ringing":
                        self.send_json(409, {"error": "На этот звонок уже нельзя ответить."})
                        return
                    connection.execute(
                        "UPDATE call_sessions SET status = 'rejected', updated_at = ? WHERE id = ?",
                        (now, call_id),
                    )
                elif kind == "end":
                    if call["status"] in ("ringing", "accepted"):
                        connection.execute(
                            "UPDATE call_sessions SET status = 'ended', updated_at = ? WHERE id = ?",
                            (now, call_id),
                        )
                else:
                    expected_sender = call["caller_id"] if kind == "offer" else call["callee_id"]
                    if call["status"] != "accepted" or user_id != expected_sender and kind != "ice":
                        self.send_json(409, {"error": "Сигнал не подходит для текущего состояния звонка."})
                        return
                    connection.execute(
                        """
                        INSERT INTO call_signals (call_id, sender_id, kind, payload, created_at)
                        VALUES (?, ?, ?, ?, ?)
                        """,
                        (call_id, user_id, kind, json.dumps(payload), now),
                    )
                    connection.execute(
                        "UPDATE call_sessions SET updated_at = ? WHERE id = ?",
                        (now, call_id),
                    )
            self.send_json(200, {"ok": True})
            return

        if path in ("/api/groups", "/api/groups/join"):
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return

            with connect_database() as connection:
                if path == "/api/groups":
                    name = data.get("name")
                    if not isinstance(name, str):
                        self.send_json(400, {"error": "Укажи название группы."})
                        return
                    name = name.strip()
                    if not 3 <= len(name) <= 40:
                        self.send_json(400, {"error": "Название группы: от 3 до 40 символов."})
                        return
                    invite_code = secrets.token_hex(6).upper()
                    cursor = connection.execute(
                        """
                        INSERT INTO chat_groups (name, invite_code, created_by, created_at)
                        VALUES (?, ?, ?, ?)
                        """,
                        (name, invite_code, user_id, datetime.now().strftime("%Y-%m-%d %H:%M")),
                    )
                    group_id = cursor.lastrowid
                    connection.execute(
                        "INSERT INTO group_members (group_id, user_id, joined_at) VALUES (?, ?, ?)",
                        (group_id, user_id, datetime.now().isoformat(timespec="seconds")),
                    )
                else:
                    invite_code = data.get("invite_code")
                    if not isinstance(invite_code, str):
                        self.send_json(400, {"error": "Введи код приглашения."})
                        return
                    invite_code = invite_code.strip()
                    if not invite_code or len(invite_code) > 20:
                        self.send_json(400, {"error": "Код приглашения должен содержать до 20 символов."})
                        return
                    if (
                        not allow_rate_limited_request(
                            ("group-join-ip", self.client_address[0]), 600
                        )
                        or not allow_rate_limited_request(
                            ("group-invite", user_id, invite_code.casefold()),
                            15,
                        )
                    ):
                        self.send_json(
                            429,
                            {"error": "Слишком много попыток. Подожди минуту и попробуй снова."},
                            [("Retry-After", str(RATE_LIMIT_WINDOW))],
                        )
                        return
                    group = connection.execute(
                        """
                        SELECT id, name, invite_code FROM chat_groups
                        WHERE invite_code = ? COLLATE NOCASE
                        """,
                        (invite_code.strip(),),
                    ).fetchone()
                    if group is None:
                        self.send_json(404, {"error": "Группа с таким кодом не найдена."})
                        return
                    group_id = group["id"]
                    invite_code = group["invite_code"]
                    name = group["name"]
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO group_members (group_id, user_id, joined_at)
                        VALUES (?, ?, ?)
                        """,
                        (group_id, user_id, datetime.now().isoformat(timespec="seconds")),
                    )
            self.send_json(201, {
                "id": group_id, "name": name, "invite_code": invite_code
            })
            return

        if path in ("/api/register", "/api/login"):
            username = data.get("username")
            password = data.get("password")
            rate_key = ("auth-ip", self.client_address[0])
            if (
                not allow_rate_limited_request(rate_key, 300)
                or (
                    isinstance(username, str)
                    and bool(username.strip())
                    and not allow_rate_limited_request(
                        (
                            "auth-account",
                            self.client_address[0],
                            username.strip().casefold()[:20],
                        ),
                        10,
                    )
                )
            ):
                self.send_json(
                    429,
                    {"error": "Слишком много попыток. Подожди минуту и попробуй снова."},
                    [("Retry-After", str(RATE_LIMIT_WINDOW))],
                )
                return
            if not isinstance(username, str) or not isinstance(password, str):
                self.send_json(400, {"error": "Введи имя пользователя и пароль."})
                return

            username = username.strip()
            if not re.fullmatch(r"[A-Za-z0-9_]{3,20}", username):
                self.send_json(400, {
                    "error": "Имя: 3–20 символов, только латинские буквы, цифры и _."
                })
                return
            if len(password) < 8 or len(password) > 128:
                self.send_json(400, {"error": "Пароль должен содержать от 8 до 128 символов."})
                return

            with connect_database() as connection:
                user = connection.execute(
                    "SELECT id, username, password_salt, password_hash, display_name, digital_id, "
                    "avatar_data IS NOT NULL AS has_avatar "
                    "FROM users WHERE username = ? COLLATE NOCASE",
                    (username,),
                ).fetchone()
                if path == "/api/register":
                    if user is not None:
                        self.send_json(409, {"error": "Это имя уже занято."})
                        return
                    salt = secrets.token_bytes(16)
                    password_hash = make_password_hash(password, salt)
                    digital_id = unique_digital_id(connection)
                    try:
                        cursor = connection.execute(
                            "INSERT INTO users "
                            "(username, password_salt, password_hash, display_name, digital_id) "
                            "VALUES (?, ?, ?, ?, ?)",
                            (username, salt, password_hash, username, digital_id),
                        )
                    except sqlite3.IntegrityError:
                        self.send_json(409, {"error": "Это имя уже занято."})
                        return
                    user_id = cursor.lastrowid
                    saved_username = username
                    saved_display_name = username
                    saved_digital_id = digital_id
                    saved_has_avatar = False
                else:
                    if user is None:
                        self.send_json(401, {"error": "Неверное имя или пароль."})
                        return
                    candidate = make_password_hash(password, user["password_salt"])
                    if not hmac.compare_digest(candidate, user["password_hash"]):
                        self.send_json(401, {"error": "Неверное имя или пароль."})
                        return
                    user_id = user["id"]
                    saved_username = user["username"]
                    saved_display_name = user["display_name"] or user["username"]
                    saved_digital_id = user["digital_id"]
                    saved_has_avatar = bool(user["has_avatar"])

            headers = self.set_session(user_id)
            self.send_json(200, {
                "id": user_id,
                "username": saved_username,
                "display_name": saved_display_name,
                "digital_id": saved_digital_id,
                "has_avatar": saved_has_avatar,
            }, headers)
            return

        if path == "/api/profile":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            display_name = data.get("display_name")
            if not isinstance(display_name, str):
                self.send_json(400, {"error": "Имя профиля должно быть текстом."})
                return
            display_name = display_name.strip()
            if not display_name or len(display_name) > 30:
                self.send_json(400, {"error": "Имя должно содержать от 1 до 30 символов."})
                return
            with connect_database() as connection:
                connection.execute(
                    "UPDATE users SET display_name = ? WHERE id = ?",
                    (display_name, user_id),
                )
                user = connection.execute(
                    "SELECT id, username, display_name, digital_id, "
                    "avatar_data IS NOT NULL AS has_avatar FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
            self.send_json(200, {
                "id": user["id"],
                "username": user["username"],
                "display_name": user["display_name"],
                "digital_id": user["digital_id"],
                "has_avatar": bool(user["has_avatar"]),
            })
            return

        if path == "/api/password":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            old_password = data.get("old_password")
            new_password = data.get("new_password")
            if not isinstance(old_password, str) or not isinstance(new_password, str):
                self.send_json(400, {"error": "Введи текущий и новый пароль."})
                return
            if len(new_password) < 8 or len(new_password) > 128:
                self.send_json(400, {"error": "Новый пароль должен содержать от 8 до 128 символов."})
                return
            with connect_database() as connection:
                user = connection.execute(
                    "SELECT password_salt, password_hash FROM users WHERE id = ?",
                    (user_id,),
                ).fetchone()
                if user is None:
                    self.send_json(401, {"error": "Аккаунт не найден."})
                    return
                candidate = make_password_hash(old_password, user["password_salt"])
                if not hmac.compare_digest(candidate, user["password_hash"]):
                    self.send_json(400, {"error": "Текущий пароль введён неверно."})
                    return
                salt = secrets.token_bytes(16)
                password_hash = make_password_hash(new_password, salt)
                connection.execute(
                    "UPDATE users SET password_salt = ?, password_hash = ? WHERE id = ?",
                    (salt, password_hash, user_id),
                )
            self.send_json(
                200, {"ok": True}, self.set_session(user_id, revoke_existing=True)
            )
            return

        if path == "/api/messages":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return

            recipient_name = data.get("to")
            text = data.get("text")
            reply_to_id = data.get("reply_to_id")
            if (not isinstance(recipient_name, str) or not isinstance(text, str)
                    or (reply_to_id is not None
                        and (type(reply_to_id) is not int
                             or not 0 < reply_to_id <= MAX_SQLITE_ID))):
                self.send_json(400, {"error": "Нужны имя собеседника и текст сообщения."})
                return
            recipient_name = recipient_name.strip()
            text = text.strip()
            if not text or len(text) > 1000:
                self.send_json(400, {"error": "Сообщение должно содержать от 1 до 1000 символов."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name)
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name,),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                if reply_to_id is not None:
                    reply_target = load_accessible_message(
                        connection, reply_to_id, user_id
                    )
                    conversation = {
                        "sender_id": user_id,
                        "recipient_id": recipient_id,
                        "group_id": group_id,
                    }
                    if (reply_target is None
                            or not same_conversation(conversation, reply_target)):
                        self.send_json(400, {
                            "error": "Ответить можно только на сообщение из этого чата."
                        })
                        return
                created_at = datetime.now().strftime("%H:%M")
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, group_id, reply_to_id)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (user_id, recipient_id, text, created_at, group_id, reply_to_id),
                )
            self.send_json(201, {"ok": True, "id": cursor.lastrowid})
            return

        if path == "/api/polls":
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            recipient_name = data.get("to")
            question = data.get("question")
            options = data.get("options")
            if not isinstance(recipient_name, str) or not isinstance(question, str):
                self.send_json(400, {"error": "Укажи собеседника и вопрос."})
                return
            if not isinstance(options, list) or not 2 <= len(options) <= 6:
                self.send_json(400, {"error": "В опросе должно быть от 2 до 6 вариантов."})
                return
            question = question.strip()
            options = [option.strip() if isinstance(option, str) else "" for option in options]
            if not question or len(question) > 200:
                self.send_json(400, {"error": "Вопрос должен содержать от 1 до 200 символов."})
                return
            if any(not option or len(option) > 100 for option in options):
                self.send_json(400, {"error": "Каждый вариант должен содержать от 1 до 100 символов."})
                return
            if len({option.casefold() for option in options}) != len(options):
                self.send_json(400, {"error": "Варианты ответа не должны повторяться."})
                return

            with connect_database() as connection:
                group_match = re.fullmatch(r"group:(\d{1,12})", recipient_name.strip())
                group_id = None
                if group_match:
                    group_id = int(group_match.group(1))
                    member = connection.execute(
                        "SELECT 1 FROM group_members WHERE group_id = ? AND user_id = ?",
                        (group_id, user_id),
                    ).fetchone()
                    if member is None:
                        self.send_json(404, {"error": "Группа не найдена."})
                        return
                    recipient_id = user_id
                else:
                    recipient = connection.execute(
                        "SELECT id FROM users WHERE username = ? COLLATE NOCASE",
                        (recipient_name.strip(),),
                    ).fetchone()
                    if recipient is None or recipient["id"] == user_id:
                        self.send_json(404, {"error": "Собеседник не найден."})
                        return
                    recipient_id = recipient["id"]
                cursor = connection.execute(
                    """
                    INSERT INTO messages
                        (sender_id, recipient_id, text, created_at, kind, group_id)
                    VALUES (?, ?, ?, ?, 'poll', ?)
                    """,
                    (user_id, recipient_id, question, datetime.now().strftime("%H:%M"), group_id),
                )
                poll_cursor = connection.execute(
                    "INSERT INTO polls (message_id, question) VALUES (?, ?)",
                    (cursor.lastrowid, question),
                )
                connection.executemany(
                    "INSERT INTO poll_options (poll_id, text) VALUES (?, ?)",
                    [(poll_cursor.lastrowid, option) for option in options],
                )
            self.send_json(201, {"ok": True})
            return

        vote_match = re.fullmatch(r"/api/polls/(\d{1,19})/vote", path)
        if vote_match:
            user_id = self.get_session()
            if user_id is None:
                self.send_json(401, {"error": "Нужно войти в аккаунт."})
                return
            try:
                poll_id = int(vote_match.group(1))
            except ValueError:
                self.send_json(400, {"error": "Неверный номер опроса."})
                return
            if poll_id > MAX_SQLITE_ID:
                self.send_json(400, {"error": "Неверный номер опроса."})
                return
            option_ids = data.get("option_ids")
            if (not isinstance(option_ids, list) or not option_ids
                    or any(type(option_id) is not int for option_id in option_ids)
                    or len(set(option_ids)) != len(option_ids)):
                self.send_json(400, {"error": "Выбери один или несколько вариантов."})
                return
            with connect_database() as connection:
                poll = connection.execute(
                    """
                    SELECT p.id
                    FROM polls p JOIN messages m ON m.id = p.message_id
                    WHERE p.id = ? AND m.deleted_at IS NULL AND (
                        (m.group_id IS NULL AND (m.sender_id = ? OR m.recipient_id = ?))
                        OR (m.group_id IS NOT NULL AND EXISTS (
                            SELECT 1 FROM group_members gm
                            WHERE gm.group_id = m.group_id AND gm.user_id = ?
                        ))
                    )
                    """,
                    (poll_id, user_id, user_id, user_id),
                ).fetchone()
                if poll is None:
                    self.send_json(404, {"error": "Опрос не найден."})
                    return
                valid_options = connection.execute(
                    "SELECT id FROM poll_options WHERE poll_id = ?",
                    (poll_id,),
                ).fetchall()
                valid_option_ids = {row["id"] for row in valid_options}
                if len(option_ids) > len(valid_option_ids) or not set(option_ids) <= valid_option_ids:
                    self.send_json(400, {"error": "Выбраны неизвестные варианты ответа."})
                    return
                connection.execute(
                    """
                    DELETE FROM poll_votes
                    WHERE user_id = ? AND option_id IN (
                        SELECT id FROM poll_options WHERE poll_id = ?
                    )
                    """,
                    (user_id, poll_id),
                )
                connection.executemany(
                    "INSERT INTO poll_votes (user_id, option_id) VALUES (?, ?)",
                    [(user_id, option_id) for option_id in option_ids],
                )
                poll_data = load_poll(connection, poll_id, user_id)
            self.send_json(200, poll_data)
            return

        self.send_json(404, {"error": "Страница не найдена."})

    def log_message(self, format_string, *args):
        print("%s - %s" % (self.address_string(), format_string % args))


def main():
    initialize_database()
    server = ThreadingHTTPServer((HOST, PORT), ChatHandler)

    print("Учебный мессенджер запущен.")
    if HOST == "127.0.0.1":
        print("Открой в браузере: http://localhost:%s" % PORT)
        print("Сервер доступен только на этом компьютере.")
    else:
        print("Сервер слушает %s:%s" % (HOST, PORT))
    print("Остановить сервер можно сочетанием Ctrl+C.")
    print("Это демо: на бесплатном хостинге база может очищаться; не отправляй личные данные.")

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗавершаю работу мессенджера.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
