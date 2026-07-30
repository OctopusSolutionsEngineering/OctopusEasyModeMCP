"""Unit tests for functions in main.py that do not depend on fastmcp."""

import asyncio
import importlib
import sys
from enum import Enum
from types import SimpleNamespace
from unittest.mock import patch, AsyncMock, MagicMock


def _import_main():
    """Import main.py while mocking out module-level side effects."""
    # Prevent register_all_runbook_tools and FastMCP from making network calls
    with patch.dict("sys.modules", {}):
        pass

    if "main" in sys.modules:
        return sys.modules["main"]

    with patch("asyncio.run", return_value=None), \
         patch("main.register_all_runbook_tools", new=AsyncMock()):
        import main
        return main


main = _import_main()

_parse_csv_env = main._parse_csv_env
_sanitize_tool_name = main._sanitize_tool_name
_sanitize_param_name = main._sanitize_param_name
_build_tool_docstring = main._build_tool_docstring
_split_session_id_variable = main._split_session_id_variable
_build_param_to_var = main._build_param_to_var
_build_branch_enum = main._build_branch_enum
_build_environment_enum = main._build_environment_enum
_inject_session_id = main._inject_session_id
_filter_prompted_variables = main._filter_prompted_variables
_resolve_runbook_environments = main._resolve_runbook_environments
_resolve_environment = main._resolve_environment


class TestParseCsvEnv:
    """Tests for _parse_csv_env."""

    def test_parses_comma_separated_values(self):
        assert _parse_csv_env("a, b, c") == ["a", "b", "c"]

    def test_strips_whitespace(self):
        assert _parse_csv_env("  foo ,  bar  ") == ["foo", "bar"]

    def test_filters_empty_entries(self):
        assert _parse_csv_env("a,,b, ,c") == ["a", "b", "c"]

    def test_empty_string_returns_empty_list(self):
        assert _parse_csv_env("") == []

    def test_single_value(self):
        assert _parse_csv_env("only") == ["only"]


class TestSanitizeToolName:
    """Tests for _sanitize_tool_name."""

    def test_replaces_special_characters(self):
        assert _sanitize_tool_name("Hello World!") == "Hello_World"

    def test_preserves_hyphens(self):
        assert _sanitize_tool_name("my-runbook") == "my-runbook"

    def test_preserves_underscores(self):
        assert _sanitize_tool_name("my_runbook") == "my_runbook"

    def test_strips_leading_trailing_underscores(self):
        assert _sanitize_tool_name("__test__") == "test"

    def test_truncates_to_64_characters(self):
        long_name = "a" * 100
        result = _sanitize_tool_name(long_name)
        assert len(result) <= 64

    def test_handles_empty_string(self):
        assert _sanitize_tool_name("") == ""


class TestSanitizeParamName:
    """Tests for _sanitize_param_name."""

    def test_replaces_dots_with_underscores(self):
        assert _sanitize_param_name("Project.Variable") == "project_variable"

    def test_lowercases(self):
        assert _sanitize_param_name("MyVar") == "myvar"

    def test_replaces_leading_digit(self):
        assert _sanitize_param_name("1variable") == "variable"

    def test_strips_underscores(self):
        assert _sanitize_param_name("__var__") == "var"

    def test_replaces_spaces(self):
        assert _sanitize_param_name("my variable") == "my_variable"

    def test_preserves_alphanumeric(self):
        assert _sanitize_param_name("abc123") == "abc123"


class TestBuildToolDocstring:
    """Tests for _build_tool_docstring."""

    def test_basic_docstring(self):
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev, Prod",
            single_env=False,
            is_tenanted=False,
            multi_tenancy_mode="Untenanted",
            param_to_var={},
        )
        assert "Run backup" in result
        assert "Projects-1" in result
        assert "Dev, Prod" in result
        assert "environment_name" in result

    def test_single_env_no_env_param(self):
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev",
            single_env=True,
            is_tenanted=False,
            multi_tenancy_mode="Untenanted",
            param_to_var={},
        )
        assert "environment_name" not in result

    def test_includes_tenant_param_when_tenanted(self):
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev",
            single_env=True,
            is_tenanted=True,
            multi_tenancy_mode="Tenanted",
            param_to_var={},
        )
        assert "tenant_name" in result
        assert "(required)" in result

    def test_includes_optional_tenant(self):
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev",
            single_env=True,
            is_tenanted=True,
            multi_tenancy_mode="TenantedOrUntenanted",
            param_to_var={},
        )
        assert "tenant_name" in result
        assert "(optional)" in result

    def test_includes_variable_params(self):
        param_to_var = {
            "database_name": {
                "name": "DatabaseName",
                "label": "Database Name",
                "description": "The DB name",
                "required": True,
                "default": "",
            }
        }
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev",
            single_env=True,
            is_tenanted=False,
            multi_tenancy_mode="Untenanted",
            param_to_var=param_to_var,
        )
        assert "database_name" in result
        assert "The DB name" in result
        assert "(required)" in result

    def test_includes_git_ref_for_cac(self):
        result = _build_tool_docstring(
            description="Run backup",
            project_id="Projects-1",
            env_help="Dev",
            single_env=True,
            is_tenanted=False,
            multi_tenancy_mode="Untenanted",
            param_to_var={},
            is_cac=True,
            default_git_ref="main",
        )
        assert "git_ref" in result
        assert "main" in result


class TestSplitSessionIdVariable:
    """Tests for _split_session_id_variable."""

    def test_separates_session_id_variable(self):
        from config import SESSION_ID_VAR

        variables = [
            {"name": "DatabaseName", "id": "1"},
            {"name": SESSION_ID_VAR, "id": "2"},
            {"name": "OtherVar", "id": "3"},
        ]
        session_var, visible = _split_session_id_variable(variables)

        assert session_var is not None
        assert session_var["name"] == SESSION_ID_VAR
        assert len(visible) == 2
        assert all(v["name"] != SESSION_ID_VAR for v in visible)

    def test_no_session_id_variable(self):
        variables = [
            {"name": "DatabaseName", "id": "1"},
            {"name": "OtherVar", "id": "2"},
        ]
        session_var, visible = _split_session_id_variable(variables)

        assert session_var is None
        assert len(visible) == 2

    def test_empty_list(self):
        session_var, visible = _split_session_id_variable([])
        assert session_var is None
        assert visible == []


class TestBuildParamToVar:
    """Tests for _build_param_to_var."""

    def test_builds_mapping(self):
        variables = [
            {"name": "DatabaseName", "id": "1"},
            {"name": "Project.Variable", "id": "2"},
        ]
        result = _build_param_to_var(variables)

        assert "databasename" in result
        assert result["databasename"]["id"] == "1"
        assert "project_variable" in result
        assert result["project_variable"]["id"] == "2"

    def test_empty_list(self):
        assert _build_param_to_var([]) == {}


class TestBuildBranchEnum:
    """Tests for _build_branch_enum."""

    def test_creates_enum_for_cac_with_branches(self):
        result = _build_branch_enum("My Runbook", True, ["main", "develop"])

        assert result is not None
        assert issubclass(result, Enum)
        assert "main" in result.__members__
        assert "develop" in result.__members__

    def test_returns_none_for_non_cac(self):
        assert _build_branch_enum("My Runbook", False, ["main"]) is None

    def test_returns_none_for_empty_branches(self):
        assert _build_branch_enum("My Runbook", True, []) is None

    def test_returns_none_for_none_branches(self):
        assert _build_branch_enum("My Runbook", True, None) is None


class TestBuildEnvironmentEnum:
    """Tests for _build_environment_enum."""

    def test_creates_enum_for_multiple_envs(self):
        result = _build_environment_enum("My Runbook", False, ["Dev", "Prod"])

        assert result is not None
        assert issubclass(result, Enum)
        assert "Dev" in result.__members__
        assert "Prod" in result.__members__

    def test_returns_none_for_single_env(self):
        assert _build_environment_enum("My Runbook", True, ["Dev"]) is None

    def test_returns_none_for_empty_env_names(self):
        assert _build_environment_enum("My Runbook", False, []) is None


class TestInjectSessionId:
    """Tests for _inject_session_id."""

    def test_injects_session_id_by_name(self):
        variable_values = {}
        session_id_var = {"id": "var-1", "name": "Project.SessionId"}
        ctx = SimpleNamespace(session_id="sess-123")

        _inject_session_id(variable_values, session_id_var, ctx, is_cac=False)

        assert variable_values["Project.SessionId"] == "sess-123"

    def test_injects_session_id_by_id_for_cac(self):
        variable_values = {}
        session_id_var = {"id": "var-1", "name": "Project.SessionId"}
        ctx = SimpleNamespace(session_id="sess-456")

        _inject_session_id(variable_values, session_id_var, ctx, is_cac=True)

        assert variable_values["var-1"] == "sess-456"

    def test_does_nothing_without_session_var(self):
        variable_values = {}
        ctx = SimpleNamespace(session_id="sess-123")

        _inject_session_id(variable_values, None, ctx, is_cac=False)

        assert variable_values == {}

    def test_does_nothing_without_ctx(self):
        variable_values = {}
        session_id_var = {"id": "var-1", "name": "Project.SessionId"}

        _inject_session_id(variable_values, session_id_var, None, is_cac=False)

        assert variable_values == {}

    def test_handles_none_session_id(self):
        variable_values = {}
        session_id_var = {"id": "var-1", "name": "Project.SessionId"}
        ctx = SimpleNamespace(session_id=None)

        _inject_session_id(variable_values, session_id_var, ctx, is_cac=False)

        assert variable_values["Project.SessionId"] == ""


class TestFilterPromptedVariables:
    """Tests for _filter_prompted_variables."""

    def test_includes_vars_with_no_process_owners(self):
        variables = [
            {"name": "Var1", "process_owners": []},
            {"name": "Var2", "process_owners": []},
        ]
        runbook = {"Id": "Runbooks-1", "Slug": "my-runbook"}

        result = _filter_prompted_variables(variables, runbook)

        assert len(result) == 2

    def test_includes_vars_scoped_to_runbook_id(self):
        variables = [
            {"name": "Var1", "process_owners": ["Runbooks-1"]},
            {"name": "Var2", "process_owners": ["Runbooks-2"]},
        ]
        runbook = {"Id": "Runbooks-1", "Slug": "my-runbook"}

        result = _filter_prompted_variables(variables, runbook)

        assert len(result) == 1
        assert result[0]["name"] == "Var1"

    def test_includes_vars_scoped_to_runbook_slug(self):
        variables = [
            {"name": "Var1", "process_owners": ["my-runbook"]},
            {"name": "Var2", "process_owners": ["other-runbook"]},
        ]
        runbook = {"Id": "Runbooks-1", "Slug": "my-runbook"}

        result = _filter_prompted_variables(variables, runbook)

        assert len(result) == 1
        assert result[0]["name"] == "Var1"

    def test_excludes_vars_scoped_to_other_runbooks(self):
        variables = [
            {"name": "Var1", "process_owners": ["Runbooks-99"]},
        ]
        runbook = {"Id": "Runbooks-1", "Slug": "my-runbook"}

        result = _filter_prompted_variables(variables, runbook)

        assert len(result) == 0

    def test_empty_variables(self):
        runbook = {"Id": "Runbooks-1", "Slug": "my-runbook"}
        assert _filter_prompted_variables([], runbook) == []


class TestResolveRunbookEnvironments:
    """Tests for _resolve_runbook_environments."""

    def test_specified_scope_filters_environments(self):
        runbook = {
            "Id": "Runbooks-1",
            "EnvironmentScope": "Specified",
            "Environments": ["Env-1", "Env-3"],
        }
        environments = [
            {"Id": "Env-1", "Name": "Dev"},
            {"Id": "Env-2", "Name": "Test"},
            {"Id": "Env-3", "Name": "Prod"},
        ]

        result = _resolve_runbook_environments(runbook, environments, {})

        assert len(result) == 2
        assert {e["Name"] for e in result} == {"Dev", "Prod"}

    def test_from_project_lifecycles_uses_map(self):
        runbook = {
            "Id": "Runbooks-1",
            "EnvironmentScope": "FromProjectLifecycles",
        }
        environments = [
            {"Id": "Env-1", "Name": "Dev"},
            {"Id": "Env-2", "Name": "Test"},
        ]
        lifecycle_env_map = {
            "Runbooks-1": [{"Id": "Env-1", "Name": "Dev"}],
        }

        result = _resolve_runbook_environments(runbook, environments, lifecycle_env_map)

        assert len(result) == 1
        assert result[0]["Name"] == "Dev"

    def test_from_project_lifecycles_falls_back_to_all(self):
        runbook = {
            "Id": "Runbooks-1",
            "EnvironmentScope": "FromProjectLifecycles",
        }
        environments = [
            {"Id": "Env-1", "Name": "Dev"},
            {"Id": "Env-2", "Name": "Test"},
        ]

        result = _resolve_runbook_environments(runbook, environments, {})

        assert len(result) == 2

    def test_unscoped_returns_all_environments(self):
        runbook = {
            "Id": "Runbooks-1",
            "EnvironmentScope": "All",
        }
        environments = [
            {"Id": "Env-1", "Name": "Dev"},
            {"Id": "Env-2", "Name": "Prod"},
        ]

        result = _resolve_runbook_environments(runbook, environments, {})

        assert len(result) == 2

    def test_no_scope_returns_all_environments(self):
        runbook = {"Id": "Runbooks-1"}
        environments = [
            {"Id": "Env-1", "Name": "Dev"},
        ]

        result = _resolve_runbook_environments(runbook, environments, {})

        assert len(result) == 1


class TestResolveEnvironment:
    """Tests for _resolve_environment."""

    def test_resolves_existing_environment(self):
        environments = [
            {"Id": "Env-1", "Name": "Development"},
            {"Id": "Env-2", "Name": "Production"},
        ]

        env_id, error = asyncio.run(_resolve_environment("Development", environments))

        assert env_id == "Env-1"
        assert error is None

    def test_case_insensitive_match(self):
        environments = [
            {"Id": "Env-1", "Name": "Development"},
        ]

        env_id, error = asyncio.run(_resolve_environment("development", environments))

        assert env_id == "Env-1"
        assert error is None

    def test_returns_error_for_unknown_environment(self):
        environments = [
            {"Id": "Env-1", "Name": "Development"},
            {"Id": "Env-2", "Name": "Production"},
        ]

        env_id, error = asyncio.run(_resolve_environment("Staging", environments))

        assert env_id is None
        assert error is not None
        assert "Staging" in error
        assert "Development" in error
        assert "Production" in error


class TestRegisterAllRunbookTools:
    """Tests for the space refresh performed by register_all_runbook_tools."""

    def test_space_id_is_refreshed_before_tools_are_removed(self):
        calls = []

        async def fake_refresh():
            calls.append("refresh")
            return "Spaces-1"

        async def fake_remove():
            calls.append("remove")

        with patch.object(main, "refresh_space_id", fake_refresh), \
             patch.object(main, "_remove_all_tools", fake_remove), \
             patch.object(main, "get_all_runbooks", AsyncMock(return_value=[])), \
             patch.object(main, "get_environments", AsyncMock(return_value=[])):
            asyncio.run(main.register_all_runbook_tools())

        assert calls == ["refresh", "remove"]

    def test_failed_space_refresh_leaves_existing_tools_registered(self):
        async def fake_refresh():
            raise RuntimeError("space not found")

        remove = AsyncMock()

        with patch.object(main, "refresh_space_id", fake_refresh), \
             patch.object(main, "_remove_all_tools", remove), \
             patch.object(main, "get_all_runbooks", AsyncMock(return_value=[])), \
             patch.object(main, "get_environments", AsyncMock(return_value=[])):
            try:
                asyncio.run(main.register_all_runbook_tools())
                raised = None
            except RuntimeError as e:
                raised = e

        assert raised is not None
        remove.assert_not_awaited()


class TestUpdateTools:
    """Tests for the update_tools tool."""

    @staticmethod
    def _ctx(calls=None):
        async def send_tool_list_changed():
            if calls is not None:
                calls.append("notify")

        return SimpleNamespace(session=SimpleNamespace(
            send_tool_list_changed=AsyncMock(side_effect=send_tool_list_changed)
        ))

    def test_reloads_runbooks_then_notifies(self):
        calls = []

        async def fake_register():
            calls.append("reload")

        before_tools = [SimpleNamespace(name="update_tools"), SimpleNamespace(name="OldTool")]
        after_tools = [SimpleNamespace(name="update_tools"), SimpleNamespace(name="NewTool")]

        ctx = self._ctx(calls)
        with patch.object(main, "register_all_runbook_tools", fake_register), \
             patch.object(main.mcp, "list_tools", AsyncMock(side_effect=[before_tools, after_tools])):
            result = asyncio.run(main.update_tools(ctx))

        assert calls == ["reload", "notify"]
        ctx.session.send_tool_list_changed.assert_awaited_once_with()
        assert result == {"status": "Notified", "added": ["NewTool"], "removed": ["OldTool"]}

    def test_failed_reload_still_notifies_and_reports_the_error(self):
        async def fake_register():
            raise RuntimeError("space not found")

        before_tools = [SimpleNamespace(name="update_tools"), SimpleNamespace(name="SomeTool")]
        after_tools = [SimpleNamespace(name="update_tools")]

        ctx = self._ctx()
        with patch.object(main, "register_all_runbook_tools", fake_register), \
             patch.object(main.mcp, "list_tools", AsyncMock(side_effect=[before_tools, after_tools])):
            result = asyncio.run(main.update_tools(ctx))

        ctx.session.send_tool_list_changed.assert_awaited_once_with()
        assert result["status"] == "Failed"
        assert "space not found" in result["error"]
        assert result["added"] == []
        assert result["removed"] == ["SomeTool"]

    def test_is_registered_as_a_tool(self):
        tools = asyncio.run(main.mcp.list_tools())
        assert "update_tools" in [tool.name for tool in tools]


class TestRemoveAllTools:
    """Tests for _remove_all_tools."""

    def test_removes_runbook_tools_but_keeps_static_tools(self):
        tools = [
            SimpleNamespace(name="Backup_Database"),
            SimpleNamespace(name="update_tools"),
            SimpleNamespace(name="Deploy_Service"),
        ]
        remove_tool = MagicMock()

        with patch.object(main.mcp, "list_tools", AsyncMock(return_value=tools)), \
             patch.object(main.mcp.local_provider, "remove_tool", remove_tool):
            asyncio.run(main._remove_all_tools())

        removed = [call.args[0] for call in remove_tool.call_args_list]
        assert removed == ["Backup_Database", "Deploy_Service"]

    def test_removal_failures_are_logged_and_do_not_stop_the_loop(self):
        tools = [SimpleNamespace(name="First"), SimpleNamespace(name="Second")]
        remove_tool = MagicMock(side_effect=[Exception("nope"), None])

        with patch.object(main.mcp, "list_tools", AsyncMock(return_value=tools)), \
             patch.object(main.mcp.local_provider, "remove_tool", remove_tool):
            asyncio.run(main._remove_all_tools())

        assert remove_tool.call_count == 2
