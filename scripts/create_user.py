"""
Create a user in the auth database.

Usage:
    python scripts/create_user.py <username> <email> <password>

Example:
    python scripts/create_user.py admin admin@spectrum.tn secretpass123
"""
import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.auth.database import get_db, init_db
from app.auth.security import hash_password


def create_user(username: str, email: str, password: str):
    init_db()
    conn = get_db()

    existing = conn.execute("SELECT id FROM users WHERE username = ? OR email = ?", (username, email)).fetchone()
    if existing:
        print(f"Error: user '{username}' or email '{email}' already exists.")
        conn.close()
        sys.exit(1)

    conn.execute(
        "INSERT INTO users (username, email, hashed_password) VALUES (?, ?, ?)",
        (username, email, hash_password(password)),
    )
    conn.commit()
    conn.close()
    print(f"User '{username}' created successfully.")


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print("Usage: python scripts/create_user.py <username> <email> <password>")
        sys.exit(1)
    create_user(sys.argv[1], sys.argv[2], sys.argv[3])
