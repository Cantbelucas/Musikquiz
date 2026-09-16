"""Registry over lydkilder.

Alle kilder i `_FACTORIES` samles af `GenreRouter`, som spillet taler med.
Admin vælger pr. genre hvilken kilde den bruger (genres.source).

Sådan tilføjer du en ny kilde senere:
  1. Lav `sources/<navn>.py` med en klasse der arver fra `TrackSource` (name = "<navn>").
  2. Tilføj den i `_FACTORIES` og et dansk navn i `SOURCE_LABELS`.
  3. Opret/redigér en genre i admin og vælg den nye kilde.
"""
from flask import current_app

from .base import CATEGORY_TYPES, DIFFICULTIES, Availability, Criteria, Track, TrackSource
from .catalog_api import CatalogClient
from .itunes import ITunesSource
from .local import LocalFileSource
from .router import GenreRouter

_FACTORIES = {
    "local": lambda app: LocalFileSource(app.config["MEDIA_DIR"]),
    "itunes": lambda app: ITunesSource(
        CatalogClient(timeout=app.config["CATALOG_TIMEOUT"]),
        country=app.config["ITUNES_COUNTRY"],
        cache_days=app.config["CATALOG_CACHE_DAYS"],
        db_path=app.config["DATABASE_PATH"],
    ),
}

# Kun til admin — spilleren ser aldrig hvilken kilde en genre bruger.
SOURCE_LABELS = {"local": "Egne filer", "itunes": "iTunes"}


def build_source(app) -> GenreRouter:
    return GenreRouter({key: factory(app) for key, factory in _FACTORIES.items()})


def get_source() -> GenreRouter:
    return current_app.extensions["musikquiz_source"]


def get_itunes() -> ITunesSource:
    return get_source().sources["itunes"]


__all__ = [
    "Availability",
    "CATEGORY_TYPES",
    "Criteria",
    "DIFFICULTIES",
    "SOURCE_LABELS",
    "Track",
    "TrackSource",
    "build_source",
    "get_itunes",
    "get_source",
]
