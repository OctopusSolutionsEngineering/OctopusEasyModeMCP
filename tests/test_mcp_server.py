"""Integration tests for the FastMCP server with mock runbook calls.

These tests verify that:
- Runbook tools are correctly registered on the MCP server
- Tool schemas have the expected parameters
- Tool invocations route through the correct pipeline
- Environment and variable validation works at the MCP layer
"""

import asyncio
import sys
from unittest.mock import patch, AsyncMock

import pytest
from fastmcp.client import Client

from tests.test_get_all_runbooks import octopus_environment  # noqa: F401 - reuse fixture


@pytest.fixture(scope="module")
def mcp_server(octopus_environment):
    """Import and return the MCP server after the Octopus environment is ready."""
    # Remove cached modules so they re-read env vars set by octopus_environment
    for mod in ["config", "octopus", "main"]:
        if mod in sys.modules:
            del sys.modules[mod]

    from main import mcp
    return mcp


def _run_with_client(mcp_server, coro_fn):
    """Helper to run an async function with a Client context."""
    async def _run():
        async with Client(mcp_server) as client:
            return await coro_fn(client)
    return asyncio.run(_run())


@pytest.mark.integration
class TestMcpToolDiscovery:
    """Tests that runbook tools are correctly discovered and registered."""

    def test_tools_are_registered(self, mcp_server):
        """Test that tools from both database and CaC runbooks are registered."""
        async def _check(client):
            tools = await client.list_tools()
            return [t.name for t in tools]

        tool_names = _run_with_client(mcp_server, _check)

        assert len(tool_names) >= 3
        assert any("Backup" in n for n in tool_names)

    def test_tool_has_description(self, mcp_server):
        """Test that registered tools have descriptions."""
        async def _check(client):
            return await client.list_tools()

        tools = _run_with_client(mcp_server, _check)

        for tool in tools:
            assert tool.description is not None
            assert len(tool.description) > 0

    def test_tool_has_input_schema(self, mcp_server):
        """Test that registered tools have input schemas."""
        async def _check(client):
            return await client.list_tools()

        tools = _run_with_client(mcp_server, _check)

        for tool in tools:
            assert tool.inputSchema is not None
            assert "properties" in tool.inputSchema


@pytest.mark.integration
class TestMcpToolSchemas:
    """Tests that tool schemas have the correct parameters."""

    def test_tool_has_environment_param(self, mcp_server):
        """Test that multi-environment tools have an environment_name parameter."""
        async def _check(client):
            tools = await client.list_tools()
            return next(
                (t for t in tools if "Deploy" in t.name and "Service" in t.name),
                None,
            )

        deploy_tool = _run_with_client(mcp_server, _check)
        if deploy_tool:
            props = deploy_tool.inputSchema.get("properties", {})
            assert "environment_name" in props

    def test_tool_has_prompted_variable_params(self, mcp_server):
        """Test that tools include prompted variable parameters."""
        async def _check(client):
            tools = await client.list_tools()
            return [t for t in tools if "Backup" in t.name and "Database" in t.name]

        backup_tools = _run_with_client(mcp_server, _check)
        assert len(backup_tools) > 0

        backup_tool = backup_tools[0]
        props = backup_tool.inputSchema.get("properties", {})
        param_names = list(props.keys())
        assert any("database" in p.lower() for p in param_names), \
            f"Expected a 'database' param, got: {param_names}"

    def test_single_env_tool_has_no_environment_param(self, mcp_server):
        """Test that single-environment tools don't expose environment_name."""
        async def _check(client):
            tools = await client.list_tools()
            return [t for t in tools if "Backup" in t.name and "Database" in t.name]

        backup_tools = _run_with_client(mcp_server, _check)
        if backup_tools:
            has_single_env = any(
                "environment_name" not in t.inputSchema.get("properties", {})
                for t in backup_tools
            )
            assert has_single_env, "Expected at least one single-env Backup Database tool"


@pytest.mark.integration
class TestMcpToolInvocation:
    """Tests that tool invocations work correctly with mocked runbook execution."""

    def test_call_tool_with_valid_args(self, mcp_server):
        """Test calling a tool with valid arguments succeeds."""
        mock_result = {
            "status": "Success",
            "taskId": "ServerTasks-999",
            "description": "Run runbook",
            "errorMessage": "",
            "duration": "00:00:05",
            "logs": "Backing up database\n",
        }

        async def _check(client):
            tools = await client.list_tools()
            backup_tools = [t for t in tools if "Backup" in t.name and "Database" in t.name]
            assert len(backup_tools) > 0
            tool = backup_tools[0]

            with patch("main.run_runbook", new=AsyncMock(return_value=mock_result)):
                return await client.call_tool(tool.name, {
                    "databasename": "testdb",
                })

        result = _run_with_client(mcp_server, _check)
        assert not result.is_error
        assert result.data is not None

    def test_call_tool_with_invalid_environment(self, mcp_server):
        """Test calling a tool with an invalid environment raises a validation error."""
        from fastmcp.exceptions import ToolError

        async def _check(client):
            tools = await client.list_tools()
            deploy_tool = next(
                (t for t in tools if "Deploy" in t.name and "Service" in t.name),
                None,
            )
            if not deploy_tool:
                pytest.skip("No Deploy Service tool found")

            with pytest.raises(ToolError, match="environment_name"):
                await client.call_tool(deploy_tool.name, {
                    "environment_name": "NonexistentEnvironment",
                    "databasename": "testdb",
                })

        _run_with_client(mcp_server, _check)


@pytest.mark.integration
class TestMcpToolCount:
    """Tests that the correct number of tools are registered."""

    def test_database_and_cac_tools_registered(self, mcp_server):
        """Test that both database-backed and CaC runbook tools are registered."""
        async def _check(client):
            tools = await client.list_tools()
            return [t.name for t in tools]

        tool_names = _run_with_client(mcp_server, _check)
        assert len(tool_names) >= 3, f"Expected at least 3 tools, got {len(tool_names)}: {tool_names}"

    def test_no_duplicate_tool_names(self, mcp_server):
        """Test that tool names are unique (no duplicates registered)."""
        async def _check(client):
            tools = await client.list_tools()
            return [t.name for t in tools]

        tool_names = _run_with_client(mcp_server, _check)
        assert len(tool_names) == len(set(tool_names)), \
            f"Duplicate tool names found: {[n for n in tool_names if tool_names.count(n) > 1]}"
