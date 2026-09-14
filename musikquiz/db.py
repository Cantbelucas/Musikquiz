"""SQLite-adgang. Én fil i DATA_DIR, WAL-mode, forbindelse pr. request."""
import sqlite3

from flask import current_app, g

SCHEMA = """
CREATE TABLE IF NOT EXISTS tracks (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    title         TEXT    NOT NULL,
    artist        TEXT    NOT NULL,
    genre         TEXT    NOT NULL,
    difficulty    TEXT    NOT NULL CHECK (difficulty IN ('easy', 'medium', 'hard')),
    start_at      REAL    NOT NULL DEFAULT 0,
    filename      TEXT    NOT NULL UNIQUE,
    original_name TEXT    NOT NULL DEFAULT '',
    mimetype      TEXT    NOT NULL,
    created_at    TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_tracks_filter ON tracks (difficulty, artist, genre);

CREATE TABLE IF NOT EXISTS games (
    id         TEXT PRIMARY KEY,
    state      TEXT NOT NULL,
    finished   INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS highscores (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id        TEXT    NOT NULL UNIQUE,
    name           TEXT    NOT NULL,
    score          INTEGER NOT NULL,
    max_score      INTEGER NOT NULL,
    songs          INTEGER NOT NULL,
    category_type  TEXT    NOT NULL,
    category_value TEXT,
    difficulty     TEXT    NOT NULL,
    created_at     TEXT    NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_highscores_score ON highscores (difficulty, score DESC);

CREATE TABLE IF NOT EXISTS login_attempts (
    ip TEXT NOT NULL,
    ts TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_login_attempts ON login_attempts (ip, ts);
"""


def connect(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 15000")
    return conn


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE_PATH"])
    return g.db


def close_db(_exc=None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()


class transaction:
    """`with transaction(db):` — BEGIN IMMEDIATE, så samtidige klik ikke kolliderer."""

    def __init__(self, db: sqlite3.Connection):
        self.db = db

    def __enter__(self):
        self.db.execute("BEGIN IMMEDIATE")
        return self.db

    def __exit__(self, exc_type, exc, tb):
        self.db.execute("ROLLBACK" if exc_type else "COMMIT")
        return False
