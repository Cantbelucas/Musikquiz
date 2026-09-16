import io
import re
import struct
import wave

from musikquiz.sources.catalog_api import CatalogClient, RemoteTrack


def make_wav(seconds: float = 0.2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(struct.pack("<h", 0) * int(8000 * seconds))
    return buf.getvalue()


def csrf(client, path="/"):
    html = client.get(path).get_data(as_text=True)
    return re.search(r'name="csrf-token" content="([^"]+)"', html).group(1)


def login(client):
    token = csrf(client, "/admin/login")
    res = client.post("/admin/login", data={"username": "admin", "password": "hemmeligt", "csrf_token": token})
    assert res.status_code == 302


def create_genre(client, name, source="local", terms="", genre_filter=""):
    token = csrf(client, "/admin/genrer/")
    return client.post(
        "/admin/genrer/",
        data={"csrf_token": token, "name": name, "source": source, "search_terms": terms, "genre_filter": genre_filter},
    )


def upload(client, title, artist="Kunstner", genre="Pop", difficulty="easy", data=None, filename="x.wav"):
    create_genre(client, genre)  # findes den allerede, afvises oprettelsen bare
    token = csrf(client, "/admin/")
    return client.post(
        "/admin/upload",
        data={
            "csrf_token": token,
            "title": title,
            "artist": artist,
            "genre": genre,
            "difficulty": difficulty,
            "start_at": "0",
            "file": (io.BytesIO(data if data is not None else make_wav()), filename),
        },
        content_type="multipart/form-data",
    )


def remote(provider, n, prefix, artist=None, genre="Hip-Hop/Rap"):
    """n falske søgeresultater, i 'popularitets'-rækkefølge."""
    return [
        RemoteTrack(
            provider=provider,
            id=f"{prefix}{i}",
            title=f"{prefix} sang {i}",
            artist=artist or f"{prefix} artist {i}",
            album="Album",
            preview_url=(
                f"https://audio-ssl.itunes.apple.com/{prefix}{i}.m4a"
                if provider == "itunes"
                else f"https://cdnt-preview.dzcdn.net/{prefix}{i}.mp3?hdnea=exp=9999999999~hmac=x"
            ),
            artwork_url="",
            genre=genre,
        )
        for i in range(n)
    ]


class FakeResponse(io.BytesIO):
    def __init__(self, body=b"", status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}

    def getcode(self):
        return self.status


class FakeCatalog(CatalogClient):
    """Erstatter HTTP-laget: ingen netværk i tests. Tæller alle kald."""

    def __init__(self):
        super().__init__()
        self.itunes = {}  # term -> list[RemoteTrack] eller Exception
        self.deezer = {}
        self.deezer_tracks = {}  # id -> RemoteTrack | None
        self.dead_urls = set()
        self.calls = []

    def _answer(self, table, provider, term):
        self.calls.append((provider, term))
        self.throttle(provider)
        value = table.get(term, [])
        if isinstance(value, Exception):
            raise value
        return list(value)

    def search_itunes(self, term, country):
        return self._answer(self.itunes, "itunes", term)

    def search_deezer(self, term):
        return self._answer(self.deezer, "deezer", term)

    def deezer_track(self, track_id):
        self.calls.append(("deezer-track", track_id))
        return self.deezer_tracks.get(track_id)

    def probe(self, url):
        self.calls.append(("probe", url))
        return url not in self.dead_urls

    def open(self, url, headers=None, timeout=None):
        self.calls.append(("open", url, (headers or {}).get("Range")))
        if url in self.dead_urls:
            return FakeResponse(status=404)
        body = b"FAKEAUDIO" * 10
        if headers and headers.get("Range") == "bytes=0-3":
            return FakeResponse(body[:4], 206, {"Content-Length": "4", "Content-Range": f"bytes 0-3/{len(body)}"})
        return FakeResponse(body, 200, {"Content-Length": str(len(body))})

    def api_calls(self):
        return [c for c in self.calls if c[0] in ("itunes", "deezer")]
