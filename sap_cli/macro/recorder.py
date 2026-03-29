"""채팅 기반 매크로 녹화기.

사용자가 CLI를 통해 명령을 실행하면 각 명령을 MacroStep으로 기록한다.
녹화 완료 후 변수를 지정하여 재사용 가능한 Macro를 생성한다.
"""

from __future__ import annotations

import re
import uuid
from collections import Counter
from datetime import datetime, timezone

from sap_cli.ontology.schema import ActionType
from sap_cli.macro.schema import Macro, MacroStep, MacroVariable

# 변수 치환 불필요한 액션 (값이 시스템 식별자인 경우)
_SYSTEM_ACTIONS = {ActionType.PRESS_BUTTON, ActionType.SEND_VKEY, ActionType.SELECT_TAB}

# 변수로 추출하지 않을 짧은 값
_MIN_VALUE_LENGTH = 2


class MacroRecorder:
    """채팅 기반 매크로 녹화기."""

    def __init__(self) -> None:
        self._recording: bool = False
        self._steps: list[MacroStep] = []
        self._tcode: str = ""
        self._name: str = ""

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

    def record_step(
        self,
        action: ActionType,
        target_id: str,
        value: str | None = None,
        description: str = "",
        screen_number: int | None = None,
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
        )
        self._steps.append(step)
        return step

    def undo_last(self) -> MacroStep | None:
        """마지막 단계 취소."""
        if not self._steps:
            return None
        removed = self._steps.pop()
        # step_number 재정렬 불필요 (finish에서 처리)
        return removed

    def finish(
        self,
        name: str,
        description: str = "",
        variables: dict[str, str] | None = None,
    ) -> Macro:
        """녹화 종료 및 Macro 생성.

        Args:
            name: 매크로 이름
            description: 매크로 설명
            variables: {"실제 사용된 값": "변수 설명"} 형태.
                steps에서 해당 값이 사용된 곳을 ${변수명}으로 치환한다.
                변수명은 target_id에서 자동 유추한다.
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

        # 변수 치환: {"사용된 값": "설명"} → 값을 ${변수명}으로 치환
        if variables:
            for actual_value, var_desc in variables.items():
                # target_id에서 변수명 자동 유추
                targets = [s.target_id for s in self._steps if s.value and actual_value in s.value]
                var_name = self._derive_var_name(actual_value, targets)

                macro_vars.append(MacroVariable(
                    name=var_name,
                    description=var_desc,
                    default=actual_value,
                ))
                placeholder = f"${{{var_name}}}"
                for step in self._steps:
                    if step.value and actual_value in step.value:
                        step.value = step.value.replace(actual_value, placeholder)

        macro = Macro(
            id=uuid.uuid4().hex[:12],
            name=name,
            description=description,
            tcode=self._tcode,
            variables=macro_vars,
            steps=list(self._steps),
            created_at=now,
            updated_at=now,
        )

        self._recording = False
        self._steps = []
        self._tcode = ""
        self._name = ""

        return macro

    def cancel(self) -> None:
        """녹화 취소."""
        self._recording = False
        self._steps = []
        self._tcode = ""
        self._name = ""

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
        - 2회 이상 반복 사용된 값
        - 사용자 입력으로 보이는 값 (SET_FIELD_VALUE, SELECT_COMBO_ITEM 등)
        - 시스템 액션(버튼, VKey)의 값은 제외
        """
        value_counter: Counter[str] = Counter()
        value_targets: dict[str, list[str]] = {}

        for step in self._steps:
            if not step.value or step.action in _SYSTEM_ACTIONS:
                continue
            val = step.value.strip()
            if len(val) < _MIN_VALUE_LENGTH:
                continue
            value_counter[val] += 1
            value_targets.setdefault(val, []).append(step.target_id)

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
                    required=count > 1,
                )
            )

        return candidates

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
