"""Tests for multi-source search query fallback."""

from __future__ import annotations

from unittest.mock import MagicMock

from core.metadata.multi_source_search import TrackQuery, search_all_sources


class _FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []

    def search_tracks(self, query, limit=10):
        self.queries.append(query)
        if self.responses:
            return self.responses.pop(0)
        return []


class _FakeTrack:
    def __init__(self, track_id, name, artists, album="", duration_ms=0):
        self.id = track_id
        self.name = name
        self.artists = artists
        self.album = album
        self.duration_ms = duration_ms
        self.image_url = ""


def test_extra_queries_tried_before_builtin_fallbacks():
    client = _FakeClient([
        [],
        [_FakeTrack("1", "Hint Title", ["Hint Artist"], duration_ms=200000)],
    ])
    query = TrackQuery(
        title="Wrong Title",
        artist="Wrong Artist",
        duration_ms=0,
    )
    result = search_all_sources(
        query,
        [("itunes", client)],
        clean_title="Wrong Title",
        extra_queries=["Hint Artist Hint Title"],
    )

    assert client.queries[0] == "Hint Artist Hint Title"
    assert result.best_match is not None
    assert result.metadata_results["itunes"][0]["name"] == "Hint Title"
