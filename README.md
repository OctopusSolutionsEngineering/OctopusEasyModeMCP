# Octopus Easy Mode MCP Server

An MCP (Model Context Protocol) server that exposes Octopus Deploy runbooks as tools. Each published or Config-as-Code enabled runbook in your Octopus space is automatically registered as an MCP tool with appropriate parameters for environments, prompted variables, and tenants.

## Features

- Automatically discovers and registers all published runbooks as MCP tools
- Supports prompted variables with type-safe parameters
- Handles manual interventions via MCP elicitation
- Multi-tenancy support (tenanted and untenanted runbooks)
- Multiple OAuth provider options (Google, GitHub, Azure, generic OAuth proxy)
- Periodic refresh of runbook tools (every 5 minutes)
- Supports both HTTP and stdio transports

## Environment Variables

### Required (always)

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_OCTOPUS_URL` | Base URL of your Octopus Deploy instance (e.g., `https://myinstance.octopus.app`) |
| `EASY_MODE_MCP_OCTOPUS_API_KEY` | Octopus Deploy API key for server-side operations (fetching runbooks, environments, etc.) |
| `EASY_MODE_MCP_OCTOPUS_SPACE_ID` | Octopus space ID (e.g., `Spaces-1`) |

### Server Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `EASY_MODE_MCP_BASE_URL` | `http://localhost:8000` | Public base URL where the MCP server is accessible |
| `EASY_MODE_MCP_TRANSPORT` | `streamable-http` | Transport mode: `streamable-http` or `stdio` |
| `EASY_MODE_MCP_HOST` | `0.0.0.0` | Address the HTTP server binds to |
| `EASY_MODE_MCP_PORT` | `8000` | Port the HTTP server listens on |
| `EASY_MODE_MCP_ALLOWED_HOSTS` | `*` | Comma-separated list of allowed `Host` header values |
| `EASY_MODE_MCP_ALLOWED_ORIGINS` | `*` | Comma-separated list of allowed CORS origins |
| `EASY_MODE_MCP_AUTH_TYPE` | `google` | Authentication type: `google`, `github`, `azure`, `oauth_proxy`, or `none` |
| `EASY_MODE_MCP_OCTOPUS_PROJECTS` | *(empty)* | Comma-separated list of project names to expose. If empty, all projects are exposed |
| `EASY_MODE_MCP_SESSION_ID_VAR` | `Project.SessionId` | Name of the prompted variable used to pass the MCP session ID to runbooks |
| `EASY_MODE_MCP_VERBOSE_LOGS` | `false` | When `true`, includes verbose Octopus task logs in tool responses. When `false`, only summary-level logs are returned |
| `EASY_MODE_MCP_LOG_TAIL` | `1000` | Number of log lines to retrieve from the Octopus task details endpoint |
| `EASY_MODE_MCP_AUTO_PROCEED_INTERVENTIONS` | `false` | When `true`, automatically selects "Proceed" for manual interventions instead of prompting the user to choose an action. The user is still prompted for notes unless `EASY_MODE_MCP_AUTO_POPULATE_INTERVENTION_NOTES` is also `true` |
| `EASY_MODE_MCP_AUTO_POPULATE_INTERVENTION_NOTES` | `false` | When `true`, automatically populates intervention notes with a generic message instead of prompting the user. When combined with `EASY_MODE_MCP_AUTO_PROCEED_INTERVENTIONS`, manual interventions are handled entirely without user interaction |
| `EASY_MODE_MCP_AUTO_ASSIGN_INTERVENTIONS` | `false` | When `true`, automatically assigns manual interventions to the current user without asking. When `false`, the user is prompted to accept responsibility |

### Task Mode Tags

These variables control which Octopus runbook tags determine the MCP task mode for each tool. The tag checked is `{group}/{name}` (e.g., `MCP Tasks/Async`). Runbooks tagged with the async tag use `mode="required"` (always async), the sync tag use `mode="forbidden"` (always synchronous), and the sync-fallback tag or no tag use `mode="optional"` (client decides).

| Variable | Default | Description |
|----------|---------|-------------|
| `EASY_MODE_MCP_TASK_TAG_GROUP` | `MCP Tasks` | Tenant tag set (group) name used for task mode tags |
| `EASY_MODE_MCP_TASK_TAG_ASYNC` | `Async` | Tag name within the group that sets task mode to `required` (always async) |
| `EASY_MODE_MCP_TASK_TAG_SYNC` | `Sync` | Tag name within the group that sets task mode to `forbidden` (always synchronous) |
| `EASY_MODE_MCP_TASK_TAG_SYNC_FALLBACK` | `Sync fallback` | Tag name within the group that sets task mode to `optional` (client decides). This is also the default when no tag is present |

### Required when auth is enabled (any type except `none`)

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_AZURE_STORAGE_CONNECTION_STRING` | Azure Table Storage connection string for session/state storage |
| `EASY_MODE_MCP_JWT_SIGNING_KEY` | Secret key for signing JWT tokens |

Generate a signing key with:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Required when `EASY_MODE_MCP_AUTH_TYPE=google` (default)

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_GOOGLE_CLIENT_ID` | Google OAuth client ID |
| `EASY_MODE_MCP_GOOGLE_CLIENT_SECRET` | Google OAuth client secret |

### Required when `EASY_MODE_MCP_AUTH_TYPE=github`

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_GITHUB_CLIENT_ID` | GitHub OAuth App client ID |
| `EASY_MODE_MCP_GITHUB_CLIENT_SECRET` | GitHub OAuth App client secret |

### Required when `EASY_MODE_MCP_AUTH_TYPE=azure`

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_AZURE_CLIENT_ID` | Azure App Registration client ID |
| `EASY_MODE_MCP_AZURE_CLIENT_SECRET` | Azure App Registration client secret |
| `EASY_MODE_MCP_AZURE_TENANT_ID` | Azure tenant ID |

### Required when `EASY_MODE_MCP_AUTH_TYPE=oauth_proxy`

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_OAUTH_AUTHORIZATION_ENDPOINT` | Upstream OAuth authorization endpoint URL |
| `EASY_MODE_MCP_OAUTH_TOKEN_ENDPOINT` | Upstream OAuth token endpoint URL |
| `EASY_MODE_MCP_OAUTH_CLIENT_ID` | Upstream OAuth client ID |
| `EASY_MODE_MCP_OAUTH_JWKS_URI` | JWKS URI for token verification |

| Variable | Default | Description |
|----------|---------|-------------|
| `EASY_MODE_MCP_OAUTH_CLIENT_SECRET` | *(none)* | Upstream OAuth client secret (optional for PKCE) |
| `EASY_MODE_MCP_OAUTH_REVOCATION_ENDPOINT` | *(none)* | Upstream token revocation endpoint |
| `EASY_MODE_MCP_OAUTH_ISSUER` | *(none)* | Expected JWT issuer claim |
| `EASY_MODE_MCP_OAUTH_AUDIENCE` | *(none)* | Expected JWT audience claim |
| `EASY_MODE_MCP_OAUTH_SCOPES` | *(none)* | Comma-separated required scopes |

### Token Exchange (when auth is enabled)

| Variable | Description |
|----------|-------------|
| `EASY_MODE_MCP_OCTOPUS_AUDIENCE` | Audience for Octopus token exchange (used to exchange the user's ID token for an Octopus access token) |

## MCP Client Configuration

### Streamable HTTP (remote server)

Add the following to your MCP client configuration (e.g., `mcp.json`):

```json
{
  "servers": {
    "octopus-easy-mode": {
      "type": "http",
      "url": "https://your-deployed-server.example.com/mcp"
    }
  }
}
```

### Streamable HTTP (local development)

```json
{
  "servers": {
    "octopus-easy-mode": {
      "type": "http",
      "url": "http://localhost:8000/mcp"
    }
  }
}
```

### stdio (local process)

```json
{
  "servers": {
    "octopus-easy-mode": {
      "type": "stdio",
      "command": "python",
      "args": ["main.py"],
      "env": {
        "EASY_MODE_MCP_TRANSPORT": "stdio",
        "EASY_MODE_MCP_AUTH_TYPE": "none",
        "EASY_MODE_MCP_OCTOPUS_URL": "https://myinstance.octopus.app",
        "EASY_MODE_MCP_OCTOPUS_API_KEY": "API-XXXXX",
        "EASY_MODE_MCP_OCTOPUS_SPACE_ID": "Spaces-1"
      }
    }
  }
}
```

## Running Locally

```bash
# Install dependencies
pip install -r requirements.txt

# Run with HTTP transport (default)
python main.py

# Run with stdio transport
EASY_MODE_MCP_TRANSPORT=stdio python main.py
```

## Docker

```bash
docker build -t octopus-easy-mode-mcp .
docker run -p 8000:8000 \
  -e EASY_MODE_MCP_OCTOPUS_URL=https://myinstance.octopus.app \
  -e EASY_MODE_MCP_OCTOPUS_API_KEY=API-XXXXX \
  -e EASY_MODE_MCP_OCTOPUS_SPACE_ID=Spaces-1 \
  -e EASY_MODE_MCP_AUTH_TYPE=none \
  octopus-easy-mode-mcp
```

