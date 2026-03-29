"""MacroRecorder 단위 테스트 - COM 불필요."""

import pytest

from sap_cli.macro.recorder import MacroRecorder
from sap_cli.ontology.schema import ActionType


@pytest.fixture
def recorder():
    return MacroRecorder()


class TestRecorderLifecycle:
    def test_start_and_record(self, recorder):
        recorder.start("test_macro")
        assert recorder.is_recording is True

        step = recorder.record_step(
            action=ActionType.RUN_TRANSACTION,
            target_id="MM03",
            value="MM03",
            screen_number=100,
        )
        assert step.step_number == 1
        assert step.value == "MM03"
        assert len(recorder.current_steps) == 1

    def test_double_start_raises(self, recorder):
        recorder.start("test")
        with pytest.raises(RuntimeError):
            recorder.start("test2")

    def test_record_without_start_raises(self, recorder):
        with pytest.raises(RuntimeError):
            recorder.record_step(ActionType.PRESS_BUTTON, "btn")

    def test_undo_last(self, recorder):
        recorder.start("test")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "f1", value="v1")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "f2", value="v2")
        removed = recorder.undo_last()
        assert removed is not None
        assert removed.target_id == "f2"
        assert len(recorder.current_steps) == 1

    def test_undo_empty(self, recorder):
        recorder.start("test")
        assert recorder.undo_last() is None

    def test_cancel(self, recorder):
        recorder.start("test")
        recorder.record_step(ActionType.PRESS_BUTTON, "btn")
        recorder.cancel()
        assert recorder.is_recording is False
        assert len(recorder.current_steps) == 0


class TestFinish:
    def test_finish_creates_macro(self, recorder):
        recorder.start("test")
        recorder.record_step(ActionType.RUN_TRANSACTION, "MM03", value="MM03")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "wnd[0]/usr/ctxtRMMG1-MATNR", value="100-100")
        recorder.record_step(ActionType.SEND_VKEY, "0", value="0")

        macro = recorder.finish(name="자재조회", description="자재 마스터 조회")
        assert macro.name == "자재조회"
        assert macro.tcode == "MM03"
        assert len(macro.steps) == 3
        assert recorder.is_recording is False

    def test_finish_with_variables(self, recorder):
        recorder.start("test")
        recorder.record_step(ActionType.RUN_TRANSACTION, "MM03", value="MM03")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "wnd[0]/usr/ctxtRMMG1-MATNR", value="100-100")

        macro = recorder.finish(
            name="자재조회",
            variables={"100-100": "조회할 자재번호"},
        )
        assert len(macro.variables) == 1
        assert macro.variables[0].default == "100-100"
        # value가 ${변수명}으로 치환되었는지 확인
        assert "${" in macro.steps[1].value

    def test_tcode_auto_detect_from_target_id(self, recorder):
        """value가 None인 기존 매크로 호환: target_id에서 tcode 감지."""
        recorder.start("test")
        recorder.record_step(ActionType.RUN_TRANSACTION, "VA01", value=None)
        macro = recorder.finish(name="수주생성")
        assert macro.tcode == "VA01"


class TestAutoDetectVariables:
    def test_detects_repeated_values(self, recorder):
        recorder.start("test")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "f1", value="100-100")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "f2", value="100-100")
        recorder.record_step(ActionType.SET_FIELD_VALUE, "f3", value="UNIQUE")

        candidates = recorder.auto_detect_variables()
        assert len(candidates) >= 1
        assert any(c.default == "100-100" for c in candidates)
