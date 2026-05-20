"""dispatch_record_step의 플레이스홀더 차단 가드 테스트."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sap_cli.dispatchers.sap_actions import dispatch_record_step, _PLACEHOLDER_PATTERN


# ---------------------------------------------------------------------------
# _PLACEHOLDER_PATTERN 정규식 단위 테스트
# ---------------------------------------------------------------------------

class TestPlaceholderPattern:
    @pytest.mark.parametrize("value", [
        "${po_number}",
        "${ po_number }",
        "{{vendor_id}}",
        "{{ vendor_id }}",
        "{some_var}",
        "  ${x}  ",
    ])
    def test_matches_placeholder(self, value):
        assert _PLACEHOLDER_PATTERN.match(value), f"'{value}'는 플레이스홀더여야 합니다"

    @pytest.mark.parametrize("value", [
        "4500001234",
        "KR10",
        "100-100",
        "2026-01-01",
        "",
        "prefix_${var}",       # 앞에 텍스트가 있으면 플레이스홀더 아님
        "${var}_suffix",       # 뒤에 텍스트가 있으면 플레이스홀더 아님
    ])
    def test_no_match_on_real_values(self, value):
        assert not _PLACEHOLDER_PATTERN.match(value), f"'{value}'는 실제 값이어야 합니다"


# ---------------------------------------------------------------------------
# dispatch_record_step 플레이스홀더 차단
# ---------------------------------------------------------------------------

def _make_ctx(slot_id="0"):
    return SimpleNamespace(slot_id=slot_id)


def _make_sap_mock():
    sap = MagicMock()
    sap.get_session_info.return_value = SimpleNamespace(screen_number=100)
    return sap


def _make_recorder_mock():
    from sap_cli.ontology.schema import ActionType
    from sap_cli.macro.schema import MacroStep
    recorder = MagicMock()
    step = MacroStep(
        step_number=1,
        action=ActionType.SET_FIELD_VALUE,
        target_id="wnd[0]/usr/ctxtMAT",
        value="100-100",
    )
    recorder.record_step.return_value = step
    return recorder


class TestRecordStepPlaceholderGuard:
    @pytest.mark.parametrize("bad_value", [
        "${po_number}",
        "{{vendor}}",
        "{matnr}",
    ])
    def test_blocks_placeholder_value(self, bad_value):
        """플레이스홀더 값은 SAP 실행 없이 error 반환."""
        result = dispatch_record_step(
            args={"action": "set", "target": "wnd[0]/usr/ctxtMAT", "value": bad_value},
            ctx=_make_ctx(),
            get_sap=lambda slot_id: _make_sap_mock(),
            get_recorder=_make_recorder_mock,
            map_action=lambda a: None,
            execute_sap_action=MagicMock(side_effect=AssertionError("SAP에 실행되면 안 됨")),
            with_post_action=lambda payload, *_: payload,
        )
        assert result.get("error") is True
        assert "플레이스홀더" in result.get("message", "")

    def test_allows_real_value(self):
        """실제 값은 정상 실행."""
        sap = _make_sap_mock()
        execute = MagicMock()
        recorder = _make_recorder_mock()

        from sap_cli.ontology.schema import ActionType
        result = dispatch_record_step(
            args={"action": "set", "target": "wnd[0]/usr/ctxtMAT", "value": "4500001234"},
            ctx=_make_ctx(),
            get_sap=lambda slot_id: sap,
            get_recorder=lambda: recorder,
            map_action=lambda a: ActionType.SET_FIELD_VALUE,
            execute_sap_action=execute,
            with_post_action=lambda payload, *_: payload,
        )
        execute.assert_called_once()
        assert result.get("recorded") is True
