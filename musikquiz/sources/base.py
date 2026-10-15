"""Abstraktion for lydkilder.

Quiz-logikken (game.py) kender KUN til `TrackSource`. En ny kilde (fx Spotify)
implementeres som en ny underklasse og registreres i `sources/__init__.py` —
uden at røre spillets regler, pointberegning eller views.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field

DIFFICULTIES = ("easy", "medium", "hard")
CATEGORY_TYPES = ("all", "artist", "genre")


@dataclass(frozen=True)
class Track:
    ref: str  # Globalt unik reference, præfikset med kildens navn, fx "local:12"
    title: str
    artist: str
    genre: str
    difficulty: str
    start_at: float = 0.0


@dataclass(frozen=True)
class Criteria:
    category: str  # "all" | "artist" | "genre"
    value: str | None  # artistnavn / genrenavn, None når category == "all"
    difficulty: str  # "easy" | "medium" | "hard"

    def __post_init__(self):
        if self.category not in CATEGORY_TYPES:
            raise ValueError(f"Ukendt kategori: {self.category}")
        if self.difficulty not in DIFFICULTIES:
            raise ValueError(f"Ukendt sværhedsgrad: {self.difficulty}")
        if self.category != "all" and not self.value:
            raise ValueError("Kategori kræver en værdi")


@dataclass
class Availability:
    """Hvor mange spor der findes pr. valg — bruges til startskærmens menuer."""

    all: dict[str, int] = field(default_factory=dict)  # difficulty -> antal
    artist: dict[str, dict[str, int]] = field(default_factory=dict)  # navn -> difficulty -> antal
    genre: dict[str, dict[str, int]] = field(default_factory=dict)
    # Genrer hvis sange endnu ikke er hentet; de hentes når første runde starter
    pending: list[str] = field(default_factory=list)


class TrackSource(ABC):
    name: str = "abstract"

    @abstractmethod
    def random_track(self, criteria: Criteria, exclude: set[str] | frozenset[str] = frozenset()) -> Track | None:
        """Hent et tilfældigt spor der matcher kategori + sværhedsgrad (eller None)."""

    @abstractmethod
    def count(self, criteria: Criteria) -> int:
        """Antal spor der matcher."""

    @abstractmethod
    def get_track(self, ref: str) -> Track | None:
        """Slå et spor op ud fra dets ref."""

    @abstractmethod
    def distractors(self, track: Track, criteria: Criteria, n: int) -> list[Track]:
        """Op til n forkerte svarmuligheder, helst fra samme kategori."""

    @abstractmethod
    def availability(self) -> Availability:
        """Oversigt over hvad der kan spilles."""

    @abstractmethod
    def audio_response(self, track: Track):
        """Flask-response der leverer lyden (fil, stream eller redirect)."""

    def prepare(self, criteria: Criteria) -> None:
        """Kaldes KUN når en runde startes, og er det eneste sted en kilde må hente
        data udefra. Under en igangværende runde skal opslag være rene DB-opslag,
        så et klik aldrig venter på et API."""
        return None

    def audio_url(self, ref: str) -> str | None:
        """Fast, cachebar URL til sporets lyd — eller None, hvis kilden kun kan
        levere lyden gennem spillets egen (ikke-cachebare) URL."""
        return None

    def owns(self, ref: str) -> bool:
        return ref.startswith(f"{self.name}:")
