"""매크로 재생기 - 저장된 매크로를 변수 치환하여 실행."""

from __future__ import annotations

import re
import time
from datetime import datetime

from sap_cli.com_wrapper import SapGuiWrapper
from sap_cli.macro.schema import (
    Macro,
    MacroRunResult,
    MacroStep,
    MacroStepResult,
)
from sap_cli.macro.variables import (
    normalize_runtime_variables,
    resolve_variables_in_data,
)
from sap_cli.ontology.schema import ActionType


class MacroPlayer:
    """매크로 재생기 - 저장된 매크로를 변수 바꿔서 실행.

    주의사항 (SAP GUI API 공식 문서 기반):
    1. 버튼 Press 후 모든 요소 참조 무효화 -> 매 스텝마다 FindById 새로 호출
    2. 재생 순서는 녹화 순서와 동일해야 함
    3. session.Busy 확인 필요 (서버 통신 중 블로킹)
    4. 각 스텝 실행 후 화면번호 검증으로 안전성 확보
    """

    _VARIABLE_PATTERN = re.compile(r"\$\{(\w+)\}")

    def __init__(self, sap: SapGuiWrapper) -> None:
        self.sap = sap

    def play(
        self,
        macro: Macro,
        variables: object = None,
    ) -> MacroRunResult:
        """매크로 실행.

        1. 변수 치환: ${var_name} -> 실제 값
        2. tcode 실행
        3. 각 step 순서대로 실행
        4. 매 step마다: 화면번호 검증 -> 액션 실행 -> 상태 확인 -> 결과 기록
        5. 에러 시 즉시 중단하고 결과 반환
        """
        variables = normalize_runtime_variables(variables, macro)
        started_at = datetime.now().isoformat()
        step_results: list[MacroStepResult] = []

        effective_vars = self._effective_variables(macro, variables)
        resolved_steps = self._resolve_all_steps(macro, effective_vars)

        # tcode 실행
        if macro.tcode:
            tcode = self._resolve_variables(macro.tcode, effective_vars)
            try:
                self.sap.run_transaction(tcode)
            except Exception as e:
                return MacroRunResult(
                    macro_id=macro.id,
                    macro_name=macro.name,
                    success=False,
                    variables_used=effective_vars,
                    step_results=[],
                    total_steps=len(macro.steps),
                    completed_steps=0,
                    error_step=0,
                    started_at=started_at,
                    finished_at=datetime.now().isoformat(),
                )

        # 각 스텝 실행
        for step in resolved_steps:
            result = self._execute_step(step)
            step_results.append(result)

            if not result.success:
                return MacroRunResult(
                    macro_id=macro.id,
                    macro_name=macro.name,
                    success=False,
                    variables_used=effective_vars,
                    step_results=step_results,
                    total_steps=len(macro.steps),
                    completed_steps=len(step_results) - 1,
                    error_step=step.step_number,
                    started_at=started_at,
                    finished_at=datetime.now().isoformat(),
                )

            # wait_after 대기
            if step.wait_after > 0:
                time.sleep(step.wait_after)

        return MacroRunResult(
            macro_id=macro.id,
            macro_name=macro.name,
            success=True,
            variables_used=effective_vars,
            step_results=step_results,
            total_steps=len(macro.steps),
            completed_steps=len(step_results),
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
        )

    def dry_run(
        self,
        macro: Macro,
        variables: object = None,
    ) -> MacroRunResult:
        """실제 실행 없이 변수 치환과 검증만 수행 (프리뷰)."""
        variables = normalize_runtime_variables(variables, macro)
        started_at = datetime.now().isoformat()
        step_results: list[MacroStepResult] = []

        # 필수 변수 누락 검사
        missing = [
            v.name for v in macro.variables
            if v.required and v.name not in variables and not v.default
        ]
        if missing:
            return MacroRunResult(
                macro_id=macro.id,
                macro_name=macro.name,
                success=False,
                variables_used=variables,
                step_results=[],
                total_steps=len(macro.steps),
                completed_steps=0,
                error_step=0,
                started_at=started_at,
                finished_at=datetime.now().isoformat(),
            )

        # 각 스텝의 변수 치환 결과를 시뮬레이션
        effective_vars = self._effective_variables(macro, variables)

        for step in macro.steps:
            resolved_value = (
                self._resolve_variables(step.value, effective_vars)
                if step.value else step.value
            )
            resolved_target = self._resolve_variables(step.target_id, effective_vars)

            step_results.append(MacroStepResult(
                step_number=step.step_number,
                success=True,
                status_message=(
                    f"[DRY RUN] {step.action.value}: "
                    f"target={resolved_target}, value={resolved_value}"
                ),
            ))

        return MacroRunResult(
            macro_id=macro.id,
            macro_name=macro.name,
            success=True,
            variables_used=effective_vars,
            step_results=step_results,
            total_steps=len(macro.steps),
            completed_steps=len(step_results),
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
        )

    def _resolve_variables(self, value: str, variables: dict[str, str]) -> str:
        """${variable_name} 패턴을 실제 값으로 치환."""
        def _replace(match: re.Match[str]) -> str:
            var_name = match.group(1)
            return variables.get(var_name, match.group(0))

        return self._VARIABLE_PATTERN.sub(_replace, value)

    @staticmethod
    def _effective_variables(
        macro: Macro,
        variables: dict[str, str],
    ) -> dict[str, str]:
        effective_vars: dict[str, str] = {
            v.name: v.default for v in macro.variables if v.default
        }
        effective_vars.update(variables)
        return effective_vars

    def _resolve_all_steps(
        self,
        macro: Macro,
        variables: dict[str, str],
    ) -> list[MacroStep]:
        """모든 스텝의 변수를 치환한 복사본 반환."""
        resolved: list[MacroStep] = []
        for step in macro.steps:
            resolved_value = (
                self._resolve_variables(step.value, variables)
                if step.value else step.value
            )
            resolved_target = self._resolve_variables(step.target_id, variables)
            resolved_params = resolve_variables_in_data(step.params, variables)
            resolved.append(step.model_copy(update={
                "value": resolved_value,
                "target_id": resolved_target,
                "params": resolved_params,
            }))
        return resolved

    def _execute_step(self, step: MacroStep) -> MacroStepResult:
        """단일 스텝 실행.

        ActionType별로 com_wrapper의 적절한 메서드 호출.
        """
        try:
            action = step.action

            if action == ActionType.SET_FIELD_VALUE:
                self.sap.set_field(step.target_id, step.value or "")

            elif action == ActionType.PRESS_BUTTON:
                self.sap.press(step.target_id)

            elif action == ActionType.SEND_VKEY:
                vkey_val = step.value or step.target_id
                self.sap.send_vkey(int(vkey_val))

            elif action == ActionType.RUN_TRANSACTION:
                tcode_val = step.value or step.target_id
                self.sap.run_transaction(tcode_val)

            elif action == ActionType.TOGGLE_CHECKBOX:
                self.sap.set_checkbox(
                    step.target_id,
                    (step.value or "").lower() == "true",
                )

            elif action == ActionType.SELECT_RADIO:
                self.sap.select_radio(step.target_id)

            elif action == ActionType.SELECT_COMBO_ITEM:
                self.sap.select_combo(step.target_id, step.value or "")

            elif action == ActionType.SELECT_TAB:
                self.sap.press(step.target_id)

            elif action == ActionType.SELECT_MENU:
                self.sap.select_menu(step.target_id)

            elif action == ActionType.SELECT_TABLE_ROW:
                self.sap.select_table_row(step.target_id, int(step.value or "0"))

            elif action == ActionType.SELECT_TABLE_CELL:
                self.sap.set_current_table_cell(
                    step.target_id,
                    self._step_row(step),
                    self._step_column(step),
                )

            elif action == ActionType.CLICK_TABLE_CELL:
                self.sap.click_table_cell(
                    step.target_id,
                    self._step_row(step),
                    self._step_column(step),
                )

            elif action == ActionType.DOUBLE_CLICK_TABLE_CELL:
                self.sap.double_click_table_cell(
                    step.target_id,
                    self._step_row(step),
                    self._step_column(step),
                )

            elif action == ActionType.PERFORM_UI_ACTION:
                ui_action = str(
                    step.params.get("ui_action")
                    or step.params.get("action")
                    or step.value
                    or ""
                )
                if not ui_action:
                    raise ValueError("ui-action 매크로 단계는 value 또는 params.ui_action이 필요합니다.")
                self.sap.perform_ui_action(
                    step.target_id,
                    ui_action,
                    self._optional_str(step.params.get("action_value", step.params.get("value"))),
                    node_key=self._optional_str(step.params.get("node_key")) or "",
                    column=self._step_column(step),
                    row=self._optional_int(step.params.get("row")),
                    max_rows=int(step.params.get("max_rows", 50) or 50),
                    columns=self._optional_columns(step.params.get("columns")),
                    method_args=step.params.get("method_args"),
                    property_name=self._optional_str(step.params.get("property_name")) or "",
                    property_value=step.params.get("property_value"),
                )

            elif action == ActionType.SELECT_TREE_NODE:
                self.sap.tree_select_node(
                    step.target_id,
                    self._require_value(step, "node_key"),
                )

            elif action == ActionType.EXPAND_TREE_NODE:
                self.sap.tree_expand_node(
                    step.target_id,
                    self._require_value(step, "node_key"),
                )

            elif action == ActionType.DOUBLE_CLICK_TREE_NODE:
                self.sap.tree_double_click_node(
                    step.target_id,
                    self._require_value(step, "node_key"),
                )

            elif action == ActionType.COLLAPSE_TREE_NODE:
                self.sap.tree_collapse_node(
                    step.target_id,
                    self._require_value(step, "node_key"),
                )

            elif action == ActionType.READ_SCREEN:
                self.sap.get_screen()

            else:
                return MacroStepResult(
                    step_number=step.step_number,
                    success=False,
                    error=f"미지원 액션: {action.value}",
                )

            # 실행 후 상태 확인
            status = self.sap.get_status()
            current_screen = self._get_current_screen()

            if status.type.value == "E":
                return MacroStepResult(
                    step_number=step.step_number,
                    success=False,
                    actual_screen=current_screen,
                    status_message=status.message,
                    status_type=status.type.value,
                    error=f"SAP 에러: {status.message}",
                )

            # 실행 후 화면번호 검증 (expected_screen은 녹화 시 실행 후 저장된 값)
            if not self._verify_screen(step.expected_screen):
                return MacroStepResult(
                    step_number=step.step_number,
                    success=False,
                    actual_screen=current_screen,
                    status_message=status.message,
                    status_type=status.type.value,
                    error=f"실행 후 화면번호 불일치: 예상={step.expected_screen}, 실제={current_screen}",
                )

            return MacroStepResult(
                step_number=step.step_number,
                success=True,
                actual_screen=current_screen,
                status_message=status.message,
                status_type=status.type.value,
            )

        except Exception as e:
            return MacroStepResult(
                step_number=step.step_number,
                success=False,
                error=str(e),
            )

    @staticmethod
    def _require_value(step: MacroStep, label: str) -> str:
        """Return a required step value with a macro-friendly error message."""
        if step.value is None or step.value == "":
            raise ValueError(
                f"{step.action.value} 액션은 value에 {label} 값이 필요합니다."
            )
        return step.value

    @staticmethod
    def _optional_str(value: object) -> str | None:
        if value is None:
            return None
        return str(value)

    @staticmethod
    def _optional_int(value: object) -> int | None:
        if value in (None, ""):
            return None
        return int(value)  # type: ignore[arg-type]

    @staticmethod
    def _optional_columns(value: object) -> list[str] | None:
        if value in (None, ""):
            return None
        if isinstance(value, list):
            return [str(item) for item in value]
        return [column.strip() for column in str(value).split(",") if column.strip()]

    def _step_row(self, step: MacroStep) -> int:
        row = self._optional_int(step.params.get("row"))
        if row is not None:
            return row
        if step.value not in (None, ""):
            return int(step.value)
        raise ValueError(f"{step.action.value} 액션은 row 또는 value(row)가 필요합니다.")

    @staticmethod
    def _step_column(step: MacroStep) -> str:
        column = step.params.get("column", "")
        return "" if column is None else str(column)

    def _verify_screen(self, expected: int | None) -> bool:
        """현재 화면번호가 예상과 일치하는지 검증."""
        if expected is None:
            return True
        return self._get_current_screen() == expected

    def _get_current_screen(self) -> int:
        """현재 화면번호 조회."""
        try:
            return self.sap.get_session_info().screen_number
        except Exception:
            return 0
