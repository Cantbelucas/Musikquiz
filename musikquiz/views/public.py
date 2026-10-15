"""Spillerens sider: start, spil, JSON-API og highscores."""
from __future__ import annotations

import hmac
from dataclasses import asdict

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    url_for,
)

from ..db import get_db
from ..game import (
    FINISHED,
    PLAYING,
    REVEALED,
    STAGES,
    GameConflict,
    GameError,
    create_game,
    game_update,
    load_game,
    locked_game,
)
from ..security import preview_signature
from ..sources import CATEGORY_TYPES, DIFFICULTIES, Criteria, get_source

bp = Blueprint("public", __name__)

NAME_MAX = 24


@bp.get("/")
def index():
    avail = get_source().availability()
    return render_template(
        "index.html",
        availability=asdict(avail),
        total_tracks=sum(avail.all.values()),
        round_length=current_app.config["ROUND_LENGTH"],
        stages=STAGES,
    )


@bp.post("/spil")
def start_game():
    category = request.form.get("category", "all")
    difficulty = request.form.get("difficulty", "")
    value = (request.form.get(f"value_{category}") or "").strip() or None
    if category == "all":
        value = None
    try:
        criteria = Criteria(category, value, difficulty)
    except ValueError:
        flash("Vælg en kategori og en sværhedsgrad.")
        return redirect(url_for(".index"))

    cfg = current_app.config
    source = get_source()
    # Eneste sted en kilde må hente data udefra (fx en iTunes-genre der aldrig er hentet)
    source.prepare(criteria)
    game = create_game(get_db(), source, criteria, cfg["ROUND_LENGTH"], cfg["CHOICES"])
    if game is None:
        flash("Der er ingen sange der matcher dit valg endnu.")
        return redirect(url_for(".index"))
    return redirect(url_for(".play", game_id=game.id))


@bp.get("/spil/<game_id>")
def play(game_id: str):
    game = load_game(get_db(), game_id)
    if game is None:
        abort(404)
    return render_template("play.html", game=game)


# --- JSON-API --------------------------------------------------------------
def _state(game) -> dict:
    state = game.public_state()
    if game.status in (PLAYING, REVEALED):
        # Kilder med en fast URL (fx iTunes-previews) bruger den, så nginx kan cache
        # klippet. Ellers serveres lyden på spillets egen, ikke-cachebare URL.
        state["audio_url"] = get_source().audio_url(game.current_ref) or url_for(
            ".audio", game_id=game.id, index=game.index
        )
    return state


def _mutate(game_id: str, action, hold_lock: bool = True):
    """Udfør en handling på spillet og gem det.

    hold_lock=False bruges til handlinger der kan kalde ud over netværket (næste
    sang): så holdes skrivelåsen ikke undervejs, og der gemmes optimistisk til sidst.
    """
    manager = locked_game if hold_lock else game_update
    try:
        with manager(get_db(), game_id) as game:
            if game is None:
                abort(404)
            extra = action(game) or {}
    except (GameError, GameConflict) as err:
        return {"error": str(err)}, 409
    return {**_state(game), **extra}


@bp.get("/api/spil/<game_id>")
def api_state(game_id: str):
    game = load_game(get_db(), game_id)
    if game is None:
        abort(404)
    return _state(game)


@bp.post("/api/spil/<game_id>/gaet")
def api_guess(game_id: str):
    payload = request.get_json(silent=True) or {}
    try:
        option = int(payload.get("option"))
    except (TypeError, ValueError):
        return {"error": "Ugyldigt svar."}, 400
    return _mutate(game_id, lambda g: {"correct": g.guess(option)})


@bp.post("/api/spil/<game_id>/skip")
def api_skip(game_id: str):
    return _mutate(game_id, lambda g: g.skip())


@bp.post("/api/spil/<game_id>/naeste")
def api_next(game_id: str):
    cfg = current_app.config
    # Uden lås: valget af næste sang kan kræve et tjek af previewet over netværket
    return _mutate(game_id, lambda g: g.next_song(get_source(), cfg["CHOICES"]), hold_lock=False)


@bp.get("/spil/<game_id>/lyd/<int:index>")
def audio(game_id: str, index: int):
    game = load_game(get_db(), game_id)
    # Kun den aktuelle sang kan hentes — ikke tidligere eller kommende.
    if game is None or game.status == FINISHED or game.index != index:
        abort(404)
    source = get_source()
    track = source.get_track(game.current_ref)
    if track is None:
        abort(404)
    return source.audio_response(track)


@bp.get("/klip/<int:track_id>/<signature>")
def preview(track_id: int, signature: str):
    """Fast URL til et lydklip: signeret, så vilkårlige spor ikke kan hentes, og
    cachebar, så gentagne afspilninger hverken rammer appen eller udbyderen.

    URL'en nævner ikke kilden — signaturen afgør hvilken kilde sporet hører til,
    så spilleren ikke kan se hvor en genres sange kommer fra.
    """
    track_source = track = None
    for name, candidate in get_source().sources.items():
        if hmac.compare_digest(signature, preview_signature(f"{name}:{track_id}")):
            track_source = candidate
            track = candidate.get_track(f"{name}:{track_id}")
            break
    if track is None:
        abort(404)
    response = track_source.audio_response(track)
    response.headers["Cache-Control"] = f"public, max-age={current_app.config['PREVIEW_CACHE_SECONDS']}"
    response.headers.pop("Expires", None)
    return response


# --- highscores --------------------------------------------------------------
@bp.post("/spil/<game_id>/highscore")
def save_highscore(game_id: str):
    name = " ".join((request.form.get("name") or "").split())[:NAME_MAX]
    if not name:
        flash("Skriv et navn for at gemme din score.")
        return redirect(url_for(".play", game_id=game_id))

    db = get_db()
    with locked_game(db, game_id) as game:
        if game is None:
            abort(404)
        if game.status != FINISHED:
            abort(400, description="Runden er ikke færdig endnu.")
        if game.highscore_saved:
            row = db.execute("SELECT id FROM highscores WHERE game_id = ?", (game.id,)).fetchone()
            return redirect(url_for(".highscores", svaerhed=game.difficulty, ny=row["id"] if row else None))
        cur = db.execute(
            """INSERT INTO highscores
               (game_id, name, score, max_score, songs, category_type, category_value, difficulty)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (game.id, name, game.score, game.max_score, game.total, game.category, game.value, game.difficulty),
        )
        game.highscore_saved = True
        new_id = cur.lastrowid
    return redirect(url_for(".highscores", svaerhed=game.difficulty, ny=new_id))


@bp.get("/highscores")
def highscores():
    difficulty = request.args.get("svaerhed", "alle")
    if difficulty not in DIFFICULTIES:
        difficulty = "alle"
    db = get_db()
    sql = "SELECT * FROM highscores"
    params: list = []
    if difficulty != "alle":
        sql += " WHERE difficulty = ?"
        params.append(difficulty)
    sql += " ORDER BY score DESC, (score * 1.0 / max_score) DESC, created_at ASC LIMIT 25"
    rows = db.execute(sql, params).fetchall()
    return render_template(
        "highscores.html",
        rows=rows,
        difficulty=difficulty,
        highlight=request.args.get("ny", type=int),
        difficulties=DIFFICULTIES,
        category_types=CATEGORY_TYPES,
    )

