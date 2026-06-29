"""YouTube download retry strategy keeps cookies on bot-check failures."""

from __future__ import annotations

from unittest.mock import patch

import core.youtube_client as yt

def test_bot_error_retry_keeps_cookies(monkeypatch):
    cookie_opts = {"cookiefile": "/cfg/youtube_cookies.txt"}
    monkeypatch.setattr(yt, "_resolve_cookie_opts", lambda: cookie_opts)

    opts = yt._apply_download_retry_strategy(
        {"format": "bestaudio/best"},
        attempt=1,
        max_retries=3,
        last_error="Sign in to confirm you're not a bot",
    )
    assert opts.get("cookiefile") == "/cfg/youtube_cookies.txt"
    assert opts.get("extractor_args") == {"youtube": {"player_client": ["tv", "web_creator", "mweb"]}}


def test_cookiefile_attempt_zero_uses_web_clients(monkeypatch):
    cookie_opts = {"cookiefile": "/cfg/youtube_cookies.txt"}
    monkeypatch.setattr(yt, "_resolve_cookie_opts", lambda: cookie_opts)

    opts = yt._apply_download_retry_strategy(
        {"format": "bestaudio/best"},
        attempt=0,
        max_retries=3,
    )
    assert opts.get("cookiefile") == "/cfg/youtube_cookies.txt"
    assert opts.get("extractor_args") == {"youtube": {"player_client": ["web", "mweb"]}}


def test_format_error_retry_drops_cookies(monkeypatch):
    cookie_opts = {"cookiefile": "/cfg/youtube_cookies.txt"}
    monkeypatch.setattr(yt, "_resolve_cookie_opts", lambda: cookie_opts)

    opts = yt._apply_download_retry_strategy(
        {"format": "bestaudio/best"},
        attempt=1,
        max_retries=3,
        last_error="Requested format not available",
    )
    assert "cookiefile" not in opts
    assert "cookiesfrombrowser" not in opts


def test_bot_error_final_retry_keeps_cookies(monkeypatch):
    cookie_opts = {"cookiefile": "/cfg/youtube_cookies.txt"}
    monkeypatch.setattr(yt, "_resolve_cookie_opts", lambda: cookie_opts)

    opts = yt._apply_download_retry_strategy(
        {"format": "bestaudio/best"},
        attempt=2,
        max_retries=3,
        last_error="Sign in to confirm you're not a bot",
    )
    assert opts.get("cookiefile") == "/cfg/youtube_cookies.txt"
    assert opts.get("format") == "best"
    assert opts.get("extractor_args") == {"youtube": {"player_client": ["ios", "web", "mweb"]}}


def test_resolve_cookie_opts_called_each_attempt(monkeypatch):
    calls = []
    monkeypatch.setattr(yt, "_resolve_cookie_opts", lambda: calls.append(1) or {})

    yt._apply_download_retry_strategy({}, attempt=0, max_retries=3)
    yt._apply_download_retry_strategy({}, attempt=1, max_retries=3, last_error="403 Forbidden")
    assert len(calls) == 2
