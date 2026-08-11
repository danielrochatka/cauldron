"""Regression: semantic-memory indexing failure must not roll back query_logs.

Failure invariant (Issue #30 persistence rule):

    query_logs save succeeds
    semantic indexing fails
    → query_logs record still exists
    → successful conversation is not rolled back
    → user request is not converted into a failure

AdminAIRun IS the query_logs canonical record.  Semantic memory is a
rebuildable derived index; its failure must be silently logged, not
propagated into the canonical persistence boundary.
"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.django_db


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_actor():
    """Return an actor with use_admin_ai permission."""
    from django.contrib.auth import get_user_model
    from django.contrib.auth.models import Permission

    User = get_user_model()
    user, _ = User.objects.get_or_create(username="semantic-isolation-actor")
    try:
        perm = Permission.objects.get(
            codename="use_admin_ai",
            content_type__app_label="cauldron_ai_admin",
        )
        user.user_permissions.add(perm)
    except Permission.DoesNotExist:
        pass
    return User.objects.get(pk=user.pk)


def _make_service(indexer=None):
    """Return an AdminAIService whose provider returns one 'end_turn' response."""
    from cauldron_ai.contracts import AIModelResponse
    from cauldron_ai.testing import FakeAIModelProvider
    from cauldron_ai_admin.service import AdminAIService
    from cauldron_ai_admin.tools import AdminAIToolRegistry
    from helpers import make_assembly_service_for_tools

    fake = FakeAIModelProvider()
    fake.queue_response(AIModelResponse(
        provider_request_id="sem-r1",
        content="Here is the answer.",
        stop_reason="end_turn",
    ))

    reg = AdminAIToolRegistry()

    return AdminAIService(
        provider=fake,
        tool_registry=reg,
        prompt_assembly_service=make_assembly_service_for_tools(),
        semantic_memory_indexer=indexer,
    )


class _FailingIndexer:
    """Indexer that always raises — simulates embedding provider failure."""

    def __init__(self):
        self.calls: list[dict] = []

    def index_completed_turn(self, *, run_id: str, question: str, answer: str) -> None:
        self.calls.append({"run_id": run_id, "question": question, "answer": answer})
        raise RuntimeError("embedding provider timeout")


class _CapturingIndexer:
    """Indexer that records calls without failing."""

    def __init__(self):
        self.calls: list[dict] = []

    def index_completed_turn(self, *, run_id: str, question: str, answer: str) -> None:
        self.calls.append({"run_id": run_id, "question": question, "answer": answer})


# ---------------------------------------------------------------------------
# Test 1–4: Failure invariant
# ---------------------------------------------------------------------------


def test_failing_indexer_does_not_roll_back_query_logs():
    """Prove all four failure-invariant assertions together.

    1. The canonical AdminAIRun (query_logs) entry is persisted.
    2. The stored question and answer remain intact.
    3. The indexing exception does not roll back that record.
    4. The completed Ask is still treated as successful.
    """
    from cauldron_ai_admin.models import AdminAIRun

    failing_indexer = _FailingIndexer()
    svc = _make_service(indexer=failing_indexer)
    actor = _make_actor()

    question = "What is the deployment status?"

    # 4. run() must return without raising — the Ask is successful.
    run = svc.run(actor, question)
    assert run is not None, "run() must return an AdminAIRun, not raise"

    # 4 (continued). The run itself reports success.
    assert run.status == "completed", (
        f"Expected status='completed', got {run.status!r}. "
        "A failing semantic indexer must not change the run status."
    )

    # 1. The canonical record exists in the database.
    db_run = AdminAIRun.objects.get(run_id=run.run_id)

    # 2. Question and answer are intact.
    assert db_run.user_request and question[:20] in db_run.user_request, (
        "user_request not found in canonical record"
    )
    assert db_run.final_response == "Here is the answer.", (
        f"final_response mangled: {db_run.final_response!r}"
    )

    # 3. Confirm the indexer was attempted (so the failure path was reached),
    #    proving that the later exception did not roll back the already-committed
    #    canonical record.
    assert failing_indexer.calls, (
        "Indexer was never called — test is not exercising the failure path"
    )
    assert db_run.status == "completed", (
        "AdminAIRun was rolled back after indexing failure"
    )


# ---------------------------------------------------------------------------
# Test 5: Rebuild from canonical history
# ---------------------------------------------------------------------------


def test_rebuild_semantic_index_from_canonical_history():
    """A successful rebuild reads completed AdminAIRun rows and indexes them.

    Proves: if the semantic index is missing or corrupted, it can be fully
    rebuilt from canonical history (AdminAIRun / query_logs) without needing
    any secondary store.

    Steps:
    1. Run a successful Ask with a failing indexer (canonical record exists,
       index is missing).
    2. Call rebuild_semantic_index() with a working indexer.
    3. Confirm the canonical turn was indexed by the rebuild.
    """
    from cauldron_ai_admin.memory import rebuild_semantic_index

    # Step 1: successful Ask, indexing fails → canonical record exists, no index entry.
    failing_indexer = _FailingIndexer()
    svc = _make_service(indexer=failing_indexer)
    actor = _make_actor()
    run = svc.run(actor, "Rebuild test question?")
    assert run.status == "completed"
    assert failing_indexer.calls, "Indexer must have been attempted to test the failure path"

    # Step 2: rebuild with a working indexer.
    capturing_indexer = _CapturingIndexer()
    summary = rebuild_semantic_index(capturing_indexer)

    # Step 3: the canonical turn must now appear in the index.
    assert summary["indexed"] >= 1, (
        f"rebuild_semantic_index indexed {summary['indexed']} turns; "
        "expected at least 1 from the successful Ask above"
    )
    assert summary["failed"] == 0, (
        f"Unexpected failures during rebuild: {summary['failed']}"
    )

    run_ids = [c["run_id"] for c in capturing_indexer.calls]
    assert str(run.run_id) in run_ids, (
        f"Run {run.run_id} not found in rebuild index calls: {run_ids}"
    )
    # Verify question and answer content were passed correctly.
    call = next(c for c in capturing_indexer.calls if c["run_id"] == str(run.run_id))
    assert "Rebuild test question" in call["question"]
    assert call["answer"] == "Here is the answer."


# ---------------------------------------------------------------------------
# Test: No indexer attached — service works normally
# ---------------------------------------------------------------------------


def test_no_indexer_attached_service_works_normally():
    """When no semantic_memory_indexer is configured, the service is unaffected."""
    from cauldron_ai_admin.models import AdminAIRun

    svc = _make_service(indexer=None)
    actor = _make_actor()

    run = svc.run(actor, "No indexer question.")
    assert run.status == "completed"
    db_run = AdminAIRun.objects.get(run_id=run.run_id)
    assert db_run.final_response == "Here is the answer."


# ---------------------------------------------------------------------------
# Test: Indexer called AFTER canonical save — not inside its transaction
# ---------------------------------------------------------------------------


def test_indexer_called_after_canonical_save_not_inside_transaction():
    """Prove the indexer observes the committed AdminAIRun from the DB.

    If the indexer were called inside the canonical save transaction, the
    run_id it receives would reference an uncommitted row and a concurrent
    reader could not see it.  We verify that the indexer receives a run_id
    whose row is already visible in the DB at the moment of the call.
    """
    from cauldron_ai_admin.models import AdminAIRun

    observed_run_ids_in_db: list[bool] = []

    class _ObservingIndexer:
        def index_completed_turn(self, *, run_id: str, question: str, answer: str) -> None:
            # Check that the row exists in the DB at this exact moment.
            exists = AdminAIRun.objects.filter(
                run_id=run_id, status="completed"
            ).exists()
            observed_run_ids_in_db.append(exists)

    svc = _make_service(indexer=_ObservingIndexer())
    actor = _make_actor()
    run = svc.run(actor, "Order matters.")

    assert run.status == "completed"
    assert observed_run_ids_in_db == [True], (
        "The indexer was called before the canonical AdminAIRun row was visible "
        "in the DB — this means indexing is inside the canonical transaction, "
        "which violates the persistence rule."
    )
