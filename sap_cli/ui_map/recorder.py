"""Record guided tool actions or native SAP GUI clicks into a UI map.

Unlike the daemon-driven traversal, here the LLM (or a human) clicks through the
screens itself via the normal ``press``/``ui_action`` tools. When a recording
session is active for a slot, each navigating action is captured: the resulting
screen is scanned into a value-free ``UiState`` and, when the click was a safe
structural control, a replayable transition is added. Everything is merged into
the map with ``save_delta`` so the map grows as the operator explores.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Callable

from .schema import MapManifest, SideEffectClass, Transition, UiMap, UiState
from .scanner import UiMapScanner

# Only these commands can change the screen and therefore a map state.
NAV_COMMANDS = frozenset({"press", "ui_action", "vkey", "run", "command"})
# A press on one of these becomes a replayable transition.
_CONTROL_ACTION = {"GuiTab": "select_tab", "GuiButton": "press", "GuiMenu": "select_menu"}
# Never record a commit/change control as a replayable route into the map.
_DANGEROUS = re.compile(
    r"(?<![a-z])(save|post|delete|release|send|print)(?![a-z])"
    r"|저장|전기|삭제|릴리스|릴리즈|승인|전송|발송|인쇄|출력",
    re.I,
)


class MapRecorder:
    """Per-slot recorder that appends operator navigation to one UI map."""

    def __init__(
        self,
        map_store: Any,
        get_wrapper: Callable[[str], Any],
        *,
        scanner: UiMapScanner | None = None,
        get_event_manager: Callable[[], Any] | None = None,
    ) -> None:
        self._store = map_store
        self._get_wrapper = get_wrapper
        self._scanner = scanner or UiMapScanner()
        self._get_event_manager = get_event_manager
        self.active = False
        self.mode = "guided"
        self.map_id = ""
        self.tcode = ""
        self.scope: dict[str, str] = {}
        self.slot_id = "ses0"
        self._current: UiState | None = None
        self.states_recorded = 0
        self.transitions_recorded = 0
        self._event_subscription_id = ""
        self._event_cursor = 0
        self._pending_component: dict[str, Any] | None = None

    # -- lifecycle ---------------------------------------------------------
    def start(self, *, map_id: str, tcode: str, slot_id: str = "ses0") -> dict[str, Any]:
        if self.active:
            raise RuntimeError("a map recording is already active for this slot")
        self.mode = "guided"
        return self._start(map_id=map_id, tcode=tcode, slot_id=slot_id)

    def start_manual(
        self, *, map_id: str, tcode: str, slot_id: str = "ses0"
    ) -> dict[str, Any]:
        """Start recording native mouse/keyboard navigation through SAP events."""

        if self.active:
            raise RuntimeError("a map recording is already active for this slot")
        if self._get_event_manager is None:
            raise RuntimeError("manual recording requires Windows SAP GUI event support")
        self.mode = "manual"
        result = self._start(map_id=map_id, tcode=tcode, slot_id=slot_id)
        wrapper = self._get_wrapper(slot_id)
        session = getattr(wrapper, "session", wrapper)
        self._set_visualization(session, True)
        try:
            manager = self._get_event_manager()
            subscription = manager.subscribe(
                slot_id=slot_id,
                scope="session",
                target=session,
                event_names=("Hit", "Change", "EndRequest"),
                owner="map-manual-record",
                ttl_seconds=3600.0,
                queue_limit=1024,
                redact_sensitive=True,
            )
            self._event_subscription_id = str(subscription["subscription_id"])
        except Exception:
            self._set_visualization(session, False)
            self._reset()
            raise
        result = self.status()
        result["message"] = "SAP GUI에서 직접 조작하세요. status/poll이 완료된 요청을 지도에 반영합니다."
        return result

    def _start(self, *, map_id: str, tcode: str, slot_id: str) -> dict[str, Any]:
        map_id = str(map_id or "").strip()
        tcode = str(tcode or "").strip().upper()
        if not map_id or not tcode:
            raise ValueError("map_id and tcode are required to start recording")
        wrapper = self._get_wrapper(slot_id)
        self.scope = self._read_scope(wrapper)
        state = self._scan(wrapper, tcode)
        self._store.save_delta(self._delta(map_id, tcode, {state.state_id: state}, []))
        self.active = True
        self.map_id = map_id
        self.tcode = tcode
        self.slot_id = slot_id
        self._current = state
        self.states_recorded = 1
        self.transitions_recorded = 0
        return self.status()

    def finish(self) -> dict[str, Any]:
        self.poll_manual()
        summary = self.status()
        summary["finished"] = True
        self._stop_manual_events()
        self._reset()
        return summary

    def cancel(self) -> dict[str, Any]:
        self.poll_manual()
        summary = self.status()
        summary["cancelled"] = True
        # Screens saved so far stay in the map (they are value-free structure);
        # cancel only stops further recording. Use map-delete to remove the map.
        self._stop_manual_events()
        self._reset()
        return summary

    def status(self) -> dict[str, Any]:
        if self.mode == "manual":
            self.poll_manual()
        result = {
            "recording": self.active,
            "mode": self.mode,
            "map_id": self.map_id,
            "tcode": self.tcode,
            "scope": dict(self.scope),
            "current_state_id": self._current.state_id if self._current else "",
            "states_recorded": self.states_recorded,
            "transitions_recorded": self.transitions_recorded,
        }
        if self.active and self.mode == "guided":
            result.update(self._guided_prompt())
        if self.active and self.mode == "manual":
            result["instruction"] = (
                "SAP GUI에서 직접 조작하세요. 이 상태를 다시 조회하면 완료된 화면 이동을 반영합니다."
            )
        return result

    def poll_manual(self) -> dict[str, Any] | None:
        """Consume native SAP events and record completed screen transitions."""

        if not self.active or self.mode != "manual" or not self._event_subscription_id:
            return None
        manager = self._get_event_manager() if self._get_event_manager else None
        if manager is None:
            return None
        result = manager.poll(
            self._event_subscription_id, cursor=self._event_cursor, limit=256
        )
        self._event_cursor = int(result["next_cursor"])
        recorded = 0
        for event in result["events"]:
            name = event.get("event_name")
            payload = event.get("payload") or {}
            if name in {"Hit", "Change"}:
                component = payload.get("Component")
                if isinstance(component, dict) and component.get("id"):
                    self._pending_component = component
            elif name == "EndRequest":
                args = {
                    "element_id": str((self._pending_component or {}).get("id", ""))
                }
                if self.record("manual_hit", args) is not None:
                    recorded += 1
                self._pending_component = None
        return {
            "events_processed": len(result["events"]),
            "screens_recorded": recorded,
            "next_cursor": self._event_cursor,
            "lost_count": result["lost_count"],
        }

    # -- per-action hook ---------------------------------------------------
    def record(self, command: str, args: dict[str, Any]) -> dict[str, Any] | None:
        """Capture the screen reached by one navigating action. Best effort."""

        if not self.active or command not in NAV_COMMANDS | {"manual_hit"}:
            return None
        wrapper = self._get_wrapper(self.slot_id)
        after = self._scan(wrapper, self.tcode)
        # A click that left this map's TCode is out of scope: do not fold a
        # foreign transaction's screens into the map.
        if after.tcode.strip().upper() != self.tcode:
            return None
        before = self._current
        self._current = after
        if before is not None and after.state_id == before.state_id:
            return None  # no screen change → nothing new to record
        transition = self._infer_transition(before, after, command, args)
        transitions = [transition] if transition is not None else []
        self._store.save_delta(
            self._delta(self.map_id, self.tcode, {after.state_id: after}, transitions)
        )
        self.states_recorded += 1
        if transitions:
            self.transitions_recorded += 1
        return {
            "recorded_state": after.state_id,
            "transition": transition.action if transition else "",
        }

    # -- internals ---------------------------------------------------------
    def _scan(self, wrapper: Any, tcode: str) -> UiState:
        session = getattr(wrapper, "session", wrapper)
        return self._scanner.scan(session, tcode=tcode)

    def _read_scope(self, wrapper: Any) -> dict[str, str]:
        getter = getattr(wrapper, "get_session_info", None)
        if not callable(getter):
            return {}
        try:
            info = getter()
        except Exception:
            return {}
        dump = getattr(info, "model_dump", None)
        raw = dump(mode="json") if callable(dump) else {}
        return {
            "system_id": str(raw.get("system_name", "") or ""),
            "client": str(raw.get("client", "") or ""),
            "language": str(raw.get("language", "") or ""),
        }

    def _delta(
        self,
        map_id: str,
        tcode: str,
        states: dict[str, UiState],
        transitions: list[Transition],
    ) -> UiMap:
        manifest = MapManifest(
            map_id=map_id,
            tcode=tcode,
            capture_mode="record",
            system_id=self.scope.get("system_id", ""),
            client=self.scope.get("client", ""),
            language=self.scope.get("language", ""),
        )
        return UiMap(manifest=manifest, states=states, transitions=transitions)

    def _infer_transition(
        self,
        before: UiState | None,
        after: UiState,
        command: str,
        args: dict[str, Any],
    ) -> Transition | None:
        # Only a control press yields a replayable transition. run/vkey change
        # the screen but are not a stable, replayable structural edge, so their
        # target screen is still recorded as a state without a transition.
        if before is None or command not in {"press", "ui_action", "manual_hit"}:
            return None
        element_id = str(args.get("element_id", "")).strip()
        if not element_id:
            return None
        component = next(
            (item for item in before.components if item.sap_id == element_id), None
        )
        if component is None:
            return None
        action = _CONTROL_ACTION.get(component.type)
        if action is None:
            return None
        label = " ".join(
            filter(None, [component.logical_id, component.sap_id, component.name, component.text])
        )
        if _DANGEROUS.search(label):
            return None
        locator = {"sap_id": component.sap_id, "component_type": component.type}
        return Transition(
            transition_id=self._transition_id(before.state_id, action, component.logical_id, locator),
            from_state_id=before.state_id,
            to_state_id=after.state_id,
            action=action,
            target_logical_id=component.logical_id,
            target_locator=locator,
            side_effect=(
                SideEffectClass.REVERSIBLE
                if action == "select_tab"
                else SideEffectClass.NAVIGATION
            ),
            reversible=(action == "select_tab"),
        )

    @staticmethod
    def _transition_id(
        state_id: str, action: str, target_logical_id: str, locator: dict[str, Any]
    ) -> str:
        payload = json.dumps(
            [state_id, action, target_logical_id, locator, {}, SideEffectClass.NAVIGATION.value],
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:24]

    def _reset(self) -> None:
        self.active = False
        self.map_id = ""
        self.tcode = ""
        self.scope = {}
        self._current = None
        self.states_recorded = 0
        self.transitions_recorded = 0
        self.mode = "guided"
        self._event_subscription_id = ""
        self._event_cursor = 0
        self._pending_component = None

    def _stop_manual_events(self) -> None:
        if self.mode != "manual":
            return
        try:
            wrapper = self._get_wrapper(self.slot_id)
            session = getattr(wrapper, "session", wrapper)
            self._set_visualization(session, False)
        except Exception:
            pass
        if self._event_subscription_id and self._get_event_manager:
            try:
                self._get_event_manager().unsubscribe(self._event_subscription_id)
            except Exception:
                pass

    @staticmethod
    def _set_visualization(session: Any, enabled: bool) -> None:
        """Enable Hit events across SAP GUI versions that expose either owner."""

        targets = [getattr(session, "ActiveWindow", None), session]
        for target in targets:
            if target is None:
                continue
            try:
                setattr(target, "ElementVisualizationMode", enabled)
                return
            except Exception:
                continue
        raise RuntimeError("SAP GUI does not expose ElementVisualizationMode")

    def _guided_prompt(self) -> dict[str, Any]:
        candidates: list[dict[str, str]] = []
        if self._current:
            for component in self._current.components:
                if component.visible is False or component.enabled is False:
                    continue
                action = _CONTROL_ACTION.get(component.type)
                if action is None:
                    continue
                label = " ".join(
                    filter(None, [component.text, component.name, component.sap_id])
                )
                if _DANGEROUS.search(label):
                    continue
                candidates.append(
                    {
                        "label": component.text or component.name or component.logical_id,
                        "element_id": component.sap_id,
                        "action": action,
                    }
                )
                if len(candidates) >= 20:
                    break
        return {
            "needs_user_input": True,
            "question": "현재 화면에서 다음에 무엇을 할까요?",
            "safe_actions": candidates,
            "instruction": (
                "화면 구조와 safe_actions를 설명하고 사용자에게 다음 행동을 하나 물으세요. "
                "응답을 받은 뒤에만 해당 SAP 도구를 호출하세요."
            ),
        }


__all__ = ["MapRecorder", "NAV_COMMANDS"]
