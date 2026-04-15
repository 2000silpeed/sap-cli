"""Phase 14-2 Session Analyzer 테스트."""

from __future__ import annotations

import pytest

from sap_cli.ontology.schema import McpSession, SessionStep
from sap_cli.session_analyzer import (
    analyze,
    extract_core_flow,
    mark_candidate,
    mark_exploration,
    mark_recovery,
    remove_retries,
)


def _step(
    num: int,
    command: str = "press",
    tool_name: str = "sap_press",
    error: str = "",
    arguments: dict | None = None,
    before_tx: str = "",
    after_tx: str = "",
    before_screen: int = 0,
    after_screen: int = 0,
) -> SessionStep:
    return SessionStep(
        step_number=num,
        command=command,
        tool_name=tool_name,
        error=error,
        arguments=arguments or {},
        before_transaction=before_tx,
        after_transaction=after_tx,
        before_screen=before_screen,
        after_screen=after_screen,
    )


def _session(steps: list[SessionStep], status: str = "completed", outcome: str = "success") -> McpSession:
    return McpSession(
        id="aabbccddeeff",
        started_at="2026-04-15T00:00:00+00:00",
        status=status,
        outcome=outcome,
        steps=steps,
    )


# --- 1. 탐색 단계 감지 ---

class TestMarkExploration:
    def test_sap_screen_tool_marked(self):
        steps = [_step(0, "screen", "sap_screen"), _step(1, "set", "sap_set_field")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True
        assert steps[1].is_exploration is False

    def test_sap_get_status_marked(self):
        steps = [_step(0, "status", "sap_get_status")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_sap_modal_marked(self):
        steps = [_step(0, "modal", "sap_modal")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_sap_table_read_marked(self):
        steps = [_step(0, "table", "sap_table_read")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_sap_get_field_marked(self):
        steps = [_step(0, "get", "sap_get_field")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_sap_tree_read_marked(self):
        steps = [_step(0, "tree", "sap_tree_read")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_command_fallback_when_tool_name_custom(self):
        steps = [_step(0, "screen", "custom_tool")]
        mark_exploration(steps)
        assert steps[0].is_exploration is True

    def test_exploration_with_error_not_marked(self):
        steps = [_step(0, "screen", "sap_screen", error="timeout")]
        mark_exploration(steps)
        assert steps[0].is_exploration is False

    def test_non_exploration_command_not_marked(self):
        steps = [_step(0, "set", "sap_set_field"), _step(1, "press", "sap_press")]
        mark_exploration(steps)
        assert all(not s.is_exploration for s in steps)


# --- 2. 에러 복구 감지 ---

class TestMarkRecovery:
    def test_error_step_marked(self):
        steps = [_step(0, error="SAP error"), _step(1)]
        mark_recovery(steps)
        assert steps[0].is_recovery is True

    def test_cancel_vkey_after_error_with_key_arg(self):
        """실제 데이터에서 vkey 인자는 'key'로 기록됨."""
        steps = [
            _step(0, error="field not found"),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(2, "set", "sap_set_field"),
        ]
        mark_recovery(steps)
        assert steps[0].is_recovery is True
        assert steps[1].is_recovery is True
        assert steps[2].is_recovery is False

    def test_cancel_vkey_with_vkey_arg_also_works(self):
        """vkey 인자명으로도 동작 (하위 호환)."""
        steps = [
            _step(0, error="err"),
            _step(1, "vkey", "sap_send_vkey", arguments={"vkey": 12}),
        ]
        mark_recovery(steps)
        assert steps[1].is_recovery is True

    def test_n_action_after_error(self):
        steps = [
            _step(0, error="confirm?"),
            _step(1, arguments={"action": "n"}),
        ]
        mark_recovery(steps)
        assert steps[0].is_recovery is True
        assert steps[1].is_recovery is True

    def test_vkey3_back(self):
        steps = [
            _step(0, error="err"),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 3}),
        ]
        mark_recovery(steps)
        assert steps[1].is_recovery is True

    def test_vkey15_not_cancel(self):
        """vkey 15(F1 Help)는 cancel이 아님."""
        steps = [
            _step(0, error="err"),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 15}),
        ]
        mark_recovery(steps)
        assert steps[0].is_recovery is True
        assert steps[1].is_recovery is False

    def test_no_error_no_recovery(self):
        steps = [_step(0), _step(1)]
        mark_recovery(steps)
        assert all(not s.is_recovery for s in steps)

    def test_recovery_chain_stops_at_non_cancel(self):
        steps = [
            _step(0, error="err"),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(3, "set", "sap_set_field"),
        ]
        mark_recovery(steps)
        assert steps[0].is_recovery is True
        assert steps[1].is_recovery is True
        assert steps[2].is_recovery is True
        assert steps[3].is_recovery is False

    def test_recovery_window_max_3_lookahead(self):
        steps = [
            _step(0, error="err"),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(3, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(4, "vkey", "sap_send_vkey", arguments={"key": 12}),
        ]
        mark_recovery(steps)
        assert steps[3].is_recovery is True
        assert steps[4].is_recovery is False


# --- 3. 반복 제거 ---

class TestRemoveRetries:
    def test_consecutive_same_command_keeps_last(self):
        steps = [
            _step(0, "set", "sap_set_field", arguments={"element_id": "A", "value": "1"}),
            _step(1, "set", "sap_set_field", arguments={"element_id": "A", "value": "1"}),
            _step(2, "set", "sap_set_field", arguments={"element_id": "A", "value": "1"}),
        ]
        result = remove_retries(steps)
        assert len(result) == 1
        assert result[0].step_number == 2

    def test_different_commands_preserved(self):
        steps = [
            _step(0, "set", "sap_set_field", arguments={"element_id": "A"}),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        result = remove_retries(steps)
        assert len(result) == 2

    def test_same_command_different_args_preserved(self):
        steps = [
            _step(0, "set", "sap_set_field", arguments={"element_id": "A", "value": "1"}),
            _step(1, "set", "sap_set_field", arguments={"element_id": "A", "value": "2"}),
        ]
        result = remove_retries(steps)
        assert len(result) == 2

    def test_same_command_different_screen_preserved(self):
        """같은 명령이라도 다른 화면에서 실행되면 중복이 아님."""
        steps = [
            _step(0, "vkey", "sap_send_vkey", arguments={"key": 0}, before_tx="VA01", before_screen=100),
            _step(1, "vkey", "sap_send_vkey", arguments={"key": 0}, before_tx="VA01", before_screen=200),
        ]
        result = remove_retries(steps)
        assert len(result) == 2

    def test_empty_list(self):
        assert remove_retries([]) == []


# --- 4. 핵심 흐름 추출 ---

class TestExtractCoreFlow:
    def test_filters_exploration_and_recovery(self):
        steps = [_step(0), _step(1), _step(2), _step(3)]
        steps[0].is_exploration = True
        steps[2].is_recovery = True
        core = extract_core_flow(steps)
        assert [s.step_number for s in core] == [1, 3]

    def test_all_core(self):
        steps = [_step(0), _step(1)]
        assert len(extract_core_flow(steps)) == 2


# --- 5. candidate 마킹 ---

class TestMarkCandidate:
    def test_success_with_tcode_and_3plus_steps(self):
        steps = [
            _step(0, "run", "sap_run", after_tx="VA01"),
            _step(1, "set", "sap_set_field"),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps)
        mark_candidate(session)
        assert session.candidate is True

    def test_not_completed_not_candidate(self):
        steps = [
            _step(0, "run", "sap_run", after_tx="VA01"),
            _step(1, "set", "sap_set_field"),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps, status="running", outcome="")
        mark_candidate(session)
        assert session.candidate is False

    def test_failed_not_candidate(self):
        steps = [
            _step(0, "run", "sap_run", after_tx="VA01"),
            _step(1, "set", "sap_set_field"),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps, outcome="failure")
        mark_candidate(session)
        assert session.candidate is False

    def test_too_few_core_steps(self):
        steps = [
            _step(0, "run", "sap_run", after_tx="VA01"),
            _step(1, "set", "sap_set_field"),
        ]
        session = _session(steps)
        mark_candidate(session)
        assert session.candidate is False

    def test_no_tcode_not_candidate(self):
        steps = [
            _step(0, "set", "sap_set_field"),
            _step(1, "set", "sap_set_field"),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps)
        mark_candidate(session)
        assert session.candidate is False

    def test_exploration_excluded_from_core_count(self):
        steps = [
            _step(0, "screen", "sap_screen"),
            _step(1, "run", "sap_run", after_tx="VA01"),
            _step(2, "set", "sap_set_field"),
        ]
        mark_exploration(steps)
        session = _session(steps)
        mark_candidate(session)
        assert session.candidate is False


# --- 6. 통합 analyze ---

class TestAnalyze:
    def test_full_pipeline(self):
        steps = [
            _step(0, "screen", "sap_screen"),
            _step(1, "run", "sap_run", after_tx="MM01"),
            _step(2, "set", "sap_set_field", error="field locked"),
            _step(3, "vkey", "sap_send_vkey", arguments={"key": 12}),
            _step(4, "set", "sap_set_field", arguments={"element_id": "MATNR", "value": "100"}),
            _step(5, "set", "sap_set_field", arguments={"element_id": "MATNR", "value": "100"}),
            _step(6, "vkey", "sap_send_vkey", arguments={"key": 0}),
            _step(7, "status", "sap_get_status"),
        ]
        session = _session(steps)
        result = analyze(session)

        assert result["exploration_count"] == 2
        assert result["recovery_count"] == 2
        assert result["candidate"] is True
        assert result["deduplicated_steps"] >= 3

    def test_analyze_returns_core_flow(self):
        steps = [
            _step(0, "run", "sap_run", after_tx="VA01"),
            _step(1, "set", "sap_set_field"),
            _step(2, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps)
        result = analyze(session)
        assert len(result["core_flow"]) == 3
        assert result["candidate"] is True

    def test_analyze_idempotent(self):
        """analyze를 두 번 호출해도 같은 결과."""
        steps = [
            _step(0, "screen", "sap_screen"),
            _step(1, "run", "sap_run", after_tx="VA01"),
            _step(2, "set", "sap_set_field"),
            _step(3, "vkey", "sap_send_vkey", arguments={"key": 0}),
        ]
        session = _session(steps)
        r1 = analyze(session)
        r2 = analyze(session)
        assert r1 == r2
