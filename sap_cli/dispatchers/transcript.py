"""Transcript query/plan command dispatch helpers.

Transcript command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ..auth.policy import Policy
from ..ontology.store import OntologyStore
from ..transcript_planner import TranscriptPlanner
from ..transcript_store import TranscriptStore


TRANSCRIPT_COMMANDS: frozenset[str] = frozenset(
    {
        "transcript_query",
        "transcript_plan",
    }
)


def is_transcript_command(command: str) -> bool:
    """Return whether ``command`` belongs to the transcript dispatcher group."""
    return command in TRANSCRIPT_COMMANDS


def dispatch_transcript_command(
    command: str,
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    """Dispatch a transcript command."""
    if command == "transcript_query":
        return dispatch_transcript_query(args, policy)
    if command == "transcript_plan":
        return dispatch_transcript_plan(args, policy)
    raise ValueError(f"미구현 transcript 명령: {command}")


def dispatch_transcript_query(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    """Query stored transcript entries."""
    store = TranscriptStore(policy.transcript if policy else None)
    return store.query(
        limit=int(args.get("limit", 20)),
        outcome=args.get("outcome"),
        command=args.get("command"),
        tool=args.get("tool"),
        agent=args.get("agent"),
        text=args.get("text"),
        full=bool(args.get("full", False)),
    )


def dispatch_transcript_plan(
    args: dict[str, Any],
    policy: Policy | None,
) -> dict[str, Any]:
    """Build review guidance from a stored transcript entry."""
    store = TranscriptStore(policy.transcript if policy else None)
    planner = TranscriptPlanner(store, ontology_store=make_ontology_store(policy))
    return planner.plan(
        limit=int(args.get("limit", 20)),
        index=int(args.get("index", 0)),
        outcome=args.get("outcome"),
        command=args.get("command"),
        tool=args.get("tool"),
        agent=args.get("agent"),
        text=args.get("text"),
    )


def make_ontology_store(policy: Policy | None) -> OntologyStore:
    """Create the ontology store colocated with transcript policy storage."""
    if policy is None:
        return OntologyStore()
    transcript_dir = Path(policy.transcript.log_dir).expanduser()
    knowledge_dir = transcript_dir.parent / "knowledge"
    return OntologyStore(knowledge_dir=knowledge_dir)
