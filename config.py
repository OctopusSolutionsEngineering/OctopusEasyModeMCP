"""Shared configuration for the Octopus Easy Mode MCP server."""

import os
import sys
import logging

logger = logging.getLogger(__name__)

_REQUIRED_ENV_VARS = [
    "EASY_MODE_MCP_OCTOPUS_URL",
    "EASY_MODE_MCP_OCTOPUS_API_KEY",
    "EASY_MODE_MCP_OCTOPUS_SPACE_ID",
]

_missing = [var for var in _REQUIRED_ENV_VARS if not os.environ.get(var)]
if _missing:
    logger.error(
        "The following required environment variables are not set: %s. "
        "Please set them before starting the server.",
        ", ".join(_missing),
    )
    sys.exit(1)

OCTOPUS_URL = os.environ["EASY_MODE_MCP_OCTOPUS_URL"]
OCTOPUS_API_KEY = os.environ["EASY_MODE_MCP_OCTOPUS_API_KEY"]
OCTOPUS_SPACE_ID = os.environ["EASY_MODE_MCP_OCTOPUS_SPACE_ID"]

# Auth type: "google", "github", "azure", "oauth_proxy", or "none" (default: "google")
_VALID_AUTH_TYPES = ("google", "github", "azure", "oauth_proxy", "none")
AUTH_TYPE = os.environ.get("EASY_MODE_MCP_AUTH_TYPE", "google").lower()

if AUTH_TYPE not in _VALID_AUTH_TYPES:
    logger.error(
        "EASY_MODE_MCP_AUTH_TYPE=%r is not a valid value. Must be one of: %s.",
        AUTH_TYPE,
        ", ".join(_VALID_AUTH_TYPES),
    )
    sys.exit(1)

AUTH_ENABLED = AUTH_TYPE != "none"

if AUTH_ENABLED and not os.environ.get("EASY_MODE_MCP_OCTOPUS_AUDIENCE"):
    logger.error(
        "EASY_MODE_MCP_OCTOPUS_AUDIENCE must be set when authentication is enabled "
        "(EASY_MODE_MCP_AUTH_TYPE=%r). Set EASY_MODE_MCP_AUTH_TYPE=none to disable auth.",
        AUTH_TYPE,
    )
    sys.exit(1)

# Task mode tag configuration
TASK_TAG_GROUP = os.environ.get("EASY_MODE_MCP_TASK_TAG_GROUP", "MCP Tasks")
TASK_TAG_ASYNC = os.environ.get("EASY_MODE_MCP_TASK_TAG_ASYNC", "Async")
TASK_TAG_SYNC = os.environ.get("EASY_MODE_MCP_TASK_TAG_SYNC", "Sync")
TASK_TAG_SYNC_FALLBACK = os.environ.get("EASY_MODE_MCP_TASK_TAG_SYNC_FALLBACK", "Sync fallback")

# Prompted variable name used to pass the MCP session ID to runbooks
SESSION_ID_VAR = os.environ.get("EASY_MODE_MCP_SESSION_ID_VAR", "Project.SessionId")

BASE_URL = os.environ.get("EASY_MODE_MCP_BASE_URL", "http://localhost:8000")

# Server binding configuration
HOST = os.environ.get("EASY_MODE_MCP_HOST", "0.0.0.0")
PORT = int(os.environ.get("EASY_MODE_MCP_PORT", "8000"))
ALLOWED_HOSTS = os.environ.get("EASY_MODE_MCP_ALLOWED_HOSTS", "*").split(",")
ALLOWED_ORIGINS = os.environ.get("EASY_MODE_MCP_ALLOWED_ORIGINS", "*").split(",")

# Comma-separated list of project names to expose (empty = all projects)
OCTOPUS_PROJECTS_CSV = os.environ.get("EASY_MODE_MCP_OCTOPUS_PROJECTS", "")

# Whether to return verbose Octopus task logs to the MCP client (default: false)
VERBOSE_LOGS = os.environ.get("EASY_MODE_MCP_VERBOSE_LOGS", "false").lower() == "true"

# Number of log lines to retrieve from the task details endpoint (default: 1000)
LOG_TAIL = int(os.environ.get("EASY_MODE_MCP_LOG_TAIL", "1000"))

# Whether to auto-proceed with manual interventions when the client doesn't support elicitation (default: false)
AUTO_PROCEED_INTERVENTIONS = os.environ.get("EASY_MODE_MCP_AUTO_PROCEED_INTERVENTIONS", "false").lower() == "true"

# Whether to automatically assign manual interventions to the current user (default: false)
AUTO_ASSIGN_INTERVENTIONS = os.environ.get("EASY_MODE_MCP_AUTO_ASSIGN_INTERVENTIONS", "false").lower() == "true"

