"""Lydlevering: X-Accel-Redirect for egne filer og faste, cachebare preview-URL'er."""
from helpers import create_genre, csrf, login, remote, upload

from musikquiz.db import get_db


def start_round(client, **data):
    token = csrf(client)
    res = client.post("/spil", data={"csrf_token": token, "difficulty": "easy", **data})
    assert res.status_code == 302, res.get_data(as_text=True)
    game_id = res.headers["Location"].rsplit("/", 1)[1]
    return client.get(f"/api/spil/{game_id}").get_json()


# --- egne filer ---------------------------------------------------------------
def test_local_audio_is_streamed_by_flask_by_default(app, client):
    login(client)
    upload(client, "Sang", genre="Dansk")
    state = start_round(client, category="genre", value_genre="Dansk")
    res = client.get(state["audio_url"])
    assert res.status_code == 200
    assert "X-Accel-Redirect" not in res.headers
    assert res.data.startswith(b"RIFF")


def test_local_audio_is_handed_to_nginx_when_enabled(app, client):
    login(client)
    upload(client, "Sang", genre="Dansk")
    app.config["X_ACCEL_MEDIA"] = "/_media/"
    state = start_round(client, category="genre", value_genre="Dansk")

    res = client.get(state["audio_url"])
    assert res.status_code == 200
    assert res.data == b""  # nginx leverer selve filen
    assert res.headers["Content-Type"].startswith("audio/")
    assert res.headers["Cache-Control"] == "private, no-cache"
    with app.app_context():
        filename = get_db().execute("SELECT filename FROM tracks").fetchone()["filename"]
    assert res.headers["X-Accel-Redirect"] == f"/_media/{filename}"

    # Også admins forhåndsvisning
    with app.app_context():
        track_id = get_db().execute("SELECT id FROM tracks").fetchone()["id"]
    admin_res = client.get(f"/admin/spor/{track_id}/lyd")
    assert admin_res.headers["X-Accel-Redirect"] == f"/_media/{filename}"


# --- previews -------------------------------------------------------------------
def test_preview_url_is_stable_cacheable_and_hides_the_source(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 9, "rap")
    create_genre(client, "Rap", "itunes", "rap")

    first = start_round(client, category="genre", value_genre="Rap")
    url = first["audio_url"]
    assert url.startswith("/klip/")
    for secret in ("itunes", "deezer", "local", "spil"):
        assert secret not in url

    res = client.get(url, headers={"Range": "bytes=0-3"})
    assert res.status_code == 206
    assert res.headers["Cache-Control"] == "public, max-age=604800"
    assert "Expires" not in res.headers

    # Samme spor giver samme URL i en ny runde, så nginx/browseren kan genbruge klippet
    for _ in range(12):
        state = start_round(client, category="genre", value_genre="Rap")
        if state["audio_url"] == url:
            break
    else:
        raise AssertionError("samme spor dukkede aldrig op igen")
    assert client.get(url).status_code == 200


def test_preview_url_requires_a_valid_signature(app, client, catalog):
    login(client)
    catalog.itunes["rap"] = remote("itunes", 4, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    state = start_round(client, category="genre", value_genre="Rap")
    track_id, signature = state["audio_url"].rsplit("/", 2)[1:]

    assert client.get(f"/klip/{track_id}/{'0' * len(signature)}").status_code == 404
    assert client.get(f"/klip/{int(track_id) + 1}/{signature}").status_code == 404
    assert client.get(f"/klip/999999/{signature}").status_code == 404
    assert client.get(f"/klip/{track_id}/{signature}").status_code == 200


def test_dead_preview_is_not_cached_as_playable(app, client, catalog):
    login(client)
    tracks = remote("itunes", 2, "rap")
    catalog.itunes["rap"] = tracks
    create_genre(client, "Rap", "itunes", "rap")
    state = start_round(client, category="genre", value_genre="Rap")
    url = state["audio_url"]
    assert client.get(url).status_code == 200

    # Previewet forsvinder hos udbyderen
    catalog.dead_urls = {t.preview_url for t in tracks}
    res = client.get(url)
    assert res.status_code == 404
    # 404 må ikke få cache-headeren (nginx cacher kun 200)
    assert "public" not in res.headers.get("Cache-Control", "")
    with app.app_context():
        assert get_db().execute("SELECT COUNT(*) FROM track_cache WHERE preview_ok = 0").fetchone()[0] == 1
