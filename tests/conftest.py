"""Ensure required environment variables are set for config.py import."""

import os

# Set dummy values so config.py doesn't call sys.exit(1) during import
for var, default in [
    ("EASY_MODE_MCP_OCTOPUS_URL", "http://localhost:8080"),
    ("EASY_MODE_MCP_OCTOPUS_API_KEY", "API-TEST"),
    ("EASY_MODE_MCP_OCTOPUS_SPACE_ID", "Spaces-1"),
    ("EASY_MODE_MCP_AUTH_TYPE", "none"),
]:
    os.environ.setdefault(var, default)
