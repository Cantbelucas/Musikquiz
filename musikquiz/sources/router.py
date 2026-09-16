"""GenreRouter: samler alle kilder bag ét TrackSource.

Hver admin-genre peger på én kilde (genres.source). Spillet taler kun med
routeren og ved derfor ikke — og viser ikke — hvilken kilde en genre bruger.

  kategori "genre"   → genrens kilde
  kategori "alle"    → alle kilder, vægtet efter hvor mange spor de har
  kategori "artist"  → alle kilder
"""
from __future__ import annotations

import random

from flask import abort

from ..db import get_db
from .base import DIFFICULTIES, Availability, Criteria, Track, TrackSource
from .itunes import track_key


class GenreRouter(TrackSource):
    name = "router"

    def __init__(self, sources: dict[str, TrackSource]):
        self.sources = sources

    def _targets(self, criteria: Criteria) -> list[TrackSource]:
        if criteria.category != "genre":
            return list(self.sources.values())
        row = get_db().execute(
            "SELECT source FROM genres WHERE name = ? COLLATE NOCASE", (criteria.value,)
        ).fetchone()
        # Kun genrer admin har oprettet kan spilles
        return [self.sources[row["source"]]] if row and row["source"] in self.sources else []

    def _owner(self, ref: str) -> TrackSource | None:
        return self.sources.get(ref.split(":", 1)[0])

    def random_track(self, criteria, exclude=frozenset()):
        weighted = [(s, s.count(criteria)) for s in self._targets(criteria)]
        weighted = [(s, n) for s, n in weighted if n > 0]
        while weighted:
            source = random.choices([s for s, _ in weighted], weights=[n for _, n in weighted])[0]
            track = source.random_track(criteria, exclude)
            if track is not None:
                return track
            weighted = [(s, n) for s, n in weighted if s is not source]
        return None

    def count(self, criteria):
        return sum(s.count(criteria) for s in self._targets(criteria))

    def get_track(self, ref):
        owner = self._owner(ref)
        return owner.get_track(ref) if owner else None

    def distractors(self, track, criteria, n):
        owner = self._owner(track.ref)
        chosen = owner.distractors(track, criteria, n) if owner else []
        if len(chosen) >= n:
            return chosen[:n]

        # For få i ejerens kilde: fyld op med tilfældige spor fra de andre kilder
        seen = {track_key(t.title, t.artist) for t in [track, *chosen]}
        used = {track.ref, *(t.ref for t in chosen)}
        order = [criteria.difficulty, *(d for d in DIFFICULTIES if d != criteria.difficulty)]
        for source in self.sources.values():
            if source is owner:
                continue
            for difficulty in order:
                misses = 0
                while len(chosen) < n and misses < 3:
                    candidate = source.random_track(Criteria("all", None, difficulty), exclude=used)
                    if candidate is None:
                        break
                    used.add(candidate.ref)
                    key = track_key(candidate.title, candidate.artist)
                    if key in seen:
                        misses += 1
                        continue
                    seen.add(key)
                    chosen.append(candidate)
        return chosen

    def availability(self):
        parts = {name: source.availability() for name, source in self.sources.items()}
        merged = Availability()

        for part in parts.values():
            for difficulty, n in part.all.items():
                merged.all[difficulty] = merged.all.get(difficulty, 0) + n
            for artist, counts in part.artist.items():
                key = next((k for k in merged.artist if k.casefold() == artist.casefold()), artist)
                bucket = merged.artist.setdefault(key, {})
                for difficulty, n in counts.items():
                    bucket[difficulty] = bucket.get(difficulty, 0) + n

        # Genrelisten kommer fra admins genrer — aldrig fra kildernes egne data
        for genre in get_db().execute("SELECT name, source FROM genres ORDER BY name COLLATE NOCASE"):
            part = parts.get(genre["source"])
            if part is None:
                continue
            counts = next((v for k, v in part.genre.items() if k.casefold() == genre["name"].casefold()), {})
            if sum(counts.values()) > 0:
                merged.genre[genre["name"]] = dict(counts)
            elif genre["name"] in part.pending:
                merged.genre[genre["name"]] = {}
                merged.pending.append(genre["name"])
        return merged

    def audio_response(self, track: Track):
        owner = self._owner(track.ref)
        if owner is None:
            abort(404)
        return owner.audio_response(track)
