import pytest

from helpers import FakeCatalog
from musikquiz import create_app
from musikquiz.sources import itunes as itunes_module


@pytest.fixture()
def app(tmp_path, monkeypatch):
    # Ingen reelle ventetider mellem API-kald i tests
    monkeypatch.setattr(itunes_module, "THROTTLE_SECONDS", {"itunes": 0.001, "deezer": 0.001})
    app = create_app(
        {
            "TESTING": True,
            "SECRET_KEY": "test",
            "ADMIN_PASSWORD": "hemmeligt",
            "DATA_DIR": str(tmp_path),
            "DATABASE_PATH": str(tmp_path / "test.sqlite3"),
            "MEDIA_DIR": str(tmp_path / "media"),
            "SESSION_COOKIE_SECURE": False,
            "PROXY_COUNT": 0,
            "ROUND_LENGTH": 3,
            "CHOICES": 4,
        }
    )
    return app


@pytest.fixture()
def client(app):
    return app.test_client()


@pytest.fixture()
def catalog(app):
    """Falsk iTunes/Deezer-klient koblet på appens iTunes-kilde."""
    source = app.extensions["musikquiz_source"].sources["itunes"]
    fake = FakeCatalog()
    fake.throttle = source._throttle
    source.client = fake
    return fake
