"""Tests for file-tag / filename metadata search hints."""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from core.metadata.file_search_hints import collect_file_search_hints


class TestCollectFileSearchHints:
    def test_prefers_embedded_tags_over_library_db(self, tmp_path):
        audio = tmp_path / "Wrong Artist - Wrong Title.flac"
        audio.write_bytes(b"not-a-real-flac")

        tags = {
            "title": "Real Title",
            "artist": "Real Artist",
            "album": "Real Album",
            "album_artist": "",
        }
        with patch("core.tag_writer.read_file_tags", return_value=tags):
            hints = collect_file_search_hints(
                str(audio),
                db_title="Wrong Title",
                db_artist="Wrong Artist",
                db_album="Wrong Album",
            )

        assert hints.title == "Real Title"
        assert hints.artist == "Real Artist"
        assert hints.album == "Real Album"
        assert hints.field_sources["title"] == "file_tag"
        assert hints.field_sources["artist"] == "file_tag"

    def test_filename_used_when_tags_missing(self, tmp_path):
        audio = tmp_path / "Artist Name - Track Name.mp3"
        audio.write_bytes(b"x")

        with patch("core.tag_writer.read_file_tags", return_value={}):
            hints = collect_file_search_hints(
                str(audio),
                db_title="Library Title",
                db_artist="Unknown Artist",
            )

        assert hints.title == "Track Name"
        assert hints.artist == "Artist Name"
        assert hints.field_sources["title"] == "filename"
        assert hints.field_sources["artist"] == "filename"

    def test_skips_unknown_artist_from_library_when_file_has_artist(self, tmp_path):
        audio = tmp_path / "Tagged Artist - Song.flac"
        audio.write_bytes(b"x")

        with patch("core.tag_writer.read_file_tags", return_value={"title": "Song"}):
            hints = collect_file_search_hints(
                str(audio),
                db_artist="Unknown Artist",
            )

        assert hints.artist == "Tagged Artist"
        assert hints.field_sources["artist"] == "filename"

    def test_falls_back_to_library_when_file_unreadable(self):
        hints = collect_file_search_hints(
            None,
            db_title="DB Title",
            db_artist="DB Artist",
            db_album="DB Album",
            db_duration_ms=180000,
        )

        assert hints.title == "DB Title"
        assert hints.artist == "DB Artist"
        assert hints.album == "DB Album"
        assert hints.duration_ms == 180000

    def test_builds_search_queries(self, tmp_path):
        audio = tmp_path / "Artist - Title.flac"
        audio.write_bytes(b"x")

        with patch("core.tag_writer.read_file_tags", return_value={
            "title": "Title",
            "artist": "Artist",
            "album": "Album",
        }):
            hints = collect_file_search_hints(str(audio))

        assert hints.search_queries
        assert any("artist" in q.lower() and "title" in q.lower() for q in hints.search_queries)


class TestCollectAlbumHintsFromFiles:
    def test_picks_dominant_album_from_tags(self, tmp_path):
        files = []
        for title in ("One", "Two", "Three"):
            p = tmp_path / f"track_{title}.flac"
            p.write_bytes(b"x")
            files.append(str(p))

        def _read_tags(path):
            return {
                "title": os.path.basename(path),
                "artist": "Real Artist",
                "album": "Real Album",
            }

        with patch("core.tag_writer.read_file_tags", side_effect=_read_tags):
            from core.metadata.file_search_hints import collect_album_hints_from_files
            hints = collect_album_hints_from_files(
                files,
                db_album="Wrong Album",
                db_artist="Unknown Artist",
            )

        assert hints.album == "Real Album"
        assert hints.artist == "Real Artist"
        assert hints.search_queries
