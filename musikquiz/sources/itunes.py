"""Lydkilde: 30-sekunders preview-klip fra iTunes Search API (Deezer som fallback).

Hvordan det hænger sammen:

  genres (admin)        én række pr. genre med kilde "itunes" og søgetermer
      │
  search_cache          rå søgeresultater pr. (udbyder, søgeterm, land) med timestamp.
      │                 Slås ALTID op før API'et kaldes.
      ▼
  track_cache           spillbare spor pr. genre, afledt af search_cache
      │                 (sværhedsgrad fra resultatrækkefølgen, admin kan overskrive)
      ▼
  itunes_playable       view: kun genrer der bruger iTunes, preview findes, ikke skjult

Spor uden preview kommer aldrig ind i cachen, og døde previews markeres
(preview_ok = 0) når de opdages — så de aldrig ender i en quizrunde.
"""
from __future__ import annotations

import json
import re
import time

from flask import Response, abort, request, url_for

from ..db import connect, get_db, transaction
from ..security import preview_signature
from .base import Availability, Criteria, Track, TrackSource
from .catalog_api import CatalogClient, CatalogError, RemoteTrack, deezer_url_expires_at, is_allowed_preview_url

MAX_TERMS = 25
ARTIST_MIN_TRACKS = 5  # iTunes-artister vises først i artist-kategorien med så mange spor
PREVIEW_CHECK_DAYS = 7  # så ofte tjekkes det at en preview stadig findes
ERROR_RETRY_MINUTES = 60  # et fejlet opslag prøves tidligst igen efter
DEEZER_REFRESH_MARGIN = 300  # sekunder før udløb en Deezer-URL fornyes
MAX_PICK_ATTEMPTS = 8
# Mindste afstand mellem søgekald pr. udbyder, på tværs af gunicorn-workers.
# iTunes tillader ca. 20 kald i minuttet; Deezer ca. 50 pr. 5 sekunder.
THROTTLE_SECONDS = {"itunes": 3.2, "deezer": 0.25}


# --- hjælpere ----------------------------------------------------------------
def split_terms(raw: str) -> list[str]:
    """Én søgeterm pr. linje. Tomme linjer og linjer der starter med # ignoreres."""
    terms, seen = [], set()
    for line in (raw or "").splitlines():
        term = " ".join(line.split())
        if not term or term.startswith("#") or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        terms.append(term[:100])
    return terms[:MAX_TERMS]


def split_filter(raw: str) -> list[str]:
    return [part.strip().casefold() for part in re.split(r"[,\n]", raw or "") if part.strip()]


def _normalize(text: str) -> str:
    base = text.casefold()
    cleaned = re.sub(r"\s*[\(\[][^)\]]*[\)\]]", "", base)  # (feat. X), [Remastered]
    cleaned = re.sub(r"\s+-\s+.*$", "", cleaned)  # " - Remastered 2011"
    cleaned = " ".join(re.sub(r"[^\w]+", " ", cleaned).split())
    return cleaned or " ".join(base.split())


def track_key(title: str, artist: str) -> str:
    """Samme sang i forskellige udgaver (single/album/remaster) får samme nøgle."""
    return f"{_normalize(artist)}|{_normalize(title)}"


def rank_difficulty(score: float) -> str:
    """Grov indikator: tidligt i iTunes' resultat = mere kendt = lettere."""
    if score < 1 / 3:
        return "easy"
    if score < 2 / 3:
        return "medium"
    return "hard"


class ITunesSource(TrackSource):
    name = "itunes"

    def __init__(self, client: CatalogClient, country: str, cache_days: int, db_path: str):
        self.client = client
        self.country = country.upper()
        self.cache_days = cache_days
        self.db_path = db_path
        self.client.throttle = self._throttle

    # --- refs og rækker ---------------------------------------------------
    def _ref(self, cache_id: int) -> str:
        return f"{self.name}:{cache_id}"

    def _id(self, ref: str) -> int | None:
        if not self.owns(ref):
            return None
        try:
            return int(ref.split(":", 1)[1])
        except ValueError:
            return None

    @staticmethod
    def _row_to_track(row) -> Track:
        return Track(
            ref=f"itunes:{row['id']}",
            title=row["title"],
            artist=row["artist"],
            genre=row["genre"],
            difficulty=row["difficulty"],
            start_at=0.0,
        )

    @staticmethod
    def _where(criteria: Criteria) -> tuple[str, list]:
        clauses, params = ["difficulty = ?"], [criteria.difficulty]
        if criteria.category == "artist":
            clauses.append("artist = ? COLLATE NOCASE")
            params.append(criteria.value)
        elif criteria.category == "genre":
            clauses.append("genre = ? COLLATE NOCASE")
            params.append(criteria.value)
        return " AND ".join(clauses), params

    # --- TrackSource ------------------------------------------------------
    def random_track(self, criteria, exclude=frozenset()):
        db = get_db()
        where, params = self._where(criteria)
        skip = [i for i in (self._id(r) for r in exclude) if i is not None]
        for _ in range(MAX_PICK_ATTEMPTS):
            ex_sql, ex_params = "", []
            if skip:
                marks = ",".join("?" * len(skip))
                # Udeluk også samme sang fra en anden genre (relevant for "Alle" og artist)
                ex_sql = f" AND id NOT IN ({marks}) AND norm_key NOT IN (SELECT norm_key FROM track_cache WHERE id IN ({marks}))"
                ex_params = skip + skip
            row = db.execute(
                "SELECT *, julianday('now') - julianday(preview_checked_at) AS checked_age"
                f" FROM itunes_playable WHERE {where}{ex_sql} ORDER BY RANDOM() LIMIT 1",
                params + ex_params,
            ).fetchone()
            if row is None:
                return None
            if self._ensure_playable(row):
                return self._row_to_track(row)
            skip.append(row["id"])
        return None

    def prepare(self, criteria):
        """Ved start af en runde: hent de søgetermer for genren der aldrig er slået op."""
        if criteria.category == "genre":
            self.ensure_genre_cached(criteria.value)

    def count(self, criteria):
        where, params = self._where(criteria)
        return get_db().execute(
            f"SELECT COUNT(DISTINCT norm_key) FROM itunes_playable WHERE {where}", params
        ).fetchone()[0]

    def get_track(self, ref):
        cache_id = self._id(ref)
        if cache_id is None:
            return None
        row = get_db().execute("SELECT * FROM itunes_playable WHERE id = ?", (cache_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def distractors(self, track, criteria, n):
        db = get_db()
        own_id = self._id(track.ref)
        seen = {track_key(track.title, track.artist)}
        chosen: list[Track] = []

        where, params = self._where(criteria)
        pools = [(where, params)]
        if criteria.category == "artist":
            pools.append(("artist = ? COLLATE NOCASE", [criteria.value]))
        pools.append(("genre = ? COLLATE NOCASE", [track.genre]))
        pools.append(("1 = 1", []))

        for pool_where, pool_params in pools:
            if len(chosen) >= n:
                break
            rows = db.execute(
                f"SELECT * FROM itunes_playable WHERE {pool_where} AND id != ? ORDER BY RANDOM() LIMIT ?",
                pool_params + [own_id or -1, n * 4],
            ).fetchall()
            for row in rows:
                if row["norm_key"] in seen:
                    continue
                seen.add(row["norm_key"])
                chosen.append(self._row_to_track(row))
                if len(chosen) >= n:
                    break
        return chosen

    def availability(self):
        db = get_db()
        avail = Availability()
        for row in db.execute(
            "SELECT difficulty, COUNT(DISTINCT norm_key) AS n FROM itunes_playable GROUP BY difficulty"
        ):
            avail.all[row["difficulty"]] = row["n"]
        for row in db.execute(
            "SELECT genre, difficulty, COUNT(*) AS n FROM itunes_playable GROUP BY genre, difficulty"
        ):
            avail.genre.setdefault(row["genre"], {})[row["difficulty"]] = row["n"]

        artists: dict[str, dict[str, int]] = {}
        for row in db.execute(
            "SELECT MIN(artist) AS artist, difficulty, COUNT(DISTINCT norm_key) AS n"
            " FROM itunes_playable GROUP BY artist COLLATE NOCASE, difficulty"
        ):
            key = next((k for k in artists if k.casefold() == row["artist"].casefold()), row["artist"])
            artists.setdefault(key, {})[row["difficulty"]] = row["n"]
        avail.artist = {k: v for k, v in artists.items() if sum(v.values()) >= ARTIST_MIN_TRACKS}

        # Genrer der aldrig er hentet: sangene hentes når første runde starter
        for genre in db.execute("SELECT * FROM genres WHERE source = 'itunes' ORDER BY name COLLATE NOCASE"):
            if genre["name"] in avail.genre:
                continue
            terms = split_terms(genre["search_terms"])
            if terms and any(self.term_needs_fetch(t, "missing") for t in terms):
                avail.pending.append(genre["name"])
        return avail

    def audio_url(self, ref):
        """Fast URL pr. spor, så nginx og browseren kan cache klippet på tværs af runder."""
        cache_id = self._id(ref)
        if cache_id is None:
            return None
        return url_for("public.preview", track_id=cache_id, signature=preview_signature(ref))

    def audio_response(self, track):
        cache_id = self._id(track.ref)
        row = get_db().execute("SELECT * FROM itunes_playable WHERE id = ?", (cache_id,)).fetchone()
        if row is None:
            abort(404)
        return self.stream_preview(row)

    # --- preview ----------------------------------------------------------
    def _mark_dead(self, cache_id: int) -> None:
        get_db().execute(
            "UPDATE track_cache SET preview_ok = 0, preview_checked_at = datetime('now') WHERE id = ?", (cache_id,)
        )

    def _refresh_deezer_url(self, row) -> str | None:
        """Ny signeret preview-URL fra Deezer. None = sporet/previewet findes ikke længere."""
        fresh = self.client.deezer_track(row["provider_track_id"])  # kan rejse CatalogError
        if fresh is None:
            self._mark_dead(row["id"])
            return None
        get_db().execute(
            "UPDATE track_cache SET preview_url = ?, preview_checked_at = datetime('now') WHERE id = ?",
            (fresh.preview_url, row["id"]),
        )
        return fresh.preview_url

    def _deezer_expiring(self, url: str) -> bool:
        expires = deezer_url_expires_at(url)
        return expires is None or expires < time.time() + DEEZER_REFRESH_MARGIN

    def _ensure_playable(self, row) -> bool:
        """Tjek (højst hver PREVIEW_CHECK_DAYS dag) at previewet stadig kan hentes."""
        if row["provider"] == "deezer" and self._deezer_expiring(row["preview_url"]):
            try:
                return self._refresh_deezer_url(row) is not None
            except CatalogError:
                return False  # udløbet URL og Deezer utilgængelig — vælg et andet spor
        age = row["checked_age"]
        if age is not None and age < PREVIEW_CHECK_DAYS:
            return True
        result = self.client.probe(row["preview_url"])
        if result is False:
            self._mark_dead(row["id"])
            return False
        if result is True:
            get_db().execute("UPDATE track_cache SET preview_checked_at = datetime('now') WHERE id = ?", (row["id"],))
        return True  # None = netværk ukendt; afspilleren håndterer fejl

    def stream_preview(self, row, _retried: bool = False):
        """Proxy preview-lyden gennem appen (Range-understøttelse, streng CSP, døde previews opdages)."""
        url = row["preview_url"]
        if row["provider"] == "deezer" and self._deezer_expiring(url):
            try:
                url = self._refresh_deezer_url(row)
            except CatalogError:
                abort(502)
            if url is None:
                abort(404)
        if not is_allowed_preview_url(url):
            abort(404)

        headers = {}
        range_header = request.headers.get("Range", "")
        if re.fullmatch(r"bytes=\d*-\d*", range_header):
            headers["Range"] = range_header
        try:
            upstream = self.client.open(url, headers=headers, timeout=10)
        except CatalogError:
            abort(502)

        status = getattr(upstream, "status", None) or upstream.getcode()
        if status in (403, 404, 410):
            upstream.close()
            if row["provider"] == "deezer" and not _retried:
                # Signaturen kan være udløbet tidligere end forventet — forny én gang
                try:
                    fresh_url = self._refresh_deezer_url(row)
                except CatalogError:
                    abort(502)
                if fresh_url:
                    return self.stream_preview({**dict(row), "preview_url": fresh_url}, _retried=True)
                abort(404)
            self._mark_dead(row["id"])
            abort(404)
        if status == 416:
            upstream.close()
            return Response(status=416)
        if status not in (200, 206):
            upstream.close()
            abort(502)

        path = url.split("?", 1)[0].lower()
        out_headers = {
            "Content-Type": "audio/mpeg" if path.endswith(".mp3") else "audio/mp4",
            "Accept-Ranges": "bytes",
            "Cache-Control": "private, no-cache",
        }
        for name in ("Content-Length", "Content-Range"):
            if upstream.headers.get(name):
                out_headers[name] = upstream.headers[name]

        def body():
            try:
                while chunk := upstream.read(64 * 1024):
                    yield chunk
            finally:
                upstream.close()

        return Response(body(), status=status, headers=out_headers, direct_passthrough=True)

    # --- cache: søgninger -------------------------------------------------
    def _throttle(self, provider: str) -> None:
        interval = THROTTLE_SECONDS.get(provider, 0)
        if not interval:
            return
        conn = connect(self.db_path)  # egen forbindelse: må ikke blande sig i requestens transaktion
        try:
            while True:
                conn.execute("BEGIN IMMEDIATE")
                row = conn.execute("SELECT next_at FROM api_throttle WHERE provider = ?", (provider,)).fetchone()
                now = time.time()
                wait = (row["next_at"] - now) if row else 0
                if wait <= 0:
                    conn.execute(
                        "INSERT INTO api_throttle (provider, next_at) VALUES (?, ?)"
                        " ON CONFLICT(provider) DO UPDATE SET next_at = excluded.next_at",
                        (provider, now + interval),
                    )
                    conn.execute("COMMIT")
                    return
                conn.execute("COMMIT")
                time.sleep(min(wait, interval))
        finally:
            conn.close()

    def _search_row(self, provider: str, term: str):
        return get_db().execute(
            "SELECT status, results, error, (julianday('now') - julianday(fetched_at)) AS age_days"
            " FROM search_cache WHERE provider = ? AND query = ? AND country = ?",
            (provider, term, self._country(provider)),
        ).fetchone()

    def _country(self, provider: str) -> str:
        return self.country if provider == "itunes" else ""

    def _needs_fetch(self, row, mode: str) -> bool:
        """mode: 'cache' (aldrig), 'missing' (kun ukendte), 'stale' (også forældede), 'refresh' (altid)."""
        if mode == "cache":
            return False
        if row is None:
            return True
        if row["status"] == "error":
            return mode == "refresh" or row["age_days"] * 24 * 60 >= ERROR_RETRY_MINUTES
        if mode == "refresh":
            return True
        return mode == "stale" and row["age_days"] >= self.cache_days

    def _search(self, provider: str, term: str, mode: str) -> tuple[list[RemoteTrack], bool, str | None]:
        """(spor, blev_hentet_nu, fejl) — slår op i search_cache før API'et."""
        row = self._search_row(provider, term)
        if self._needs_fetch(row, mode):
            try:
                if provider == "itunes":
                    tracks = self.client.search_itunes(term, self.country)
                else:
                    tracks = self.client.search_deezer(term)
            except CatalogError as err:
                if row is None or row["status"] == "error":
                    self._store_search(provider, term, "error", [], str(err))
                    return [], False, str(err)
                # Behold de tidligere gode resultater
                return self._decode(row), False, str(err)
            self._store_search(provider, term, "ok", tracks, "")
            return tracks, True, None
        if row is None:
            return [], False, None
        if row["status"] == "error":
            return [], False, row["error"]
        return self._decode(row), False, None

    @staticmethod
    def _decode(row) -> list[RemoteTrack]:
        return [RemoteTrack.from_dict(item) for item in json.loads(row["results"] or "[]")]

    def _store_search(self, provider, term, status, tracks, error) -> None:
        get_db().execute(
            """INSERT INTO search_cache (provider, query, country, status, results, error, fetched_at)
               VALUES (?, ?, ?, ?, ?, ?, datetime('now'))
               ON CONFLICT(provider, query, country) DO UPDATE SET
                 status = excluded.status, results = excluded.results,
                 error = excluded.error, fetched_at = excluded.fetched_at""",
            (provider, term, self._country(provider), status,
             json.dumps([t.to_dict() for t in tracks], ensure_ascii=False), error[:500]),
        )

    def term_needs_fetch(self, term: str, mode: str) -> bool:
        itunes = self._search_row("itunes", term)
        if self._needs_fetch(itunes, mode):
            return True
        if itunes["status"] == "ok" and itunes["results"] != "[]":
            return False
        return self._needs_fetch(self._search_row("deezer", term), mode)

    def fetch_term(self, term: str, mode: str) -> dict:
        """iTunes først; finder iTunes intet med preview, prøves Deezer."""
        tracks, fetched, error = self._search("itunes", term, mode)
        if tracks:
            return {"term": term, "provider": "itunes", "found": len(tracks), "fetched": fetched, "error": None}
        dz_tracks, dz_fetched, dz_error = self._search("deezer", term, mode)
        return {
            "term": term,
            "provider": "deezer" if dz_tracks else None,
            "found": len(dz_tracks),
            "fetched": fetched or dz_fetched,
            "error": None if dz_tracks else (dz_error or error),
        }

    # --- cache: spor pr. genre -------------------------------------------
    def _cached_results(self, term: str) -> list[RemoteTrack]:
        tracks, _, _ = self._search("itunes", term, "cache")
        if tracks:
            return tracks
        tracks, _, _ = self._search("deezer", term, "cache")
        return tracks

    def rebuild_genre(self, genre) -> None:
        """Genopbyg genrens spor ud fra search_cache (ingen API-kald). Admins valg bevares."""
        filters = split_filter(genre["genre_filter"])
        desired: dict[str, tuple[float, str, RemoteTrack]] = {}
        for term in split_terms(genre["search_terms"]):
            results = self._cached_results(term)
            if filters and results and results[0].provider == "itunes":
                results = [t for t in results if any(f in t.genre.casefold() for f in filters)]
            ordered, seen = [], set()
            for t in results:
                key = track_key(t.title, t.artist)
                if key not in seen:
                    seen.add(key)
                    ordered.append((key, t))
            for position, (key, t) in enumerate(ordered):
                score = position / len(ordered)
                if key not in desired or score < desired[key][0]:
                    desired[key] = (score, term, t)

        db = get_db()
        with transaction(db):
            existing = {
                row["norm_key"]: row
                for row in db.execute(
                    "SELECT id, norm_key, provider, provider_track_id, preview_url FROM track_cache WHERE genre_id = ?",
                    (genre["id"],),
                )
            }
            for key, (score, term, t) in desired.items():
                values = {
                    "genre_id": genre["id"], "norm_key": key, "provider": t.provider, "pid": t.id,
                    "title": t.title, "artist": t.artist, "album": t.album, "preview_url": t.preview_url,
                    "artwork_url": t.artwork_url, "term": term, "score": score, "diff": rank_difficulty(score),
                }
                old = existing.get(key)
                if old is None:
                    db.execute(
                        """INSERT INTO track_cache (genre_id, norm_key, provider, provider_track_id, title, artist,
                               album, preview_url, artwork_url, search_term, rank_score, rank_difficulty)
                           VALUES (:genre_id, :norm_key, :provider, :pid, :title, :artist, :album, :preview_url,
                               :artwork_url, :term, :score, :diff)""",
                        values,
                    )
                    continue
                # Nyt spor/ny preview-URL for samme sang → giv den en ny chance
                changed_source = (old["provider"], old["provider_track_id"], old["preview_url"]) != (
                    t.provider, t.id, t.preview_url)
                source_sql = (
                    ", provider = :provider, provider_track_id = :pid, preview_url = :preview_url,"
                    " preview_ok = 1, preview_checked_at = NULL"
                    if changed_source else ""
                )
                db.execute(
                    "UPDATE track_cache SET title = :title, artist = :artist, album = :album,"
                    " artwork_url = :artwork_url, search_term = :term, rank_score = :score,"
                    f" rank_difficulty = :diff, fetched_at = datetime('now'){source_sql}"
                    " WHERE id = :id",
                    {**values, "id": old["id"]},
                )
            stale = [row["id"] for key, row in existing.items() if key not in desired]
            for i in range(0, len(stale), 500):
                chunk = stale[i:i + 500]
                db.execute(f"DELETE FROM track_cache WHERE id IN ({','.join('?' * len(chunk))})", chunk)

    def cache_genre(self, genre, mode: str = "stale", term_index: int | None = None) -> dict:
        """Hent (efter behov) søgeresultater for genrens termer og genopbyg sporene."""
        terms = split_terms(genre["search_terms"])
        selected = terms if term_index is None else terms[term_index:term_index + 1]
        results = [self.fetch_term(term, mode) for term in selected]
        self.rebuild_genre(genre)
        return {"terms": results, "total_terms": len(terms), **self.genre_stats(genre)}

    def ensure_genre_cached(self, genre_name: str) -> None:
        """Kaldes når en runde startes: hent kun termer der aldrig er slået op."""
        db = get_db()
        if db.in_transaction:
            return  # sikkerhedsnet: aldrig API-kald mens en transaktion er åben
        genre = db.execute(
            "SELECT * FROM genres WHERE name = ? COLLATE NOCASE AND source = 'itunes'", (genre_name,)
        ).fetchone()
        if genre is None:
            return
        terms = split_terms(genre["search_terms"])
        if any(self.term_needs_fetch(t, "missing") for t in terms):
            self.cache_genre(genre, mode="missing")
        elif not db.execute("SELECT 1 FROM track_cache WHERE genre_id = ? LIMIT 1", (genre["id"],)).fetchone():
            self.rebuild_genre(genre)

    def genre_stats(self, genre) -> dict:
        db = get_db()
        by_difficulty = {"easy": 0, "medium": 0, "hard": 0}
        for row in db.execute(
            "SELECT difficulty, COUNT(*) AS n FROM track_cache_effective"
            " WHERE genre_id = ? AND preview_ok = 1 AND hidden = 0 GROUP BY difficulty",
            (genre["id"],),
        ):
            by_difficulty[row["difficulty"]] = row["n"]
        totals = db.execute(
            "SELECT COUNT(*) AS total, SUM(hidden) AS hidden, SUM(preview_ok = 0) AS dead"
            " FROM track_cache WHERE genre_id = ?",
            (genre["id"],),
        ).fetchone()
        terms = split_terms(genre["search_terms"])
        last = None
        if terms:
            marks = ",".join("?" * len(terms))
            last = db.execute(
                f"SELECT MAX(fetched_at) FROM search_cache WHERE status = 'ok' AND query IN ({marks})", terms
            ).fetchone()[0]
        return {
            "playable": sum(by_difficulty.values()),
            "by_difficulty": by_difficulty,
            "cached_total": totals["total"] or 0,
            "hidden": totals["hidden"] or 0,
            "dead": totals["dead"] or 0,
            "terms_missing": sum(1 for t in terms if self.term_needs_fetch(t, "missing")),
            "last_fetched": last,
        }
