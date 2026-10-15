"""Spillet må aldrig holde SQLites skrivelås, mens det venter på netværket."""
import sqlite3

import pytest
from helpers import create_genre, csrf, login, remote

from musikquiz.db import get_db
from musikquiz.game import GameConflict, game_update, load_game


def itunes_round(client, catalog, songs=9):
    login(client)
    catalog.itunes["rap"] = remote("itunes", songs, "rap")
    create_genre(client, "Rap", "itunes", "rap")
    token = csrf(client)
    res = client.post(
        "/spil", data={"csrf_token": token, "category": "genre", "value_genre": "Rap", "difficulty": "easy"}
    )
    assert res.status_code == 302
    return res.headers["Location"].rsplit("/", 1)[1], token


def skip_to_reveal(client, game_id, token):
    headers = {"X-CSRF-Token": token}
    state = client.get(f"/api/spil/{game_id}").get_json()
    while state["status"] == "playing":
        state = client.post(f"/api/spil/{game_id}/skip", headers=headers, json={}).get_json()
    return state


def test_preview_check_does_not_block_other_players(app, client, catalog):
    """Mens appen tjekker et preview over netværket, skal andre stadig kunne skrive."""
    game_id, token = itunes_round(client, catalog)
    writes = []
    real_probe = catalog.probe

    def probe_while_someone_else_writes(url):
        other = sqlite3.connect(app.config["DATABASE_PATH"], timeout=0.5, isolation_level=None)
        try:
            other.execute("BEGIN IMMEDIATE")  # som et klik fra en anden spiller
            other.execute("UPDATE games SET updated_at = datetime('now')")
            other.execute("COMMIT")
            writes.append("ok")
        except sqlite3.OperationalError as err:
            writes.append(f"blokeret: {err}")
        finally:
            other.close()
        return real_probe(url)

    catalog.probe = probe_while_someone_else_writes
    skip_to_reveal(client, game_id, token)
    state = client.post(f"/api/spil/{game_id}/naeste", headers={"X-CSRF-Token": token}, json={}).get_json()

    assert state["status"] == "playing" and state["index"] == 1
    assert writes, "previewet blev aldrig tjekket — testen siger intet"
    assert set(writes) == {"ok"}, writes


def test_parallel_change_is_detected_and_not_overwritten(app, client, catalog):
    game_id, _ = itunes_round(client, catalog)
    with app.app_context():
        db = get_db()
        before = load_game(db, game_id)
        with pytest.raises(GameConflict):
            with game_update(db, game_id) as game:
                game.score += 99
                # En anden forespørgsel når at ændre runden imens
                db.execute("UPDATE games SET version = version + 1 WHERE id = ?", (game_id,))
        assert load_game(db, game_id).score == before.score


def test_next_song_returns_409_when_the_round_changed_meanwhile(app, client, catalog):
    game_id, token = itunes_round(client, catalog)
    skip_to_reveal(client, game_id, token)

    source = app.extensions["musikquiz_source"].sources["itunes"]
    real_random = source.random_track

    def change_round_meanwhile(criteria, exclude=frozenset()):
        other = sqlite3.connect(app.config["DATABASE_PATH"], isolation_level=None)
        other.execute("UPDATE games SET version = version + 1 WHERE id = ?", (game_id,))
        other.close()
        source.random_track = real_random
        return real_random(criteria, exclude)

    source.random_track = change_round_meanwhile
    res = client.post(f"/api/spil/{game_id}/naeste", headers={"X-CSRF-Token": token}, json={})
    assert res.status_code == 409
    assert "Prøv igen" in res.get_json()["error"]

    # Runden er intakt og kan fortsættes
    assert client.post(f"/api/spil/{game_id}/naeste", headers={"X-CSRF-Token": token}, json={}).status_code == 200
