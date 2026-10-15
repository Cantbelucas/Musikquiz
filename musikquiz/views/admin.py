"""Login-beskyttet admin-panel: upload, redigering og sletning af sange."""
from __future__ import annotations

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

from .. import genres, library
from ..db import get_db
from ..security import (
    admin_required,
    check_credentials,
    clear_failed_logins,
    client_ip,
    csrf_token,
    login_blocked,
    record_failed_login,
)
from ..sources import DIFFICULTIES

bp = Blueprint("admin", __name__, url_prefix="/admin")


def _safe_next(target: str | None) -> str:
    if target and target.startswith("/admin") and not target.startswith("//"):
        return target
    return url_for(".dashboard")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        ip = client_ip()
        if login_blocked(ip):
            flash("For mange forkerte forsøg. Vent et kvarter og prøv igen.")
            return render_template("admin/login.html"), 429
        if check_credentials(request.form.get("username", ""), request.form.get("password", "")):
            clear_failed_logins(ip)
            session.clear()
            session.permanent = True
            session["is_admin"] = True
            csrf_token()
            return redirect(_safe_next(request.args.get("next")))
        record_failed_login(ip)
        flash("Forkert brugernavn eller adgangskode.")
        return render_template("admin/login.html"), 401
    if not current_app.config["ADMIN_PASSWORD"]:
        flash("ADMIN_PASSWORD er ikke sat i .env — admin-login er slået fra.")
    return render_template("admin/login.html")


@bp.post("/logout")
def logout():
    session.clear()
    return redirect(url_for("public.index"))


@bp.get("/")
@admin_required
def dashboard():
    db = get_db()
    q = (request.args.get("q") or "").strip()
    sql = "SELECT * FROM tracks"
    params: list = []
    if q:
        sql += " WHERE title LIKE ? OR artist LIKE ? OR genre LIKE ?"
        like = f"%{q}%"
        params = [like, like, like]
    sql += " ORDER BY artist COLLATE NOCASE, title COLLATE NOCASE"
    tracks = db.execute(sql, params).fetchall()
    stats = {d: 0 for d in DIFFICULTIES}
    for row in db.execute("SELECT difficulty, COUNT(*) AS n FROM tracks GROUP BY difficulty"):
        stats[row["difficulty"]] = row["n"]
    return render_template(
        "admin/dashboard.html",
        tracks=tracks,
        q=q,
        stats=stats,
        total=sum(stats.values()),
        suggestions=_suggestions(db),
        difficulties=DIFFICULTIES,
        form=session.pop("upload_form", {}),
        max_upload_mb=current_app.config["MAX_UPLOAD_MB"],
    )


def _check_genre(db, meta: dict, errors: list[str]) -> None:
    if not meta.get("genre"):
        return
    canonical = genres.resolve_local_genre(db, meta["genre"])
    if canonical is None:
        errors.append("Vælg en genre med kilden “Egne filer”. Genrer oprettes under Genrer.")
    else:
        meta["genre"] = canonical


def _suggestions(db) -> dict:
    return {
        "artists": [r[0] for r in db.execute("SELECT DISTINCT artist FROM tracks ORDER BY artist COLLATE NOCASE")],
        # Kun admin-genrer med kilden "Egne filer" kan vælges til uploadede sange
        "genres": genres.local_genre_names(db),
    }


@bp.post("/upload")
@admin_required
def upload():
    meta, errors = library.clean_metadata(request.form)
    _check_genre(get_db(), meta, errors)
    upload_file = request.files.get("file")
    if not upload_file or not upload_file.filename:
        errors.append("Vælg en lydfil (mp3 eller wav).")

    saved = None
    if not errors:
        saved = library.save_audio(upload_file.stream, upload_file.filename, current_app.config["MEDIA_DIR"])
        if saved is None:
            errors.append("Filen er ikke en gyldig mp3- eller wav-fil.")

    if errors:
        for e in errors:
            flash(e)
        # Behold udfyldte felter (bortset fra filen)
        session["upload_form"] = {k: request.form.get(k, "") for k in ("title", "artist", "genre", "difficulty", "start_at")}
        return redirect(url_for(".dashboard") + "#upload")

    filename, mimetype = saved
    library.insert_track(get_db(), meta, filename, mimetype, upload_file.filename)
    # Behold genre og sværhedsgrad til næste upload — praktisk ved mange sange i træk
    session["upload_form"] = {"genre": meta["genre"], "difficulty": meta["difficulty"]}
    flash(f"“{meta['title']}” af {meta['artist']} er tilføjet.", "ok")
    return redirect(url_for(".dashboard") + "#upload")


@bp.route("/spor/<int:track_id>", methods=["GET", "POST"])
@admin_required
def edit(track_id: int):
    db = get_db()
    track = db.execute("SELECT * FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if track is None:
        abort(404)
    if request.method == "POST":
        meta, errors = library.clean_metadata(request.form)
        _check_genre(db, meta, errors)
        if errors:
            for e in errors:
                flash(e)
            return render_template(
                "admin/edit.html", track=track, form=request.form, suggestions=_suggestions(db), difficulties=DIFFICULTIES
            ), 400
        db.execute(
            """UPDATE tracks SET title = :title, artist = :artist, genre = :genre,
               difficulty = :difficulty, start_at = :start_at WHERE id = :id""",
            {**meta, "id": track_id},
        )
        flash("Ændringerne er gemt.", "ok")
        return redirect(url_for(".dashboard"))
    return render_template(
        "admin/edit.html", track=track, form=track, suggestions=_suggestions(db), difficulties=DIFFICULTIES
    )


@bp.post("/spor/<int:track_id>/slet")
@admin_required
def delete(track_id: int):
    if library.delete_track(get_db(), current_app.config["MEDIA_DIR"], track_id):
        flash("Sangen er slettet.", "ok")
    return redirect(url_for(".dashboard"))


@bp.get("/spor/<int:track_id>/lyd")
@admin_required
def preview(track_id: int):
    row = get_db().execute("SELECT filename, mimetype FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if row is None:
        abort(404)
    return library.send_media(row["filename"], row["mimetype"])
