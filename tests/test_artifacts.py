"""Unit tests for runbook run artifact downloads in octopus.py."""

import asyncio
import base64
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

import octopus


def _artifact(artifact_id: str = "Artifacts-1", filename: str = "report.txt", content_link: str | None = None) -> dict:
    """Build an artifact resource as returned by the Octopus API."""
    artifact = {
        "Id": artifact_id,
        "Filename": filename,
        "Created": "2026-08-16T00:00:00.000+00:00",
    }
    if content_link:
        artifact["Links"] = {"Content": content_link}
    return artifact


def _content_client(content: bytes, items: list[dict] | None = None) -> MagicMock:
    """Build a mock httpx client returning the given artifact list and content."""
    list_response = MagicMock()
    list_response.json.return_value = {"Items": items if items is not None else []}
    list_response.raise_for_status.return_value = None

    content_response = MagicMock()
    content_response.content = content
    content_response.raise_for_status.return_value = None

    client = MagicMock()

    async def get(url, **kwargs):
        return content_response if url.endswith("/content") else list_response

    client.get = AsyncMock(side_effect=get)
    return client


class TestBuildArtifactResult:
    """Unit tests for _build_artifact_result."""

    def test_text_content_is_returned_as_text(self):
        result = octopus._build_artifact_result(_artifact(), b"hello world")

        assert result["id"] == "Artifacts-1"
        assert result["filename"] == "report.txt"
        assert result["encoding"] == "text"
        assert result["content"] == "hello world"
        assert result["size"] == len("hello world")
        assert result["truncated"] is False

    def test_binary_content_is_base64_encoded(self):
        binary = b"\x89PNG\r\n\x1a\n\xff\xfe"

        result = octopus._build_artifact_result(_artifact(filename="chart.png"), binary)

        assert result["encoding"] == "base64"
        assert base64.b64decode(result["content"]) == binary
        assert result["size"] == len(binary)
        assert result["truncated"] is False

    def test_large_text_content_is_truncated(self):
        with patch.object(octopus, "MAX_ARTIFACT_SIZE", 5):
            result = octopus._build_artifact_result(_artifact(), b"0123456789")

        assert result["content"] == "01234"
        assert result["size"] == 10
        assert result["truncated"] is True

    def test_large_binary_content_is_truncated(self):
        binary = b"\xff" * 10

        with patch.object(octopus, "MAX_ARTIFACT_SIZE", 4):
            result = octopus._build_artifact_result(_artifact(), binary)

        assert base64.b64decode(result["content"]) == b"\xff" * 4
        assert result["size"] == 10
        assert result["truncated"] is True

    def test_multibyte_text_is_not_split_into_binary(self):
        """Truncating multi-byte text must not misclassify the artifact as binary."""
        with patch.object(octopus, "MAX_ARTIFACT_SIZE", 3):
            result = octopus._build_artifact_result(_artifact(), "☃☃☃☃".encode("utf-8"))

        assert result["encoding"] == "text"
        assert result["content"] == "☃☃☃"
        assert result["truncated"] is True

    def test_missing_filename_falls_back_to_id(self):
        result = octopus._build_artifact_result({"Id": "Artifacts-7"}, b"data")

        assert result["filename"] == "Artifacts-7"


class TestListTaskArtifacts:
    """Unit tests for list_task_artifacts."""

    def test_queries_artifacts_regarding_the_task(self):
        client = _content_client(b"", items=[_artifact()])

        artifacts = asyncio.run(octopus.list_task_artifacts(client, "ServerTasks-1"))

        assert artifacts == [_artifact()]
        url, kwargs = client.get.call_args[0][0], client.get.call_args[1]
        assert url == f"/api/{octopus.OCTOPUS_SPACE_ID}/artifacts"
        assert kwargs["params"]["regarding"] == "ServerTasks-1"


class TestDownloadArtifact:
    """Unit tests for download_artifact."""

    def test_uses_the_content_link_from_the_artifact(self):
        client = _content_client(b"contents")

        result = asyncio.run(
            octopus.download_artifact(client, _artifact(content_link="/api/Spaces-1/artifacts/Artifacts-1/content"))
        )

        assert client.get.call_args[0][0] == "/api/Spaces-1/artifacts/Artifacts-1/content"
        assert result["content"] == "contents"

    def test_falls_back_to_the_conventional_content_url(self):
        client = _content_client(b"contents")

        asyncio.run(octopus.download_artifact(client, _artifact()))

        assert client.get.call_args[0][0] == f"/api/{octopus.OCTOPUS_SPACE_ID}/artifacts/Artifacts-1/content"


class TestGetTaskArtifacts:
    """Unit tests for get_task_artifacts."""

    def test_downloads_every_artifact_for_the_task(self):
        items = [_artifact("Artifacts-1", "one.txt"), _artifact("Artifacts-2", "two.txt")]
        client = _content_client(b"body", items=items)

        with patch.object(octopus, "DOWNLOAD_ARTIFACTS", True):
            results = asyncio.run(octopus.get_task_artifacts(client, "ServerTasks-1"))

        assert [r["filename"] for r in results] == ["one.txt", "two.txt"]
        assert all(r["content"] == "body" for r in results)

    def test_returns_empty_list_when_downloads_are_disabled(self):
        client = _content_client(b"body", items=[_artifact()])

        with patch.object(octopus, "DOWNLOAD_ARTIFACTS", False):
            results = asyncio.run(octopus.get_task_artifacts(client, "ServerTasks-1"))

        assert results == []
        client.get.assert_not_awaited()

    def test_listing_failure_does_not_raise(self):
        client = MagicMock()
        client.get = AsyncMock(side_effect=httpx.HTTPError("boom"))

        with patch.object(octopus, "DOWNLOAD_ARTIFACTS", True):
            results = asyncio.run(octopus.get_task_artifacts(client, "ServerTasks-1"))

        assert results == []

    def test_a_failed_download_skips_only_that_artifact(self):
        items = [_artifact("Artifacts-1", "one.txt"), _artifact("Artifacts-2", "two.txt")]
        list_response = MagicMock()
        list_response.json.return_value = {"Items": items}
        list_response.raise_for_status.return_value = None
        content_response = MagicMock()
        content_response.content = b"body"
        content_response.raise_for_status.return_value = None

        client = MagicMock()

        async def get(url, **kwargs):
            if not url.endswith("/content"):
                return list_response
            if "Artifacts-1" in url:
                raise httpx.HTTPError("boom")
            return content_response

        client.get = AsyncMock(side_effect=get)

        with patch.object(octopus, "DOWNLOAD_ARTIFACTS", True):
            results = asyncio.run(octopus.get_task_artifacts(client, "ServerTasks-1"))

        assert [r["filename"] for r in results] == ["two.txt"]


class TestBuildTaskResultArtifacts:
    """Unit tests for the artifacts field of build_task_result."""

    def test_artifacts_are_included(self):
        artifacts = [{"id": "Artifacts-1", "filename": "one.txt", "content": "body"}]

        result = octopus.build_task_result({"State": "Success"}, "ServerTasks-1", "logs", artifacts)

        assert result["artifacts"] == artifacts

    def test_artifacts_default_to_an_empty_list(self):
        result = octopus.build_task_result({"State": "Success"}, "ServerTasks-1", "logs")

        assert result["artifacts"] == []


class TestPollTaskToCompletion:
    """Unit tests for artifact collection when a task completes."""

    def test_completed_task_result_includes_artifacts(self):
        client = MagicMock()

        with patch.object(octopus, "get_task_status", new_callable=AsyncMock) as mock_status, \
             patch.object(octopus, "get_task_details_log", new_callable=AsyncMock) as mock_log, \
             patch.object(octopus, "get_task_artifacts", new_callable=AsyncMock) as mock_artifacts:
            mock_status.return_value = {"State": "Success", "Description": "Run runbook"}
            mock_log.return_value = "log output"
            mock_artifacts.return_value = [{"id": "Artifacts-1", "filename": "one.txt", "content": "body"}]

            result = asyncio.run(octopus._poll_task_to_completion(client, "ServerTasks-1"))

        mock_artifacts.assert_awaited_once_with(client, "ServerTasks-1")
        assert result["status"] == "Success"
        assert result["logs"] == "log output"
        assert result["artifacts"][0]["filename"] == "one.txt"
