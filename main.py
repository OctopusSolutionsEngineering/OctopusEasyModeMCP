import asyncio
import inspect
import logging
import os
import re
from contextlib import asynccontextmanager
from enum import Enum

import httpx
from pydantic import BaseModel, Field

from fastmcp.server.auth.oauth_proxy import OAuthProxy
from fastmcp.server.auth.providers.azure import AzureProvider
from fastmcp.server.auth.providers.github import GitHubProvider
from fastmcp.server.context import AcceptedElicitation
from fastmcp.server.tasks import TaskConfig

from key_value.aio.stores.azure_tables import AzureTablesStore
from key_value.aio.stores.azure_tables.store import AzureTablesSanitizationStrategy

from auto_register_provider import AutoRegisterGoogleProvider
from config import AUTH_TYPE, TASK_TAG_GROUP, TASK_TAG_ASYNC, TASK_TAG_SYNC, SESSION_ID_VAR, BASE_URL, \
    OCTOPUS_PROJECTS_CSV, HOST, PORT, ALLOWED_HOSTS, ALLOWED_ORIGINS, AUTO_PROCEED_INTERVENTIONS, \
    AUTO_ASSIGN_INTERVENTIONS, AUTO_POPULATE_INTERVENTION_NOTES
from fastmcp import FastMCP, Context
from octopus import (
    get_all_runbooks,
    get_project_prompted_variables,
    get_project_branches,
    get_environments,
    get_runbook_environments,
    get_project_ids_by_names,
    get_pending_interruptions,
    submit_interruption,
    take_interruption_responsibility,
    parse_interruption_form,
    run_runbook,
    resolve_tenant_for_tool,
)


def _parse_csv_env(value: str) -> list[str]:
    """Parse a comma-separated string into a list of trimmed, non-empty strings."""
    return [name.strip() for name in value.split(",") if name.strip()]


def _create_auth():
    """Create the OAuth auth provider based on EASY_MODE_MCP_AUTH_TYPE."""
    if AUTH_TYPE == "none":
        return None

    storage_backend = AzureTablesStore(
        connection_string=os.environ["EASY_MODE_MCP_AZURE_STORAGE_CONNECTION_STRING"],
        table_name="mcpsessions",
        key_sanitization_strategy=AzureTablesSanitizationStrategy(),
        collection_sanitization_strategy=AzureTablesSanitizationStrategy(),
    )

    if AUTH_TYPE == "github":
        return GitHubProvider(
            client_id=os.environ["EASY_MODE_MCP_GITHUB_CLIENT_ID"],
            client_secret=os.environ["EASY_MODE_MCP_GITHUB_CLIENT_SECRET"],
            base_url=BASE_URL,
            required_scopes=["read:user","user:email"],
            client_storage=storage_backend,
            jwt_signing_key=os.environ["EASY_MODE_MCP_JWT_SIGNING_KEY"],
        )

    if AUTH_TYPE == "azure":
        return AzureProvider(
            client_id=os.environ["EASY_MODE_MCP_AZURE_CLIENT_ID"],
            client_secret=os.environ["EASY_MODE_MCP_AZURE_CLIENT_SECRET"],
            tenant_id=os.environ["EASY_MODE_MCP_AZURE_TENANT_ID"],
            base_url=BASE_URL,
            required_scopes=["openid", "email", "profile"],
            client_storage=storage_backend,
            jwt_signing_key=os.environ["EASY_MODE_MCP_JWT_SIGNING_KEY"],
        )

    if AUTH_TYPE == "oauth_proxy":
        from fastmcp.server.auth.providers.jwt import JWTVerifier

        token_verifier = JWTVerifier(
            jwks_uri=os.environ["EASY_MODE_MCP_OAUTH_JWKS_URI"],
            issuer=os.environ.get("EASY_MODE_MCP_OAUTH_ISSUER"),
            audience=os.environ.get("EASY_MODE_MCP_OAUTH_AUDIENCE"),
            required_scopes=os.environ.get("EASY_MODE_MCP_OAUTH_SCOPES", "").split(",") if os.environ.get("EASY_MODE_MCP_OAUTH_SCOPES") else None,
        )

        return OAuthProxy(
            upstream_authorization_endpoint=os.environ["EASY_MODE_MCP_OAUTH_AUTHORIZATION_ENDPOINT"],
            upstream_token_endpoint=os.environ["EASY_MODE_MCP_OAUTH_TOKEN_ENDPOINT"],
            upstream_client_id=os.environ["EASY_MODE_MCP_OAUTH_CLIENT_ID"],
            upstream_client_secret=os.environ.get("EASY_MODE_MCP_OAUTH_CLIENT_SECRET"),
            upstream_revocation_endpoint=os.environ.get("EASY_MODE_MCP_OAUTH_REVOCATION_ENDPOINT"),
            token_verifier=token_verifier,
            base_url=BASE_URL,
            client_storage=storage_backend,
            jwt_signing_key=os.environ["EASY_MODE_MCP_JWT_SIGNING_KEY"],
        )

    # Default: google
    return AutoRegisterGoogleProvider(
        client_id=os.environ["EASY_MODE_MCP_GOOGLE_CLIENT_ID"],
        client_secret=os.environ["EASY_MODE_MCP_GOOGLE_CLIENT_SECRET"],
        base_url=BASE_URL,
        required_scopes=["openid", "email", "profile"],
        client_storage=storage_backend,
        jwt_signing_key=os.environ["EASY_MODE_MCP_JWT_SIGNING_KEY"],
    )


async def _periodic_refresh() -> None:
    """Periodically re-register all runbook tools every 5 minutes."""
    while True:
        await asyncio.sleep(300)  # 5 minutes
        try:
            logger.info("Refreshing runbook tools...")
            await register_all_runbook_tools()
            logger.info("Runbook tools refreshed successfully.")
        except Exception as e:
            logger.error(f"Failed to refresh runbook tools: {e}")


@asynccontextmanager
async def _app_lifespan(app: FastMCP):
    """Start the periodic refresh background task on server startup."""
    task = asyncio.create_task(_periodic_refresh())
    try:
        yield
    finally:
        task.cancel()


def _sanitize_tool_name(name: str) -> str:
    """Convert a runbook name into a valid MCP tool name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
    return sanitized.strip("_")[:64]


def _sanitize_param_name(name: str) -> str:
    """Convert a variable name into a valid Python parameter name."""
    sanitized = re.sub(r"[^a-zA-Z0-9_]", "_", name)
    sanitized = re.sub(r"^[0-9]", "_", sanitized)
    return sanitized.strip("_").lower()


class InterventionResponse(BaseModel):
    """Choose whether to proceed with or abort the deployment, and provide any instructions."""
    action: str = Field(title="Action", description="Choose whether to proceed with or reject the deployment", json_schema_extra={"enum": ["Proceed", "Reject Deployment"]})
    instructions: str = Field(default="", title="Instructions", description="Additional instructions or notes for this intervention")


class InterventionNotesResponse(BaseModel):
    """Provide notes for the manual intervention."""
    notes: str = Field(default="", title="Notes", description="Notes or instructions for this intervention")


async def _handle_intervention(client: httpx.AsyncClient, interruption: dict, ctx: Context, task_id: str, task: dict) -> dict | None:
    """Handle a single manual intervention. Returns a result dict if the task should stop, else None."""
    instructions, notes_element_id, result_element_id = parse_interruption_form(interruption)

    title = interruption.get("Title", "Manual Intervention")
    message = f"**{title}**\n\n{instructions}" if instructions else title

    # Automatically take responsibility if configured
    if AUTO_ASSIGN_INTERVENTIONS:
        logger.info(f"Auto-assigning intervention '{interruption['Id']}' to current user")
        await take_interruption_responsibility(client, interruption['Id'])
    else:
        # Ask the user to take responsibility or cancel
        try:
            responsibility_result = await ctx.elicit(
                message=f"{message}\n\nDo you want to take responsibility for this intervention?",
                response_type=["Assign to me", "Cancel"],
                response_title="Responsibility",
                response_description="Choose whether to assign this intervention to yourself or cancel",
            )
        except Exception:
            # Client doesn't support elicitation
            if AUTO_PROCEED_INTERVENTIONS:
                logger.info(f"Elicitation not supported by client, auto-proceeding with intervention '{interruption['Id']}'")
                await take_interruption_responsibility(client, interruption['Id'])
                submit_payload = {
                    "Instructions": None,
                    "Notes": "Auto-proceeded via MCP (client does not support elicitation)",
                    "Result": "Proceed",
                }
                await submit_interruption(client, interruption['Id'], submit_payload)
                logger.info(f"Manual intervention '{title}' auto-proceeded")
                return None
            else:
                logger.warning(f"Elicitation not supported by client and auto-proceed is disabled for intervention '{interruption['Id']}'")
                return {
                    "status": "Failed",
                    "taskId": task_id,
                    "description": task.get("Description", ""),
                    "errorMessage": f"Manual intervention '{title}' requires elicitation support, which this client does not provide. Set EASY_MODE_MCP_AUTO_PROCEED_INTERVENTIONS=true to auto-proceed.",
                }

        if not isinstance(responsibility_result, AcceptedElicitation) or responsibility_result.data == "Cancel":
            logger.info(f"User cancelled taking responsibility for interruption '{interruption['Id']}'")
            return {
                "status": "Cancelled",
                "taskId": task_id,
                "description": task.get("Description", ""),
                "errorMessage": "User declined to take responsibility for the manual intervention.",
            }

        # Take responsibility
        await take_interruption_responsibility(client, interruption['Id'])

    if AUTO_PROCEED_INTERVENTIONS and AUTO_POPULATE_INTERVENTION_NOTES:
        # Both flags are true: skip all prompting entirely
        logger.info(f"Auto-proceeding with intervention '{interruption['Id']}' (both AUTO_PROCEED and AUTO_POPULATE_NOTES enabled)")
        submit_payload = {
            "Instructions": None,
            "Notes": "Auto-proceeded via MCP (AUTO_PROCEED_INTERVENTIONS=true, AUTO_POPULATE_INTERVENTION_NOTES=true)",
            "Result": "Proceed",
        }
        await submit_interruption(client, interruption['Id'], submit_payload)
        logger.info(f"Manual intervention '{title}' auto-proceeded")
        return None

    if AUTO_PROCEED_INTERVENTIONS:
        # Action is auto-proceeded; only ask for notes
        try:
            result = await ctx.elicit(
                message=f"{message}\n\nThis intervention will proceed automatically. Please provide any notes.",
                response_type=InterventionNotesResponse,
            )
        except Exception:
            logger.info(f"Elicitation not supported by client, auto-proceeding with intervention '{interruption['Id']}'")
            submit_payload = {
                "Instructions": None,
                "Notes": "Auto-proceeded via MCP (client does not support elicitation)",
                "Result": "Proceed",
            }
            await submit_interruption(client, interruption['Id'], submit_payload)
            logger.info(f"Manual intervention '{title}' auto-proceeded")
            return None

        action = "Proceed"
        if isinstance(result, AcceptedElicitation):
            user_instructions = result.data.notes
        else:
            user_instructions = ""
    else:
        # Ask for both action and notes
        try:
            result = await ctx.elicit(
                message=message,
                response_type=InterventionResponse,
            )
        except Exception:
            logger.warning(f"Elicitation not supported by client and auto-proceed is disabled for intervention '{interruption['Id']}'")
            return {
                "status": "Failed",
                "taskId": task_id,
                "description": task.get("Description", ""),
                "errorMessage": f"Manual intervention '{title}' requires elicitation support, which this client does not provide. Set EASY_MODE_MCP_AUTO_PROCEED_INTERVENTIONS=true to auto-proceed.",
            }

        if isinstance(result, AcceptedElicitation):
            action = result.data.action
            user_instructions = "Auto-populated via MCP" if AUTO_POPULATE_INTERVENTION_NOTES else result.data.instructions
        else:
            action = "Reject Deployment"
            user_instructions = ""

    notes_text = f"Responded via MCP: {action}"
    if user_instructions:
        notes_text += f"\nInstructions: {user_instructions}"

    submit_payload = {
        "Instructions": None,
        "Notes": notes_text,
        "Result": action,
    }

    logger.info(f"Submitting intervention with payload: {submit_payload}")
    await submit_interruption(client, interruption['Id'], submit_payload)
    logger.info(f"Manual intervention '{title}' resolved with: {action}")
    return None


async def _handle_pending_interventions(client: httpx.AsyncClient, task_id: str, task: dict, ctx: Context) -> dict | None:
    """Process all pending interventions for a task. Returns a result dict if the task should stop."""
    interruptions = await get_pending_interruptions(client, task_id)
    for interruption in interruptions:
        if not interruption.get("IsPending"):
            continue
        logger.info(f"Interruption details: {interruption}")
        stop_result = await _handle_intervention(client, interruption, ctx, task_id, task)
        if stop_result:
            return stop_result
    return None


def _build_tool_params(single_env: bool, environment_enum, param_to_var: dict, is_tenanted: bool, multi_tenancy_mode: str, is_cac: bool = False, default_git_ref: str = "", branch_enum=None) -> list[inspect.Parameter]:
    """Build the list of inspect.Parameter objects for a runbook tool."""
    if single_env:
        params = [
            inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context),
        ]
    else:
        params = [
            inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context),
            inspect.Parameter("environment_name", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=environment_enum),
        ]

    for param_name, var in param_to_var.items():
        # Every parameter is required (i.e. the type is a string). We pass empty values by default.
        default = var["default"] if var["default"] else inspect.Parameter.empty
        params.append(
            inspect.Parameter(
                param_name,
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default,
                annotation=str,
            )
        )

    if is_tenanted:
        tenant_required = multi_tenancy_mode == "Tenanted"
        params.append(
            inspect.Parameter(
                "tenant_name",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=inspect.Parameter.empty if tenant_required else None,
                annotation=str if tenant_required else str | None,
            )
        )

    if is_cac and branch_enum:
        params.append(
            inspect.Parameter(
                "git_ref",
                inspect.Parameter.POSITIONAL_OR_KEYWORD,
                default=default_git_ref or None,
                annotation=branch_enum | None,
            )
        )

    # Sort: ctx first, then required, then optional
    ctx_params = [p for p in params if p.name == "ctx"]
    required_params = [p for p in params if p.name != "ctx" and p.default is inspect.Parameter.empty]
    optional_params = [p for p in params if p.name != "ctx" and p.default is not inspect.Parameter.empty]
    return ctx_params + required_params + optional_params


def _build_tool_docstring(description: str, project_id: str, env_help: str, single_env: bool, is_tenanted: bool, multi_tenancy_mode: str, param_to_var: dict, is_cac: bool = False, default_git_ref: str = "") -> str:
    """Build the docstring for a runbook tool."""
    if single_env:
        args_doc = ""
    else:
        args_doc = "    environment_name: The name of the environment to run the runbook in\n"
    if is_tenanted:
        tenant_req_str = " (required)" if multi_tenancy_mode == "Tenanted" else " (optional)"
        args_doc += f"    tenant_name: The name of the tenant to run the runbook for{tenant_req_str}\n"
    for param_name, var in param_to_var.items():
        required_str = " (required)" if var["required"] else " (optional)"
        var_desc = var["description"] or var["label"]
        args_doc += f"    {param_name}: {var_desc}{required_str}\n"
    if is_cac:
        default_str = f" (optional, defaults to '{default_git_ref}')" if default_git_ref else " (optional)"
        args_doc += f"    git_ref: The git branch to use for the runbook{default_str}\n"

    return (
        f"{description}\n\n"
        f"Project ID: {project_id}\n"
        f"Available environments: {env_help}\n\n"
        f"Args:\n"
        f"{args_doc}"
    )


def _build_tool_annotations(single_env: bool, EnvironmentEnum, is_tenanted: bool, multi_tenancy_mode: str, param_to_var: dict, is_cac: bool = False, BranchEnum=None) -> dict:
    """Build the __annotations__ dict for a runbook tool."""
    annotations = {"return": dict, "ctx": Context}
    if not single_env:
        annotations["environment_name"] = EnvironmentEnum
    if is_tenanted:
        annotations["tenant_name"] = str if multi_tenancy_mode == "Tenanted" else str | None
    for param_name, var in param_to_var.items():
        annotations[param_name] = str | None if not var["required"] else str
    if is_cac and BranchEnum:
        annotations["git_ref"] = BranchEnum | None
    return annotations


async def _resolve_environment(environment_name: str, environments: list[dict]) -> tuple[str | None, str | None]:
    """Resolve an environment name to its ID."""
    env_map = {e["Name"].lower(): e["Id"] for e in environments}
    env_id = env_map.get(environment_name.lower())
    if not env_id:
        env_help = ", ".join(e["Name"] for e in environments)
        return None, f"Environment '{environment_name}' not found. Available: {env_help}"
    return env_id, None


async def _collect_variable_values(kwargs: dict, param_to_var: dict, ctx: Context | None, use_var_id: bool = False) -> tuple[dict[str, str], dict | None]:
    """Collect variable values from kwargs, eliciting missing required values.

    Args:
        kwargs: The keyword arguments from the tool call.
        param_to_var: Mapping of parameter names to variable info dicts.
        ctx: The MCP context for elicitation.
        use_var_id: If True, use the variable ID as the form value key (for CaC runbooks).
    """
    variable_values = {}
    for param_name, var in param_to_var.items():
        key = var["id"] if use_var_id else var["name"]
        value = kwargs.get(param_name)
        if value is not None:
            variable_values[key] = value
        elif var["default"]:
            # Include default values (especially important for CaC runbooks)
            variable_values[key] = var["default"]
        elif var["required"] and ctx:
            var_desc = var["description"] or var["label"]
            elicit_result = await ctx.elicit(
                message=f"Please provide a value for **{var['label']}**\n\n{var_desc}",
                response_type=str,
            )
            if isinstance(elicit_result, AcceptedElicitation):
                variable_values[key] = elicit_result.data
            else:
                return {}, {
                    "status": "Failed",
                    "error": f"Required variable '{var['label']}' was not provided and user declined to supply a value.",
                }
        elif var["required"] and not ctx:
            return {}, {
                "status": "Failed",
                "error": f"Required variable '{var['label']}' was not provided.",
            }
    return variable_values, None


def _split_session_id_variable(prompted_variables: list[dict]) -> tuple[dict | None, list[dict]]:
    """Separate the session ID variable from visible prompted variables."""
    session_id_var = None
    visible = []
    for var in prompted_variables:
        if var["name"] == SESSION_ID_VAR:
            session_id_var = var
        else:
            visible.append(var)
    return session_id_var, visible


def _build_param_to_var(variables: list[dict]) -> dict[str, dict]:
    """Build a mapping from sanitized parameter names to variable info."""
    return {_sanitize_param_name(var["name"]): var for var in variables}


def _build_branch_enum(runbook_name: str, is_cac: bool, branch_names: list[str] | None):
    """Create a dynamic Enum for branch names if this is a CaC project."""
    if not is_cac or not branch_names:
        return None
    return Enum(
        f"Branch_{_sanitize_tool_name(runbook_name)}",
        {name: name for name in branch_names},
        type=str,
    )


def _build_environment_enum(runbook_name: str, single_env: bool, env_names: list[str]):
    """Create a dynamic Enum for environment names when multiple environments exist."""
    if single_env or not env_names:
        return None
    return Enum(
        f"Environment_{_sanitize_tool_name(runbook_name)}",
        {name: name for name in env_names},
        type=str,
    )


def _inject_session_id(variable_values: dict[str, str], session_id_var: dict | None, ctx, is_cac: bool) -> None:
    """Inject the session ID into variable values if the prompted variable exists."""
    if session_id_var and ctx:
        sid_key = session_id_var["id"] if is_cac else session_id_var["name"]
        variable_values[sid_key] = ctx.session_id or ""


async def _resolve_tool_environment(
    kwargs: dict, environments: list[dict], single_env: bool, env_help: str
) -> tuple[str | None, dict | None]:
    """Resolve the environment for a tool invocation.

    Returns (env_id, error_response). If error_response is not None, the caller
    should return it immediately.
    """
    environment_name = kwargs.get("environment_name", environments[0]["Name"] if single_env else None)
    if not environment_name:
        return None, {"status": "Failed", "error": f"Environment name is required. Available: {env_help}"}

    env_id, env_error = await _resolve_environment(str(environment_name), environments)
    if env_error:
        return None, {"status": "Failed", "error": env_error}
    return env_id, None


def _register_runbook_tool(runbook: dict, environments: list[dict], prompted_variables: list[dict], branch_names: list[str] | None = None) -> None:
    """Register a single runbook as an MCP tool with task support."""
    runbook_id = runbook["Id"]
    runbook_name = runbook["Name"]
    project_id = runbook.get("ProjectId", "")
    description = runbook.get("Description") or f"Run the '{runbook_name}' runbook"
    tool_name = _sanitize_tool_name(runbook_name)
    multi_tenancy_mode = runbook.get("MultiTenancyMode", "Untenanted")
    is_tenanted = multi_tenancy_mode in ("Tenanted", "TenantedOrUntenanted")
    is_cac = bool(runbook.get("_git_ref"))
    default_git_ref = runbook.get("_git_ref", "")
    runbook_slug = runbook.get("Slug", "")

    print(
        f"Registering runbook tool: {tool_name} (runbook_id={runbook_id}, project_id={project_id}, "
        f"prompted_variables={[v['name'] for v in prompted_variables]})"
    )

    env_names = [e["Name"] for e in environments]
    env_help = ", ".join(env_names) if env_names else "No environments found"
    single_env = len(environments) == 1

    # Create a dynamic Enum for environment names
    environment_enum = _build_environment_enum(runbook_name, single_env, env_names)

    # Create a dynamic Enum for branch names (CaC projects only)
    branch_enum = _build_branch_enum(runbook_name, is_cac, branch_names)

    # Separate the session ID variable (if present) from the prompted variables
    # so it is not exposed as a tool argument.
    session_id_var, visible_prompted_variables = _split_session_id_variable(prompted_variables)

    # Build a mapping from sanitized param name to variable info
    param_to_var = _build_param_to_var(visible_prompted_variables)

    params = _build_tool_params(single_env, environment_enum, param_to_var, is_tenanted, multi_tenancy_mode, is_cac=is_cac, default_git_ref=default_git_ref, branch_enum=branch_enum)

    async def run_tool(**kwargs) -> dict:
        """placeholder"""
        ctx = kwargs.pop("ctx", None)
        env_id, env_error = await _resolve_tool_environment(kwargs, environments, single_env, env_help)
        if env_error:
            return env_error

        variable_values, var_error = await _collect_variable_values(kwargs, param_to_var, ctx, use_var_id=is_cac)
        if var_error:
            return var_error

        # Inject the session ID into variable values if the prompted variable exists
        _inject_session_id(variable_values, session_id_var, ctx, is_cac)

        resolved_tenant_id, tenant_error = await resolve_tenant_for_tool(kwargs.get("tenant_name"), is_tenanted, multi_tenancy_mode, project_id, env_id)
        if tenant_error:
            return tenant_error

        effective_git_ref = kwargs.get("git_ref", default_git_ref) if is_cac else default_git_ref

        async def intervention_handler(client, task_id, task):
            return await _handle_pending_interventions(client, task_id, task, ctx)

        return await run_runbook(runbook_id, env_id, variable_values if variable_values else None, intervention_handler=intervention_handler if ctx else None, tenant_id=resolved_tenant_id, project_id=project_id, is_cac=is_cac, git_ref=effective_git_ref, runbook_slug=runbook_slug)

    run_tool.__doc__ = _build_tool_docstring(description, project_id, env_help, single_env, is_tenanted, multi_tenancy_mode, param_to_var, is_cac=is_cac, default_git_ref=default_git_ref)
    run_tool.__name__ = tool_name
    run_tool.__signature__ = inspect.Signature(params)
    run_tool.__annotations__ = _build_tool_annotations(single_env, environment_enum, is_tenanted, multi_tenancy_mode, param_to_var, is_cac=is_cac, BranchEnum=branch_enum)

    task_config = _resolve_task_config(runbook.get("RunbookTags", []))

    mcp.tool(name=tool_name, description=description, task=task_config)(run_tool)


    # If docket is already running (dynamic refresh after startup), register the
    # new tool so background-task execution can find it by key.
    if mcp._docket is not None:
        tool_key = f"tool:{tool_name}@"
        tool_obj = mcp.local_provider._components.get(tool_key)
        if tool_obj:
            tool_obj.register_with_docket(mcp._docket)


def _resolve_task_config(runbook_tags: list[str]) -> TaskConfig:
    """Determine the TaskConfig mode based on runbook tags."""
    async_tag = f"{TASK_TAG_GROUP}/{TASK_TAG_ASYNC}"
    sync_tag = f"{TASK_TAG_GROUP}/{TASK_TAG_SYNC}"
    if async_tag in runbook_tags:
        return TaskConfig(mode="required")
    elif sync_tag in runbook_tags:
        return TaskConfig(mode="forbidden")
    else:
        return TaskConfig(mode="optional")


async def _remove_all_tools() -> None:
    """Remove all currently registered tools from the MCP server."""
    tools = await mcp.list_tools()
    for tool in tools:
        try:
            mcp.local_provider.remove_tool(tool.name)
        except Exception as e:
            logger.warning(f"Failed to remove tool '{tool.name}': {e}")


async def _filter_runbooks_by_project(runbooks: list[dict]) -> list[dict]:
    """Filter runbooks to only those belonging to configured project names."""
    project_filter = _parse_csv_env(OCTOPUS_PROJECTS_CSV)
    if not project_filter:
        return runbooks
    allowed_project_ids = await get_project_ids_by_names(project_filter)
    filtered = [rb for rb in runbooks if rb.get("ProjectId") in allowed_project_ids]
    logger.info(f"Filtered to {len(filtered)} runbooks from projects: {project_filter}")
    return filtered


async def _fetch_project_prompted_vars(runbooks: list[dict]) -> tuple[dict[str, list[dict]], dict[str, str | None]]:
    """Fetch prompted variables for each unique project referenced by runbooks.

    Returns a tuple of (project_prompted_vars, project_git_refs).
    """
    project_git_refs: dict[str, str | None] = {}
    for rb in runbooks:
        pid = rb.get("ProjectId", "")
        if pid and pid not in project_git_refs:
            project_git_refs[pid] = rb.get("_git_ref")

    project_ids = list(project_git_refs.keys())
    project_vars = await asyncio.gather(
        *[get_project_prompted_variables(pid, git_ref=project_git_refs.get(pid)) for pid in project_ids]
    )
    return dict(zip(project_ids, project_vars)), project_git_refs


async def _fetch_project_branches(project_git_refs: dict[str, str | None]) -> dict[str, list[str]]:
    """Fetch git branches for CaC projects."""
    cac_project_ids = [pid for pid, ref in project_git_refs.items() if ref]
    if not cac_project_ids:
        return {}
    branch_results = await asyncio.gather(
        *[get_project_branches(pid) for pid in cac_project_ids],
        return_exceptions=True,
    )
    project_branches: dict[str, list[str]] = {}
    for pid, result in zip(cac_project_ids, branch_results):
        if isinstance(result, Exception):
            logger.warning(f"Failed to fetch branches for project {pid}: {result}")
        else:
            project_branches[pid] = result
    return project_branches


async def _fetch_lifecycle_env_map(runbooks: list[dict]) -> dict[str, list[dict]]:
    """Fetch lifecycle environments for runbooks with FromProjectLifecycles scope."""
    lifecycle_runbooks = [rb for rb in runbooks if rb.get("EnvironmentScope") == "FromProjectLifecycles"]
    lifecycle_envs = await asyncio.gather(
        *[get_runbook_environments(rb) for rb in lifecycle_runbooks]
    )
    return {rb["Id"]: envs for rb, envs in zip(lifecycle_runbooks, lifecycle_envs)}


def _filter_prompted_variables(all_prompted: list[dict], runbook: dict) -> list[dict]:
    """Filter prompted variables to those applicable to a specific runbook."""
    runbook_id = runbook["Id"]
    runbook_slug = runbook["Slug"]
    return [
        var for var in all_prompted
        if not var.get("process_owners") or runbook_id in var["process_owners"] or runbook_slug in var["process_owners"]
    ]


def _resolve_runbook_environments(
    runbook: dict, environments: list[dict], lifecycle_env_map: dict[str, list[dict]]
) -> list[dict]:
    """Resolve the environments applicable to a runbook based on its EnvironmentScope."""
    scope = runbook.get("EnvironmentScope")
    if scope == "Specified":
        allowed_env_ids = set(runbook.get("Environments", []))
        return [e for e in environments if e["Id"] in allowed_env_ids]
    elif scope == "FromProjectLifecycles":
        return lifecycle_env_map.get(runbook["Id"], environments)
    return environments


async def register_all_runbook_tools() -> None:
    """Fetch runbooks and environments, then register each runbook as a tool."""
    await _remove_all_tools()

    runbooks, environments = await asyncio.gather(
        get_all_runbooks(),
        get_environments(),
    )

    # Filter by project names if configured
    runbooks = await _filter_runbooks_by_project(runbooks)

    # Fetch prompted variables for each unique project
    project_prompted_vars, project_git_refs = await _fetch_project_prompted_vars(runbooks)

    # Fetch git branches for CaC projects
    project_branches = await _fetch_project_branches(project_git_refs)

    # Fetch lifecycle environments for runbooks with FromProjectLifecycles scope
    lifecycle_env_map = await _fetch_lifecycle_env_map(runbooks)

    for runbook in runbooks:
        all_prompted = project_prompted_vars.get(runbook.get("ProjectId", ""), [])

        # Filter prompted variables: include only those with no ProcessOwner scope
        # or where this runbook is listed as a process owner
        prompted = _filter_prompted_variables(all_prompted, runbook)

        # Filter environments based on the runbook's EnvironmentScope
        runbook_environments = _resolve_runbook_environments(runbook, environments, lifecycle_env_map)

        _register_runbook_tool(runbook, runbook_environments, prompted, branch_names=project_branches.get(runbook.get("ProjectId", "")))

logging.info(f"Base URL: {BASE_URL}")

auth = _create_auth()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("OctopusEasyMode", auth=auth, lifespan=_app_lifespan)

# Register tools at import time by running the async setup
asyncio.run(register_all_runbook_tools())


if __name__ == "__main__":
    transport = os.environ.get("EASY_MODE_MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        mcp.run(transport="stdio")
    else:
        mcp.run(transport="streamable-http", host=HOST, port=PORT, allowed_hosts=ALLOWED_HOSTS, allowed_origins=ALLOWED_ORIGINS)
