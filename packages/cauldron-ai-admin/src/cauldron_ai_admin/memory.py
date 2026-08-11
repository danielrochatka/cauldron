"""Semantic-memory indexing contract for Admin AI.

Semantic memory is a rebuildable derived index over the canonical
conversation history stored in AdminAIRun (query_logs).  It may improve
future context retrieval, but it is *not* authoritative persistence.

Authority boundary
------------------
AdminAIRun (query_logs) = source of truth.
Semantic-memory index   = rebuildable derived data.

If the semantic index becomes incomplete or corrupted the system can
rebuild it from canonical history using ``rebuild_semantic_index``.

Failure contract
----------------
Failures in ``SemanticMemoryIndexer.index_completed_turn`` must never
reach the canonical persistence boundary.  ``AdminAIService`` calls the
indexer after the AdminAIRun save is committed, inside a try/except that
logs but does not re-raise, so an embedding provider error, timeout, or
vector-index error cannot roll back an already-successful query_logs entry.
"""
from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable


logger = logging.getLogger(__name__)


@runtime_checkable
class SemanticMemoryIndexer(Protocol):
    """Protocol for semantic-memory index backends.

    Implementations translate a completed Q&A turn into an embedding and
    upsert it into a vector store.  The method MAY raise — the caller
    (AdminAIService._finalize_success) is responsible for isolating any
    exception so it cannot roll back the canonical AdminAIRun record.
    """

    def index_completed_turn(
        self,
        *,
        run_id: str,
        question: str,
        answer: str,
    ) -> None:
        """Index one completed Q&A turn.

        Parameters
        ----------
        run_id:
            UUID string identifying the AdminAIRun row.
        question:
            The user request text (already redacted, as stored in AdminAIRun).
        answer:
            The final response text (already redacted, as stored in AdminAIRun).
        """
        ...


def rebuild_semantic_index(indexer: SemanticMemoryIndexer) -> dict:
    """Rebuild the semantic index from canonical AdminAIRun history.

    Iterates every completed AdminAIRun row (ordered oldest-first) and
    passes each turn to ``indexer.index_completed_turn``.  Individual
    failures are counted and logged; partial completion is returned to the
    caller so they can decide whether to retry.

    This function is the recovery path when the semantic index is missing,
    incomplete, or corrupted.  It proves that the index is rebuildable from
    canonical history (AdminAIRun / query_logs) and is the implementation
    of test requirement 5.

    Returns a summary dict with keys:
      - ``indexed``: number of turns successfully indexed
      - ``failed``: number of turns that raised during indexing
    """
    from .models import AdminAIRun

    indexed = 0
    failed = 0

    qs = AdminAIRun.objects.filter(
        status="completed",
    ).order_by("created_at").only(
        "run_id", "user_request", "final_response",
    )

    for run in qs.iterator():
        try:
            indexer.index_completed_turn(
                run_id=str(run.run_id),
                question=run.user_request or "",
                answer=run.final_response or "",
            )
            indexed += 1
        except Exception:
            logger.exception(
                "rebuild_semantic_index: failed to index run %s", run.run_id
            )
            failed += 1

    return {"indexed": indexed, "failed": failed}
