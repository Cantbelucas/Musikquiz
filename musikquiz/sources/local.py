"""Lydkilde: filer uploadet af admin, gemt i MEDIA_DIR med metadata i SQLite."""
from __future__ import annotations

import os

from flask import abort, send_file

from ..db import get_db
from .base import Availability, Criteria, Track, TrackSource


class LocalFileSource(TrackSource):
    name = "local"

    def __init__(self, media_dir: str):
        self.media_dir = media_dir

    # --- hjælpere -------------------------------------------------------
    def _ref(self, track_id: int) -> str:
        return f"{self.name}:{track_id}"

    def _id(self, ref: str) -> int | None:
        if not self.owns(ref):
            return None
        try:
            return int(ref.split(":", 1)[1])
        except ValueError:
            return None

    def _row_to_track(self, row) -> Track:
        return Track(
            ref=self._ref(row["id"]),
            title=row["title"],
            artist=row["artist"],
            genre=row["genre"],
            difficulty=row["difficulty"],
            start_at=float(row["start_at"] or 0),
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

    def _exclude_sql(self, exclude) -> tuple[str, list]:
        ids = [i for i in (self._id(r) for r in exclude) if i is not None]
        if not ids:
            return "", []
        return f" AND id NOT IN ({','.join('?' * len(ids))})", ids

    # --- TrackSource ------------------------------------------------------
    def random_track(self, criteria, exclude=frozenset()):
        where, params = self._where(criteria)
        ex_sql, ex_params = self._exclude_sql(exclude)
        row = get_db().execute(
            f"SELECT * FROM tracks WHERE {where}{ex_sql} ORDER BY RANDOM() LIMIT 1",
            params + ex_params,
        ).fetchone()
        return self._row_to_track(row) if row else None

    def count(self, criteria):
        where, params = self._where(criteria)
        return get_db().execute(f"SELECT COUNT(*) FROM tracks WHERE {where}", params).fetchone()[0]

    def get_track(self, ref):
        track_id = self._id(ref)
        if track_id is None:
            return None
        row = get_db().execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
        return self._row_to_track(row) if row else None

    def distractors(self, track, criteria, n):
        """Forkerte svar: først samme kategori, så samme sværhedsgrad, så alt andet."""
        db = get_db()
        chosen: list[Track] = []
        seen = {(track.title.casefold(), track.artist.casefold())}
        own_id = self._id(track.ref)

        pools = []
        where, params = self._where(criteria)
        pools.append((where, params))
        if criteria.category != "all":
            # Samme artist/genre uanset sværhedsgrad
            if criteria.category == "artist":
                pools.append(("artist = ? COLLATE NOCASE", [criteria.value]))
            else:
                pools.append(("genre = ? COLLATE NOCASE", [criteria.value]))
        pools.append(("genre = ? COLLATE NOCASE", [track.genre]))
        pools.append(("1 = 1", []))

        for pool_where, pool_params in pools:
            if len(chosen) >= n:
                break
            rows = db.execute(
                f"SELECT * FROM tracks WHERE {pool_where} AND id != ? ORDER BY RANDOM() LIMIT ?",
                pool_params + [own_id, n * 4],
            ).fetchall()
            for row in rows:
                key = (row["title"].casefold(), row["artist"].casefold())
                if key in seen:
                    continue
                seen.add(key)
                chosen.append(self._row_to_track(row))
                if len(chosen) >= n:
                    break
        return chosen

    def availability(self):
        avail = Availability()
        rows = get_db().execute(
            "SELECT artist, genre, difficulty, COUNT(*) AS n FROM tracks GROUP BY artist, genre, difficulty"
        ).fetchall()
        for row in rows:
            d, n = row["difficulty"], row["n"]
            avail.all[d] = avail.all.get(d, 0) + n
            for bucket, key in ((avail.artist, row["artist"]), (avail.genre, row["genre"])):
                # Case-insensitiv gruppering, første stavemåde vinder
                existing = next((k for k in bucket if k.casefold() == key.casefold()), key)
                bucket.setdefault(existing, {})
                bucket[existing][d] = bucket[existing].get(d, 0) + n
        return avail

    def audio_response(self, track):
        track_id = self._id(track.ref)
        row = get_db().execute("SELECT filename, mimetype FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            abort(404)
        path = os.path.join(self.media_dir, row["filename"])
        if not os.path.isfile(path):
            abort(404)
        # conditional=True giver Range-understøttelse, så browseren kan spole til start_at.
        response = send_file(path, mimetype=row["mimetype"], conditional=True, max_age=0)
        response.headers["Cache-Control"] = "private, no-cache"
        return response
