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


# --- migrationer -------------------------------------------------------------
# PRAGMA user_version holder styr på hvilke der er kørt. Tilføj nye bagerst.

# Sværhedsgrad for cachede spor: admins valg > popularity (hvis et senere lag
# har udfyldt den) > iTunes/Deezer-resultatrækkefølge.
AUTO_DIFFICULTY_SQL = """
    CASE WHEN c.popularity IS NULL THEN c.rank_difficulty
         WHEN c.popularity >= 65 THEN 'easy'
         WHEN c.popularity >= 40 THEN 'medium'
         ELSE 'hard' END
"""

MIGRATION_2_GENRES_AND_CACHE = [
    # Nullable popularity (0-100). Udfyldes IKKE af nogen kilde endnu.
    "ALTER TABLE tracks ADD COLUMN popularity INTEGER"
    " CHECK (popularity IS NULL OR popularity BETWEEN 0 AND 100)",
    """CREATE TABLE genres (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        name         TEXT NOT NULL UNIQUE COLLATE NOCASE,
        source       TEXT NOT NULL,
        search_terms TEXT NOT NULL DEFAULT '',
        genre_filter TEXT NOT NULL DEFAULT '',
        created_at   TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
    )""",
    # Rå søgeresultater pr. (udbyder, søgeterm, land) — slås altid op før API'et kaldes.
    """CREATE TABLE search_cache (
        provider   TEXT NOT NULL,
        query      TEXT NOT NULL COLLATE NOCASE,
        country    TEXT NOT NULL DEFAULT '',
        status     TEXT NOT NULL CHECK (status IN ('ok', 'error')),
        results    TEXT NOT NULL DEFAULT '[]',
        error      TEXT NOT NULL DEFAULT '',
        fetched_at TEXT NOT NULL DEFAULT (datetime('now')),
        PRIMARY KEY (provider, query, country)
    )""",
    # Spillbare spor pr. genre, afledt af search_cache.
    """CREATE TABLE track_cache (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        genre_id            INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
        norm_key            TEXT    NOT NULL,
        provider            TEXT    NOT NULL,
        provider_track_id   TEXT    NOT NULL,
        title               TEXT    NOT NULL,
        artist              TEXT    NOT NULL,
        album               TEXT    NOT NULL DEFAULT '',
        preview_url         TEXT    NOT NULL,
        artwork_url         TEXT    NOT NULL DEFAULT '',
        search_term         TEXT    NOT NULL,
        rank_score          REAL    NOT NULL,
        rank_difficulty     TEXT    NOT NULL CHECK (rank_difficulty IN ('easy', 'medium', 'hard')),
        difficulty_override TEXT    CHECK (difficulty_override IS NULL OR difficulty_override IN ('easy', 'medium', 'hard')),
        popularity          INTEGER CHECK (popularity IS NULL OR popularity BETWEEN 0 AND 100),
        hidden              INTEGER NOT NULL DEFAULT 0,
        preview_ok          INTEGER NOT NULL DEFAULT 1,
        preview_checked_at  TEXT,
        fetched_at          TEXT    NOT NULL DEFAULT (datetime('now')),
        UNIQUE (genre_id, norm_key)
    )""",
    "CREATE INDEX idx_track_cache_artist ON track_cache (artist COLLATE NOCASE)",
    f"""CREATE VIEW track_cache_effective AS
        SELECT c.*, g.name AS genre, g.source AS genre_source,
               {AUTO_DIFFICULTY_SQL} AS auto_difficulty,
               COALESCE(c.difficulty_override, {AUTO_DIFFICULTY_SQL}) AS difficulty
        FROM track_cache c JOIN genres g ON g.id = c.genre_id""",
    # Kun det der må ende i en quizrunde: genren bruger iTunes, preview findes, ikke skjult.
    """CREATE VIEW itunes_playable AS
        SELECT * FROM track_cache_effective
        WHERE genre_source = 'itunes' AND preview_ok = 1 AND hidden = 0""",
    """CREATE TABLE api_throttle (
        provider TEXT PRIMARY KEY,
        next_at  REAL NOT NULL
    )""",
    # Eksisterende fritekst-genrer på uploadede sange bliver til admin-genrer med kilden "Egne filer".
    """INSERT OR IGNORE INTO genres (name, source)
        SELECT MIN(genre), 'local' FROM tracks GROUP BY genre COLLATE NOCASE""",
]

MIGRATIONS: list[tuple[int, list[str]]] = [
    (2, MIGRATION_2_GENRES_AND_CACHE),
]


def init_db(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        for target, statements in MIGRATIONS:
            if version >= target:
                continue
            conn.execute("BEGIN IMMEDIATE")
            try:
                for statement in statements:
                    conn.execute(statement)
                conn.execute(f"PRAGMA user_version = {int(target)}")
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
            version = target
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
