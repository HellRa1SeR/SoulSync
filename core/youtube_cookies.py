"""YouTube cookie options for yt-dlp — a browser store *or* a pasted cookies.txt.

Settings → YouTube offers two ways to authenticate yt-dlp:

* a **browser dropdown** (Chrome/Firefox/…) → yt-dlp ``cookiesfrombrowser``, which
  reads a logged-in browser's cookie store *on the same machine as SoulSync*. Great
  for local installs, useless on a headless server / Docker box (no browser there).
* a **"Paste cookies.txt"** mode → yt-dlp ``cookiefile``, a Netscape-format cookie
  file the user exports (e.g. with a "Get cookies.txt LOCALLY" extension) and pastes
  in. This is the only path that works for server/Docker users, and it's what makes
  *private* playlists — a user's "Liked Music" (``list=LM``) — actually visible.

This module centralises the precedence and the pasted-file validation so the live
opts (:func:`build_youtube_cookie_opts`) and the settings-save write agree, and so
the seam is unit-testable without I/O. The web layer owns *where* the file lives
(beside the database under ``/app/data`` in Docker); this module decides the opts
and validates content.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any, Dict, Optional, Union

from utils.logging_config import get_logger

logger = get_logger("youtube_cookies")

# Sentinel dropdown value meaning "use a pasted cookies.txt file" rather than a
# browser name. Anything else non-empty is treated as a browser for cookiesfrombrowser.
PASTE_MODE = "custom"

COOKIE_FILENAME = "youtube_cookies.txt"

# Cookie names that indicate a logged-in YouTube session (names only — never log values).
YOUTUBE_AUTH_COOKIE_HINTS = frozenset({
    "SID",
    "HSID",
    "SSID",
    "APISID",
    "SAPISID",
    "LOGIN_INFO",
    "SIDCC",
    "__Secure-1PSID",
    "__Secure-3PSID",
    "__Secure-1PAPISID",
    "__Secure-3PAPISID",
    "__Secure-1PSIDTS",
    "__Secure-3PSIDTS",
})

# Any one of these strongly suggests the export captured a login session.
YOUTUBE_AUTH_COOKIE_REQUIRED_ANY = frozenset({
    "SID",
    "LOGIN_INFO",
    "__Secure-1PSID",
    "__Secure-3PSID",
})


def get_cookiefile_path(
    config_path: Union[str, Path],
    database_path: Union[str, Path, None] = None,
) -> Path:
    """Canonical location for the pasted cookies.txt file.

    Prefers ``{database_dir}/youtube_cookies.txt`` (same volume as SQLite in Docker).
    Falls back to ``{config_dir}/youtube_cookies.txt`` when no database path is known.
    """
    if database_path:
        return Path(database_path).parent / COOKIE_FILENAME
    return Path(config_path).parent / COOKIE_FILENAME


def legacy_cookiefile_path(config_path: Union[str, Path]) -> Path:
    """Previous location next to config.json — kept for one-time migration."""
    return Path(config_path).parent / COOKIE_FILENAME


def migrate_cookiefile_to_canonical(
    config_path: Union[str, Path],
    database_path: Union[str, Path, None] = None,
) -> Path:
    """Ensure the cookie file lives at the canonical path, copying from legacy if needed."""
    canonical = get_cookiefile_path(config_path, database_path)
    if canonical.exists():
        return canonical
    legacy = legacy_cookiefile_path(config_path)
    if legacy.exists() and legacy != canonical:
        try:
            canonical.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(str(legacy), str(canonical))
            try:
                os.chmod(canonical, 0o600)
            except OSError:
                pass
            logger.info(
                "Migrated YouTube cookies from legacy path %s to %s",
                legacy,
                canonical,
            )
        except OSError as exc:
            logger.warning("Could not migrate YouTube cookies to %s: %s", canonical, exc)
            return legacy
    return canonical


def count_cookie_rows(path: Union[str, Path]) -> int:
    """Count valid Netscape cookie rows in a file (no values logged)."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return 0
    count = 0
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        if len(line.split("\t")) >= 6:
            count += 1
    return count


def cookiefile_has_valid_rows(path: Union[str, Path]) -> bool:
    """True when ``path`` exists and contains at least one real cookie row."""
    return bool(path) and os.path.exists(path) and count_cookie_rows(path) > 0


def list_cookie_names(path: Union[str, Path]) -> list[str]:
    """Return cookie names from a Netscape file (no values)."""
    names: list[str] = []
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return names
    for raw in text.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) >= 6:
            names.append(parts[5])
    return names


def audit_youtube_cookiefile(path: Union[str, Path]) -> Dict[str, Any]:
    """Non-secret audit of a cookies.txt for YouTube auth suitability."""
    path = Path(path)
    row_count = count_cookie_rows(path) if path.exists() else 0
    auth_present: list[str] = []
    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            text = ""
        for raw in text.splitlines():
            line = raw.rstrip("\n")
            if not line or line.lstrip().startswith("#"):
                continue
            parts = line.split("\t")
            if len(parts) < 7:
                continue
            domain, name = parts[0], parts[5]
            if "youtube.com" not in domain.lower():
                continue
            if name in YOUTUBE_AUTH_COOKIE_HINTS or name.startswith("__Secure-"):
                auth_present.append(name)
    auth_ok = any(n in auth_present for n in YOUTUBE_AUTH_COOKIE_REQUIRED_ANY)
    return {
        "path": str(path),
        "file_exists": path.exists(),
        "row_count": row_count,
        "youtube_auth_names": sorted(set(auth_present)),
        "auth_ok": auth_ok,
    }


def cookiefile_has_youtube_auth(content: Any) -> bool:
    """True when pasted content includes at least one YouTube login cookie name."""
    if not content or not isinstance(content, str):
        return False
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        domain, name = parts[0], parts[5]
        if "youtube.com" not in domain.lower():
            continue
        if name in YOUTUBE_AUTH_COOKIE_REQUIRED_ANY:
            return True
    return False


def resolve_active_cookiefile_path(
    config_path: Union[str, Path],
    database_path: Union[str, Path, None] = None,
    stored_path: str = "",
) -> str:
    """Pick the best on-disk cookies.txt: canonical, then stored, then legacy."""
    migrate_cookiefile_to_canonical(config_path, database_path)
    canonical = get_cookiefile_path(config_path, database_path)
    legacy = legacy_cookiefile_path(config_path)
    candidates: list[Path] = [canonical]
    if stored_path:
        candidates.append(Path(stored_path))
    if legacy not in candidates:
        candidates.append(legacy)

    seen: set[str] = set()
    for candidate in candidates:
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        if cookiefile_has_valid_rows(candidate):
            return str(candidate)

    for candidate in candidates:
        if candidate.exists():
            return str(candidate)
    return str(canonical)


def build_youtube_cookie_opts(
    mode: Any,
    cookiefile_path: str = "",
    *,
    cookiefile_exists: bool = False,
) -> Dict[str, Any]:
    """Return the yt-dlp cookie options for a given Settings→YouTube ``mode``. Pure.

    * ``mode == PASTE_MODE`` → ``{'cookiefile': path}`` when the file exists, else
      ``{}`` (a stale/missing path must never become a broken cookiefile arg).
    * ``mode`` is any other non-empty string → ``{'cookiesfrombrowser': (mode,)}``.
    * ``mode`` falsy → ``{}`` (anonymous; public playlists only).

    Precedence is structural: a browser name is never ``PASTE_MODE``, so the two
    cookie sources can't both be emitted. No I/O here — the caller passes
    ``cookiefile_exists`` (the ``os.path.exists`` result) so this stays pure.
    """
    m = str(mode or "").strip()
    if m == PASTE_MODE:
        if cookiefile_path and cookiefile_exists:
            return {"cookiefile": str(cookiefile_path)}
        return {}
    if m:
        return {"cookiesfrombrowser": (m,)}
    return {}


def resolve_youtube_cookie_state(
    mode: Any,
    cookiefile_path: str = "",
    *,
    allow_orphan_file_fallback: bool = True,
) -> Dict[str, Any]:
    """Resolve live yt-dlp cookie opts plus diagnostic metadata for logging.

    Returns a dict with keys:
    * ``opts`` — pass-through to yt-dlp
    * ``mode``, ``path``, ``file_exists``, ``file_size``, ``cookie_row_count``
    * ``reason`` — ``ok``, ``browser_mode``, ``mode_empty``, ``file_missing``,
      ``file_empty``, ``orphan_file_fallback``
    """
    m = str(mode or "").strip()
    path = str(cookiefile_path or "").strip()
    file_exists = bool(path) and os.path.exists(path)
    file_size = 0
    row_count = 0
    if file_exists:
        try:
            file_size = os.path.getsize(path)
        except OSError:
            file_size = 0
        row_count = count_cookie_rows(path)

    effective_mode = m
    reason = "mode_empty"

    if m == PASTE_MODE:
        if file_exists and row_count > 0:
            reason = "ok"
        elif file_exists:
            reason = "file_empty"
        else:
            reason = "file_missing"
    elif m:
        reason = "browser_mode"
    elif (
        allow_orphan_file_fallback
        and path
        and file_exists
        and row_count > 0
    ):
        # cookies_browser was wiped but the saved file still exists — heal it.
        effective_mode = PASTE_MODE
        reason = "orphan_file_fallback"
        logger.warning(
            "YouTube cookies file exists at %s but mode is empty — using cookiefile fallback",
            path,
        )
    elif path and not file_exists:
        reason = "file_missing"
    else:
        reason = "mode_empty"

    opts = build_youtube_cookie_opts(
        effective_mode,
        path,
        cookiefile_exists=file_exists and row_count > 0,
    )

    audit: Dict[str, Any] = {}
    if path and file_exists:
        audit = audit_youtube_cookiefile(path)

    return {
        "opts": opts,
        "mode": m,
        "effective_mode": effective_mode,
        "path": path,
        "file_exists": file_exists,
        "file_size": file_size,
        "cookie_row_count": row_count,
        "reason": reason,
        "audit": audit,
    }


def youtube_cookie_summary(state: Dict[str, Any]) -> Dict[str, Any]:
    """Non-secret summary suitable for API responses."""
    audit = state.get("audit") or {}
    return {
        "cookies_browser": state.get("effective_mode") or state.get("mode") or "",
        "cookies_configured": bool(state.get("opts")),
        "cookies_file_present": bool(
            state.get("file_exists") and state.get("cookie_row_count", 0) > 0
        ),
        "cookies_auth_ok": audit.get("auth_ok", False),
        "cookies_file": state.get("path") or "",
    }


def looks_like_cookiefile(content: Any) -> bool:
    """True when ``content`` plausibly is a Netscape/Mozilla ``cookies.txt``.

    Requires at least one real cookie row — a non-comment line with >= 6 TAB-separated
    fields (domain, flag, path, secure, expiry, name[, value]). The ``# Netscape HTTP
    Cookie File`` header alone is NOT enough: a header-only paste carries no auth and
    would silently save a useless file. This guards the save path so pasting junk (a
    URL, JSON, or just the header) is rejected up front instead of being written out
    and making yt-dlp raise mid-extraction.
    """
    if not content or not isinstance(content, str):
        return False
    for raw in content.splitlines():
        line = raw.rstrip("\n")
        if not line or line.lstrip().startswith("#"):
            continue
        if len(line.split("\t")) >= 6:
            return True
    return False


def write_pasted_cookiefile(content: Any, dest_path: str) -> str:
    """Validate + write a pasted ``cookies.txt`` to ``dest_path``.

    Returns the written path on success, or ``""`` when the content is empty /
    doesn't look like a cookie file / can't be written — in which case the caller
    leaves any existing file untouched (a blank save must not wipe a saved cookie).
    Best-effort ``0600`` perms since the file holds live session secrets.
    """
    if not looks_like_cookiefile(content):
        return ""
    try:
        dest = Path(dest_path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        text = content if content.endswith("\n") else content + "\n"
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            os.chmod(dest, 0o600)
        except OSError:
            pass
        return str(dest)
    except OSError:
        return ""


def is_bot_check_error(error_msg: str) -> bool:
    """True when yt-dlp/YouTube rejected the request as a bot-check."""
    if not error_msg:
        return False
    lower = error_msg.lower()
    return (
        "sign in to confirm" in lower
        or "not a bot" in lower
        or "confirm you're not a bot" in lower
        or "confirm you’re not a bot" in lower
    )


__all__ = [
    "PASTE_MODE",
    "COOKIE_FILENAME",
    "YOUTUBE_AUTH_COOKIE_REQUIRED_ANY",
    "audit_youtube_cookiefile",
    "build_youtube_cookie_opts",
    "cookiefile_has_valid_rows",
    "cookiefile_has_youtube_auth",
    "count_cookie_rows",
    "get_cookiefile_path",
    "is_bot_check_error",
    "legacy_cookiefile_path",
    "list_cookie_names",
    "looks_like_cookiefile",
    "migrate_cookiefile_to_canonical",
    "resolve_active_cookiefile_path",
    "resolve_youtube_cookie_state",
    "write_pasted_cookiefile",
    "youtube_cookie_summary",
]
