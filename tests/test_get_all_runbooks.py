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


def upload_package(url: str, api_key: str, space_id: str, package_path: str) -> None:
    """Upload a package to the Octopus built-in feed."""
    headers = {"X-Octopus-ApiKey": api_key}
    with open(package_path, "rb") as f:
        resp = httpx.post(
            f"{url}/api/{space_id}/packages/raw",
            headers=headers,
            files={"file": (os.path.basename(package_path), f, "application/zip")},
            timeout=30,
        )
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

        # Upload a test package to the built-in feed
        package_path = os.path.join(os.path.dirname(__file__), "packages", "dummy.1.0.0.zip")
        upload_package(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, package_path)

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


@pytest.mark.integration
class TestGetProjectPromptedVariables:
    """Tests for the get_project_prompted_variables function."""

    def test_returns_prompted_variables(self, octopus_environment):
        """Test that prompted variables are returned for the test project."""
        from octopus import get_all_runbooks, get_project_prompted_variables

        runbooks = asyncio.run(get_all_runbooks())
        project_id = runbooks[0]["ProjectId"]

        prompted = asyncio.run(get_project_prompted_variables(project_id))

        assert len(prompted) >= 2
        names = [v["name"] for v in prompted]
        assert "DatabaseName" in names
        assert "NotifyOnCompletion" in names

    def test_prompted_variables_have_expected_fields(self, octopus_environment):
        """Test that prompted variables contain the expected fields."""
        from octopus import get_all_runbooks, get_project_prompted_variables

        runbooks = asyncio.run(get_all_runbooks())
        project_id = runbooks[0]["ProjectId"]

        prompted = asyncio.run(get_project_prompted_variables(project_id))

        for var in prompted:
            assert "id" in var
            assert "name" in var
            assert "label" in var
            assert "description" in var
            assert "required" in var
            assert "default" in var

    def test_prompted_variable_required_flag(self, octopus_environment):
        """Test that the required flag is correctly set."""
        from octopus import get_all_runbooks, get_project_prompted_variables

        runbooks = asyncio.run(get_all_runbooks())
        project_id = runbooks[0]["ProjectId"]

        prompted = asyncio.run(get_project_prompted_variables(project_id))

        db_name_var = next(v for v in prompted if v["name"] == "DatabaseName")
        assert db_name_var["required"] is True
        assert db_name_var["label"] == "Database Name"

        notify_var = next(v for v in prompted if v["name"] == "NotifyOnCompletion")
        assert notify_var["required"] is False
        assert notify_var["default"] == "false"


@pytest.mark.integration
class TestGetLatestPackageVersion:
    """Tests for the get_latest_package_version function."""

    def test_returns_version_for_uploaded_package(self, octopus_environment):
        """Test that get_latest_package_version returns the correct version."""
        from octopus import get_latest_package_version, octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID

        async def _get_version():
            # Get the built-in feed ID
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=octopus_headers()) as client:
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/feeds", params={"feedType": "BuiltIn"})
                resp.raise_for_status()
                feeds = resp.json()["Items"]
                feed_id = feeds[0]["Id"]

                return await get_latest_package_version(client, feed_id, "dummy")

        version = asyncio.run(_get_version())
        assert version == "1.0.0"

    def test_returns_empty_for_nonexistent_package(self, octopus_environment):
        """Test that get_latest_package_version returns empty string for unknown package."""
        from octopus import get_latest_package_version, octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID

        async def _get_version():
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=octopus_headers()) as client:
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/feeds", params={"feedType": "BuiltIn"})
                resp.raise_for_status()
                feeds = resp.json()["Items"]
                feed_id = feeds[0]["Id"]

                return await get_latest_package_version(client, feed_id, "nonexistent-package")

        version = asyncio.run(_get_version())
        assert version == ""
