"""Transcript planner 단위 테스트."""

from __future__ import annotations

from sap_cli.auth.policy import TranscriptConfig
from sap_cli.ontology.schema import ScreenOntology, TransactionOntology
from sap_cli.ontology.store import OntologyStore
from sap_cli.transcript_planner import TranscriptPlanner
from sap_cli.transcript_store import TranscriptStore


def test_plan_success_entry_returns_replay_guidance(tmp_path):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    ontology_store = OntologyStore(knowledge_dir=tmp_path / "knowledge")
    ontology = TransactionOntology(tcode="MM03", title="자재조회", related_transactions=["MM02"])
    ontology.add_screen(ScreenOntology(transaction="MM03", screen_number=4000, title="Material Display"))
    ontology_store.save_transaction(ontology)
    transcript_dir.joinpath("transcript-2026-04-01.jsonl").write_text(
        """
{"ts":"2026-04-01T00:00:00+00:00","source":"mcp","command":"run","tool":"sap_run","agent":"operator","outcome":"success","allowed":true,"approval_required":false,"permission":"execute","resource":"tx:MM03","reason":"operator on tx:*","arguments":{"tcode":"MM03"},"before":{"session":{"transaction":"SESSION_MANAGER","screen_number":100}},"after":{"session":{"transaction":"MM03","screen_number":4000},"status":{"type":"S","message":"ok"},"screen_summary":{"title":"Material Display"}}}
""".strip(),
        encoding="utf-8",
    )
    store = TranscriptStore(TranscriptConfig(log_dir=str(transcript_dir)))
    planner = TranscriptPlanner(store, ontology_store=ontology_store)

    result = planner.plan(command="run")

    assert result["found"] is True
    assert result["risk_level"] == "medium"
    assert result["replayable"] is True
    assert result["entry"]["command"] == "run"
    assert result["ontology_context"]["available"] is True
    assert result["ontology_context"]["tcode"] == "MM03"
    assert result["ontology_context"]["screen_count"] == 1
    assert result["proposed_sequence"][2]["command"] == "sap ontology MM03"
    assert result["proposed_sequence"][3]["command"] == "sap run MM03"
    assert "원본 transcript에서는 이 단계가 성공했습니다." in result["why"]
    assert any("sap ontology MM03" in item for item in result["suggested_checks"])


def test_plan_approval_required_entry_marks_non_replayable(tmp_path):
    transcript_dir = tmp_path / "transcripts"
    transcript_dir.mkdir()
    transcript_dir.joinpath("transcript-2026-04-01.jsonl").write_text(
        """
{"ts":"2026-04-01T00:00:00+00:00","source":"cli","command":"command","tool":"sap_command","agent":"operator","outcome":"approval_required","allowed":true,"approval_required":true,"permission":"execute","resource":"tx:SM30","reason":"테이블 직접 수정","arguments":{"cmd":"/nSM30"},"before":{"session":{"transaction":"SESSION_MANAGER","screen_number":100}}}
""".strip(),
        encoding="utf-8",
    )
    store = TranscriptStore(TranscriptConfig(log_dir=str(transcript_dir)))
    planner = TranscriptPlanner(store)

    result = planner.plan(tool="sap_command")

    assert result["found"] is True
    assert result["risk_level"] == "high"
    assert result["replayable"] is False
    assert result["requires_human_approval"] is True
    assert result["ontology_context"]["available"] is False
    assert result["ontology_context"]["tcode"] == "SM30"
    assert "사람 승인을 받아야 합니다." in result["preconditions"][-1]
