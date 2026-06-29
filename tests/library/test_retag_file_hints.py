"""Tests for retag planner file-hint title matching."""

from core.library.retag_planner import match_source_tracks


def test_match_source_tracks_uses_match_title_from_file_hints():
    source_tracks = [
        {"name": "Real Song", "track_number": 1, "disc_number": 1},
    ]
    library_tracks = [
        {
            "title": "Wrong Library Title",
            "match_title": "Real Song",
            "track_number": 2,
            "disc_number": 1,
        },
    ]
    pairs = match_source_tracks(source_tracks, library_tracks)
    assert pairs[0][1] is not None
    assert pairs[0][1]["name"] == "Real Song"
