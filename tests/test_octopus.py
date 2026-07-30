"""Unit tests for space resolution in octopus.py."""

import asyncio
from contextlib import contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import octopus


def _spaces_client(spaces: list[dict]) -> MagicMock:
    """Build a mock httpx client whose /api/spaces call returns the given spaces."""
    response = MagicMock()
    response.json.return_value = {"Items": spaces}
    response.raise_for_status.return_value = None
    client = MagicMock()
    client.get = AsyncMock(return_value=response)
    return client


@contextmanager
def _space_config(space_name: str, space_id: str = "", resolve_by_name: bool = True):
    """Temporarily override the module-level space configuration."""
    original_name = octopus.OCTOPUS_SPACE_NAME
    original_id = octopus.OCTOPUS_SPACE_ID
    original_resolve = octopus._RESOLVE_SPACE_BY_NAME
    octopus.OCTOPUS_SPACE_NAME = space_name
    octopus.OCTOPUS_SPACE_ID = space_id
    octopus._RESOLVE_SPACE_BY_NAME = resolve_by_name
    try:
        yield
    finally:
        octopus.OCTOPUS_SPACE_NAME = original_name
        octopus.OCTOPUS_SPACE_ID = original_id
        octopus._RESOLVE_SPACE_BY_NAME = original_resolve


@contextmanager
def _patched_async_client(client: MagicMock):
    """Patch httpx.AsyncClient so octopus uses the supplied mock client."""
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=client)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    with patch.object(octopus.httpx, "AsyncClient", factory):
        yield factory


class TestResolveSpaceIdFromName:
    """Tests for _resolve_space_id_from_name."""

    def test_returns_the_id_of_the_matching_space(self):
        client = _spaces_client([{"Id": "Spaces-7", "Name": "My Space"}])
        space_id = asyncio.run(octopus._resolve_space_id_from_name(client, "My Space"))
        assert space_id == "Spaces-7"
        client.get.assert_awaited_once_with(
            "/api/spaces", params={"partialName": "My Space", "take": 1000}
        )

    def test_match_is_case_insensitive(self):
        client = _spaces_client([{"Id": "Spaces-7", "Name": "My Space"}])
        assert asyncio.run(octopus._resolve_space_id_from_name(client, "my space")) == "Spaces-7"

    def test_exact_match_preferred_over_partial_matches(self):
        client = _spaces_client([
            {"Id": "Spaces-8", "Name": "My Space Two"},
            {"Id": "Spaces-7", "Name": "My Space"},
        ])
        assert asyncio.run(octopus._resolve_space_id_from_name(client, "My Space")) == "Spaces-7"

    def test_partial_match_is_not_accepted(self):
        client = _spaces_client([{"Id": "Spaces-8", "Name": "My Space Two"}])
        with pytest.raises(RuntimeError) as exc_info:
            asyncio.run(octopus._resolve_space_id_from_name(client, "My Space"))
        assert "My Space Two" in str(exc_info.value)

    def test_no_spaces_returned_raises(self):
        client = _spaces_client([])
        with pytest.raises(RuntimeError, match="No Octopus space named 'Nope'"):
            asyncio.run(octopus._resolve_space_id_from_name(client, "Nope"))


class TestRefreshSpaceId:
    """Tests for refresh_space_id."""

    def test_configured_space_id_is_used_without_a_lookup(self):
        client = _spaces_client([{"Id": "Spaces-7", "Name": "My Space"}])
        with _space_config("My Space", space_id="Spaces-42", resolve_by_name=False), \
             _patched_async_client(client):
            assert asyncio.run(octopus.refresh_space_id()) == "Spaces-42"
        client.get.assert_not_awaited()

    def test_space_name_is_resolved_and_cached(self):
        client = _spaces_client([{"Id": "Spaces-7", "Name": "My Space"}])
        with _space_config("My Space"), _patched_async_client(client):
            assert asyncio.run(octopus.refresh_space_id()) == "Spaces-7"
            # The module global is updated so subsequent API calls use the new ID
            assert octopus.OCTOPUS_SPACE_ID == "Spaces-7"

    def test_recreated_space_picks_up_the_new_id(self):
        """A space deleted and recreated between refreshes gets its new ID."""
        with _space_config("My Space", space_id="Spaces-7"):
            recreated = _spaces_client([{"Id": "Spaces-99", "Name": "My Space"}])
            with _patched_async_client(recreated):
                assert asyncio.run(octopus.refresh_space_id()) == "Spaces-99"
            assert octopus.OCTOPUS_SPACE_ID == "Spaces-99"

    def test_missing_space_raises_and_keeps_the_previous_id(self):
        client = _spaces_client([])
        with _space_config("My Space", space_id="Spaces-7"), _patched_async_client(client):
            with pytest.raises(RuntimeError):
                asyncio.run(octopus.refresh_space_id())
            assert octopus.OCTOPUS_SPACE_ID == "Spaces-7"

    def test_http_error_propagates(self):
        client = _spaces_client([])
        request = httpx.Request("GET", "http://localhost/api/spaces")
        client.get = AsyncMock(return_value=httpx.Response(500, text="boom", request=request))
        with _space_config("My Space"), _patched_async_client(client):
            with pytest.raises(httpx.HTTPStatusError):
                asyncio.run(octopus.refresh_space_id())
