"""Hook til et senere popularitets-lag (fx Spotify client credentials, server-til-server).

Ingen kilde udfylder `popularity` i dag. Et separat lag kan kalde
`set_popularity(db, ref, værdi)` for et spor, og så:

  * spor fra iTunes-genrer: sværhedsgraden udledes af popularity i stedet for
    iTunes' resultatrækkefølge (se AUTO_DIFFICULTY_SQL i db.py) —
    medmindre admin har valgt en sværhedsgrad manuelt, som altid vinder.
  * uploadede spor: værdien gemmes, men sværhedsgraden forbliver admins valg.
"""
from __future__ import annotations

POPULARITY_TABLES = {"local": "tracks", "itunes": "track_cache"}


def set_popularity(db, ref: str, popularity: int | None) -> bool:
    """Sæt popularity (0-100, eller None for at rydde) på et spor. Returnerer False hvis sporet ikke findes."""
    if popularity is not None and not (isinstance(popularity, int) and 0 <= popularity <= 100):
        raise ValueError("popularity skal være et heltal mellem 0 og 100")
    source, _, raw_id = ref.partition(":")
    table = POPULARITY_TABLES.get(source)
    if table is None or not raw_id.isdigit():
        raise ValueError(f"Ukendt spor-reference: {ref}")
    cur = db.execute(f"UPDATE {table} SET popularity = ? WHERE id = ?", (popularity, int(raw_id)))
    return cur.rowcount > 0
