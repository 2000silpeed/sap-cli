"""ProcessExecutor 단위 테스트 - COM 불필요."""

import pytest
from pathlib import Path

from sap_cli.ontology.schema import (
    BusinessProcess,
    DataFlow,
    ProcessStep,
)
from sap_cli.ontology.process_executor import ProcessExecutor


@pytest.fixture
def procurement_process():
    return BusinessProcess(
        id="test_procurement",
        name="테스트 조달",
        module="MM",
        steps=[
            ProcessStep(step_number=0, tcode="MM01", description="자재 생성",
                        input_fields={"MTART": "ROH"},
                        expected_output=["MATNR"]),
            ProcessStep(step_number=1, tcode="ME21N", description="구매오더",
                        expected_output=["EBELN"]),
            ProcessStep(step_number=2, tcode="MIGO", description="입고",
                        expected_output=["MBLNR"],
                        optional=True),
        ],
        data_flows=[
            DataFlow(source_step=0, source_field="MATNR",
                     target_step=1, target_field="MATNR"),
            DataFlow(source_step=1, source_field="EBELN",
                     target_step=2, target_field="EBELN"),
        ],
    )


@pytest.fixture
def executor(procurement_process, tmp_path):
    return ProcessExecutor(procurement_process, executions_dir=tmp_path / "executions")


class TestProcessExecutorLifecycle:
    def test_start(self, executor):
        result = executor.start()
        assert result.status == "running"
        assert result.current_step == 0

    def test_complete_step(self, executor):
        executor.start()
        result = executor.complete_step(0, outputs={"MATNR": "100-100"})
        assert result.status == "success"
        assert result.tcode == "MM01"
        assert executor.execution.context["step0.MATNR"] == "100-100"

    def test_data_flow_propagation(self, executor):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        inputs = executor.get_step_inputs(1)
        assert inputs["MATNR"] == "100-100"

    def test_chained_data_flow(self, executor):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        executor.complete_step(1, outputs={"EBELN": "4500012345"})
        inputs = executor.get_step_inputs(2)
        assert inputs["EBELN"] == "4500012345"

    def test_fixed_inputs_merged(self, executor):
        executor.start()
        inputs = executor.get_step_inputs(0)
        assert inputs["MTART"] == "ROH"

    def test_finish_saves_file(self, executor, tmp_path):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        executor.complete_step(1, outputs={"EBELN": "4500012345"})
        executor.complete_step(2, outputs={"MBLNR": "5000000001"})
        path = executor.finish()
        assert path.exists()
        assert path.suffix == ".json"

    def test_auto_finish_on_last_step(self, executor):
        """모든 스텝 완료 시 상태가 정확한지."""
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        executor.complete_step(1, outputs={"EBELN": "4500012345"})
        executor.complete_step(2, outputs={"MBLNR": "5000000001"})
        assert executor.execution.status == "running"
        path = executor.finish()
        assert executor.execution.status == "completed"

    def test_skip_optional_step(self, executor):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        executor.complete_step(1, outputs={"EBELN": "4500012345"})
        result = executor.skip_step(2, "입고 불필요")
        assert result.status == "skipped"

    def test_skip_required_step_raises(self, executor):
        executor.start()
        with pytest.raises(ValueError, match="필수 스텝"):
            executor.skip_step(0)

    def test_abort(self, executor, tmp_path):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        path = executor.abort("시스템 에러")
        assert path.exists()
        assert executor.execution.status == "aborted"

    def test_get_status(self, executor):
        executor.start()
        executor.complete_step(0, outputs={"MATNR": "100-100"})
        status = executor.get_status()
        assert status["process_id"] == "test_procurement"
        assert status["completed_steps"] == 1
        assert status["total_steps"] == 3
        assert status["current_step"] == 1
        assert status["current_tcode"] == "ME21N"
        assert "MATNR" in status["pending_inputs"]


class TestDocumentExtraction:
    def test_extract_material_number(self, executor):
        assert executor.extract_document_number("Material 100-100 created") == "100-100"

    def test_extract_po_number(self, executor):
        assert executor.extract_document_number("Standard PO 4500012345 has been created") == "4500012345"

    def test_extract_material_doc(self, executor):
        assert executor.extract_document_number("Material document 5000012345 posted") == "5000012345"

    def test_extract_korean(self, executor):
        assert executor.extract_document_number("자재 100-200 생성") == "100-200"

    def test_extract_fallback_long_number(self, executor):
        assert executor.extract_document_number("Something 1234567890 done") == "1234567890"

    def test_extract_no_match(self, executor):
        assert executor.extract_document_number("No numbers here") is None


class TestInvalidStep:
    def test_complete_nonexistent_step(self, executor):
        executor.start()
        with pytest.raises(ValueError, match="스텝 99"):
            executor.complete_step(99)

    def test_get_inputs_nonexistent_step(self, executor):
        executor.start()
        with pytest.raises(ValueError, match="스텝 99"):
            executor.get_step_inputs(99)
