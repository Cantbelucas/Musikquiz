"""Konfiguration — alt læses fra miljøvariabler (se .env.example)."""
import os


def _bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "ja"}


def _int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


class Config:
    def __init__(self) -> None:
        self.SECRET_KEY = os.environ.get("SECRET_KEY", "")
        self.ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
        self.ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")

        self.DATA_DIR = os.environ.get("DATA_DIR", "/data")
        self.DATABASE_PATH = os.environ.get(
            "DATABASE_PATH", os.path.join(self.DATA_DIR, "musikquiz.sqlite3")
        )
        self.MEDIA_DIR = os.environ.get("MEDIA_DIR", os.path.join(self.DATA_DIR, "media"))

        # iTunes/Deezer-kilden (genrer med kilden "iTunes"). Ingen nøgler — kun uautentificerede kald.
        self.ITUNES_COUNTRY = os.environ.get("ITUNES_COUNTRY", "DK").strip().upper() or "DK"
        self.CATALOG_CACHE_DAYS = _int("CATALOG_CACHE_DAYS", 30)
        self.CATALOG_TIMEOUT = _int("CATALOG_TIMEOUT", 10)

        # Lad nginx sende de uploadede filer (X-Accel-Redirect). Tom = Flask streamer selv.
        self.X_ACCEL_MEDIA = os.environ.get("X_ACCEL_MEDIA", "").strip()
        # Hvor længe nginx/browseren må cache et preview-klip på dets faste URL
        self.PREVIEW_CACHE_SECONDS = _int("PREVIEW_CACHE_SECONDS", 7 * 24 * 3600)

        self.ROUND_LENGTH = _int("ROUND_LENGTH", 10)
        self.CHOICES = _int("CHOICES", 6)

        self.MAX_UPLOAD_MB = _int("MAX_UPLOAD_MB", 50)
        self.MAX_CONTENT_LENGTH = self.MAX_UPLOAD_MB * 1024 * 1024

        self.SESSION_COOKIE_NAME = "musikquiz_session"
        self.SESSION_COOKIE_HTTPONLY = True
        self.SESSION_COOKIE_SAMESITE = "Lax"
        self.SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", True)
        self.PERMANENT_SESSION_LIFETIME = 60 * 60 * 12

        # Antal reverse proxies foran appen, der sætter X-Forwarded-*.
        self.PROXY_COUNT = _int("PROXY_COUNT", 1)

        self.LOGIN_MAX_ATTEMPTS = _int("LOGIN_MAX_ATTEMPTS", 5)
        self.LOGIN_WINDOW_MINUTES = _int("LOGIN_WINDOW_MINUTES", 15)
