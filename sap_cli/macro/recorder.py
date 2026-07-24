"""채팅 기반 매크로 녹화기.

사용자가 CLI를 통해 명령을 실행하면 각 명령을 MacroStep으로 기록한다.
녹화 완료 후 변수를 지정하여 재사용 가능한 Macro를 생성한다.
"""

from __future__ import annotations

import json
import re
import uuid
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sap_cli.ontology.schema import ActionType
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable
from sap_cli.macro.variables import (
    normalize_variable_name,
    replace_literal_in_data,
    value_from_mapping,
)
from sap_cli.storage import atomic_write_json

# 변수 치환 불필요한 액션 (값이 시스템 식별자/네비게이션인 경우)
_NON_VARIABLE_ACTIONS = {
    ActionType.RUN_TRANSACTION,
    ActionType.PRESS_BUTTON,
    ActionType.SEND_VKEY,
    ActionType.SELECT_TAB,
    ActionType.SELECT_MENU,
    ActionType.READ_SCREEN,
    ActionType.EXPAND_TREE_NODE,
    ActionType.COLLAPSE_TREE_NODE,
    ActionType.CLICK_TABLE_CELL,
    ActionType.DOUBLE_CLICK_TABLE_CELL,
}

# 변수로 추출하지 않을 짧은 값
_MIN_VALUE_LENGTH = 2

# AI가 finish 단계에서 실제 값 대신 placeholder 자체를 넘기는 경우 방지.
_PLACEHOLDER_ONLY_PATTERN = re.compile(
    r"^\s*(?:\$\{\w+\}|\{\{\s*[\w.-]+\s*\}\}|\{[\w.-]+\})\s*$"
)


_DEFAULT_STATE_FILE = Path.home() / ".sap_cli" / ".recorder_state.json"


class MacroRecorder:
    """채팅 기반 매크로 녹화기.

    CLI에서 각 명령이 별도 프로세스로 실행되므로,
    녹화 상태를 파일로 영속화하여 프로세스 간 상태 유지.
    """

    def __init__(self, state_file: Path | None = None) -> None:
        self._recording: bool = False
        self._steps: list[MacroStep] = []
        self._tcode: str = ""
        self._name: str = ""
        self._state_file = state_file or _DEFAULT_STATE_FILE
        self._load_state()

    # ------------------------------------------------------------------
    # 녹화 제어
    # ------------------------------------------------------------------

    def start(self, name: str, tcode: str = "") -> None:
        """녹화 시작."""
        if self._recording:
            raise RuntimeError("이미 녹화 중입니다. finish() 또는 cancel()을 먼저 호출하세요.")
        self._recording = True
        self._steps = []
        self._tcode = tcode
        self._name = name
        self._save_state()

    def record_step(
        self,
        action: ActionType,
        target_id: str,
        value: str | None = None,
        description: str = "",
        screen_number: int | None = None,
        params: dict[str, Any] | None = None,
    ) -> MacroStep:
        """단계 기록."""
        if not self._recording:
            raise RuntimeError("녹화가 시작되지 않았습니다.")

        step = MacroStep(
            step_number=len(self._steps) + 1,
            action=action,
            target_id=target_id,
            value=value,
            description=description,
            expected_screen=screen_number,
            params=params or {},
        )
        self._steps.append(step)
        self._save_state()
        return step

    def undo_last(self) -> MacroStep | None:
        """마지막 단계 취소."""
        if not self._steps:
            return None
        removed = self._steps.pop()
        self._save_state()
        return removed

    def undo(self, count: int = 1) -> list[MacroStep]:
        """Remove the most recent ``count`` recorded steps."""
        if count < 1:
            raise ValueError("undo count must be at least 1")
        removed: list[MacroStep] = []
        for _ in range(min(count, len(self._steps))):
            removed.append(self._steps.pop())
        if removed:
            self._save_state()
        return removed

    def finish(
        self,
        name: str,
        description: str = "",
        variables: dict[str, Any] | None = None,
    ) -> Macro:
        """녹화 종료 및 Macro 생성.

        Args:
            name: 매크로 이름
            description: 매크로 설명
            variables: 선택 변수 지정. 지정값을 먼저 반영한 뒤, 남은 사용자
                입력값은 매크로 등록 정책에 따라 항상 자동 변수화한다.
        """
        if not self._recording:
            raise RuntimeError("녹화가 시작되지 않았습니다.")

        now = datetime.now(timezone.utc).isoformat()
        macro_vars: list[MacroVariable] = []

        # step_number 재정렬
        for i, step in enumerate(self._steps, 1):
            step.step_number = i

        # tcode 자동 감지 (첫 RUN_TRANSACTION에서)
        if not self._tcode:
            for step in self._steps:
                if step.action == ActionType.RUN_TRANSACTION:
                    self._tcode = step.value or step.target_id
                    if self._tcode:
                        break

        # 변수 치환
        # 지원 형식:
        #   1) {"변수명": "실제값"}
        #   2) {"변수명": {"value": "실제값", "desc": "설명"}}
        #   3) {"실제값": "설명"} (레거시)
        if isinstance(variables, dict) and set(variables) <= {"variables", "vars", "values"}:
            variables = (
                variables.get("variables")
                or variables.get("vars")
                or variables.get("values")
            )

        if variables:
            if isinstance(variables, list):
                for item in variables:
                    if isinstance(item, dict):
                        raw_actual = value_from_mapping(item, "value", "actual", "default")
                        raw_desc = value_from_mapping(item, "desc", "description")
                        var_name = self._normalize_var_name(
                            str(value_from_mapping(item, "name", "key", "id", "variable") or "")
                        )
                        actual_value = "" if raw_actual is None else str(raw_actual)
                        var_desc = actual_value if raw_desc is None else str(raw_desc)
                    elif isinstance(item, str) and ("=" in item or ":" in item):
                        sep = "=" if "=" in item else ":"
                        raw_name, raw_value = item.split(sep, 1)
                        var_name = self._normalize_var_name(raw_name)
                        actual_value = raw_value
                        var_desc = raw_value
                    else:
                        continue

                    self._add_variable_replacement(
                        macro_vars,
                        var_name,
                        var_desc,
                        actual_value,
                    )
            elif not isinstance(variables, dict):
                pass
            else:
                for key, val in variables.items():
                    # 형식 2: 값이 dict이면 {"value": ..., "desc": ...}
                    if isinstance(val, dict):
                        raw_actual = value_from_mapping(val, "value", "actual", "default")
                        raw_desc = value_from_mapping(val, "desc", "description")
                        var_name = self._normalize_var_name(str(key))
                        actual_value = "" if raw_actual is None else str(raw_actual)
                        var_desc = actual_value if raw_desc is None else str(raw_desc)
                        if not self._can_replace_value(actual_value):
                            continue
                    else:
                        key = str(key)
                        val = str(val)
                        val_in_steps = self._literal_exists_in_steps(val)
                        key_in_steps = self._literal_exists_in_steps(key)

                        if val_in_steps:
                            var_name = self._normalize_var_name(key)
                            actual_value = val
                            var_desc = val
                        elif key_in_steps:
                            targets = [
                                s.target_id for s in self._steps
                                if self._literal_exists_in_step(s, key)
                            ]
                            var_name = self._derive_var_name(key, targets)
                            actual_value = key
                            var_desc = val
                        else:
                            continue

                        if not self._can_replace_value(actual_value):
                            continue

                    self._add_variable_replacement(
                        macro_vars,
                        var_name,
                        var_desc,
                        actual_value,
                    )

        # 매크로 등록 정책: explicit vars가 없어도 안전한 사용자 입력값은 항상 변수화한다.
        for candidate in self.auto_detect_variables():
            self._add_variable_replacement(
                macro_vars,
                candidate.name,
                candidate.description,
                candidate.default,
                required=True,
            )

        macro = Macro(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            tcode=self._tcode,
            variables=macro_vars,
            steps=list(self._steps),
            output_schema=[
                {
                    "name": str(step.params.get("as") or step.params.get("name") or step.target_id),
                    "field": step.target_id,
                }
                for step in self._steps
                if step.action == ActionType.READ_FIELD
            ],
            created_at=now,
            updated_at=now,
        )

        self._recording = False
        self._steps = []
        self._tcode = ""
        self._name = ""
        self._clear_state()

        return macro

    def cancel(self) -> None:
        """녹화 취소."""
        self._recording = False
        self._steps = []
        self._tcode = ""
        self._name = ""
        self._clear_state()

    # ------------------------------------------------------------------
    # 속성
    # ------------------------------------------------------------------

    @property
    def is_recording(self) -> bool:
        return self._recording

    @property
    def current_steps(self) -> list[MacroStep]:
        return list(self._steps)

    # ------------------------------------------------------------------
    # 변수 자동 감지
    # ------------------------------------------------------------------

    def auto_detect_variables(self) -> list[MacroVariable]:
        """녹화된 steps에서 변수 후보를 자동 추출.

        기준:
        - 매크로 등록 시 안전한 사용자 입력값은 1회 사용이어도 변수 후보로 본다.
        - 사용자 입력으로 보이는 값(SET_FIELD_VALUE, SELECT_COMBO_ITEM 등)을 대상으로 한다.
        - 트랜잭션 실행/버튼/VKey/탭/메뉴 같은 시스템 네비게이션 값은 제외한다.
        """
        value_counter: Counter[str] = Counter()
        value_targets: dict[str, list[str]] = {}

        for step in self._steps:
            if step.action in _NON_VARIABLE_ACTIONS:
                continue
            values: list[tuple[str, str]] = []
            if step.value and step.action != ActionType.PERFORM_UI_ACTION:
                values.append((step.value, step.target_id))
            for key in ("value", "action_value", "property_value"):
                param_value = step.params.get(key)
                if isinstance(param_value, str):
                    target_hint = str(step.params.get("column") or key)
                    values.append((param_value, f"{step.target_id}/{target_hint}"))
            for raw_value, target_id in values:
                val = raw_value.strip()
                if len(val) < _MIN_VALUE_LENGTH:
                    continue
                if not self._can_replace_value(val):
                    continue
                value_counter[val] += 1
                value_targets.setdefault(val, []).append(target_id)

        candidates: list[MacroVariable] = []
        seen_names: set[str] = set()

        for val, count in value_counter.most_common():
            var_name = self._derive_var_name(val, value_targets.get(val, []))
            if var_name in seen_names:
                var_name = f"{var_name}_{len(seen_names)}"
            seen_names.add(var_name)

            candidates.append(
                MacroVariable(
                    name=var_name,
                    description=f"값: {val} (사용 {count}회)",
                    default=val,
                    required=True,
                )
            )

        return candidates

    # ------------------------------------------------------------------
    # 상태 영속화 (CLI 프로세스 간 상태 유지)
    # ------------------------------------------------------------------

    def _save_state(self) -> None:
        """녹화 상태를 파일에 저장."""
        state = {
            "recording": self._recording,
            "name": self._name,
            "tcode": self._tcode,
            "steps": [s.model_dump(mode="json") for s in self._steps],
        }
        atomic_write_json(self._state_file, state, mode=0o644)

    def _load_state(self) -> None:
        """파일에서 녹화 상태 복원."""
        if not self._state_file.exists():
            return
        try:
            state = json.loads(self._state_file.read_text(encoding="utf-8"))
            self._recording = state.get("recording", False)
            self._name = state.get("name", "")
            self._tcode = state.get("tcode", "")
            self._steps = [MacroStep.model_validate(s) for s in state.get("steps", [])]
        except Exception:
            self._clear_state()

    def _clear_state(self) -> None:
        """상태 파일 삭제."""
        if self._state_file.exists():
            self._state_file.unlink()

    @staticmethod
    def _derive_var_name(value: str, target_ids: list[str]) -> str:
        """target_id에서 변수명을 유추한다."""
        if target_ids:
            last_part = target_ids[0].rsplit("/", 1)[-1]
            # "ctxtRMMG1-MATNR" -> "matnr", "txtMATNR" -> "matnr"
            match = re.search(r"[A-Z][A-Z0-9_]+$", last_part)
            if match:
                return match.group().lower()
            clean = re.sub(r"^(ctxt|txt|cmb|chk|rad|btn)", "", last_part)
            if clean:
                return re.sub(r"[^a-zA-Z0-9_]", "_", clean).lower().strip("_") or "var"
        return re.sub(r"[^a-zA-Z0-9_]", "_", value).lower().strip("_")[:20] or "var"

    def _add_variable_replacement(
        self,
        macro_vars: list[MacroVariable],
        name: str,
        description: str,
        actual_value: str,
        *,
        required: bool = True,
    ) -> bool:
        """Register a macro variable and replace its literal value in steps."""
        actual_value = str(actual_value)
        if not self._can_replace_value(actual_value):
            return False
        if any(var.default == actual_value for var in macro_vars):
            return False
        if not self._literal_exists_in_steps(actual_value):
            return False

        var_name = self._unique_var_name(
            self._normalize_var_name(name),
            {var.name for var in macro_vars},
        )
        macro_vars.append(
            MacroVariable(
                name=var_name,
                description=description,
                default=actual_value,
                required=required,
            )
        )

        placeholder = f"${{{var_name}}}"
        for step in self._steps:
            if step.value and actual_value in step.value:
                step.value = step.value.replace(actual_value, placeholder)
            if step.params:
                step.params = replace_literal_in_data(
                    step.params,
                    actual_value,
                    placeholder,
                )
        return True

    @staticmethod
    def _unique_var_name(name: str, existing: set[str]) -> str:
        """Return a variable name that does not collide with existing names."""
        if name not in existing:
            return name
        i = 2
        while f"{name}_{i}" in existing:
            i += 1
        return f"{name}_{i}"

    @staticmethod
    def _can_replace_value(actual_value: str) -> bool:
        """Return whether a variable actual value is safe to replace in steps.

        finish()는 녹화된 literal 값을 placeholder로 치환하는 단계다.
        AI가 실수로 ${var}, {{var}}, {var} 같은 placeholder 자체나 빈 값을
        actual value로 넘기면 replace("", ...)로 매크로가 오염될 수 있으므로
        보수적으로 건너뛴다.
        """
        if not actual_value.strip():
            return False
        return _PLACEHOLDER_ONLY_PATTERN.match(actual_value) is None

    @staticmethod
    def _normalize_var_name(name: str) -> str:
        """Normalize AI-provided variable names into ${...}-compatible names."""
        return normalize_variable_name(name)

    @staticmethod
    def _literal_exists_in_data(value: Any, literal: str) -> bool:
        if isinstance(value, str):
            return literal in value
        if isinstance(value, list):
            return any(MacroRecorder._literal_exists_in_data(item, literal) for item in value)
        if isinstance(value, dict):
            return any(MacroRecorder._literal_exists_in_data(item, literal) for item in value.values())
        return False

    @staticmethod
    def _literal_exists_in_step(step: MacroStep, literal: str) -> bool:
        return bool(
            (step.value and literal in step.value)
            or MacroRecorder._literal_exists_in_data(step.params, literal)
        )

    def _literal_exists_in_steps(self, literal: str) -> bool:
        return any(self._literal_exists_in_step(step, literal) for step in self._steps)
