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

        # Hvilken lydkilde quizzen bruger. Kun "local" findes i denne version.
        self.TRACK_SOURCE = os.environ.get("TRACK_SOURCE", "local")

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
