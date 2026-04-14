"""업무 프로세스 실행 엔진.

BusinessProcess의 스텝을 순서대로 실행하면서:
1. DataFlow 기반으로 앞 스텝 출력 → 뒤 스텝 입력 자동 연결
2. 각 스텝의 실행 결과와 추출된 출력값을 기록
3. 완료된 실행 기록을 JSON으로 저장
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .schema import (
    BusinessProcess,
    ProcessExecution,
    SapStatusMessage,
    StepResult,
)


class ProcessExecutor:
    """업무 프로세스 실행기.

    AI 에이전트가 직접 SAP를 조작하는 게 아니라,
    실행 컨텍스트(스텝 간 데이터 전달)를 관리하는 역할.

    사용 흐름:
        executor = ProcessExecutor(process, executions_dir)
        executor.start()

        # AI가 스텝 0 실행 후:
        executor.complete_step(0, outputs={"MATNR": "100-100"})

        # 다음 스텝의 입력값 조회:
        inputs = executor.get_step_inputs(1)
        # → {"MATNR": "100-100"}  (DataFlow 기반)

        # AI가 스텝 1 실행 후:
        executor.complete_step(1, outputs={"EBELN": "4500012345"})

        # 실행 기록 저장:
        executor.finish()
    """

    def __init__(
        self,
        process: BusinessProcess,
        executions_dir: str | Path | None = None,
    ):
        self.process = process
        if executions_dir is None:
            executions_dir = Path.home() / ".sap_cli" / "executions"
        self.executions_dir = Path(executions_dir)
        self.executions_dir.mkdir(parents=True, exist_ok=True)

        self.execution = ProcessExecution(
            process_id=process.id,
            process_name=process.name,
        )

    def start(self) -> ProcessExecution:
        """프로세스 실행 시작."""
        self.execution.started_at = _now_iso()
        self.execution.status = "running"
        first_step = min(s.step_number for s in self.process.steps)
        self.execution.current_step = first_step
        self.execution.context = {}
        self.execution.step_results = []
        return self.execution

    def get_step_inputs(self, step_number: int) -> dict[str, str]:
        """DataFlow 기반으로 이 스텝에 입력해야 할 값들을 반환.

        고정 입력값(input_fields) + DataFlow에서 온 값을 합친다.
        """
        step = self._find_step(step_number)
        inputs: dict[str, str] = dict(step.input_fields)

        for df in self.process.data_flows:
            if df.target_step == step_number:
                source_key = f"step{df.source_step}.{df.source_field}"
                if source_key in self.execution.context:
                    inputs[df.target_field] = self.execution.context[source_key]

        return inputs

    def complete_step(
        self,
        step_number: int,
        *,
        outputs: dict[str, str] | None = None,
        status: str = "success",
        error: str = "",
    ) -> StepResult:
        """스텝 실행 완료를 기록하고 출력값을 컨텍스트에 저장."""
        step = self._find_step(step_number)
        result = StepResult(
            step_number=step_number,
            tcode=step.tcode,
            status=status,
            outputs=outputs or {},
            error=error,
            timestamp=_now_iso(),
        )
        self.execution.step_results.append(result)

        if outputs:
            for key, value in outputs.items():
                self.execution.context[f"step{step_number}.{key}"] = value

        next_steps = [s for s in self.process.steps if s.step_number > step_number]
        if next_steps:
            self.execution.current_step = min(s.step_number for s in next_steps)

        return result

    def skip_step(self, step_number: int, reason: str = "") -> StepResult:
        """optional 스텝을 건너뛴다."""
        step = self._find_step(step_number)
        if not step.optional:
            raise ValueError(f"스텝 {step_number}은 필수 스텝이라 건너뛸 수 없습니다")
        return self.complete_step(step_number, status="skipped", error=reason)

    def finish(self, status: str = "completed") -> Path:
        """실행 완료 후 결과를 JSON으로 저장."""
        self.execution.finished_at = _now_iso()
        self.execution.status = status
        return self._save()

    def abort(self, reason: str = "") -> Path:
        """실행 중단."""
        self.execution.finished_at = _now_iso()
        self.execution.status = "aborted"
        if reason:
            last_step = self.execution.current_step
            self.complete_step(last_step, status="error", error=reason)
        return self._save()

    def get_status(self) -> dict[str, Any]:
        """현재 실행 상태 요약."""
        total = len(self.process.steps)
        completed = len([r for r in self.execution.step_results if r.status == "success"])
        failed = len([r for r in self.execution.step_results if r.status == "error"])

        pending_inputs = {}
        if self.execution.status == "running":
            pending_inputs = self.get_step_inputs(self.execution.current_step)

        return {
            "process_id": self.process.id,
            "process_name": self.process.name,
            "status": self.execution.status,
            "total_steps": total,
            "completed_steps": completed,
            "failed_steps": failed,
            "current_step": self.execution.current_step,
            "current_tcode": self._find_step(self.execution.current_step).tcode if self.execution.status == "running" else None,
            "context": dict(self.execution.context),
            "pending_inputs": pending_inputs,
        }

    def extract_document_number(self, status_message: str) -> str | None:
        """SAP 상태바 메시지에서 문서번호를 추출.

        예: "Material 100-100 created" → "100-100"
             "Standard PO 4500012345 has been created" → "4500012345"
             "Material document 5000012345 posted" → "5000012345"
        """
        patterns = [
            r"(?:Material|자재)\s+(\S+)\s+(?:created|생성)",
            r"(?:PO|Purchase order|구매오더)\s+(\d+)",
            r"(?:Material document|자재문서)\s+(\d+)",
            r"(?:Document|문서)\s+(\d+)",
            r"(\d{10,})",  # 10자리 이상 숫자 fallback
        ]
        for pattern in patterns:
            match = re.search(pattern, status_message, re.IGNORECASE)
            if match:
                return match.group(1)
        return None

    def _find_step(self, step_number: int) -> Any:
        for step in self.process.steps:
            if step.step_number == step_number:
                return step
        raise ValueError(f"스텝 {step_number}이 프로세스에 없습니다")

    def _save(self) -> Path:
        ts = self.execution.started_at.replace(":", "").replace("-", "")[:15]
        filename = f"{self.process.id}_{ts}.json"
        path = self.executions_dir / filename
        data = self.execution.model_dump(mode="json", exclude_none=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
