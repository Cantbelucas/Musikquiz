"""Registry over lydkilder. Vælges med miljøvariablen TRACK_SOURCE.

Sådan tilføjer du en ny kilde senere (fx Spotify):
  1. Lav `sources/spotify.py` med en klasse der arver fra `TrackSource`.
  2. Tilføj den i `_FACTORIES` herunder.
  3. Sæt TRACK_SOURCE=spotify i .env.
"""
from flask import current_app

from .base import CATEGORY_TYPES, DIFFICULTIES, Availability, Criteria, Track, TrackSource
from .local import LocalFileSource

_FACTORIES = {
    "local": lambda app: LocalFileSource(app.config["MEDIA_DIR"]),
}


def build_source(app) -> TrackSource:
    key = app.config["TRACK_SOURCE"]
    if key not in _FACTORIES:
        raise RuntimeError(f"Ukendt TRACK_SOURCE '{key}'. Mulige: {', '.join(_FACTORIES)}")
    return _FACTORIES[key](app)


def get_source() -> TrackSource:
    return current_app.extensions["musikquiz_source"]


__all__ = [
    "Availability",
    "CATEGORY_TYPES",
    "Criteria",
    "DIFFICULTIES",
    "Track",
    "TrackSource",
    "build_source",
    "get_source",
]
