"""Unit tests for the space configuration in config.py."""

import importlib.util
import os
from unittest.mock import patch

import pytest

_CONFIG_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "config.py")

_BASE_ENV = {
    "EASY_MODE_MCP_OCTOPUS_URL": "http://localhost:8080",
    "EASY_MODE_MCP_OCTOPUS_API_KEY": "API-TEST",
    "EASY_MODE_MCP_AUTH_TYPE": "none",
}


def _load_config(env: dict):
    """Execute config.py in a fresh namespace with the given environment.

    The module is not added to sys.modules, so the already-imported config used
    by other tests is left untouched.
    """
    full_env = {**_BASE_ENV, **env}
    # Clear any space variables not explicitly provided by the caller
    for var in ("EASY_MODE_MCP_OCTOPUS_SPACE_ID", "EASY_MODE_MCP_OCTOPUS_SPACE_NAME"):
        full_env.setdefault(var, "")

    spec = importlib.util.spec_from_file_location("config_under_test", _CONFIG_PATH)
    module = importlib.util.module_from_spec(spec)

    with patch.dict(os.environ, full_env, clear=False):
        spec.loader.exec_module(module)

    return module


class TestSpaceConfiguration:
    """Tests for the space ID and space name environment variables."""

    def test_space_id_is_read(self):
        module = _load_config({"EASY_MODE_MCP_OCTOPUS_SPACE_ID": "Spaces-42"})
        assert module.OCTOPUS_SPACE_ID == "Spaces-42"
        assert module.OCTOPUS_SPACE_NAME == ""

    def test_space_name_is_read(self):
        module = _load_config({"EASY_MODE_MCP_OCTOPUS_SPACE_NAME": "My Space"})
        assert module.OCTOPUS_SPACE_ID == ""
        assert module.OCTOPUS_SPACE_NAME == "My Space"

    def test_both_may_be_set(self):
        module = _load_config({
            "EASY_MODE_MCP_OCTOPUS_SPACE_ID": "Spaces-42",
            "EASY_MODE_MCP_OCTOPUS_SPACE_NAME": "My Space",
        })
        assert module.OCTOPUS_SPACE_ID == "Spaces-42"
        assert module.OCTOPUS_SPACE_NAME == "My Space"

    def test_missing_space_id_and_name_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_config({})
        assert exc_info.value.code == 1

    def test_missing_url_exits(self):
        with pytest.raises(SystemExit) as exc_info:
            _load_config({
                "EASY_MODE_MCP_OCTOPUS_URL": "",
                "EASY_MODE_MCP_OCTOPUS_SPACE_ID": "Spaces-1",
            })
        assert exc_info.value.code == 1
