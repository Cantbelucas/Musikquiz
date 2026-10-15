"""Håndtering af uploadede lydfiler + metadata (bruges af admin-panelet og CLI-import)."""
from __future__ import annotations

import os
import uuid

from flask import abort, current_app, send_file

from .sources import DIFFICULTIES

AUDIO_TYPES = {
    "mp3": "audio/mpeg",
    "wav": "audio/wav",
}
FIELD_MAX = 200


def detect_audio_type(header: bytes, filename: str) -> str | None:
    """Returnér 'mp3' / 'wav' hvis både filendelse og filens indhold passer."""
    ext = os.path.splitext(filename or "")[1].lower().lstrip(".")
    if ext == "mp3":
        is_id3 = header[:3] == b"ID3"
        is_frame = len(header) >= 2 and header[0] == 0xFF and (header[1] & 0xE0) == 0xE0
        return "mp3" if (is_id3 or is_frame) else None
    if ext == "wav":
        return "wav" if header[:4] == b"RIFF" and header[8:12] == b"WAVE" else None
    return None


def clean_metadata(form) -> tuple[dict, list[str]]:
    """Validér titel/artist/genre/sværhedsgrad/start fra en formular eller dict."""
    errors: list[str] = []
    data = {}
    for key, label in (("title", "Titel"), ("artist", "Artist"), ("genre", "Genre")):
        value = " ".join((form.get(key) or "").split())
        if not value:
            errors.append(f"{label} skal udfyldes.")
        elif len(value) > FIELD_MAX:
            errors.append(f"{label} er for lang.")
        data[key] = value

    difficulty = form.get("difficulty") or ""
    if difficulty not in DIFFICULTIES:
        errors.append("Vælg en sværhedsgrad.")
    data["difficulty"] = difficulty

    raw_start = (form.get("start_at") or "0").strip().replace(",", ".")
    try:
        start_at = _parse_time(raw_start)
        if start_at < 0 or start_at > 3600:
            raise ValueError
    except ValueError:
        errors.append("Starttidspunkt skal være sekunder (fx 42) eller mm:ss (fx 1:05).")
        start_at = 0.0
    data["start_at"] = start_at
    return data, errors


def _parse_time(raw: str) -> float:
    if ":" in raw:
        minutes, seconds = raw.split(":", 1)
        return int(minutes) * 60 + float(seconds)
    return float(raw or 0)


def save_audio(stream, original_name: str, media_dir: str) -> tuple[str, str] | None:
    """Gem en upload-stream i media_dir. Returnér (filnavn, mimetype) eller None hvis ugyldig."""
    header = stream.read(12)
    kind = detect_audio_type(header, original_name)
    if kind is None:
        return None
    filename = f"{uuid.uuid4().hex}.{kind}"
    final_path = os.path.join(media_dir, filename)
    tmp_path = final_path + ".part"
    try:
        with open(tmp_path, "wb") as out:
            out.write(header)
            while chunk := stream.read(1024 * 1024):
                out.write(chunk)
        os.replace(tmp_path, final_path)
    except BaseException:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise
    return filename, AUDIO_TYPES[kind]


def insert_track(db, meta: dict, filename: str, mimetype: str, original_name: str) -> int:
    cur = db.execute(
        """INSERT INTO tracks (title, artist, genre, difficulty, start_at, filename, original_name, mimetype)
           VALUES (:title, :artist, :genre, :difficulty, :start_at, :filename, :original_name, :mimetype)""",
        {**meta, "filename": filename, "original_name": original_name[:FIELD_MAX], "mimetype": mimetype},
    )
    return cur.lastrowid


def delete_track(db, media_dir: str, track_id: int) -> bool:
    row = db.execute("SELECT filename FROM tracks WHERE id = ?", (track_id,)).fetchone()
    if not row:
        return False
    db.execute("DELETE FROM tracks WHERE id = ?", (track_id,))
    path = os.path.join(media_dir, row["filename"])
    if os.path.isfile(path):
        os.remove(path)
    return True


def send_media(filename: str, mimetype: str):
    """Lever en uploadet fil.

    Er X_ACCEL_MEDIA sat, svarer appen kun med et X-Accel-Redirect-header, og nginx
    sender selve filen (ingen gunicorn-tråd bundet af streaming). Er den tom,
    streamer Flask filen selv — fx lokal udvikling eller en opsætning uden nginx.
    """
    prefix = current_app.config["X_ACCEL_MEDIA"]
    if prefix:
        response = current_app.response_class(b"", mimetype=mimetype)
        response.headers["X-Accel-Redirect"] = prefix + filename
        response.headers["Accept-Ranges"] = "bytes"
        response.headers["Cache-Control"] = "private, no-cache"
        return response

    path = os.path.join(current_app.config["MEDIA_DIR"], filename)
    if not os.path.isfile(path):
        abort(404)
    # conditional=True giver Range-understøttelse, så browseren kan spole til start_at.
    response = send_file(path, mimetype=mimetype, conditional=True, max_age=0)
    response.headers["Cache-Control"] = "private, no-cache"
    return response
