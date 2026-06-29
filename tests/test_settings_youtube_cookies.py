"""Settings API: YouTube pasted cookies save + mode wipe guard."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from core.youtube_cookies import PASTE_MODE

web_server = pytest.importorskip("web_server")

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tsecretvalue\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tanother\n"
)


class _FakeConfigManager:
    def __init__(self, tmp_path: Path):
        self.config_path = tmp_path / "config" / "config.json"
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        self.database_path = tmp_path / "data" / "db.sqlite"
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._data = {
            "youtube": {"cookies_browser": "", "cookies_file": "", "download_delay": 3},
        }

    def get(self, key, default=None):
        cur = self._data
        for part in key.split("."):
            if isinstance(cur, dict) and part in cur:
                cur = cur[part]
            else:
                return default
        return cur

    def set(self, key, value):
        parts = key.split(".")
        cur = self._data
        for part in parts[:-1]:
            cur = cur.setdefault(part, {})
        cur[parts[-1]] = value


@pytest.fixture()
def client(monkeypatch, tmp_path):
    web_server.app.config["TESTING"] = True
    fake_cm = _FakeConfigManager(tmp_path)
    monkeypatch.setattr(web_server, "config_manager", fake_cm)
    monkeypatch.setattr(web_server, "get_current_profile_id", lambda: 1)
    monkeypatch.setattr(web_server, "download_orchestrator", None)
    monkeypatch.setattr(web_server, "spotify_client", None)
    monkeypatch.setattr(web_server, "lastfm_worker", None)
    monkeypatch.setattr(web_server, "genius_worker", None)
    monkeypatch.setattr(web_server, "tidal_enrichment_worker", None)
    monkeypatch.setattr(web_server, "media_server_engine", MagicMock(client=lambda _x: None))
    monkeypatch.setattr(web_server, "invalidate_metadata_status_caches", lambda: None)
    monkeypatch.setattr(web_server, "add_activity_item", lambda *a, **k: None)
    return web_server.app.test_client(), fake_cm


def test_post_paste_sets_custom_mode_and_writes_file(client):
    flask_client, fake_cm = client
    r = flask_client.post(
        "/api/settings",
        json={
            "youtube": {
                "cookies_browser": "custom",
                "download_delay": 3,
                "cookies_paste": NETSCAPE,
            }
        },
    )
    assert r.status_code == 200
    body = r.get_json()
    assert body["success"] is True
    assert body["youtube"]["cookies_configured"] is True
    assert fake_cm.get("youtube.cookies_browser") == PASTE_MODE
    path = fake_cm.get("youtube.cookies_file")
    assert path and Path(path).exists()


def test_post_empty_browser_does_not_wipe_when_file_exists(client):
    flask_client, fake_cm = client
    cookie_path = fake_cm.database_path.parent / "youtube_cookies.txt"
    cookie_path.write_text(NETSCAPE)
    fake_cm.set("youtube.cookies_file", str(cookie_path))
    fake_cm.set("youtube.cookies_browser", PASTE_MODE)

    r = flask_client.post(
        "/api/settings",
        json={"youtube": {"cookies_browser": "", "download_delay": 3, "cookies_paste": ""}},
    )
    assert r.status_code == 200
    assert fake_cm.get("youtube.cookies_browser") == PASTE_MODE
    assert Path(fake_cm.get("youtube.cookies_file")).exists()


def test_post_paste_without_login_cookies_rejected(client):
    flask_client, _fake_cm = client
    r = flask_client.post(
        "/api/settings",
        json={
            "youtube": {
                "cookies_browser": "custom",
                "download_delay": 3,
                "cookies_paste": ".youtube.com\tTRUE\t/\tTRUE\t123\tPREF\tnovalue\n",
            }
        },
    )
    assert r.status_code == 400
    assert "login cookies" in r.get_json()["error"].lower()


def test_post_empty_paste_preserves_existing_file(client):
    flask_client, fake_cm = client
    cookie_path = fake_cm.database_path.parent / "youtube_cookies.txt"
    cookie_path.write_text(NETSCAPE)
    fake_cm.set("youtube.cookies_file", str(cookie_path))
    fake_cm.set("youtube.cookies_browser", PASTE_MODE)
    before = cookie_path.read_text()

    r = flask_client.post(
        "/api/settings",
        json={"youtube": {"cookies_browser": "custom", "download_delay": 3, "cookies_paste": ""}},
    )
    assert r.status_code == 200
    assert cookie_path.read_text() == before
