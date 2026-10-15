import io
import sqlite3

from helpers import create_genre, csrf, login, make_wav, remote, upload

from musikquiz import create_app
from musikquiz.db import MIGRATIONS, SCHEMA, get_db
from musikquiz.popularity import set_popularity
from musikquiz.sources.catalog_api import CatalogClient, CatalogError


def genre_id(app, name):
    with app.app_context():
        return get_db().execute("SELECT id FROM genres WHERE name = ?", (name,)).fetchone()["id"]


def query(app, sql, params=()):
    with app.app_context():
        return [dict(r) for r in get_db().execute(sql, params).fetchall()]


def prefill(client, gid, terms, refresh=False):
    token = csrf(client, f"/admin/genrer/{gid}")
    results = []
    for i in range(terms):
        res = client.post(
            f"/admin/genrer/{gid}/cache",
            json={"term_index": i, "refresh": refresh},
            headers={"X-CSRF-Token": token},
        )
        assert res.status_code == 200, res.get_data(as_text=True)
        results.append(res.get_json())
    return results


# --- API-parsing ---------------------------------------------------------------
def test_itunes_parser_drops_tracks_without_preview(monkeypatch):
    client = CatalogClient()
    payload = {
        "results": [
            {"wrapperType": "track", "kind": "song", "trackId": 1, "trackName": "Med preview", "artistName": "A",
             "previewUrl": "https://audio-ssl.itunes.apple.com/a.m4a", "primaryGenreName": "Pop"},
            {"wrapperType": "track", "kind": "song", "trackId": 2, "trackName": "Uden preview", "artistName": "A"},
            {"wrapperType": "track", "kind": "song", "trackId": 3, "trackName": "Fremmed host", "artistName": "A",
             "previewUrl": "https://evil.example.com/x.m4a"},
            {"wrapperType": "track", "kind": "music-video", "trackId": 4, "trackName": "Video", "artistName": "A",
             "previewUrl": "https://audio-ssl.itunes.apple.com/v.m4v"},
        ]
    }
    urls = []
    monkeypatch.setattr(client, "get_json", lambda url: urls.append(url) or payload)
    tracks = client.search_itunes("artist:Kim Larsen", "DK")
    assert [t.title for t in tracks] == ["Med preview"]
    assert "attribute=artistTerm" in urls[0] and "term=Kim+Larsen" in urls[0] and "country=DK" in urls[0]


def test_deezer_parser_and_error(monkeypatch):
    client = CatalogClient()
    payload = {"data": [
        {"id": 7, "type": "track", "readable": True, "title": "Midt om natten", "preview": "https://cdnt-preview.dzcdn.net/x.mp3",
         "artist": {"name": "Kim Larsen"}, "album": {"title": "Midt Om Natten", "cover_medium": "c"}},
        {"id": 8, "type": "track", "readable": True, "title": "Tom", "preview": "", "artist": {"name": "X"}, "album": {}},
    ]}
    monkeypatch.setattr(client, "get_json", lambda url: payload)
    assert [t.id for t in client.search_deezer("kim larsen")] == ["7"]

    monkeypatch.setattr(client, "get_json", lambda url: {"error": {"type": "Exception", "message": "Quota limit exceeded", "code": 4}})
    try:
        client.search_deezer("x")
        raise AssertionError("forventede CatalogError")
    except CatalogError as err:
        assert "Quota" in str(err)


# --- migration -----------------------------------------------------------------
def test_migration_turns_existing_track_genres_into_local_genres(tmp_path):
    db_path = tmp_path / "old.sqlite3"
    conn = sqlite3.connect(db_path)
    conn.executescript(SCHEMA)
    for i, genre in enumerate(["Rock", "rock", "Dansk pop"]):
        conn.execute(
            "INSERT INTO tracks (title, artist, genre, difficulty, filename, mimetype) VALUES (?, 'A', ?, 'easy', ?, 'audio/wav')",
            (f"S{i}", genre, f"f{i}.wav"),
        )
    conn.commit()
    conn.close()

    app = create_app({"TESTING": True, "DATABASE_PATH": str(db_path), "MEDIA_DIR": str(tmp_path / "m"), "PROXY_COUNT": 0})
    rows = query(app, "SELECT name, source FROM genres ORDER BY name")
    assert [(r["name"].lower(), r["source"]) for r in rows] == [("dansk pop", "local"), ("rock", "local")]
    assert "popularity" in {r["name"] for r in query(app, "PRAGMA table_info(tracks)")}
    assert query(app, "PRAGMA user_version")[0]["user_version"] == MIGRATIONS[-1][0]

    # Idempotent: anden opstart ændrer intet
    create_app({"TESTING": True, "DATABASE_PATH": str(db_path), "MEDIA_DIR": str(tmp_path / "m"), "PROXY_COUNT": 0})
    assert len(query(app, "SELECT * FROM genres")) == 2


# --- cache + fallback ---------------------------------------------------------------
def test_prefill_caches_and_derives_difficulty_from_rank(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 6, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    gid = genre_id(app, "Rap")
    assert catalog.api_calls() == []  # oprettelse kalder aldrig API'et

    [result] = prefill(client, gid, 1)
    assert result["terms"][0] == {"term": "rap", "provider": "itunes", "found": 6, "fetched": True, "error": None}
    assert result["by_difficulty"] == {"easy": 2, "medium": 2, "hard": 2}

    rows = query(app, "SELECT title, difficulty FROM track_cache_effective ORDER BY rank_score")
    assert [r["difficulty"] for r in rows] == ["easy", "easy", "medium", "medium", "hard", "hard"]
    cached = query(app, "SELECT provider, query, country, status, fetched_at FROM search_cache")
    assert cached[0]["provider"] == "itunes" and cached[0]["country"] == "DK" and cached[0]["fetched_at"]

    # Anden forudfyldning: alt kommer fra cachen
    [again] = prefill(client, gid, 1)
    assert again["terms"][0]["fetched"] is False
    assert len(catalog.api_calls()) == 1

    # "Hent alt igen" kalder API'et
    prefill(client, gid, 1, refresh=True)
    assert len(catalog.api_calls()) == 2


def test_deezer_fallback_when_itunes_finds_nothing(app, client, catalog):
    login(client)
    catalog.itunes["dansk undergrund"] = []
    catalog.deezer["dansk undergrund"] = remote("deezer", 3, "ug")
    create_genre(client, "Undergrund", "itunes", "dansk undergrund")
    [result] = prefill(client, genre_id(app, "Undergrund"), 1)
    assert result["terms"][0]["provider"] == "deezer"
    assert catalog.api_calls() == [("itunes", "dansk undergrund"), ("deezer", "dansk undergrund")]
    assert {r["provider"] for r in query(app, "SELECT provider FROM track_cache")} == {"deezer"}


def test_itunes_error_falls_back_and_is_not_retried_immediately(app, client, catalog):
    login(client)
    catalog.itunes["pop"] = CatalogError("Rate-limit ramt (HTTP 403)")
    catalog.deezer["pop"] = remote("deezer", 2, "pop")
    create_genre(client, "Pop", "itunes", "pop")
    gid = genre_id(app, "Pop")
    prefill(client, gid, 1)
    prefill(client, gid, 1)  # fejlen caches i en time — ingen nye kald
    assert catalog.api_calls() == [("itunes", "pop"), ("deezer", "pop")]


def test_genre_filter_and_deduplication(app, client, catalog):
    login(client)
    tracks = remote("itunes", 3, "mix", genre="Hip-Hop/Rap") + remote("itunes", 2, "pop", genre="Pop")
    # Samme sang i remaster-udgave må kun optræde én gang
    tracks.append(tracks[0].__class__(**{**tracks[0].to_dict(), "id": "dup", "title": "mix sang 0 (Remastered 2020)"}))
    catalog.itunes["blandet"] = tracks
    create_genre(client, "Rap", "itunes", "blandet", genre_filter="hip-hop")
    prefill(client, genre_id(app, "Rap"), 1)
    titles = sorted(r["title"] for r in query(app, "SELECT title FROM track_cache"))
    assert titles == ["mix sang 0", "mix sang 1", "mix sang 2"]


# --- spillet -------------------------------------------------------------------------
def test_player_never_sees_source_and_plays_itunes_genre(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 9, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    upload(client, "Egen sang", genre="Dansk undergrund")

    # Genren er aldrig hentet: vises som "hentes ved start", uden kald til API'et
    html = client.get("/").get_data(as_text=True)
    assert "Rap" in html and "Dansk undergrund" in html
    for secret in ("itunes", "iTunes", "deezer", "Egne filer", "local"):
        assert secret not in html
    assert catalog.api_calls() == []

    token = csrf(client)
    res = client.post("/spil", data={"csrf_token": token, "category": "genre", "value_genre": "rap", "difficulty": "easy"})
    assert res.status_code == 302 and "/spil/" in res.headers["Location"]
    assert catalog.api_calls() == [("itunes", "rap")]  # hentet ved første runde

    game_id = res.headers["Location"].rsplit("/", 1)[1]
    state = client.get(f"/api/spil/{game_id}").get_json()
    assert state["total"] == 3 and len(state["options"]) == 4
    assert all("itunes" not in str(v).lower() for v in state.values() if not isinstance(v, list))

    # Lyden proxies gennem appen med Range
    audio = client.get(state["audio_url"], headers={"Range": "bytes=0-3"})
    assert audio.status_code == 206
    assert audio.headers["Content-Type"] == "audio/mp4"
    assert audio.data == b"FAKE"

    # Næste runde i samme genre bruger cachen
    client.post("/spil", data={"csrf_token": token, "category": "genre", "value_genre": "Rap", "difficulty": "hard"})
    assert catalog.api_calls() == [("itunes", "rap")]

    # Highscore-listen er fælles og viser ikke kilden
    assert "itunes" not in client.get("/highscores").get_data(as_text=True).lower()


def test_dead_previews_never_end_up_in_a_round(app, client, catalog):
    login(client)
    tracks = remote("itunes", 3, "rap")
    catalog.itunes["rap"] = tracks
    catalog.dead_urls = {tracks[0].preview_url, tracks[1].preview_url}
    create_genre(client, "Rap", "itunes", "rap")
    prefill(client, genre_id(app, "Rap"), 1)
    # Gør alle spor "let", så de er i samme pulje
    for row in query(app, "SELECT id FROM track_cache"):
        with app.app_context():
            get_db().execute("UPDATE track_cache SET difficulty_override = 'easy' WHERE id = ?", (row["id"],))

    token = csrf(client)
    for _ in range(5):
        res = client.post("/spil", data={"csrf_token": token, "category": "genre", "value_genre": "Rap", "difficulty": "easy"})
        game_id = res.headers["Location"].rsplit("/", 1)[1]
        with app.app_context():
            from musikquiz.game import load_game

            game = load_game(get_db(), game_id)
        assert game.current_ref.startswith("itunes:")
        chosen = query(app, "SELECT title FROM track_cache WHERE id = ?", (int(game.current_ref.split(":")[1]),))
        assert chosen[0]["title"] == "rap sang 2"

    dead = query(app, "SELECT title FROM track_cache WHERE preview_ok = 0 ORDER BY title")
    assert [r["title"] for r in dead] == ["rap sang 0", "rap sang 1"]


def test_expired_deezer_preview_is_refreshed(app, client, catalog):
    login(client)
    expired = remote("deezer", 1, "dz")[0]
    expired = expired.__class__(**{**expired.to_dict(), "preview_url": "https://cdnt-preview.dzcdn.net/dz0.mp3?hdnea=exp=1000~x"})
    catalog.itunes["dz"] = []
    catalog.deezer["dz"] = [expired]
    fresh_url = "https://cdnt-preview.dzcdn.net/dz0.mp3?hdnea=exp=9999999999~y"
    catalog.deezer_tracks["dz0"] = expired.__class__(**{**expired.to_dict(), "preview_url": fresh_url})
    create_genre(client, "Dz", "itunes", "dz")
    prefill(client, genre_id(app, "Dz"), 1)

    cache_id = query(app, "SELECT id FROM track_cache")[0]["id"]
    res = client.get(f"/admin/genrer/spor/{cache_id}/lyd")
    assert res.status_code == 200 and res.headers["Content-Type"] == "audio/mpeg"
    assert query(app, "SELECT preview_url FROM track_cache")[0]["preview_url"] == fresh_url
    assert ("open", fresh_url, None) in catalog.calls

    # Findes sporet ikke længere hos Deezer, markeres det og kan ikke spilles
    with app.app_context():
        get_db().execute("UPDATE track_cache SET preview_url = ? WHERE id = ?", (expired.preview_url, cache_id))
    catalog.deezer_tracks["dz0"] = None
    assert client.get(f"/admin/genrer/spor/{cache_id}/lyd").status_code == 404
    assert query(app, "SELECT preview_ok FROM track_cache")[0]["preview_ok"] == 0


# --- admin: sværhedsgrad, skjul, popularity --------------------------------------------
def test_admin_override_hide_and_popularity(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 3, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    prefill(client, genre_id(app, "Rap"), 1)
    first, second, third = query(app, "SELECT id, difficulty FROM track_cache_effective ORDER BY rank_score")
    assert (first["difficulty"], third["difficulty"]) == ("easy", "hard")

    token = csrf(client, "/admin/genrer/")
    res = client.post(f"/admin/genrer/spor/{third['id']}", json={"difficulty": "easy"}, headers={"X-CSRF-Token": token})
    assert res.get_json()["difficulty"] == "easy" and res.get_json()["auto_difficulty"] == "hard"

    client.post(f"/admin/genrer/spor/{first['id']}", json={"hidden": True}, headers={"X-CSRF-Token": token})
    playable = {r["id"] for r in query(app, "SELECT id FROM itunes_playable")}
    assert first["id"] not in playable and third["id"] in playable

    # Popularity (fra et senere lag) styrer auto-sværhedsgraden; admins valg vinder stadig
    with app.app_context():
        db = get_db()
        assert set_popularity(db, f"itunes:{second['id']}", 90)
        assert set_popularity(db, f"itunes:{third['id']}", 5)
    rows = {r["id"]: r for r in query(app, "SELECT id, auto_difficulty, difficulty FROM track_cache_effective")}
    assert rows[second["id"]]["difficulty"] == "easy"
    assert rows[third["id"]]["auto_difficulty"] == "hard" and rows[third["id"]]["difficulty"] == "easy"

    # Redigering af søgetermer/filter bevarer admins valg
    client.post(f"/admin/genrer/{genre_id(app, 'Rap')}", data={
        "csrf_token": token, "name": "Rap", "source": "itunes", "search_terms": "rap", "genre_filter": ""})
    assert query(app, "SELECT difficulty_override FROM track_cache WHERE id = ?", (third["id"],))[0]["difficulty_override"] == "easy"


def test_genre_admin_rules(app, client, catalog):
    login(client)
    upload(client, "Sang", genre="Dansk undergrund")
    gid = genre_id(app, "Dansk undergrund")
    token = csrf(client, "/admin/genrer/")

    # Kan ikke skifte kilde eller slette mens der er uploadede sange
    res = client.post(f"/admin/genrer/{gid}", data={"csrf_token": token, "name": "Dansk undergrund", "source": "itunes", "search_terms": "x"})
    assert res.status_code == 400
    client.post(f"/admin/genrer/{gid}/slet", data={"csrf_token": token})
    assert query(app, "SELECT COUNT(*) AS n FROM genres")[0]["n"] == 1

    # Omdøbning følger med på sangene
    client.post(f"/admin/genrer/{gid}", data={"csrf_token": token, "name": "DK undergrund", "source": "local"})
    assert query(app, "SELECT genre FROM tracks")[0]["genre"] == "DK undergrund"

    # iTunes-genre kræver søgetermer, og navne er unikke uanset store/små bogstaver
    assert create_genre(client, "Rap", "itunes", "").status_code == 400
    assert create_genre(client, "dk UNDERGRUND", "local").status_code == 400

    # Uploads kan ikke lægges i en iTunes-genre
    create_genre(client, "Rap", "itunes", "rap")
    token = csrf(client, "/admin/")
    client.post("/admin/upload", data={
        "csrf_token": token, "title": "Forkert", "artist": "A", "genre": "Rap", "difficulty": "easy",
        "file": (io.BytesIO(make_wav()), "x.wav")}, content_type="multipart/form-data")
    assert query(app, "SELECT COUNT(*) AS n FROM tracks WHERE title = 'Forkert'")[0]["n"] == 0

    # Spilleren kan ikke spille en genre admin ikke har oprettet
    res = client.post("/spil", data={"csrf_token": csrf(client), "category": "genre", "value_genre": "Findes ikke", "difficulty": "easy"})
    assert res.headers["Location"].endswith("/")


def test_genre_pages_require_login(client):
    assert client.get("/admin/genrer/").status_code == 302
    assert client.post("/admin/genrer/1/cache", json={}).status_code in (302, 400)


def test_cli_cache_genres(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 4, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    result = app.test_cli_runner().invoke(args=["cache-genres"])
    assert result.exit_code == 0, result.output
    assert "4 spor i quizzen" in result.output
    assert catalog.api_calls() == [("itunes", "rap")]


def test_new_search_term_is_not_fetched_in_the_middle_of_a_round(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 9, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    gid = genre_id(app, "Rap")
    prefill(client, gid, 1)
    token = csrf(client)
    res = client.post("/spil", data={"csrf_token": token, "category": "genre", "value_genre": "Rap", "difficulty": "easy"})
    game_id = res.headers["Location"].rsplit("/", 1)[1]

    # Admin tilføjer en søgeterm mens runden kører
    client.post(f"/admin/genrer/{gid}", data={"csrf_token": token, "name": "Rap", "source": "itunes", "search_terms": "rap\nny term"})
    headers = {"X-CSRF-Token": token}
    state = client.get(f"/api/spil/{game_id}").get_json()
    while state["status"] == "playing":
        state = client.post(f"/api/spil/{game_id}/skip", headers=headers, json={}).get_json()
    state = client.post(f"/api/spil/{game_id}/naeste", headers=headers, json={}).get_json()
    assert state["status"] == "playing" and state["index"] == 1
    assert ("itunes", "ny term") not in catalog.api_calls()
