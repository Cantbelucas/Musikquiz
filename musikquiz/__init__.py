"""Musikquiz — Flask app factory."""
import os
import sys

from flask import Flask, current_app, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from . import db
from .config import Config
from .security import csrf_token, is_admin, verify_csrf
from .sources import build_source

DIFFICULTY_LABELS = {"easy": "Let", "medium": "Mellem", "hard": "Svær"}
CATEGORY_LABELS = {"all": "Alle", "artist": "Artist", "genre": "Genre"}


def create_app(overrides: dict | None = None) -> Flask:
    app = Flask(__name__)
    app.config.from_object(Config())
    if overrides:
        app.config.update(overrides)

    if not app.config["SECRET_KEY"]:
        if app.config.get("TESTING") or app.debug:
            app.config["SECRET_KEY"] = "dev-only-not-secret"
        else:
            sys.exit("SECRET_KEY mangler. Sæt den i .env (se .env.example).")

    if app.config["PROXY_COUNT"] > 0:
        n = app.config["PROXY_COUNT"]
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=n, x_proto=n, x_host=n)

    os.makedirs(app.config["MEDIA_DIR"], exist_ok=True)
    db.init_db(app.config["DATABASE_PATH"])
    app.teardown_appcontext(db.close_db)

    app.extensions["musikquiz_source"] = build_source(app)

    app.before_request(verify_csrf)

    @app.context_processor
    def inject_globals():
        return {
            "csrf_token": csrf_token,
            "is_admin": is_admin(),
            "DIFFICULTY_LABELS": DIFFICULTY_LABELS,
            "CATEGORY_LABELS": CATEGORY_LABELS,
        }

    @app.after_request
    def security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; media-src 'self' blob:; img-src 'self' data:; "
            "style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        )
        return response

    @app.get("/healthz")
    def healthz():
        return {"ok": True}

    @app.get("/favicon.ico")
    def favicon():
        return app.send_static_file("favicon.svg")

    for code, title in ((400, "Ugyldig forespørgsel"), (404, "Siden findes ikke"), (413, "Filen er for stor")):
        app.register_error_handler(code, _error_handler(code, title))

    from .cli import register_cli
    from .views.admin import bp as admin_bp
    from .views.public import bp as public_bp

    app.register_blueprint(public_bp)
    app.register_blueprint(admin_bp)
    register_cli(app)
    return app


def _error_handler(code: int, title: str):
    def handler(err):
        # Brug kun beskrivelser vi selv har sat (Werkzeugs standardtekster er engelske).
        message = getattr(err, "description", "") or ""
        if message == getattr(type(err), "description", None):
            message = ""
        if code == 413:
            message = f"Uploads må højst være {current_app.config['MAX_UPLOAD_MB']} MB."
        if request.path.startswith("/api/"):
            return {"error": message or title}, code
        return render_template("error.html", code=code, title=title, message=message), code

    return handler
