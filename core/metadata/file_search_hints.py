"""Build metadata-search prompts from a file's embedded tags and filename.

When library DB metadata is wrong or thin (Plex scrape drift, "Unknown
Artist", missing album), the on-disk file often carries better hints in
its tags or ``Artist - Title.ext`` filename. Callers pass a resolved file
path plus optional DB fallbacks; this module merges them into a
``TrackQuery`` and a ranked list of search-query strings for
``multi_source_search``.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.imports.filename import parse_filename_metadata
from utils.logging_config import get_logger

logger = get_logger("metadata.file_search_hints")

_UNKNOWN_ARTIST_NAMES = frozenset({
    "unknown",
    "unknown artist",
    "various artists",
    "various artist",
    "va",
    "<unknown>",
    "?",
})

_VERSION_SUFFIX_RE = re.compile(
    r"\s*[\(\[](single version|album version|remaster(?:ed)?|deluxe|"
    r"bonus|explicit|clean|radio edit)[\)\]]",
    re.IGNORECASE,
)


@dataclass
class FileSearchHints:
    """Merged search inputs derived from a file (+ optional DB fallbacks)."""
    title: str
    artist: str
    album: str = ""
    duration_ms: int = 0
    # Where each field came from — useful for UI/debug.
    field_sources: Dict[str, str] = field(default_factory=dict)
    # Extra query strings beyond the default per-source builder.
    search_queries: List[str] = field(default_factory=list)


def _clean_title(title: str) -> str:
    return _VERSION_SUFFIX_RE.sub("", title or "").strip()


def _is_unknown_artist(name: str) -> bool:
    return (name or "").strip().lower() in _UNKNOWN_ARTIST_NAMES


def _pick(*values: str, reject_unknown: bool = False) -> str:
    for value in values:
        text = (value or "").strip()
        if not text:
            continue
        if reject_unknown and _is_unknown_artist(text):
            continue
        return text
    return ""


def _duration_ms_from_file(file_path: str) -> int:
    try:
        from core.library.file_tags import read_embedded_tags

        info = read_embedded_tags(file_path)
        if info.get("available") and info.get("duration"):
            return int(float(info["duration"]) * 1000)
    except Exception as exc:
        logger.debug("duration read failed for %s: %s", file_path, exc)
    return 0


def collect_file_search_hints(
    file_path: Optional[str],
    *,
    db_title: str = "",
    db_artist: str = "",
    db_album: str = "",
    db_duration_ms: int = 0,
) -> FileSearchHints:
    """Merge embedded tags, filename parsing, and DB fallbacks.

    Priority per field:
    - **title / album**: embedded tag → filename/path parse → DB
    - **artist**: embedded artist → album artist tag → filename → DB
      (DB artist skipped when it is a known placeholder)
    - **duration**: file audio length → DB duration
    """
    tag_title = tag_artist = tag_album = ""
    tag_album_artist = ""

    if file_path and os.path.isfile(file_path):
        try:
            from core.tag_writer import read_file_tags

            tags = read_file_tags(file_path) or {}
            tag_title = (tags.get("title") or "").strip()
            tag_artist = (tags.get("artist") or "").strip()
            tag_album_artist = (tags.get("album_artist") or "").strip()
            tag_album = (tags.get("album") or "").strip()
        except Exception as exc:
            logger.debug("tag read failed for %s: %s", file_path, exc)

    parsed: Dict[str, Any] = {}
    if file_path:
        parsed = parse_filename_metadata(file_path)

    title = _pick(tag_title, parsed.get("title"), db_title)
    artist = _pick(
        tag_artist,
        tag_album_artist,
        parsed.get("artist"),
        db_artist,
        reject_unknown=True,
    )
    if not artist:
        artist = _pick(db_artist)

    album = _pick(tag_album, parsed.get("album"), db_album)

    duration_ms = db_duration_ms or 0
    file_dur = 0
    if file_path and os.path.isfile(file_path):
        file_dur = _duration_ms_from_file(file_path)
        if file_dur > 0:
            duration_ms = file_dur

    field_sources: Dict[str, str] = {}
    if title:
        if tag_title and title == tag_title:
            field_sources["title"] = "file_tag"
        elif parsed.get("title") and title == parsed.get("title"):
            field_sources["title"] = "filename"
        else:
            field_sources["title"] = "library"
    if artist:
        if tag_artist and artist == tag_artist:
            field_sources["artist"] = "file_tag"
        elif tag_album_artist and artist == tag_album_artist:
            field_sources["artist"] = "album_artist_tag"
        elif parsed.get("artist") and artist == parsed.get("artist"):
            field_sources["artist"] = "filename"
        else:
            field_sources["artist"] = "library"
    if album:
        if tag_album and album == tag_album:
            field_sources["album"] = "file_tag"
        elif parsed.get("album") and album == parsed.get("album"):
            field_sources["album"] = "filename"
        else:
            field_sources["album"] = "library"
    if duration_ms:
        field_sources["duration_ms"] = "file_audio" if file_dur == duration_ms else "library"

    return FileSearchHints(
        title=title,
        artist=artist,
        album=album,
        duration_ms=duration_ms,
        field_sources=field_sources,
        search_queries=_build_search_queries(title, artist, album),
    )


def _build_search_queries(title: str, artist: str, album: str) -> List[str]:
    """Ranked query strings for multi-source search."""
    if not title and not artist:
        return []

    clean = _clean_title(title)
    queries: List[str] = []

    try:
        from core.matching_engine import MusicMatchingEngine

        engine = MusicMatchingEngine()
        temp_track = type("_TempTrack", (), {
            "name": title,
            "artists": [artist] if artist else [],
            "album": album or None,
        })()
        queries.extend(engine.generate_download_queries(temp_track))
    except Exception as exc:
        logger.debug("matching_engine query generation failed: %s", exc)

    # Simple fallbacks when the engine yields nothing useful.
    if artist and clean:
        queries.append(f"{artist} {clean}".strip())
    if artist and album and clean:
        queries.append(f"{artist} {album} {clean}".strip())
    if clean:
        queries.append(clean)
    if title and title != clean:
        queries.append(f"{artist} {title}".strip() if artist else title)

    seen: set[str] = set()
    unique: List[str] = []
    for q in queries:
        key = q.lower().strip()
        if q and key not in seen:
            unique.append(q.strip())
            seen.add(key)
    return unique


def _build_album_search_queries(artist: str, album: str) -> List[str]:
    """Ranked album search query strings."""
    queries: List[str] = []
    if artist and album:
        queries.append(f"{artist} {album}".strip())
        try:
            from core.matching_engine import MusicMatchingEngine
            engine = MusicMatchingEngine()
            clean_album = engine.clean_album_name(album)
            if clean_album and clean_album != album.lower():
                queries.append(f"{artist} {clean_album}".strip())
        except Exception as exc:
            logger.debug("album query clean failed: %s", exc)
    if album:
        queries.append(album.strip())
    seen: set[str] = set()
    unique: List[str] = []
    for q in queries:
        key = q.lower().strip()
        if q and key not in seen:
            unique.append(q.strip())
            seen.add(key)
    return unique


def collect_album_hints_from_files(
    file_paths: List[str],
    *,
    db_album: str = "",
    db_artist: str = "",
) -> FileSearchHints:
    """Aggregate album/artist hints across multiple audio files.

    Picks the dominant album tag (then dominant artist within that album),
    matching the consensus logic auto-import uses for folder identification.
    Falls back to filename/path parsing and DB metadata per file when tags
    are thin.
    """
    album_counts: Dict[str, int] = {}
    album_artist_counts: Dict[str, Dict[str, int]] = {}
    per_file: List[FileSearchHints] = []

    for path in file_paths or []:
        if not path:
            continue
        hints = collect_file_search_hints(path, db_album=db_album, db_artist=db_artist)
        per_file.append(hints)
        album_key = (hints.album or "").lower().strip()
        if album_key:
            album_counts[album_key] = album_counts.get(album_key, 0) + 1
            artist_key = (hints.artist or "").lower().strip()
            if artist_key:
                album_artist_counts.setdefault(album_key, {})
                album_artist_counts[album_key][artist_key] = (
                    album_artist_counts[album_key].get(artist_key, 0) + 1
                )

    if album_counts:
        best_album_key = max(album_counts, key=album_counts.get)
        best_artist_key = ""
        if best_album_key in album_artist_counts and album_artist_counts[best_album_key]:
            best_artist_key = max(
                album_artist_counts[best_album_key],
                key=album_artist_counts[best_album_key].get,
            )
        # Recover original casing from a contributing file hint.
        album = db_album
        artist = db_artist
        for hints in per_file:
            if (hints.album or "").lower().strip() == best_album_key:
                album = hints.album or album
                if best_artist_key and (hints.artist or "").lower().strip() == best_artist_key:
                    artist = hints.artist or artist
                    break
        if not artist and best_artist_key:
            artist = best_artist_key
        return FileSearchHints(
            title="",
            artist=artist,
            album=album,
            search_queries=_build_album_search_queries(artist, album),
        )

    if per_file:
        return per_file[0]

    return FileSearchHints(
        title="",
        artist=db_artist,
        album=db_album,
        search_queries=_build_album_search_queries(db_artist, db_album),
    )


def resolve_album_source_from_hints(
    hints: FileSearchHints,
    source_chain: List[str],
) -> Optional[tuple[str, str]]:
    """Search metadata sources for an album using file-derived hints.

    Returns ``(source_name, album_source_id)`` when a result clears the
    0.4 similarity threshold, or ``None`` when every source/query fails.
    """
    from difflib import SequenceMatcher

    from core.metadata_service import get_client_for_source

    target_album = (hints.album or "").strip()
    target_artist = (hints.artist or "").strip()
    if not target_album:
        return None

    queries = hints.search_queries or _build_album_search_queries(target_artist, target_album)

    def _score(result) -> float:
        r_album = (getattr(result, "name", "") or "").strip()
        r_artist = ""
        artists = getattr(result, "artists", None) or []
        if artists:
            a = artists[0]
            r_artist = a.get("name", str(a)) if isinstance(a, dict) else str(a)
        album_sim = SequenceMatcher(
            None, target_album.lower(), r_album.lower(),
        ).ratio()
        score = album_sim * 0.6
        if target_artist and r_artist:
            score += SequenceMatcher(
                None, target_artist.lower(), r_artist.lower(),
            ).ratio() * 0.4
        return score

    for source in source_chain:
        client = get_client_for_source(source)
        if not client or not hasattr(client, "search_albums"):
            continue
        for query in queries:
            try:
                results = client.search_albums(query, limit=5) or []
            except Exception as exc:
                logger.debug("album search failed on %s for %r: %s", source, query, exc)
                continue
            if not results:
                continue
            best = max(results, key=_score)
            if _score(best) >= 0.4:
                album_id = str(getattr(best, "id", "") or "")
                if album_id:
                    logger.info(
                        "Resolved album source via file hints: %s / %s → %s %s",
                        target_artist, target_album, source, album_id,
                    )
                    return source, album_id
    return None


def enrich_track_titles_from_files(
    library_tracks: List[Dict[str, Any]],
    *,
    resolve_path_fn=None,
) -> List[Dict[str, Any]]:
    """Return library track dicts with ``match_title`` set from file hints.

    ``match_source_tracks`` can prefer ``match_title`` over the library DB
    ``title`` when pairing against a metadata source tracklist.
    """
    enriched: List[Dict[str, Any]] = []
    for track in library_tracks:
        row = dict(track)
        file_path = row.get("file_path")
        resolved = file_path
        if resolve_path_fn and file_path:
            try:
                resolved = resolve_path_fn(file_path) or file_path
            except Exception:
                resolved = file_path
        hints = collect_file_search_hints(
            resolved if resolved and os.path.isfile(resolved) else None,
            db_title=row.get("title") or "",
        )
        if hints.title:
            row["match_title"] = hints.title
        enriched.append(row)
    return enriched


def hints_to_track_query(
    hints: FileSearchHints,
    *,
    spotify_track_id: Optional[str] = None,
    deezer_id: Optional[str] = None,
):
    """Convert hints into a ``TrackQuery`` for ``search_all_sources``."""
    from core.metadata.multi_source_search import TrackQuery

    return TrackQuery(
        title=hints.title,
        artist=hints.artist,
        album=hints.album,
        duration_ms=hints.duration_ms,
        spotify_track_id=spotify_track_id,
        deezer_id=deezer_id,
    )


__all__ = [
    "FileSearchHints",
    "collect_file_search_hints",
    "collect_album_hints_from_files",
    "enrich_track_titles_from_files",
    "hints_to_track_query",
    "resolve_album_source_from_hints",
]
