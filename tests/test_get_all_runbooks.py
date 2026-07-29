"""Integration tests for octopus.get_all_runbooks using testcontainers."""

import asyncio
import importlib
import json
import os
import shutil
import subprocess
import sys
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

        # Publish runbooks so they have snapshots
        publish_runbook(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, "Test Project", "Backup Database")
        publish_runbook(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, "Test Project", "Deploy Service")
        publish_runbook(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, "Test Project", "Manual Intervention Runbook")

        # Upload a test package to the built-in feed
        package_path = os.path.join(os.path.dirname(__file__), "packages", "dummy.1.0.0.zip")
        upload_package(OCTOPUS_URL, OCTOPUS_API_KEY, space_id, package_path)

        # Set environment variables used by octopus.py via config.py
        os.environ["EASY_MODE_MCP_OCTOPUS_URL"] = OCTOPUS_URL
        os.environ["EASY_MODE_MCP_OCTOPUS_API_KEY"] = OCTOPUS_API_KEY
        os.environ["EASY_MODE_MCP_OCTOPUS_SPACE_ID"] = space_id
        os.environ["EASY_MODE_MCP_AUTH_TYPE"] = "none"

        # Reload config and octopus modules so they pick up the real env vars
        # (they may have been imported earlier with dummy values from conftest)
        if "config" in sys.modules:
            importlib.reload(sys.modules["config"])
        if "octopus" in sys.modules:
            importlib.reload(sys.modules["octopus"])

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


@pytest.mark.integration
class TestGetTaskStatusAndLog:
    """Tests for get_task_status and get_task_raw_log functions."""

    def test_task_status_after_runbook_run(self, octopus_environment):
        """Test that get_task_status returns valid status for a completed task."""
        from octopus import (
            get_task_status, octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID,
            create_runbook_run, get_published_snapshot_id, build_form_values,
        )

        async def _run_and_check():
            headers = octopus_headers()
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=headers) as client:
                # Find the Backup Database runbook
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/runbooks", params={"take": 100})
                resp.raise_for_status()
                runbooks = resp.json()["Items"]
                runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

                # Get its published snapshot
                snapshot_id = await get_published_snapshot_id(client, runbook["Id"])
                assert snapshot_id is not None

                # Get environment ID
                env_id = runbook["Environments"][0]

                # Build form values with required prompted variables
                form_values = await build_form_values(
                    client, snapshot_id, env_id, {"DatabaseName": "testdb"}
                )

                # Run the runbook
                task_id = await create_runbook_run(
                    client, runbook["Id"], snapshot_id, env_id, form_values, tenant_id=None
                )
                assert task_id

                # Poll until complete
                import asyncio as aio
                for _ in range(60):
                    task = await get_task_status(client, task_id)
                    if task.get("State") in ("Success", "Failed", "Canceled", "TimedOut"):
                        break
                    await aio.sleep(2)

                return task_id, task

        task_id, task = asyncio.run(_run_and_check())

        assert "State" in task
        assert task["State"] == "Success"
        assert task.get("HasBeenPickedUpByProcessor") is True

    def test_task_raw_log_after_runbook_run(self, octopus_environment):
        """Test that get_task_details_log returns log content for a completed task."""
        from octopus import (
            get_task_status, get_task_details_log, octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID,
            create_runbook_run, get_published_snapshot_id, build_form_values,
        )

        async def _run_and_get_log():
            headers = octopus_headers()
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=headers) as client:
                # Find the Backup Database runbook
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/runbooks", params={"take": 100})
                resp.raise_for_status()
                runbooks = resp.json()["Items"]
                runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

                snapshot_id = await get_published_snapshot_id(client, runbook["Id"])
                env_id = runbook["Environments"][0]

                form_values = await build_form_values(
                    client, snapshot_id, env_id, {"DatabaseName": "testdb"}
                )

                task_id = await create_runbook_run(
                    client, runbook["Id"], snapshot_id, env_id, form_values, tenant_id=None
                )

                # Poll until complete
                import asyncio as aio
                for _ in range(60):
                    task = await get_task_status(client, task_id)
                    if task.get("State") in ("Success", "Failed", "Canceled", "TimedOut"):
                        break
                    await aio.sleep(2)

                log_text = await get_task_details_log(client, task_id)
                return log_text

        log = asyncio.run(_run_and_get_log())

        assert isinstance(log, str)
        assert len(log) > 0
        # The script echoes "Backing up database"
        assert "Backing up database" in log


@pytest.mark.integration
class TestInterventions:
    """Tests for get_pending_interruptions, take_interruption_responsibility, and submit_interruption."""

    def test_intervention_workflow(self, octopus_environment):
        """Test the full intervention workflow: get, take responsibility, and submit."""
        from octopus import (
            get_task_status, get_pending_interruptions,
            take_interruption_responsibility, submit_interruption,
            octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID,
            create_runbook_run, get_published_snapshot_id, build_form_values,
        )

        async def _run_intervention_workflow():
            headers = octopus_headers()
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=headers) as client:
                # Find the Manual Intervention Runbook
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/runbooks", params={"take": 100})
                resp.raise_for_status()
                runbooks = resp.json()["Items"]
                runbook = next(rb for rb in runbooks if rb["Name"] == "Manual Intervention Runbook")

                # Get its published snapshot
                snapshot_id = await get_published_snapshot_id(client, runbook["Id"])
                assert snapshot_id is not None

                env_id = runbook["Environments"][0]

                # Build form values with required prompted variables
                form_values = await build_form_values(
                    client, snapshot_id, env_id, {"DatabaseName": "testdb"}
                )

                # Run the runbook (will pause at manual intervention)
                task_id = await create_runbook_run(
                    client, runbook["Id"], snapshot_id, env_id, form_values, tenant_id=None
                )
                assert task_id

                # Wait for the task to have pending interruptions
                import asyncio as aio
                interruptions = []
                for _ in range(60):
                    task = await get_task_status(client, task_id)
                    if task.get("HasPendingInterruptions"):
                        interruptions = await get_pending_interruptions(client, task_id)
                        if interruptions:
                            break
                    if task.get("State") in ("Success", "Failed", "Canceled", "TimedOut"):
                        break
                    await aio.sleep(2)

                assert len(interruptions) > 0, "No pending interruptions found"

                interruption = interruptions[0]
                assert interruption.get("IsPending") is True
                interruption_id = interruption["Id"]

                # Take responsibility
                await take_interruption_responsibility(client, interruption_id)

                # Submit the intervention (proceed)
                submit_payload = {
                    "Instructions": None,
                    "Notes": "Approved via test",
                    "Result": "Proceed",
                }
                await submit_interruption(client, interruption_id, submit_payload)

                # Wait for task to complete after intervention
                for _ in range(60):
                    task = await get_task_status(client, task_id)
                    if task.get("State") in ("Success", "Failed", "Canceled", "TimedOut"):
                        break
                    await aio.sleep(2)

                return task

        task = asyncio.run(_run_intervention_workflow())
        assert task["State"] == "Success"

    def test_no_interruptions_for_normal_runbook(self, octopus_environment):
        """Test that a normal runbook has no pending interruptions."""
        from octopus import (
            get_task_status, get_pending_interruptions,
            octopus_headers, OCTOPUS_URL, OCTOPUS_SPACE_ID,
            create_runbook_run, get_published_snapshot_id, build_form_values,
        )

        async def _run_and_check():
            headers = octopus_headers()
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=headers) as client:
                resp = await client.get(f"/api/{OCTOPUS_SPACE_ID}/runbooks", params={"take": 100})
                resp.raise_for_status()
                runbooks = resp.json()["Items"]
                runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

                snapshot_id = await get_published_snapshot_id(client, runbook["Id"])
                env_id = runbook["Environments"][0]

                form_values = await build_form_values(
                    client, snapshot_id, env_id, {"DatabaseName": "testdb"}
                )

                task_id = await create_runbook_run(
                    client, runbook["Id"], snapshot_id, env_id, form_values, tenant_id=None
                )

                # Wait for completion
                import asyncio as aio
                for _ in range(60):
                    task = await get_task_status(client, task_id)
                    if task.get("State") in ("Success", "Failed", "Canceled", "TimedOut"):
                        break
                    await aio.sleep(2)

                interruptions = await get_pending_interruptions(client, task_id)
                return interruptions

        interruptions = asyncio.run(_run_and_check())
        assert len(interruptions) == 0


@pytest.mark.integration
class TestGetEnvironments:
    """Tests for get_environments."""

    def test_returns_environments(self, octopus_environment):
        """Test that get_environments returns the environments created by terraform."""
        from octopus import get_environments

        environments = asyncio.run(get_environments())

        assert len(environments) >= 2
        env_names = [e["Name"] for e in environments]
        assert "Development" in env_names
        assert "Production" in env_names

    def test_environments_have_expected_fields(self, octopus_environment):
        """Test that environments contain expected fields."""
        from octopus import get_environments

        environments = asyncio.run(get_environments())

        for env in environments:
            assert "Id" in env
            assert "Name" in env


@pytest.mark.integration
class TestGetRunbookEnvironments:
    """Tests for get_runbook_environments."""

    def test_returns_environments_for_specified_scope(self, octopus_environment):
        """Test that get_runbook_environments returns correct environments."""
        from octopus import get_all_runbooks, get_runbook_environments

        runbooks = asyncio.run(get_all_runbooks())
        deploy_runbook = next(rb for rb in runbooks if rb["Name"] == "Deploy Service")

        environments = asyncio.run(get_runbook_environments(deploy_runbook))

        assert len(environments) >= 2

    def test_single_environment_runbook(self, octopus_environment):
        """Test runbook scoped to a single environment."""
        from octopus import get_all_runbooks, get_runbook_environments

        runbooks = asyncio.run(get_all_runbooks())
        backup_runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

        environments = asyncio.run(get_runbook_environments(backup_runbook))

        assert len(environments) >= 1


@pytest.mark.integration
class TestGetProjectIdsByNames:
    """Tests for get_project_ids_by_names."""

    def test_returns_project_id_for_known_project(self, octopus_environment):
        """Test that get_project_ids_by_names returns the correct project ID."""
        from octopus import get_project_ids_by_names

        project_ids = asyncio.run(get_project_ids_by_names(["Test Project"]))

        assert len(project_ids) == 1
        project_id = list(project_ids)[0]
        assert project_id.startswith("Projects-")

    def test_returns_empty_for_unknown_project(self, octopus_environment):
        """Test that get_project_ids_by_names returns empty set for unknown project."""
        from octopus import get_project_ids_by_names

        project_ids = asyncio.run(get_project_ids_by_names(["Nonexistent Project XYZ"]))

        assert len(project_ids) == 0

    def test_case_insensitive_match(self, octopus_environment):
        """Test that project name matching is case-insensitive."""
        from octopus import get_project_ids_by_names

        project_ids = asyncio.run(get_project_ids_by_names(["test project"]))

        assert len(project_ids) == 1


@pytest.mark.integration
class TestGetPublishedSnapshotId:
    """Tests for get_published_snapshot_id."""

    def test_returns_snapshot_id_for_published_runbook(self, octopus_environment):
        """Test that published runbooks have a snapshot ID."""
        from octopus import get_all_runbooks, get_published_snapshot_id, octopus_headers, OCTOPUS_URL

        runbooks = asyncio.run(get_all_runbooks())
        runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

        async def _get_snapshot():
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=octopus_headers()) as client:
                return await get_published_snapshot_id(client, runbook["Id"])

        snapshot_id = asyncio.run(_get_snapshot())
        assert snapshot_id is not None
        assert snapshot_id.startswith("RunbookSnapshots-")


@pytest.mark.integration
class TestResolveDbRunbookPackages:
    """Tests for resolve_db_runbook_packages."""

    def test_returns_empty_for_runbook_without_packages(self, octopus_environment):
        """Test that resolve_db_runbook_packages returns empty list for script-only runbooks."""
        from octopus import get_all_runbooks, resolve_db_runbook_packages, octopus_headers, OCTOPUS_URL

        runbooks = asyncio.run(get_all_runbooks())
        runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")

        async def _resolve():
            async with httpx.AsyncClient(base_url=OCTOPUS_URL, headers=octopus_headers()) as client:
                return await resolve_db_runbook_packages(client, runbook["Id"])

        packages = asyncio.run(_resolve())
        assert packages == []


@pytest.mark.integration
class TestRunRunbook:
    """Tests for the run_runbook function."""

    def test_run_runbook_succeeds(self, octopus_environment):
        """Test that run_runbook executes a runbook and returns success."""
        from octopus import get_all_runbooks, get_environments, run_runbook

        runbooks = asyncio.run(get_all_runbooks())
        runbook = next(rb for rb in runbooks if rb["Name"] == "Backup Database")
        environments = asyncio.run(get_environments())
        dev_env = next(e for e in environments if e["Name"] == "Development")

        result = asyncio.run(run_runbook(
            runbook_id=runbook["Id"],
            environment_id=dev_env["Id"],
            project_id=runbook["ProjectId"],
            variable_values={"DatabaseName": "testdb"},
        ))

        assert result["status"] == "Success"
        assert "taskId" in result
        assert "logs" in result

    def test_run_runbook_with_intervention_handler(self, octopus_environment):
        """Test that run_runbook calls the intervention handler."""
        from octopus import get_all_runbooks, get_environments, run_runbook, get_pending_interruptions, take_interruption_responsibility, submit_interruption

        runbooks = asyncio.run(get_all_runbooks())
        runbook = next(rb for rb in runbooks if rb["Name"] == "Manual Intervention Runbook")
        environments = asyncio.run(get_environments())
        dev_env = next(e for e in environments if e["Name"] == "Development")

        async def intervention_handler(client, task_id, task):
            interruptions = await get_pending_interruptions(client, task_id)
            for interruption in interruptions:
                if interruption.get("IsPending"):
                    await take_interruption_responsibility(client, interruption["Id"])
                    await submit_interruption(client, interruption["Id"], {
                        "Instructions": None,
                        "Notes": "Auto-approved by test",
                        "Result": "Proceed",
                    })
            return None

        result = asyncio.run(run_runbook(
            runbook_id=runbook["Id"],
            environment_id=dev_env["Id"],
            project_id=runbook["ProjectId"],
            variable_values={"DatabaseName": "testdb"},
            intervention_handler=intervention_handler,
        ))

        assert result["status"] == "Success"


@pytest.mark.integration
class TestResolveTenantForTool:
    """Tests for resolve_tenant_for_tool."""

    def test_returns_none_when_not_tenanted(self, octopus_environment):
        """Test that resolve_tenant_for_tool returns None for untenanted runbooks."""
        from octopus import resolve_tenant_for_tool

        tenant_id, error = asyncio.run(resolve_tenant_for_tool(
            tenant_name=None,
            is_tenanted=False,
            multi_tenancy_mode="Untenanted",
            project_id="Projects-1",
            env_id="Environments-1",
        ))

        assert tenant_id is None
        assert error is None

    def test_returns_error_when_tenanted_but_no_name(self, octopus_environment):
        """Test that resolve_tenant_for_tool returns error when tenant is required but not provided."""
        from octopus import resolve_tenant_for_tool

        tenant_id, error = asyncio.run(resolve_tenant_for_tool(
            tenant_name=None,
            is_tenanted=True,
            multi_tenancy_mode="Tenanted",
            project_id="Projects-1",
            env_id="Environments-1",
        ))

        assert tenant_id is None
        assert error is not None
        assert "required" in error["error"].lower()


class TestParseInterruptionForm:
    """Unit tests for parse_interruption_form (no Octopus instance needed)."""

    def test_parses_form_elements(self):
        """Test that parse_interruption_form extracts instructions and element IDs."""
        from octopus import parse_interruption_form

        interruption = {
            "Form": {
                "Elements": [
                    {"Name": "instructions", "Control": {"Type": "Paragraph", "Text": "Please approve"}},
                    {"Name": "notes", "Control": {"Type": "TextArea"}},
                    {"Name": "result", "Control": {"Type": "Select"}},
                ]
            }
        }

        instructions, notes_id, result_id = parse_interruption_form(interruption)

        assert instructions == "Please approve"
        assert notes_id == "notes"
        assert result_id == "result"

    def test_handles_empty_form(self):
        """Test parse_interruption_form with no form elements."""
        from octopus import parse_interruption_form

        interruption = {}

        instructions, notes_id, result_id = parse_interruption_form(interruption)

        assert instructions == ""
        assert notes_id is None
        assert result_id is None


class TestMapVariablesToFormValues:
    """Unit tests for map_variables_to_form_values (no Octopus instance needed)."""

    def test_maps_by_control_label(self):
        """Test mapping variables by control label."""
        from octopus import map_variables_to_form_values

        elements = [
            {"Name": "elem-1", "Control": {"Label": "DatabaseName", "Name": "db", "Description": ""}},
        ]
        form_values = {}
        variable_values = {"DatabaseName": "mydb"}

        result = map_variables_to_form_values(variable_values, elements, form_values)

        assert result["elem-1"] == "mydb"

    def test_maps_by_control_name(self):
        """Test mapping variables by control name."""
        from octopus import map_variables_to_form_values

        elements = [
            {"Name": "elem-2", "Control": {"Label": "other", "Name": "NotifyFlag", "Description": ""}},
        ]
        form_values = {}
        variable_values = {"NotifyFlag": "true"}

        result = map_variables_to_form_values(variable_values, elements, form_values)

        assert result["elem-2"] == "true"

    def test_preserves_unmatched_defaults(self):
        """Test that unmatched form values are preserved."""
        from octopus import map_variables_to_form_values

        elements = [
            {"Name": "elem-1", "Control": {"Label": "SomeVar", "Name": "", "Description": ""}},
        ]
        form_values = {"elem-1": "default_value", "elem-2": "other_default"}
        variable_values = {}

        result = map_variables_to_form_values(variable_values, elements, form_values)

        assert result["elem-1"] == "default_value"
        assert result["elem-2"] == "other_default"


class TestBuildTaskResult:
    """Unit tests for build_task_result (no Octopus instance needed)."""

    def test_builds_result_dict(self):
        """Test that build_task_result creates the expected structure."""
        from octopus import build_task_result

        task = {
            "State": "Success",
            "Description": "Run runbook",
            "ErrorMessage": "",
            "Duration": "00:00:05",
        }

        result = build_task_result(task, "ServerTasks-123", "log output here")

        assert result["status"] == "Success"
        assert result["taskId"] == "ServerTasks-123"
        assert result["description"] == "Run runbook"
        assert result["errorMessage"] == ""
        assert result["duration"] == "00:00:05"
        assert result["logs"] == "log output here"

    def test_handles_failed_task(self):
        """Test build_task_result with a failed task."""
        from octopus import build_task_result

        task = {
            "State": "Failed",
            "Description": "Deploy",
            "ErrorMessage": "Something went wrong",
            "Duration": "00:01:30",
        }

        result = build_task_result(task, "ServerTasks-456", "error logs")

        assert result["status"] == "Failed"
        assert result["errorMessage"] == "Something went wrong"


@pytest.mark.integration
class TestCaCRunbooks:
    """Tests for config-as-code runbooks fetched from a git repository."""

    def test_cac_runbooks_are_discovered(self, octopus_environment):
        """Test that get_all_runbooks includes CaC runbooks from the git repo."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())

        # CaC runbooks have a _git_ref field
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]
        assert len(cac_runbooks) >= 3

        cac_names = [rb["Name"] for rb in cac_runbooks]
        assert "Backup Database" in cac_names
        assert "Deploy Service" in cac_names
        assert "Manual Intervention Runbook" in cac_names

    def test_cac_runbooks_have_git_ref(self, octopus_environment):
        """Test that CaC runbooks include the git ref."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]

        for rb in cac_runbooks:
            assert rb["_git_ref"] == "main"

    def test_cac_runbooks_have_expected_fields(self, octopus_environment):
        """Test that CaC runbooks contain expected fields."""
        from octopus import get_all_runbooks

        runbooks = asyncio.run(get_all_runbooks())
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]

        for rb in cac_runbooks:
            assert "Id" in rb
            assert "Name" in rb
            assert "ProjectId" in rb
            assert "Slug" in rb

    def test_cac_project_branches(self, octopus_environment):
        """Test that get_project_branches returns branches for the CaC project."""
        from octopus import get_all_runbooks, get_project_branches

        runbooks = asyncio.run(get_all_runbooks())
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]
        assert len(cac_runbooks) > 0

        project_id = cac_runbooks[0]["ProjectId"]
        branches = asyncio.run(get_project_branches(project_id))

        assert isinstance(branches, list)
        assert "main" in branches

    def test_cac_prompted_variables(self, octopus_environment):
        """Test that get_project_prompted_variables works for CaC projects."""
        from octopus import get_all_runbooks, get_project_prompted_variables

        runbooks = asyncio.run(get_all_runbooks())
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]
        assert len(cac_runbooks) > 0

        project_id = cac_runbooks[0]["ProjectId"]
        git_ref = cac_runbooks[0]["_git_ref"]

        prompted = asyncio.run(get_project_prompted_variables(project_id, git_ref=git_ref))

        assert len(prompted) >= 2
        names = [v["name"] for v in prompted]
        assert "DatabaseName" in names
        assert "NotifyOnCompletion" in names

    def test_cac_runbook_environments(self, octopus_environment):
        """Test that get_runbook_environments works for CaC runbooks."""
        from octopus import get_all_runbooks, get_runbook_environments

        runbooks = asyncio.run(get_all_runbooks())
        cac_runbooks = [rb for rb in runbooks if "_git_ref" in rb]
        deploy_runbook = next(rb for rb in cac_runbooks if rb["Name"] == "Deploy Service")

        environments = asyncio.run(get_runbook_environments(deploy_runbook))

        assert len(environments) >= 2

