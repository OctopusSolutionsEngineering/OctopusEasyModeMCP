"""Unit tests for manual intervention handling and related env-var flags."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _import_main():
    """Import main.py while mocking out module-level side effects."""
    import sys

    if "main" in sys.modules:
        return sys.modules["main"]

    with patch("asyncio.run", return_value=None), \
         patch("main.register_all_runbook_tools", new=AsyncMock()):
        import main
        return main


main = _import_main()

_handle_intervention = main._handle_intervention
_handle_pending_interventions = main._handle_pending_interventions
InterventionResponse = main.InterventionResponse
InterventionNotesResponse = main.InterventionNotesResponse


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_interruption(interruption_id="Interruptions-1", title="Approve Deploy", is_pending=True):
    """Create a minimal interruption dict matching the Octopus API shape."""
    return {
        "Id": interruption_id,
        "Title": title,
        "IsPending": is_pending,
        "Form": {
            "Elements": [
                {
                    "Name": "Instructions",
                    "Control": {"Type": "Paragraph", "Text": "Please review the deployment."},
                },
                {
                    "Name": "Notes",
                    "Control": {"Type": "TextArea"},
                },
                {
                    "Name": "Result",
                    "Control": {"Type": "Select"},
                },
            ],
        },
    }


def _make_task(task_id="ServerTasks-1", description="Deploy v1"):
    return {"Id": task_id, "Description": description}


def _accepted(data):
    """Wrap data in a SimpleNamespace that mimics AcceptedElicitation."""
    from fastmcp.server.context import AcceptedElicitation
    return AcceptedElicitation(data=data)


def _make_ctx(**elicit_side_effects):
    """Create a mock context whose elicit() returns the given side effects in order.

    Each positional arg can be a value (returned) or an Exception subclass (raised).
    """
    ctx = MagicMock()
    ctx.session_id = "sess-123"

    if elicit_side_effects.get("elicit_returns") is not None:
        ctx.elicit = AsyncMock(side_effect=elicit_side_effects["elicit_returns"])
    elif elicit_side_effects.get("elicit_raises") is not None:
        ctx.elicit = AsyncMock(side_effect=elicit_side_effects["elicit_raises"])
    else:
        ctx.elicit = AsyncMock()
    return ctx


# ---------------------------------------------------------------------------
# _handle_intervention – AUTO_ASSIGN_INTERVENTIONS
# ---------------------------------------------------------------------------

class TestAutoAssignInterventions:
    """Tests for the AUTO_ASSIGN_INTERVENTIONS flag in _handle_intervention."""

    def test_auto_assign_takes_responsibility_without_elicitation(self):
        """When AUTO_ASSIGN_INTERVENTIONS=true, responsibility is taken automatically
        and the responsibility elicitation is skipped."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        # The second elicitation (action+notes) will be called; provide a response.
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Proceed", instructions="All good")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        # Responsibility was taken automatically
        mock_take.assert_awaited_once_with(client, "Interruptions-1")
        # The action/notes elicitation was still shown
        ctx.elicit.assert_awaited_once()
        # Intervention was submitted with user's choices
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        assert "All good" in payload["Notes"]
        assert result is None

    def test_no_auto_assign_prompts_for_responsibility(self):
        """When AUTO_ASSIGN_INTERVENTIONS=false, the user is asked to take responsibility."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        # First elicit: responsibility, second elicit: action+notes
        ctx = _make_ctx(elicit_returns=[
            _accepted("Assign to me"),
            _accepted(InterventionResponse(action="Proceed", instructions="")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock):
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        # User was asked twice (responsibility + action/notes)
        assert ctx.elicit.await_count == 2
        # Responsibility was taken after user accepted
        mock_take.assert_awaited_once_with(client, "Interruptions-1")
        assert result is None

    def test_no_auto_assign_user_cancels_responsibility(self):
        """When the user declines responsibility, the intervention returns Cancelled."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[_accepted("Cancel")])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is not None
        assert result["status"] == "Cancelled"
        mock_take.assert_not_awaited()
        mock_submit.assert_not_awaited()

    def test_no_auto_assign_elicitation_not_supported_auto_proceed_enabled(self):
        """When elicitation fails and AUTO_PROCEED_INTERVENTIONS=true, auto-proceed."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_raises=Exception("not supported"))

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        # Both responsibility taken and intervention submitted automatically
        mock_take.assert_awaited_once()
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        assert result is None

    def test_no_auto_assign_elicitation_not_supported_auto_proceed_disabled(self):
        """When elicitation fails and AUTO_PROCEED_INTERVENTIONS=false, return failure."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_raises=Exception("not supported"))

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is not None
        assert result["status"] == "Failed"
        assert "elicitation" in result["errorMessage"].lower()
        mock_take.assert_not_awaited()
        mock_submit.assert_not_awaited()


# ---------------------------------------------------------------------------
# _handle_intervention – AUTO_PROCEED_INTERVENTIONS
# ---------------------------------------------------------------------------

class TestAutoProceedInterventions:
    """Tests for the AUTO_PROCEED_INTERVENTIONS flag."""

    def test_auto_proceed_only_prompts_notes_with_notes_response(self):
        """When AUTO_PROCEED_INTERVENTIONS=true but AUTO_POPULATE_INTERVENTION_NOTES=false,
        the user is prompted for notes only using InterventionNotesResponse."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionNotesResponse(notes="Looks good")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        assert "Looks good" in payload["Notes"]

        # Verify the elicitation used InterventionNotesResponse
        elicit_call = ctx.elicit.call_args
        assert elicit_call.kwargs.get("response_type") is InterventionNotesResponse or \
               (len(elicit_call.args) > 1 and elicit_call.args[1] is InterventionNotesResponse)

    def test_auto_proceed_elicitation_fails_still_proceeds(self):
        """When AUTO_PROCEED_INTERVENTIONS=true and elicitation raises, auto-proceed."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_raises=Exception("not supported"))

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"

    def test_no_auto_proceed_uses_full_intervention_response(self):
        """When AUTO_PROCEED_INTERVENTIONS=false, InterventionResponse is used (action + notes)."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Reject Deployment", instructions="Not ready")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Reject Deployment"
        assert "Not ready" in payload["Notes"]

    def test_no_auto_proceed_elicitation_fails_returns_failure(self):
        """When AUTO_PROCEED_INTERVENTIONS=false and elicitation fails, return failure."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_raises=Exception("not supported"))

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is not None
        assert result["status"] == "Failed"
        mock_submit.assert_not_awaited()

    def test_no_auto_proceed_user_declines_elicitation(self):
        """When the user declines (non-AcceptedElicitation), action becomes Reject Deployment."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        # Return something that is NOT an AcceptedElicitation
        ctx = _make_ctx(elicit_returns=["declined"])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Reject Deployment"


# ---------------------------------------------------------------------------
# _handle_intervention – AUTO_POPULATE_INTERVENTION_NOTES
# ---------------------------------------------------------------------------

class TestAutoPopulateInterventionNotes:
    """Tests for the AUTO_POPULATE_INTERVENTION_NOTES flag."""

    def test_auto_populate_notes_overrides_user_notes(self):
        """When AUTO_POPULATE_INTERVENTION_NOTES=true and AUTO_PROCEED=false,
        the user is prompted for action but notes are auto-populated."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Proceed", instructions="User typed this")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", True), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        # Notes are auto-populated, NOT the user's typed value
        assert "Auto-populated via MCP" in payload["Notes"]
        assert "User typed this" not in payload["Notes"]

    def test_auto_populate_notes_false_uses_user_notes(self):
        """When AUTO_POPULATE_INTERVENTION_NOTES=false, user's notes are used."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Proceed", instructions="My notes")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        payload = mock_submit.call_args[0][2]
        assert "My notes" in payload["Notes"]

    def test_auto_populate_notes_uses_custom_value(self):
        """When AUTO_POPULATE_INTERVENTION_NOTES=true and a custom value is set, it is used."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Reject Deployment", instructions="ignored")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES_VALUE", "Standard approval note"), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        payload = mock_submit.call_args[0][2]
        assert "Standard approval note" in payload["Notes"]
        assert "ignored" not in payload["Notes"]


# ---------------------------------------------------------------------------
# _handle_intervention – Both AUTO_PROCEED + AUTO_POPULATE_NOTES
# ---------------------------------------------------------------------------

class TestBothAutoProceedAndAutoPopulateNotes:
    """Tests for when both AUTO_PROCEED_INTERVENTIONS and AUTO_POPULATE_INTERVENTION_NOTES are true."""

    def test_both_flags_skip_all_elicitation(self):
        """When both AUTO_PROCEED and AUTO_POPULATE_NOTES are true, no elicitation occurs."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx()

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", True), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        # The user was NOT prompted at all (only the responsibility elicit would be called
        # but AUTO_ASSIGN_INTERVENTIONS is also true)
        ctx.elicit.assert_not_awaited()
        # Intervention was submitted
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        assert payload["Notes"] == "Auto-populated via MCP"

    def test_both_flags_uses_custom_notes_value(self):
        """When both flags are true and a custom notes value is set, it is used."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx()

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES_VALUE", "Approved by automation"), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        payload = mock_submit.call_args[0][2]
        assert payload["Notes"] == "Approved by automation"

    def test_both_flags_no_assign_still_skips_action_notes(self):
        """When AUTO_ASSIGN=false but both proceed+notes are true:
        responsibility is asked but action/notes are fully auto-submitted.
        If the user accepts responsibility, the intervention auto-proceeds."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        # Only the responsibility elicitation fires
        ctx = _make_ctx(elicit_returns=[_accepted("Assign to me")])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", True), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", True), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        # Responsibility elicitation was shown once
        ctx.elicit.assert_awaited_once()
        mock_take.assert_awaited_once()
        # Intervention auto-submitted without further prompting
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"


# ---------------------------------------------------------------------------
# _handle_intervention – All flags false (fully interactive)
# ---------------------------------------------------------------------------

class TestFullyInteractive:
    """Tests when all auto-flags are false (fully interactive mode)."""

    def test_full_interactive_proceed(self):
        """User is asked for responsibility, then for action+notes."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted("Assign to me"),
            _accepted(InterventionResponse(action="Proceed", instructions="Ship it")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock) as mock_take, \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        assert ctx.elicit.await_count == 2
        mock_take.assert_awaited_once()
        mock_submit.assert_awaited_once()
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        assert "Ship it" in payload["Notes"]

    def test_full_interactive_reject(self):
        """User chooses to reject the deployment."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted("Assign to me"),
            _accepted(InterventionResponse(action="Reject Deployment", instructions="Rollback needed")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Reject Deployment"
        assert "Rollback needed" in payload["Notes"]

    def test_full_interactive_no_instructions(self):
        """User proceeds without providing notes."""
        client = AsyncMock()
        interruption = _make_interruption()
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted("Assign to me"),
            _accepted(InterventionResponse(action="Proceed", instructions="")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", False), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock) as mock_submit:
            result = asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        assert result is None
        payload = mock_submit.call_args[0][2]
        assert payload["Result"] == "Proceed"
        # No instructions appended
        assert "Instructions" not in payload["Notes"]


# ---------------------------------------------------------------------------
# _handle_pending_interventions
# ---------------------------------------------------------------------------

class TestHandlePendingInterventions:
    """Tests for _handle_pending_interventions."""

    def test_processes_pending_interruptions(self):
        """All pending interruptions are handled in order."""
        client = AsyncMock()
        task = _make_task()
        ctx = _make_ctx()

        interruptions = [
            _make_interruption("Int-1", is_pending=True),
            _make_interruption("Int-2", is_pending=True),
        ]

        with patch("main.get_pending_interruptions", new_callable=AsyncMock, return_value=interruptions), \
             patch("main._handle_intervention", new_callable=AsyncMock, return_value=None) as mock_handle:
            result = asyncio.run(
                _handle_pending_interventions(client, "ServerTasks-1", task, ctx)
            )

        assert result is None
        assert mock_handle.await_count == 2

    def test_skips_non_pending_interruptions(self):
        """Non-pending interruptions are skipped."""
        client = AsyncMock()
        task = _make_task()
        ctx = _make_ctx()

        interruptions = [
            _make_interruption("Int-1", is_pending=False),
            _make_interruption("Int-2", is_pending=True),
        ]

        with patch("main.get_pending_interruptions", new_callable=AsyncMock, return_value=interruptions), \
             patch("main._handle_intervention", new_callable=AsyncMock, return_value=None) as mock_handle:
            result = asyncio.run(
                _handle_pending_interventions(client, "ServerTasks-1", task, ctx)
            )

        assert result is None
        mock_handle.assert_awaited_once()
        assert mock_handle.call_args[0][1]["Id"] == "Int-2"

    def test_stops_on_first_error(self):
        """If a handler returns a result dict, processing stops."""
        client = AsyncMock()
        task = _make_task()
        ctx = _make_ctx()

        interruptions = [
            _make_interruption("Int-1", is_pending=True),
            _make_interruption("Int-2", is_pending=True),
        ]
        error_result = {"status": "Failed", "errorMessage": "User cancelled"}

        with patch("main.get_pending_interruptions", new_callable=AsyncMock, return_value=interruptions), \
             patch("main._handle_intervention", new_callable=AsyncMock, return_value=error_result) as mock_handle:
            result = asyncio.run(
                _handle_pending_interventions(client, "ServerTasks-1", task, ctx)
            )

        assert result == error_result
        # Only the first interruption was processed
        mock_handle.assert_awaited_once()

    def test_no_interruptions_returns_none(self):
        """When there are no interruptions, returns None."""
        client = AsyncMock()
        task = _make_task()
        ctx = _make_ctx()

        with patch("main.get_pending_interruptions", new_callable=AsyncMock, return_value=[]):
            result = asyncio.run(
                _handle_pending_interventions(client, "ServerTasks-1", task, ctx)
            )

        assert result is None


# ---------------------------------------------------------------------------
# _handle_intervention – message formatting
# ---------------------------------------------------------------------------

class TestInterventionMessageFormatting:
    """Tests for message construction in _handle_intervention."""

    def test_message_includes_title_and_instructions(self):
        """The elicitation message should include the title and instructions from the form."""
        client = AsyncMock()
        interruption = _make_interruption(title="Review Deployment")
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Proceed", instructions="")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock):
            asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        # The message passed to elicit should contain the title
        call_kwargs = ctx.elicit.call_args
        message = call_kwargs.kwargs.get("message") or call_kwargs.args[0]
        assert "Review Deployment" in message

    def test_message_uses_title_only_when_no_form_instructions(self):
        """When the form has no paragraph element, just the title is used."""
        client = AsyncMock()
        interruption = {
            "Id": "Int-1",
            "Title": "Quick Check",
            "IsPending": True,
            "Form": {"Elements": []},
        }
        task = _make_task()
        ctx = _make_ctx(elicit_returns=[
            _accepted(InterventionResponse(action="Proceed", instructions="")),
        ])

        with patch("main.AUTO_ASSIGN_INTERVENTIONS", True), \
             patch("main.AUTO_PROCEED_INTERVENTIONS", False), \
             patch("main.AUTO_POPULATE_INTERVENTION_NOTES", False), \
             patch("main.take_interruption_responsibility", new_callable=AsyncMock), \
             patch("main.submit_interruption", new_callable=AsyncMock):
            asyncio.run(
                _handle_intervention(client, interruption, ctx, "ServerTasks-1", task)
            )

        call_kwargs = ctx.elicit.call_args
        message = call_kwargs.kwargs.get("message") or call_kwargs.args[0]
        assert message == "Quick Check"

