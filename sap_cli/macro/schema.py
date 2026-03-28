"""매크로 데이터 모델.

SAP GUI 작업 시퀀스를 녹화/재생 가능한 매크로로 정의한다.
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from sap_cli.ontology.schema import ActionType


class MacroVariable(BaseModel):
    """매크로 변수 정의. 재생 시 사용자가 값을 주입할 수 있다."""

    name: str
    description: str = ""
    default: str = ""
    required: bool = True


class MacroStep(BaseModel):
    """매크로의 한 단계."""

    step_number: int
    action: ActionType
    target_id: str
    value: str | None = None
    description: str = ""
    expected_screen: int | None = None
    wait_after: float = 0


class MacroStepResult(BaseModel):
    """단계 실행 결과."""

    step_number: int
    success: bool
    actual_screen: int = 0
    status_message: str = ""
    status_type: str = "S"
    error: str | None = None


class Macro(BaseModel):
    """완성된 매크로. 저장/로드/재생의 단위."""

    id: str
    name: str
    description: str = ""
    tcode: str = ""
    variables: list[MacroVariable] = Field(default_factory=list)
    steps: list[MacroStep] = Field(default_factory=list)
    created_at: str = ""
    updated_at: str = ""
    tags: list[str] = Field(default_factory=list)


class MacroRunResult(BaseModel):
    """매크로 실행 결과."""

    macro_id: str
    macro_name: str
    success: bool
    variables_used: dict[str, str] = Field(default_factory=dict)
    step_results: list[MacroStepResult] = Field(default_factory=list)
    total_steps: int = 0
    completed_steps: int = 0
    error_step: int | None = None
    started_at: str = ""
    finished_at: str = ""
