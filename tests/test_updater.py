from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app import __version__, main, updater
from app.storage import Store

REAL_CLIENT = httpx.AsyncClient


def test_versions_and_script():
    assert updater.parse_version("v0.17.0") < updater.parse_version("0.18.0") < updater.parse_version("1.0")
    assert updater.parse_version("0.10.0") > updater.parse_version("0.9.9")
    script = updater.swap_script(Path("/Applications/Pirouette.app"), Path("/Applications/Pirouette.app.nouvelle"))
    assert "pgrep -f '/Applications/Pirouette.app/Contents/MacOS/'" in script
    assert "open " not in script  # l'app ne se relance pas toute seule : l'utilisateur la rouvre
    assert "mv '/Applications/Pirouette.app.nouvelle' '/Applications/Pirouette.app'" in script
    assert updater.app_bundle() is None  # pas l'app empaquetée : pas d'installation automatique
    assert updater.pending_app() is None


def test_pending_app(tmp_path):
    bundle = tmp_path / "Pirouette.app"
    assert updater.pending_app(bundle) is None
    (tmp_path / "Pirouette.app.nouvelle" / "Contents").mkdir(parents=True)
    assert updater.pending_app(bundle) == tmp_path / "Pirouette.app.nouvelle"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(main, "store", Store(tmp_path))
    return TestClient(main.app)


def fake_github(monkeypatch, status, payload=None):
    def handler(request):
        return httpx.Response(status, json=payload or {})
    def patched(*args, **kwargs):
        kwargs["transport"] = httpx.MockTransport(handler)
        return REAL_CLIENT(*args, **kwargs)
    monkeypatch.setattr(updater.httpx, "AsyncClient", patched)


def test_update_check(client, monkeypatch):
    fake_github(monkeypatch, 200, {"tag_name": "v99.0.0", "html_url": "https://github.com/x/releases/v99.0.0", "assets": [
        {"name": updater.asset_name(), "browser_download_url": "https://github.com/x/Pirouette.dmg"}]})
    info = client.get("/api/update").json()
    assert info["available"] and info["latest"] == "99.0.0" and info["current"] == __version__
    assert info["url"] == "https://github.com/x/Pirouette.dmg" and info["can_install"] is False

    fake_github(monkeypatch, 200, {"tag_name": f"v{__version__}", "assets": []})
    assert client.get("/api/update").json()["available"] is False
    fake_github(monkeypatch, 404)
    assert client.get("/api/update").json()["error"] == "private"
    assert client.post("/api/update/install", json={"url": "https://exemple.com/x.dmg"}).status_code == 400


def test_quit_stops_the_desktop_app(client, monkeypatch):
    started = []
    monkeypatch.setattr(main.threading, "Timer", lambda delay, fn: type("T", (), {"start": lambda self: started.append(delay)})())
    monkeypatch.delenv("PIROUETTE_DESKTOP", raising=False)
    assert client.post("/api/quit").status_code == 400 and not started  # pas dans un navigateur
    monkeypatch.setenv("PIROUETTE_DESKTOP", "1")
    assert client.post("/api/quit").json() == {"quitting": True} and started == [0.4]
