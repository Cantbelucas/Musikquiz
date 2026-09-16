"""Admin: opret/redigér/slet genrer, vælg kilde + søgetermer, forudfyld og styr iTunes-cachen."""
from __future__ import annotations

from flask import Blueprint, abort, flash, redirect, render_template, request, url_for

from .. import genres
from ..db import get_db, transaction
from ..security import admin_required
from ..sources import DIFFICULTIES, SOURCE_LABELS, get_itunes
from ..sources.itunes import split_terms

bp = Blueprint("admin_genres", __name__, url_prefix="/admin/genrer")

PAGE_SIZE = 100
TRACK_VIEWS = {"synlige": "preview_ok = 1 AND hidden = 0", "skjulte": "hidden = 1", "uden-preview": "preview_ok = 0"}


@bp.before_request
@admin_required
def _require_admin():
    return None


def _genre_or_404(genre_id: int):
    genre = genres.get_genre(get_db(), genre_id)
    if genre is None:
        abort(404)
    return genre


def _stats(db, genre) -> dict:
    if genre["source"] == "itunes":
        return get_itunes().genre_stats(genre)
    return {"playable": genres.local_track_count(db, genre["name"])}


def _render_form_page(template: str, status: int = 200, **context):
    return render_template(template, source_labels=SOURCE_LABELS, difficulties=DIFFICULTIES, **context), status


@bp.get("/")
def index():
    db = get_db()
    rows = [{"genre": g, "stats": _stats(db, g), "terms": split_terms(g["search_terms"])} for g in genres.all_genres(db)]
    return _render_form_page("admin/genres.html", rows=rows, form={"source": "itunes"})


@bp.post("/")
def create():
    db = get_db()
    data, errors = genres.clean_genre_form(request.form)
    if data["name"] and genres.find_genre(db, data["name"]):
        errors.append(f"Genren “{data['name']}” findes allerede.")
    if errors:
        for e in errors:
            flash(e)
        rows = [{"genre": g, "stats": _stats(db, g), "terms": split_terms(g["search_terms"])} for g in genres.all_genres(db)]
        return _render_form_page("admin/genres.html", 400, rows=rows, form=request.form)

    cur = db.execute(
        "INSERT INTO genres (name, source, search_terms, genre_filter) VALUES (:name, :source, :search_terms, :genre_filter)",
        data,
    )
    genre = genres.get_genre(db, cur.lastrowid)
    if genre["source"] == "itunes":
        get_itunes().rebuild_genre(genre)  # genbrug evt. allerede cachede søgninger — ingen API-kald
        flash(f"Genren “{genre['name']}” er oprettet. Forudfyld cachen, så første runde starter hurtigt.", "ok")
    else:
        flash(f"Genren “{genre['name']}” er oprettet. Upload sange til den under Uploadede sange.", "ok")
    return redirect(url_for(".edit", genre_id=genre["id"]))


@bp.route("/<int:genre_id>", methods=["GET", "POST"])
def edit(genre_id: int):
    db = get_db()
    genre = _genre_or_404(genre_id)
    form = genre

    if request.method == "POST":
        data, errors = genres.clean_genre_form(request.form)
        dup = genres.find_genre(db, data["name"]) if data["name"] else None
        if dup and dup["id"] != genre_id:
            errors.append(f"Genren “{data['name']}” findes allerede.")
        local_count = genres.local_track_count(db, genre["name"])
        if genre["source"] == "local" and data["source"] != "local" and local_count:
            errors.append(
                f"Genren har {local_count} uploadede sange. Flyt dem til en anden genre eller slet dem, før kilden skiftes."
            )
        if errors:
            for e in errors:
                flash(e)
            form = request.form
        else:
            with transaction(db):
                db.execute(
                    """UPDATE genres SET name = :name, source = :source, search_terms = :search_terms,
                       genre_filter = :genre_filter, updated_at = datetime('now') WHERE id = :id""",
                    {**data, "id": genre_id},
                )
                if data["name"] != genre["name"]:
                    db.execute("UPDATE tracks SET genre = ? WHERE genre = ? COLLATE NOCASE", (data["name"], genre["name"]))
            genre = genres.get_genre(db, genre_id)
            if genre["source"] == "itunes":
                get_itunes().rebuild_genre(genre)
            flash("Genren er gemt.", "ok")
            return redirect(url_for(".edit", genre_id=genre_id))

    context = {"genre": genre, "form": form, "stats": _stats(db, genre), "terms": split_terms(genre["search_terms"])}
    if genre["source"] == "itunes":
        context.update(_track_listing(db, genre))
    return _render_form_page("admin/genre_edit.html", 400 if request.method == "POST" else 200, **context)


def _track_listing(db, genre) -> dict:
    view = request.args.get("vis", "synlige")
    if view not in TRACK_VIEWS:
        view = "synlige"
    difficulty = request.args.get("svaerhed", "")
    q = (request.args.get("q") or "").strip()
    page = max(request.args.get("side", 1, type=int), 1)

    where, params = ["genre_id = ?", TRACK_VIEWS[view]], [genre["id"]]
    if difficulty in DIFFICULTIES:
        where.append("difficulty = ?")
        params.append(difficulty)
    if q:
        where.append("(title LIKE ? OR artist LIKE ?)")
        params += [f"%{q}%", f"%{q}%"]
    sql_where = " AND ".join(where)
    total = db.execute(f"SELECT COUNT(*) FROM track_cache_effective WHERE {sql_where}", params).fetchone()[0]
    tracks = db.execute(
        f"SELECT * FROM track_cache_effective WHERE {sql_where}"
        " ORDER BY rank_score, artist COLLATE NOCASE, title COLLATE NOCASE LIMIT ? OFFSET ?",
        params + [PAGE_SIZE, (page - 1) * PAGE_SIZE],
    ).fetchall()
    return {
        "tracks": tracks,
        "track_total": total,
        "track_view": view,
        "track_views": list(TRACK_VIEWS),
        "track_difficulty": difficulty,
        "track_q": q,
        "page": page,
        "pages": max(1, -(-total // PAGE_SIZE)),
    }


@bp.post("/<int:genre_id>/slet")
def delete(genre_id: int):
    db = get_db()
    genre = _genre_or_404(genre_id)
    local_count = genres.local_track_count(db, genre["name"])
    if local_count:
        flash(f"Genren har {local_count} uploadede sange. Flyt eller slet dem først.")
        return redirect(url_for(".edit", genre_id=genre_id))
    db.execute("DELETE FROM genres WHERE id = ?", (genre_id,))  # cachede spor slettes via ON DELETE CASCADE
    flash(f"Genren “{genre['name']}” er slettet.", "ok")
    return redirect(url_for(".index"))


@bp.post("/<int:genre_id>/cache")
def fill_cache(genre_id: int):
    """Forudfyld cachen. JSON: én søgeterm pr. kald (admin.js viser fremskridt). Formular: alle på én gang."""
    genre = _genre_or_404(genre_id)
    if genre["source"] != "itunes":
        abort(400, description="Kun genrer med kilden iTunes har en cache.")
    itunes = get_itunes()

    if request.is_json:
        payload = request.get_json(silent=True) or {}
        mode = "refresh" if payload.get("refresh") else "stale"
        try:
            term_index = int(payload.get("term_index", 0))
        except (TypeError, ValueError):
            return {"error": "Ugyldig søgeterm."}, 400
        if not 0 <= term_index < len(split_terms(genre["search_terms"])):
            return {"error": "Ugyldig søgeterm."}, 400
        return itunes.cache_genre(genre, mode=mode, term_index=term_index)

    mode = "refresh" if request.form.get("refresh") else "stale"
    result = itunes.cache_genre(genre, mode=mode)
    failed = [t["term"] for t in result["terms"] if t["error"]]
    flash(f"Cachen har nu {result['playable']} spillbare spor.", "ok")
    if failed:
        flash(f"Kunne ikke hente: {', '.join(failed)}. Prøv igen om lidt.")
    return redirect(url_for(".edit", genre_id=genre_id))


@bp.post("/spor/<int:cache_id>")
def update_track(cache_id: int):
    """Overskriv sværhedsgrad og/eller skjul et cachet spor."""
    db = get_db()
    row = db.execute("SELECT id, genre_id FROM track_cache WHERE id = ?", (cache_id,)).fetchone()
    if row is None:
        abort(404)
    payload = (request.get_json(silent=True) or {}) if request.is_json else request.form

    updates, params = [], []
    if "difficulty" in payload:
        value = payload.get("difficulty") or None
        if value is not None and value not in DIFFICULTIES:
            abort(400, description="Ugyldig sværhedsgrad.")
        updates.append("difficulty_override = ?")
        params.append(value)
    if "hidden" in payload:
        updates.append("hidden = ?")
        params.append(1 if str(payload.get("hidden")).lower() in ("1", "true", "on") else 0)
    if updates:
        db.execute(f"UPDATE track_cache SET {', '.join(updates)} WHERE id = ?", params + [cache_id])

    if request.is_json:
        current = db.execute(
            "SELECT difficulty, auto_difficulty, hidden FROM track_cache_effective WHERE id = ?", (cache_id,)
        ).fetchone()
        return {"ok": True, **dict(current)}
    target = request.form.get("next") or ""
    if not target.startswith("/admin/genrer/") or target.startswith("//"):
        target = url_for(".edit", genre_id=row["genre_id"])
    return redirect(target)


@bp.get("/spor/<int:cache_id>/lyd")
def preview(cache_id: int):
    row = get_db().execute("SELECT * FROM track_cache WHERE id = ?", (cache_id,)).fetchone()
    if row is None:
        abort(404)
    return get_itunes().stream_preview(row)
