import secrets
from datetime import datetime, timezone


REACTION_EMOJIS = (
    "😀", "😂", "😊", "😍", "🥰", "😎", "😭", "😡", "👍",
    "👎", "❤️", "🎉", "🔥", "🙏", "🤔", "✅", "👋", "💯",
)


def new_digital_id():
    return "MR-" + secrets.token_hex(6).upper()


def unique_digital_id(connection):
    while True:
        digital_id = new_digital_id()
        if connection.execute(
            "SELECT 1 FROM users WHERE digital_id = ?", (digital_id,)
        ).fetchone() is None:
            return digital_id


def utc_timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def load_accessible_message(connection, message_id, user_id):
    return connection.execute(
        """
        SELECT m.*
        FROM messages m
        WHERE m.id = ? AND (
            (m.group_id IS NULL AND (m.sender_id = ? OR m.recipient_id = ?))
            OR (m.group_id IS NOT NULL AND EXISTS (
                SELECT 1 FROM group_members gm
                WHERE gm.group_id = m.group_id AND gm.user_id = ?
            ))
        )
        """,
        (message_id, user_id, user_id, user_id),
    ).fetchone()


def same_conversation(first, second):
    if first["group_id"] is not None:
        return second["group_id"] == first["group_id"]
    if second["group_id"] is not None:
        return False
    return (
        first["sender_id"] == second["sender_id"]
        and first["recipient_id"] == second["recipient_id"]
    ) or (
        first["sender_id"] == second["recipient_id"]
        and first["recipient_id"] == second["sender_id"]
    )


def migrate_update_schema(connection):
    user_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(users)")
    }
    if "digital_id" not in user_columns:
        connection.execute("ALTER TABLE users ADD COLUMN digital_id TEXT")

    message_columns = {
        row["name"] for row in connection.execute("PRAGMA table_info(messages)")
    }
    for column, definition in (
        ("reply_to_id", "INTEGER REFERENCES messages(id) ON DELETE SET NULL"),
        ("edited_at", "TEXT"),
        ("deleted_at", "TEXT"),
    ):
        if column not in message_columns:
            connection.execute(
                "ALTER TABLE messages ADD COLUMN %s %s" % (column, definition)
            )

    existing_ids = set()
    existing_users = connection.execute(
        "SELECT id, digital_id FROM users WHERE digital_id IS NOT NULL AND digital_id != ''"
    ).fetchall()
    for user in existing_users:
        digital_id = user["digital_id"]
        if digital_id in existing_ids:
            digital_id = new_digital_id()
            while digital_id in existing_ids:
                digital_id = new_digital_id()
            connection.execute(
                "UPDATE users SET digital_id = ? WHERE id = ?",
                (digital_id, user["id"]),
            )
        existing_ids.add(digital_id)
    users_without_id = connection.execute(
        "SELECT id FROM users WHERE digital_id IS NULL OR digital_id = ''"
    ).fetchall()
    for user in users_without_id:
        digital_id = new_digital_id()
        while digital_id in existing_ids:
            digital_id = new_digital_id()
        connection.execute(
            "UPDATE users SET digital_id = ? WHERE id = ?",
            (digital_id, user["id"]),
        )
        existing_ids.add(digital_id)

    connection.executescript(
        """
        CREATE UNIQUE INDEX IF NOT EXISTS users_by_digital_id
            ON users(digital_id);
        CREATE INDEX IF NOT EXISTS messages_by_reply
            ON messages(reply_to_id) WHERE reply_to_id IS NOT NULL;
        CREATE TABLE IF NOT EXISTS message_reactions (
            message_id INTEGER NOT NULL REFERENCES messages(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            emoji TEXT NOT NULL,
            created_at TEXT NOT NULL,
            PRIMARY KEY (message_id, user_id, emoji)
        );
        CREATE INDEX IF NOT EXISTS reactions_by_message
            ON message_reactions(message_id);
        CREATE TABLE IF NOT EXISTS message_pins (
            message_id INTEGER PRIMARY KEY REFERENCES messages(id) ON DELETE CASCADE,
            pinned_by INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            pinned_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS pins_by_time
            ON message_pins(pinned_at DESC);
        CREATE TABLE IF NOT EXISTS typing_status (
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            chat_key TEXT NOT NULL,
            updated_at REAL NOT NULL,
            PRIMARY KEY (user_id, chat_key)
        );
        CREATE INDEX IF NOT EXISTS typing_status_by_chat
            ON typing_status(chat_key, updated_at);
        CREATE TABLE IF NOT EXISTS user_presence (
            user_id INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
            last_seen_at REAL NOT NULL
        );
        """
    )
