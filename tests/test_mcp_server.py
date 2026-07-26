"""Integration tests for the FastMCP server with mock runbook calls.

These tests verify that:
- Runbook tools are correctly registered on the MCP server
- Tool schemas have the expected parameters
- Tool invocations route through the correct pipeline
- Environment and variable validation works at the MCP layer
"""

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


@pytest.fixture
async def client(mcp_server):
    """Create a FastMCP test client."""
    async with Client(mcp_server) as c:
        yield c


@pytest.mark.integration
class TestMcpToolDiscovery:
    """Tests that runbook tools are correctly discovered and registered."""

    async def test_tools_are_registered(self, client):
        """Test that tools from both database and CaC runbooks are registered."""
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]

        assert len(tool_names) >= 3
        assert "Backup_Database" in tool_names or "Backup-Database" in tool_names or any("Backup" in n for n in tool_names)

    async def test_tool_has_description(self, client):
        """Test that registered tools have descriptions."""
        tools = await client.list_tools()

        for tool in tools:
            assert tool.description is not None
            assert len(tool.description) > 0

    async def test_tool_has_input_schema(self, client):
        """Test that registered tools have input schemas."""
        tools = await client.list_tools()

        for tool in tools:
            assert tool.inputSchema is not None
            assert "properties" in tool.inputSchema


@pytest.mark.integration
class TestMcpToolSchemas:
    """Tests that tool schemas have the correct parameters."""

    async def test_tool_has_environment_param(self, client):
        """Test that multi-environment tools have an environment_name parameter."""
        tools = await client.list_tools()

        # Find a tool that should have multiple environments (Deploy Service)
        deploy_tool = next(
            (t for t in tools if "Deploy" in t.name and "Service" in t.name),
            None,
        )
        if deploy_tool:
            props = deploy_tool.inputSchema.get("properties", {})
            assert "environment_name" in props

    async def test_tool_has_prompted_variable_params(self, client):
        """Test that tools include prompted variable parameters."""
        tools = await client.list_tools()

        # Find a tool that should have the DatabaseName prompted variable
        backup_tools = [t for t in tools if "Backup" in t.name and "Database" in t.name]
        assert len(backup_tools) > 0

        backup_tool = backup_tools[0]
        props = backup_tool.inputSchema.get("properties", {})
        # The prompted variable "DatabaseName" should appear as a parameter
        param_names = list(props.keys())
        assert any("database" in p.lower() for p in param_names), \
            f"Expected a 'database' param, got: {param_names}"

    async def test_single_env_tool_has_no_environment_param(self, client):
        """Test that single-environment tools don't expose environment_name."""
        tools = await client.list_tools()

        # Backup Database is scoped to only Development
        backup_tools = [t for t in tools if "Backup" in t.name and "Database" in t.name]
        if backup_tools:
            # At least one of the backup tools (db-backed, single env) should not have environment_name
            has_single_env = any(
                "environment_name" not in t.inputSchema.get("properties", {})
                for t in backup_tools
            )
            assert has_single_env, "Expected at least one single-env Backup Database tool"


@pytest.mark.integration
class TestMcpToolInvocation:
    """Tests that tool invocations work correctly with mocked runbook execution."""

    async def test_call_tool_with_valid_args(self, client):
        """Test calling a tool with valid arguments succeeds."""
        tools = await client.list_tools()

        # Find a Backup Database tool
        backup_tools = [t for t in tools if "Backup" in t.name and "Database" in t.name]
        assert len(backup_tools) > 0
        tool = backup_tools[0]

        mock_result = {
            "status": "Success",
            "taskId": "ServerTasks-999",
            "description": "Run runbook",
            "errorMessage": "",
            "duration": "00:00:05",
            "logs": "Backing up database\n",
        }

        with patch("main.run_runbook", new=AsyncMock(return_value=mock_result)):
            result = await client.call_tool(tool.name, {
                "databasename": "testdb",
            })

        assert not result.is_error
        assert result.data is not None

    async def test_call_tool_with_invalid_environment(self, client):
        """Test calling a tool with an invalid environment raises a validation error."""
        from fastmcp.exceptions import ToolError

        tools = await client.list_tools()

        # Find a multi-environment tool (Deploy Service)
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


@pytest.mark.integration
class TestMcpToolCount:
    """Tests that the correct number of tools are registered."""

    async def test_database_and_cac_tools_registered(self, client):
        """Test that both database-backed and CaC runbook tools are registered."""
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]

        # We should have tools from both the database-backed project and the CaC project
        # At minimum: Backup Database, Deploy Service, Manual Intervention Runbook
        # Some may appear twice (db + CaC) but names are sanitized
        assert len(tool_names) >= 3, f"Expected at least 3 tools, got {len(tool_names)}: {tool_names}"

    async def test_no_duplicate_tool_names(self, client):
        """Test that tool names are unique (no duplicates registered)."""
        tools = await client.list_tools()
        tool_names = [t.name for t in tools]

        # Tool names should be unique
        assert len(tool_names) == len(set(tool_names)), \
            f"Duplicate tool names found: {[n for n in tool_names if tool_names.count(n) > 1]}"
