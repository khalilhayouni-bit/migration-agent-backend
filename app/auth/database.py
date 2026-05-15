import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "auth.db")
DB_PATH = os.path.abspath(DB_PATH)


def get_db() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_db()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            email TEXT UNIQUE NOT NULL,
            hashed_password TEXT,
            google_id TEXT UNIQUE,
            is_active BOOLEAN NOT NULL DEFAULT 1,
            role TEXT NOT NULL DEFAULT 'user',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_users_google_id ON users(google_id) WHERE google_id IS NOT NULL")

    # Backfill: add role column to existing databases that predate this change
    cols = {row["name"] for row in conn.execute("PRAGMA table_info(users)").fetchall()}
    if "role" not in cols:
        conn.execute("ALTER TABLE users ADD COLUMN role TEXT NOT NULL DEFAULT 'user'")

    conn.commit()
    conn.close()


init_db()


def sync_user_role(conn, email: str, admin_emails: set[str]) -> str:
    """Set a user's role based on the admin_emails allowlist.

    Promotes to 'admin' if email is in the allowlist, demotes back to 'user'
    otherwise. Returns the resulting role. Caller is responsible for commit.
    """
    target_role = "admin" if email.lower() in admin_emails else "user"
    conn.execute("UPDATE users SET role = ? WHERE email = ?", (target_role, email))
    return target_role
