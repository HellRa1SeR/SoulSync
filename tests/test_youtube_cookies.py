"""Settings → YouTube cookie options: browser store vs a pasted cookies.txt.

#902: syncing a YouTube *Music* "Liked Music" playlist (list=LM) needs auth, and on
a server/Docker box there's no local browser for cookiesfrombrowser to read — so we
let users paste a cookies.txt (yt-dlp cookiefile). These pin the precedence (so the
two cookie sources can never both be emitted), the paste validation (junk must not be
written out and break yt-dlp), and the fail-safe write (a blank save never wipes a
saved file).
"""

from __future__ import annotations

from core.youtube_cookies import (
    PASTE_MODE,
    audit_youtube_cookiefile,
    build_youtube_cookie_opts,
    cookiefile_has_youtube_auth,
    count_cookie_rows,
    get_cookiefile_path,
    is_bot_check_error,
    legacy_cookiefile_path,
    looks_like_cookiefile,
    migrate_cookiefile_to_canonical,
    resolve_active_cookiefile_path,
    resolve_youtube_cookie_state,
    write_pasted_cookiefile,
    youtube_cookie_summary,
)

NETSCAPE = (
    "# Netscape HTTP Cookie File\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tLOGIN_INFO\tsecretvalue\n"
    ".youtube.com\tTRUE\t/\tTRUE\t1999999999\tSID\tanother\n"
)


# ── precedence (pure opts) ──────────────────────────────────────────────────

def test_empty_mode_is_anonymous():
    assert build_youtube_cookie_opts("") == {}
    assert build_youtube_cookie_opts(None) == {}


def test_browser_mode_uses_cookiesfrombrowser():
    assert build_youtube_cookie_opts("firefox") == {"cookiesfrombrowser": ("firefox",)}


def test_paste_mode_uses_cookiefile_when_present():
    opts = build_youtube_cookie_opts(PASTE_MODE, "/cfg/youtube_cookies.txt", cookiefile_exists=True)
    assert opts == {"cookiefile": "/cfg/youtube_cookies.txt"}


def test_paste_mode_without_a_real_file_is_anonymous_not_broken():
    # stale/missing path must NOT become a cookiefile arg yt-dlp would choke on
    assert build_youtube_cookie_opts(PASTE_MODE, "/cfg/gone.txt", cookiefile_exists=False) == {}
    assert build_youtube_cookie_opts(PASTE_MODE, "", cookiefile_exists=True) == {}


def test_sources_are_mutually_exclusive():
    # a browser name is never PASTE_MODE, so cookiefile + cookiesfrombrowser can't co-occur
    for mode in ("chrome", "firefox", PASTE_MODE, ""):
        opts = build_youtube_cookie_opts(mode, "/x.txt", cookiefile_exists=True)
        assert not ("cookiefile" in opts and "cookiesfrombrowser" in opts)


# ── paste validation ────────────────────────────────────────────────────────

def test_accepts_netscape_header_and_cookie_rows():
    assert looks_like_cookiefile(NETSCAPE) is True
    # no header but a valid tab-separated cookie row still counts
    assert looks_like_cookiefile(".youtube.com\tTRUE\t/\tTRUE\t123\tSID\tv") is True


def test_rejects_junk_paste():
    assert looks_like_cookiefile("") is False
    assert looks_like_cookiefile("   ") is False
    assert looks_like_cookiefile(None) is False
    assert looks_like_cookiefile("https://music.youtube.com/playlist?list=LM") is False
    assert looks_like_cookiefile('{"cookies": []}') is False
    assert looks_like_cookiefile("# Netscape HTTP Cookie File\n# only comments\n") is False


# ── fail-safe write ─────────────────────────────────────────────────────────

def test_write_persists_valid_cookiefile(tmp_path):
    dest = tmp_path / "youtube_cookies.txt"
    out = write_pasted_cookiefile(NETSCAPE, str(dest))
    assert out == str(dest)
    assert dest.read_text().startswith("# Netscape HTTP Cookie File")


def test_write_appends_trailing_newline(tmp_path):
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE.rstrip("\n"), str(dest))
    assert dest.read_text().endswith("\n")


def test_write_refuses_junk_and_leaves_no_file(tmp_path):
    dest = tmp_path / "c.txt"
    assert write_pasted_cookiefile("not a cookie file", str(dest)) == ""
    assert not dest.exists()


def test_write_refuses_junk_without_clobbering_existing(tmp_path):
    # a blank/garbage save must NOT wipe a previously-saved cookie file
    dest = tmp_path / "c.txt"
    write_pasted_cookiefile(NETSCAPE, str(dest))
    before = dest.read_text()
    assert write_pasted_cookiefile("", str(dest)) == ""
    assert dest.read_text() == before


# ── regression: youtube_client must USE the helper, not pass 'custom' as a browser ──
# (Docker bug: pasted cookies threw yt-dlp 'unsupported browser: "custom"' because the
#  client built cookiesfrombrowser=('custom',) instead of a cookiefile.)

def test_resolve_cookie_opts_routes_custom_to_cookiefile(monkeypatch, tmp_path):
    import core.youtube_client as yt
    cookiefile = tmp_path / "youtube_cookies.txt"
    cookiefile.write_text(NETSCAPE)
    cfg = {'youtube.cookies_browser': 'custom', 'youtube.cookies_file': str(cookiefile)}
    monkeypatch.setattr('config.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    monkeypatch.setattr('config.settings.config_manager.config_path', tmp_path / "config" / "config.json")
    monkeypatch.setattr('config.settings.config_manager.database_path', tmp_path / "data" / "db.sqlite")
    opts = yt._resolve_cookie_opts()
    assert opts == {'cookiefile': str(cookiefile)}
    assert 'cookiesfrombrowser' not in opts          # never the bogus browser arg


def test_resolve_cookie_opts_browser_mode_unchanged(monkeypatch):
    import core.youtube_client as yt
    cfg = {'youtube.cookies_browser': 'firefox', 'youtube.cookies_file': ''}
    monkeypatch.setattr('config.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    assert yt._resolve_cookie_opts() == {'cookiesfrombrowser': ('firefox',)}


def test_resolve_cookie_opts_custom_missing_file_is_anonymous(monkeypatch):
    import core.youtube_client as yt
    cfg = {'youtube.cookies_browser': 'custom', 'youtube.cookies_file': '/nope/gone.txt'}
    monkeypatch.setattr('config.settings.config_manager.get',
                        lambda k, d=None: cfg.get(k, d))
    monkeypatch.setattr('config.settings.config_manager.config_path', __import__('pathlib').Path('/cfg/config.json'))
    monkeypatch.setattr('config.settings.config_manager.database_path', __import__('pathlib').Path('/data/db.sqlite'))
    assert yt._resolve_cookie_opts() == {}            # not a broken cookiefile arg


# ── resolve_youtube_cookie_state + path helpers ─────────────────────────────

def test_file_exists_without_custom_mode_still_uses_cookiefile(tmp_path):
    cookiefile = tmp_path / "youtube_cookies.txt"
    cookiefile.write_text(NETSCAPE)
    state = resolve_youtube_cookie_state("", str(cookiefile))
    assert state["opts"] == {"cookiefile": str(cookiefile)}
    assert state["reason"] == "orphan_file_fallback"
    assert state["effective_mode"] == PASTE_MODE


def test_resolve_state_reports_reason_and_row_count(tmp_path):
    cookiefile = tmp_path / "youtube_cookies.txt"
    cookiefile.write_text(NETSCAPE)
    state = resolve_youtube_cookie_state(PASTE_MODE, str(cookiefile))
    assert state["reason"] == "ok"
    assert state["cookie_row_count"] >= 2
    assert state["file_exists"] is True
    assert state["opts"] == {"cookiefile": str(cookiefile)}


def test_migration_reads_old_config_dir_file(tmp_path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    legacy = legacy_cookiefile_path(config_dir / "config.json")
    legacy.write_text(NETSCAPE)
    canonical = migrate_cookiefile_to_canonical(config_dir / "config.json", data_dir / "db.sqlite")
    assert canonical.exists()
    assert canonical.parent == data_dir
    assert count_cookie_rows(canonical) >= 2


def test_get_cookiefile_path_prefers_database_dir(tmp_path):
    path = get_cookiefile_path(tmp_path / "config" / "config.json", tmp_path / "data" / "db.sqlite")
    assert path == tmp_path / "data" / "youtube_cookies.txt"


def test_youtube_cookie_summary_non_secret():
    state = resolve_youtube_cookie_state(PASTE_MODE, "/cfg/c.txt", allow_orphan_file_fallback=False)
    summary = youtube_cookie_summary(state)
    assert "cookies_configured" in summary
    assert "cookies_file_present" in summary
    assert "secret" not in str(summary).lower()


def test_is_bot_check_error():
    assert is_bot_check_error("Sign in to confirm you're not a bot") is True
    assert is_bot_check_error("HTTP Error 403: Forbidden") is False


def test_cookiefile_has_youtube_auth_requires_login_cookie():
    assert cookiefile_has_youtube_auth(NETSCAPE) is True
    assert cookiefile_has_youtube_auth(".youtube.com\tTRUE\t/\tTRUE\t123\tPREF\tv\n") is False


def test_audit_youtube_cookiefile_reports_auth(tmp_path):
    cookiefile = tmp_path / "c.txt"
    cookiefile.write_text(NETSCAPE)
    audit = audit_youtube_cookiefile(cookiefile)
    assert audit["auth_ok"] is True
    assert "SID" in audit["youtube_auth_names"]


def test_resolve_active_cookiefile_prefers_canonical(tmp_path):
    config_dir = tmp_path / "config"
    data_dir = tmp_path / "data"
    config_dir.mkdir()
    data_dir.mkdir()
    legacy = legacy_cookiefile_path(config_dir / "config.json")
    legacy.write_text(".youtube.com\tTRUE\t/\tTRUE\t123\tPREF\tx\n")
    canonical = get_cookiefile_path(config_dir / "config.json", data_dir / "db.sqlite")
    canonical.write_text(NETSCAPE)
    path = resolve_active_cookiefile_path(
        config_dir / "config.json",
        data_dir / "db.sqlite",
        str(legacy),
    )
    assert path == str(canonical)
