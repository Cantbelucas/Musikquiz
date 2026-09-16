"""Uautentificerede HTTP-kald til iTunes Search API og Deezer API.

Kun Pythons standardbibliotek (urllib). Ingen API-nøgler, intet login.
Alle resultater uden 30-sekunders preview frasorteres allerede her.
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass
from urllib.error import HTTPError, URLError

USER_AGENT = "Musikquiz/1.0 (selvhostet musikquiz)"
ITUNES_SEARCH_URL = "https://itunes.apple.com/search"
DEEZER_SEARCH_URL = "https://api.deezer.com/search"
DEEZER_TRACK_URL = "https://api.deezer.com/track/{id}"

ITUNES_LIMIT = 200  # iTunes' maksimum pr. søgning
DEEZER_LIMIT = 100
MAX_JSON_BYTES = 5 * 1024 * 1024

# Preview-lyd må kun hentes fra udbydernes egne CDN'er (beskytter mod SSRF).
PREVIEW_HOST_SUFFIXES = (".apple.com", ".mzstatic.com", ".dzcdn.net", ".deezer.com")


class CatalogError(Exception):
    """API'et kunne ikke nås eller svarede med en fejl (inkl. rate-limit)."""


@dataclass(frozen=True)
class RemoteTrack:
    provider: str  # "itunes" | "deezer"
    id: str
    title: str
    artist: str
    album: str
    preview_url: str
    artwork_url: str
    genre: str  # iTunes' primaryGenreName; tom for Deezer

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "RemoteTrack":
        return cls(**{k: data.get(k, "") for k in cls.__dataclass_fields__})


def is_allowed_preview_url(url: str) -> bool:
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return False
    host = (parts.hostname or "").lower()
    return parts.scheme == "https" and any(host.endswith(s) for s in PREVIEW_HOST_SUFFIXES)


def deezer_url_expires_at(url: str) -> int | None:
    """Deezers preview-URL'er er signerede og udløber (hdnea=exp=<unix-tid>)."""
    match = re.search(r"exp=(\d+)", url)
    return int(match.group(1)) if match else None


def parse_term(term: str) -> tuple[str, str]:
    """'artist:Kim Larsen' → ('artist', 'Kim Larsen'); ellers ('all', term)."""
    prefix, sep, rest = term.partition(":")
    if sep and prefix.strip().lower() == "artist" and rest.strip():
        return "artist", rest.strip()
    return "all", term.strip()


class CatalogClient:
    def __init__(self, timeout: float = 10.0, throttle=None):
        self.timeout = timeout
        # throttle(provider) kaldes før hvert søgekald — sættes af ITunesSource.
        self.throttle = throttle or (lambda provider: None)

    # --- transport ---------------------------------------------------------
    def open(self, url: str, headers: dict | None = None, timeout: float | None = None):
        """Returnér et response-objekt (også ved HTTP-fejlstatus). Rejser CatalogError ved netværksfejl."""
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, **(headers or {})})
        try:
            return urllib.request.urlopen(request, timeout=timeout or self.timeout)
        except HTTPError as err:
            return err
        except (URLError, TimeoutError, OSError) as err:
            raise CatalogError(f"Kunne ikke kontakte {urllib.parse.urlsplit(url).hostname}: {err}") from err

    def get_json(self, url: str) -> dict:
        response = self.open(url, headers={"Accept": "application/json"})
        with response:
            status = getattr(response, "status", None) or response.getcode()
            if status in (403, 429):
                raise CatalogError(f"Rate-limit ramt (HTTP {status})")
            if status != 200:
                raise CatalogError(f"HTTP {status}")
            try:
                return json.loads(response.read(MAX_JSON_BYTES))
            except (ValueError, OSError) as err:
                raise CatalogError("Ugyldigt svar fra API'et") from err

    # --- iTunes --------------------------------------------------------------
    def search_itunes(self, term: str, country: str) -> list[RemoteTrack]:
        field, text = parse_term(term)
        params = {"term": text, "media": "music", "entity": "song", "limit": ITUNES_LIMIT, "country": country}
        if field == "artist":
            params["attribute"] = "artistTerm"
        self.throttle("itunes")
        data = self.get_json(f"{ITUNES_SEARCH_URL}?{urllib.parse.urlencode(params)}")
        tracks = []
        for item in data.get("results") or []:
            if item.get("wrapperType") != "track" or item.get("kind") != "song":
                continue
            track = _make_track(
                "itunes",
                item.get("trackId"),
                item.get("trackName"),
                item.get("artistName"),
                item.get("collectionName"),
                item.get("previewUrl"),
                item.get("artworkUrl100"),
                item.get("primaryGenreName"),
            )
            if track:
                tracks.append(track)
        return tracks

    # --- Deezer --------------------------------------------------------------
    def search_deezer(self, term: str) -> list[RemoteTrack]:
        field, text = parse_term(term)
        query = f'artist:"{text}"' if field == "artist" else text
        self.throttle("deezer")
        data = self.get_json(f"{DEEZER_SEARCH_URL}?{urllib.parse.urlencode({'q': query, 'limit': DEEZER_LIMIT})}")
        _raise_deezer_error(data)
        tracks = []
        for item in data.get("data") or []:
            if item.get("type", "track") != "track" or item.get("readable") is False:
                continue
            track = _deezer_item(item)
            if track:
                tracks.append(track)
        return tracks

    def deezer_track(self, track_id: str) -> RemoteTrack | None:
        """Hent et enkelt Deezer-spor igen — bruges til at forny en udløbet preview-URL."""
        data = self.get_json(DEEZER_TRACK_URL.format(id=urllib.parse.quote(str(track_id))))
        if data.get("error"):
            code = (data["error"] or {}).get("code")
            if code == 800:  # "no data" — sporet findes ikke længere
                return None
            _raise_deezer_error(data)
        return _deezer_item(data)

    # --- preview ------------------------------------------------------------
    def probe(self, url: str) -> bool | None:
        """True = preview kan hentes, False = væk (4xx), None = ukendt (netværk/5xx)."""
        if not is_allowed_preview_url(url):
            return False
        try:
            response = self.open(url, headers={"Range": "bytes=0-1"}, timeout=5)
        except CatalogError:
            return None
        with response:
            status = getattr(response, "status", None) or response.getcode()
        if status in (200, 206):
            return True
        if 400 <= status < 500:
            return False
        return None


def _deezer_item(item: dict) -> RemoteTrack | None:
    return _make_track(
        "deezer",
        item.get("id"),
        item.get("title"),
        (item.get("artist") or {}).get("name"),
        (item.get("album") or {}).get("title"),
        item.get("preview"),
        (item.get("album") or {}).get("cover_medium"),
        "",
    )


def _make_track(provider, track_id, title, artist, album, preview, artwork, genre) -> RemoteTrack | None:
    title, artist, preview = (title or "").strip(), (artist or "").strip(), (preview or "").strip()
    # Intet preview → aldrig med i quizzen
    if not track_id or not title or not artist or not preview or not is_allowed_preview_url(preview):
        return None
    return RemoteTrack(
        provider=provider,
        id=str(track_id),
        title=title[:200],
        artist=artist[:200],
        album=(album or "").strip()[:200],
        preview_url=preview,
        artwork_url=(artwork or "").strip(),
        genre=(genre or "").strip(),
    )


def _raise_deezer_error(data: dict) -> None:
    error = data.get("error")
    if error:
        raise CatalogError(f"Deezer: {error.get('message') or error.get('type') or 'ukendt fejl'}")

