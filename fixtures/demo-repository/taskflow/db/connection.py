"""SQLite connection management."""
import sqlite3

from taskflow.config import DATABASE_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, username TEXT UNIQUE, password_hash TEXT, is_admin INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS tasks (
    id INTEGER PRIMARY KEY, owner TEXT, title TEXT, done INTEGER DEFAULT 0, created_at TEXT
);
"""


def connect() -> sqlite3.Connection:
    """Open the database, creating the schema on first use."""
    connection = sqlite3.connect(DATABASE_PATH)
    connection.executescript(SCHEMA)
    return connection
