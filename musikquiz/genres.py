"""Admin-styrede genrer. Kun admin opretter dem; spilleren vælger blandt dem."""
from __future__ import annotations

from .sources import SOURCE_LABELS
from .sources.itunes import MAX_TERMS, split_terms

NAME_MAX = 60


def all_genres(db):
    return db.execute("SELECT * FROM genres ORDER BY name COLLATE NOCASE").fetchall()


def get_genre(db, genre_id: int):
    return db.execute("SELECT * FROM genres WHERE id = ?", (genre_id,)).fetchone()


def find_genre(db, name: str):
    return db.execute("SELECT * FROM genres WHERE name = ? COLLATE NOCASE", (name,)).fetchone()


def local_genre_names(db) -> list[str]:
    return [r["name"] for r in db.execute("SELECT name FROM genres WHERE source = 'local' ORDER BY name COLLATE NOCASE")]


def resolve_local_genre(db, name: str) -> str | None:
    """Genrens officielle stavemåde, hvis den findes og bruger egne filer."""
    row = find_genre(db, name or "")
    return row["name"] if row and row["source"] == "local" else None


def local_track_count(db, name: str) -> int:
    return db.execute("SELECT COUNT(*) FROM tracks WHERE genre = ? COLLATE NOCASE", (name,)).fetchone()[0]


def clean_genre_form(form) -> tuple[dict, list[str]]:
    errors: list[str] = []
    name = " ".join((form.get("name") or "").split())
    if not name:
        errors.append("Genren skal have et navn.")
    elif len(name) > NAME_MAX:
        errors.append(f"Navnet må højst være {NAME_MAX} tegn.")

    source = form.get("source") or ""
    if source not in SOURCE_LABELS:
        errors.append("Vælg en kilde.")

    raw_terms = form.get("search_terms") or ""
    terms = split_terms(raw_terms)
    if source == "itunes":
        if not terms:
            errors.append("Angiv mindst én søgeterm for en iTunes-genre.")
        elif len([t for t in raw_terms.splitlines() if t.strip() and not t.strip().startswith("#")]) > MAX_TERMS:
            errors.append(f"Højst {MAX_TERMS} søgetermer pr. genre.")

    # Termer og filter gemmes også for "Egne filer", så de ikke går tabt hvis kilden skiftes frem og tilbage
    data = {
        "name": name,
        "source": source,
        "search_terms": "\n".join(terms),
        "genre_filter": ", ".join(p.strip() for p in (form.get("genre_filter") or "").split(",") if p.strip())[:300],
    }
    return data, errors
