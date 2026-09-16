from helpers import csrf, login, upload

from musikquiz.game import STAGES


def test_admin_requires_login(client):
    res = client.get("/admin/")
    assert res.status_code == 302
    assert "/admin/login" in res.headers["Location"]


def test_wrong_password_is_throttled(client):
    token = csrf(client, "/admin/login")
    for _ in range(5):
        assert client.post("/admin/login", data={"username": "admin", "password": "nej", "csrf_token": token}).status_code == 401
    assert client.post("/admin/login", data={"username": "admin", "password": "hemmeligt", "csrf_token": token}).status_code == 429


def test_post_without_csrf_is_rejected(client):
    assert client.post("/spil", data={"category": "all", "difficulty": "easy"}).status_code == 400


def test_upload_rejects_non_audio(client):
    login(client)
    upload(client, "Fake", data=b"not audio at all", filename="fake.mp3")
    html = client.get("/admin/").get_data(as_text=True)
    assert "ikke en gyldig" in html
    assert "Fake</strong>" not in html


def test_full_round(client, app):
    login(client)
    for i in range(5):
        assert upload(client, f"Sang {i}").status_code == 302
    upload(client, "Svær sang", difficulty="hard")

    token = csrf(client)
    res = client.post("/spil", data={"csrf_token": token, "category": "all", "difficulty": "easy"})
    assert res.status_code == 302
    game_url = res.headers["Location"]
    game_id = game_url.rsplit("/", 1)[1]
    headers = {"X-CSRF-Token": token}

    state = client.get(f"/api/spil/{game_id}").get_json()
    assert state["total"] == 3
    assert state["stage"] == 0
    assert len(state["options"]) == 4
    assert "answer_id" not in state  # svaret må ikke lække

    # Lyd for aktuel sang kan hentes, andre indeks ikke
    assert client.get(state["audio_url"]).status_code == 200
    assert client.get(f"/spil/{game_id}/lyd/{state['index'] + 1}").status_code == 404

    # Sang 1: skip hele vejen → 0 point
    for expected_stage in range(1, len(STAGES)):
        state = client.post(f"/api/spil/{game_id}/skip", headers=headers, json={}).get_json()
        assert state["stage"] == expected_stage
    state = client.post(f"/api/spil/{game_id}/skip", headers=headers, json={}).get_json()
    assert state["status"] == "revealed"
    assert state["reveal"]["points"] == 0
    assert client.post(f"/api/spil/{game_id}/skip", headers=headers, json={}).status_code == 409

    # Sang 2: find rigtigt svar ved at prøve alle (første rigtige gæt afslører)
    state = client.post(f"/api/spil/{game_id}/naeste", headers=headers, json={}).get_json()
    assert state["index"] == 1 and state["status"] == "playing"
    for opt in state["options"]:
        state = client.post(f"/api/spil/{game_id}/gaet", headers=headers, json={"option": opt["id"]}).get_json()
        if state["status"] == "revealed":
            break
    assert state["reveal"]["solved"] in (True, False)

    # Sang 3
    state = client.post(f"/api/spil/{game_id}/naeste", headers=headers, json={}).get_json()
    right = None
    while state["status"] == "playing":
        opt = next(o for o in state["options"] if not o["wrong"])
        state = client.post(f"/api/spil/{game_id}/gaet", headers=headers, json={"option": opt["id"]}).get_json()
        right = state.get("correct")
    if right:
        assert state["reveal"]["points"] in [p for _, p in STAGES]

    state = client.post(f"/api/spil/{game_id}/naeste", headers=headers, json={}).get_json()
    assert state["status"] == "finished"
    assert len(state["results"]) == 3
    assert state["score"] == sum(r["points"] for r in state["results"])
    titles = {r["title"] for r in state["results"]}
    assert "Svær sang" not in titles and len(titles) == 3

    # Highscore kan kun gemmes én gang
    res = client.post(f"/spil/{game_id}/highscore", data={"csrf_token": token, "name": "  Lucas  "})
    assert res.status_code == 302
    client.post(f"/spil/{game_id}/highscore", data={"csrf_token": token, "name": "Igen"})
    html = client.get("/highscores").get_data(as_text=True)
    assert html.count("score-row") == 1
    assert "Lucas" in html


def test_correct_first_guess_gives_max_points(client, app):
    login(client)
    for i in range(4):
        upload(client, f"Sang {i}", artist="Kim Larsen", genre="Rock", difficulty="medium")
    token = csrf(client)
    res = client.post(
        "/spil", data={"csrf_token": token, "category": "artist", "value_artist": "kim larsen", "difficulty": "medium"}
    )
    game_id = res.headers["Location"].rsplit("/", 1)[1]
    with app.app_context():
        from musikquiz.db import get_db
        from musikquiz.game import load_game

        game = load_game(get_db(), game_id)
        answer = next(i for i, o in enumerate(game.options) if o.ref == game.current_ref)
    state = client.post(f"/api/spil/{game_id}/gaet", headers={"X-CSRF-Token": token}, json={"option": answer}).get_json()
    assert state["correct"] is True
    assert state["score"] == STAGES[0][1]


def test_no_matching_tracks(client):
    token = csrf(client)
    res = client.post("/spil", data={"csrf_token": token, "category": "genre", "value_genre": "Jazz", "difficulty": "hard"})
    assert res.status_code == 302
    assert res.headers["Location"].endswith("/")
