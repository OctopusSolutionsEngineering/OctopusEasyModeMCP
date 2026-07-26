"""Integration tests for octopus.get_all_runbooks using testcontainers."""

import asyncio
import json
import os
import shutil
import subprocess
import tempfile

import httpx
import pytest
from testcontainers.core.container import DockerContainer
from testcontainers.core.network import Network
from testcontainers.core.waiting_utils import wait_for_logs

OCTOPUS_API_KEY = "API-ABCDEFGHIJKLMNOPQURTUVWXYZ12345"
OCTOPUS_URL = "http://localhost:8080"


def run_terraform(directory: str, url: str, api_key: str, space_id: str | None = None) -> str:
    """Run terraform init and apply against the given directory."""
    with tempfile.TemporaryDirectory() as temp_dir:
        test_file_path = os.path.dirname(__file__)
        joined_path = os.path.join(test_file_path, directory)
        absolute_path = os.path.abspath(joined_path)

        if not os.path.exists(absolute_path):
            raise FileNotFoundError(
                f"Path does not exist: {absolute_path}. "
                f"Created from file path {test_file_path} and directory {directory}."
            )

        shutil.copytree(absolute_path, temp_dir, dirs_exist_ok=True)

        subprocess.run(["terraform", "init"], check=True, cwd=temp_dir, capture_output=True)

        args = [
            "terraform", "apply", "-auto-approve",
            f"-var=octopus_server={url}",
            f"-var=octopus_apikey={api_key}",
        ]
        if space_id is not None:
            args.append(f"-var=octopus_space_id={space_id}")

        subprocess.run(args, check=True, cwd=temp_dir, capture_output=True)

        output = subprocess.run(
            ["terraform", "output", "-json"],
            check=True, cwd=temp_dir, capture_output=True,
        )
        return output.stdout.decode()


def publish_runbook(url: str, api_key: str, space_id: str, project_name: str, runbook_name: str) -> None:
    """Publish a runbook by creating a snapshot and setting it as the published snapshot."""
    headers = {"X-Octopus-ApiKey": api_key}

    # Get project
    resp = httpx.get(f"{url}/api/{space_id}/projects", headers=headers, params={"take": 100})
    resp.raise_for_status()
    projects = resp.json()["Items"]
    project = next(p for p in projects if p["Name"] == project_name)

    # Get runbook
    resp = httpx.get(f"{url}/api/{space_id}/projects/{project['Id']}/runbooks", headers=headers, params={"take": 100})
    resp.raise_for_status()
    runbooks = resp.json()["Items"]
    runbook = next(rb for rb in runbooks if rb["Name"] == runbook_name)

    # Get snapshot template
    resp = httpx.get(
        f"{url}/api/{space_id}/runbookProcesses/{runbook['RunbookProcessId']}/runbookSnapshotTemplate",
        headers=headers,
    )
    resp.raise_for_status()
    template = resp.json()

    # Create snapshot
    snapshot_payload = {
        "ProjectId": project["Id"],
        "RunbookId": runbook["Id"],
        "Name": template["NextNameIncrement"],
        "SelectedPackages": [],
    }

    for package in template.get("Packages", []):
        resp = httpx.get(
            f"{url}/api/{space_id}/feeds/{package['FeedId']}/packages/versions",
            headers=headers,
            params={"packageId": package["PackageId"]},
        )
        resp.raise_for_status()
        packages = resp.json()["Items"]
        if packages:
            snapshot_payload["SelectedPackages"].append({
                "ActionName": package["ActionName"],
                "Version": packages[0]["Version"],
                "PackageReferenceName": package["PackageReferenceName"],
            })

    resp = httpx.post(f"{url}/api/{space_id}/runbookSnapshots", headers=headers, json=snapshot_payload)
    resp.raise_for_status()
    snapshot = resp.json()

    # Publish the snapshot
    runbook["PublishedRunbookSnapshotId"] = snapshot["Id"]
    resp = httpx.put(f"{url}/api/{space_id}/runbooks/{runbook['Id']}", headers=headers, json=runbook)
    resp.raise_for_status()


@pytest.fixture(scope="module")
def octopus_environment():
    """Start MSSQL and Octopus containers, apply terraform, and set env vars."""
    license_key = os.environ.get("LICENSE")
    if not license_key:
        pytest.skip("LICENSE environment variable not set")

    # Create a shared network for inter-container communication
    network = Network()
    network.create()

    try:
        mssql = (
            DockerContainer("mcr.microsoft.com/mssql/server:2022-latest")
            .with_env("ACCEPT_EULA", "True")
            .with_env("SA_PASSWORD", "Password01!")
            .with_network(network)
            .with_network_aliases("mssql")
        )
        mssql.start()
        wait_for_logs(mssql, "SQL Server is now ready for client connections")

        octopus = (
            DockerContainer("octopusdeploy/octopusdeploy")
            .with_bind_ports(8080, 8080)
            .with_env("ACCEPT_EULA", "Y")
            .with_env(
                "DB_CONNECTION_STRING",
                "Server=mssql,1433;Database=OctopusDeploy;User=sa;Password=Password01!",
            )
            .with_env("ADMIN_API_KEY", OCTOPUS_API_KEY)
            .with_env("DISABLE_DIND", "Y")
            .with_env("ADMIN_USERNAME", "admin")
            .with_env("ADMIN_PASSWORD", "Password01!")
            .with_env("OCTOPUS_SERVER_BASE64_LICENSE", license_key)
            .with_env("ENABLE_USAGE", "N")
            .with_network(network)
            .with_network_aliases("octopus")
        )
        octopus.start()
        wait_for_logs(octopus, "Web server is ready to process requests", timeout=300)

        # Create a space
        output = run_terraform("terraform/space_creation", OCTOPUS_URL, OCTOPUS_API_KEY)
        space_id = json.loads(output)["octopus_space_id"]["value"]

        # Populate the space with projects and runbooks
        run_terraform("terraform/space_population", OCTOPUS_URL, OCTOPUS_API_KEY, space_id)

        # Publish both runbooks so they have snapshots
        publish_runbook(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, "Test Project", "Backup Database")
        publish_runbook(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, "Test Project", "Deploy Service")

        # Set environment variables used by octopus.py via config.py
        os.environ["EASY_MODE_MCP_OCTOPUS_URL"] = OCTOPUS_URL
        os.environ["EASY_MODE_MCP_OCTOPUS_API_KEY"] = OCTOPUS_API_KEY
        os.environ["EASY_MODE_MCP_OCTOPUS_SPACE_ID"] = space_id
        os.environ["EASY_MODE_MCP_AUTH_TYPE"] = "none"

        yield {"space_id": space_id, "url": OCTOPUS_URL, "api_key": OCTOPUS_API_KEY}

    finally:
        # Cleanup
        octopus.stop()
        mssql.stop()
        network.remove()


@pytest.mark.integration
class TestGetAllRunbooks:
    """Tests for the get_all_runbooks function against a real Octopus instance."""

    def test_get_all_runbooks_returns_runbooks(self, octopus_environment):
        """Test that get_all_runbooks returns the runbooks created by terraform."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())

        assert len(runbooks) >= 2
        runbook_names = [rb["Name"] for rb in runbooks]
        assert "Backup Database" in runbook_names
        assert "Deploy Service" in runbook_names

    def test_runbooks_have_expected_fields(self, octopus_environment):
        """Test that returned runbooks contain expected fields."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())

        for runbook in runbooks:
            assert "Id" in runbook
            assert "Name" in runbook
            assert "ProjectId" in runbook

    def test_runbooks_have_correct_environment_scope(self, octopus_environment):
        """Test that runbooks have the correct environment scope."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())

        backup_runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")
        assert backup_runbook["EnvironmentScope"] == "Specified"
        assert len(backup_runbook.get("Environments", [])) == 1

        deploy_runbook = next(rb for rb in runbooks if rb["Name"] == "Deploy Service")
        assert deploy_runbook["EnvironmentScope"] == "Specified"
        assert len(deploy_runbook.get("Environments", [])) == 2

    def test_runbooks_are_published(self, octopus_environment):
        """Test that only published runbooks are returned."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())

        for runbook in runbooks:
            # Database-backed runbooks must have a published snapshot
            if "_git_ref" not in runbook:
                assert runbook.get("PublishedRunbookSnapshotId") is not None
