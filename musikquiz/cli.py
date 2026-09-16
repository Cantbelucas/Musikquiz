"""CLI-kommandoer:

  flask --app wsgi import-dir /data/import --genre Pop --difficulty easy
  flask --app wsgi cache-genres [GENRE ...] [--refresh]
"""
from __future__ import annotations

import csv
import os

import click
from flask import current_app

from . import genres, library
from .db import get_db
from .sources import get_itunes


def register_cli(app) -> None:
    @app.cli.command("import-dir")
    @click.argument("folder", type=click.Path(exists=True, file_okay=False))
    @click.option("--genre", help="Genre til filer uden CSV-række.")
    @click.option("--difficulty", type=click.Choice(["easy", "medium", "hard"]), help="Sværhedsgrad til filer uden CSV-række.")
    @click.option("--csv", "csv_path", type=click.Path(exists=True, dir_okay=False),
                  help="CSV med kolonnerne file,title,artist,genre,difficulty[,start_at]")
    @click.option("--dry-run", is_flag=True, help="Vis hvad der ville ske, uden at importere.")
    def import_dir(folder, genre, difficulty, csv_path, dry_run):
        """Importér mp3/wav-filer fra FOLDER.

        Uden CSV læses artist og titel fra filnavnet: "Artist - Titel.mp3".
        """
        rows: dict[str, dict] = {}
        if csv_path:
            with open(csv_path, newline="", encoding="utf-8-sig") as fh:
                for row in csv.DictReader(fh):
                    rows[(row.get("file") or "").strip()] = row

        db = get_db()
        media_dir = current_app.config["MEDIA_DIR"]
        imported = skipped = 0

        for name in sorted(os.listdir(folder)):
            path = os.path.join(folder, name)
            if not os.path.isfile(path) or not name.lower().endswith((".mp3", ".wav")):
                continue

            meta_src = dict(rows.get(name, {}))
            if not meta_src:
                stem = os.path.splitext(name)[0]
                if " - " not in stem:
                    click.echo(f"SKIP  {name}: filnavn skal være 'Artist - Titel' (eller brug --csv)")
                    skipped += 1
                    continue
                artist, title = (part.strip() for part in stem.split(" - ", 1))
                meta_src = {"artist": artist, "title": title, "genre": genre, "difficulty": difficulty}

            meta, errors = library.clean_metadata(meta_src)
            if not errors:
                genre_error = _ensure_local_genre(db, meta, dry_run)
                if genre_error:
                    errors.append(genre_error)
            if errors:
                click.echo(f"SKIP  {name}: {' '.join(errors)}")
                skipped += 1
                continue

            dup = db.execute(
                "SELECT 1 FROM tracks WHERE title = ? COLLATE NOCASE AND artist = ? COLLATE NOCASE",
                (meta["title"], meta["artist"]),
            ).fetchone()
            if dup:
                click.echo(f"SKIP  {name}: findes allerede")
                skipped += 1
                continue

            if dry_run:
                click.echo(f"OK    {name} -> {meta['artist']} - {meta['title']} [{meta['genre']}, {meta['difficulty']}]")
                imported += 1
                continue

            with open(path, "rb") as fh:
                saved = library.save_audio(fh, name, media_dir)
            if saved is None:
                click.echo(f"SKIP  {name}: ikke en gyldig mp3/wav-fil")
                skipped += 1
                continue
            library.insert_track(db, meta, saved[0], saved[1], name)
            click.echo(f"OK    {name} -> {meta['artist']} - {meta['title']}")
            imported += 1

        verb = "ville blive importeret" if dry_run else "importeret"
        click.echo(f"\n{imported} {verb}, {skipped} sprunget over.")

    @app.cli.command("cache-genres")
    @click.argument("names", nargs=-1)
    @click.option("--refresh", is_flag=True, help="Kald API'et for alle søgetermer, også dem der er i cachen.")
    def cache_genres(names, refresh):
        """Forudfyld cachen for iTunes-genrer (alle, hvis ingen navne angives).

        Uden --refresh hentes kun søgetermer der mangler eller er ældre end CATALOG_CACHE_DAYS.
        Egnet til et natligt cron-job.
        """
        db = get_db()
        itunes = get_itunes()
        rows = db.execute("SELECT * FROM genres WHERE source = 'itunes' ORDER BY name COLLATE NOCASE").fetchall()
        if names:
            wanted = {n.casefold() for n in names}
            unknown = wanted - {r["name"].casefold() for r in rows}
            for name in sorted(unknown):
                click.echo(f"SKIP  {name}: ingen iTunes-genre med det navn")
            rows = [r for r in rows if r["name"].casefold() in wanted]

        mode = "refresh" if refresh else "stale"
        failures = 0
        for genre in rows:
            click.echo(f"{genre['name']}:")
            result = itunes.cache_genre(genre, mode=mode)
            for term in result["terms"]:
                if term["error"]:
                    failures += 1
                    click.echo(f"  FEJL  {term['term']}: {term['error']}")
                else:
                    how = "hentet" if term["fetched"] else "cache"
                    click.echo(f"  OK    {term['term']}: {term['found']} sange ({term['provider'] or 'ingen'}, {how})")
            counts = result["by_difficulty"]
            click.echo(
                f"  = {result['playable']} spor i quizzen"
                f" (let {counts['easy']}, mellem {counts['medium']}, svær {counts['hard']})"
            )
        if failures:
            raise SystemExit(1)


def _ensure_local_genre(db, meta: dict, dry_run: bool) -> str | None:
    """CLI køres af admin: opret manglende genrer som "Egne filer"; afvis iTunes-genrer."""
    existing = genres.find_genre(db, meta["genre"])
    if existing is None:
        if not dry_run:
            db.execute("INSERT INTO genres (name, source) VALUES (?, 'local')", (meta["genre"],))
            click.echo(f"NY    genre '{meta['genre']}' oprettet med kilden Egne filer")
        return None
    if existing["source"] != "local":
        return f"genren '{existing['name']}' bruger ikke egne filer"
    meta["genre"] = existing["name"]
    return None
