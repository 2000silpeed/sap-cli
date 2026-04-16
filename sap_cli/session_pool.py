"""병렬 멀티 세션 관리 — SessionPool.

여러 SapGuiWrapper 인스턴스를 slot_id로 관리하여
에이전트별 독립 SAP 세션을 운영한다.

Usage:
    pool = SessionPool()
    wrapper = pool.get_or_create("ses0")
    wrapper.connect(system="PR1")
    # 다른 에이전트
    wrapper2 = pool.get_or_create("ses1", session_index=1)
    wrapper2.connect(system="PR1")
"""

from __future__ import annotations

import logging
import re
import threading
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from .com_wrapper import SapGuiError, SapGuiWrapper

logger = logging.getLogger(__name__)

_SLOT_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,32}$")


def _validate_slot_id(slot_id: str) -> str:
    """slot_id를 검증하고 정규화(strip)."""
    slot_id = slot_id.strip()
    if not slot_id or not _SLOT_ID_PATTERN.match(slot_id):
        raise ValueError(
            f"유효하지 않은 slot_id: {slot_id!r}. "
            f"영문/숫자/언더스코어/하이픈만 사용 (1-32자)."
        )
    return slot_id

DEFAULT_SLOT = "ses0"
DEFAULT_MAX_SESSIONS = 6
DEFAULT_TTL_SECONDS = 3600  # 1시간 미사용 시 자동 해제


@dataclass
class SessionSlot:
    """에이전트에 할당된 SAP 세션 슬롯."""
    slot_id: str
    wrapper: SapGuiWrapper
    connection_index: int = 0
    session_index: int = 0
    system_id: str | None = None
    assigned_agent: str | None = None
    created_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    last_used_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class SessionPool:
    """slot_id 기반 SapGuiWrapper 풀.

    각 슬롯이 독립적인 SapGuiWrapper 인스턴스를 소유하며,
    서로 다른 SAP 세션에 연결되어 병렬 작업을 지원한다.
    """

    def __init__(
        self,
        max_sessions: int = DEFAULT_MAX_SESSIONS,
        ttl_seconds: int = DEFAULT_TTL_SECONDS,
    ) -> None:
        self._slots: dict[str, SessionSlot] = {}
        self._max_sessions = max_sessions
        self._ttl_seconds = ttl_seconds
        self._lock = threading.Lock()

    @property
    def max_sessions(self) -> int:
        return self._max_sessions

    def _sweep_expired_locked(self) -> list[str]:
        """TTL 만료 슬롯 정리 (lock 보유 상태에서 호출)."""
        now = datetime.now(timezone.utc)
        expired: list[str] = []
        for slot_id, slot in list(self._slots.items()):
            last_used = datetime.fromisoformat(slot.last_used_at)
            if (now - last_used).total_seconds() > self._ttl_seconds:
                expired.append(slot_id)
                try:
                    if slot.wrapper.is_connected:
                        slot.wrapper.send_vkey(12)
                except Exception:
                    pass
                del self._slots[slot_id]
                logger.info("TTL 만료 슬롯 해제: %s", slot_id)
        return expired

    def get_or_create(
        self,
        slot_id: str = DEFAULT_SLOT,
        agent_name: str | None = None,
    ) -> SapGuiWrapper:
        """slot_id에 해당하는 wrapper를 반환. 없으면 새 슬롯 생성.

        Returns:
            해당 슬롯의 SapGuiWrapper 인스턴스
        """
        slot_id = _validate_slot_id(slot_id)
        with self._lock:
            if slot_id in self._slots:
                slot = self._slots[slot_id]
                slot.last_used_at = datetime.now(timezone.utc).isoformat()
                if agent_name:
                    slot.assigned_agent = agent_name
                return slot.wrapper

            if len(self._slots) >= self._max_sessions:
                # TTL 만료 슬롯 정리 후 재시도
                self._sweep_expired_locked()
                if len(self._slots) >= self._max_sessions:
                    raise SapGuiError(
                        f"최대 세션 수({self._max_sessions})에 도달했습니다. "
                        f"활성 슬롯: {list(self._slots.keys())}. "
                        f"sap_session_release로 슬롯을 해제하세요."
                    )

            wrapper = SapGuiWrapper()
            slot = SessionSlot(
                slot_id=slot_id,
                wrapper=wrapper,
                assigned_agent=agent_name,
            )
            self._slots[slot_id] = slot
            logger.info("슬롯 생성: %s (agent=%s)", slot_id, agent_name)
            return wrapper

    def get(self, slot_id: str = DEFAULT_SLOT) -> SapGuiWrapper | None:
        """슬롯이 있으면 wrapper 반환, 없으면 None."""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot:
                slot.last_used_at = datetime.now(timezone.utc).isoformat()
                return slot.wrapper
            return None

    def release(self, slot_id: str) -> bool:
        """슬롯 해제 (wrapper만 제거, SAP 세션은 유지).

        Returns:
            해제 성공 여부
        """
        with self._lock:
            if slot_id not in self._slots:
                return False
            del self._slots[slot_id]
            logger.info("슬롯 해제: %s", slot_id)
            return True

    def close(self, slot_id: str) -> bool:
        """슬롯 해제 + SAP 세션도 종료 시도.

        Returns:
            해제 성공 여부
        """
        with self._lock:
            slot = self._slots.pop(slot_id, None)
            if slot is None:
                return False
            # SAP 세션 종료 시도 (실패해도 슬롯은 제거)
            try:
                if slot.wrapper.is_connected:
                    slot.wrapper.send_vkey(12)  # F12 (Cancel)
            except Exception:
                pass
            logger.info("슬롯 종료: %s", slot_id)
            return True

    def list_slots(self) -> list[dict[str, Any]]:
        """활성 슬롯 목록 반환 (만료 슬롯 자동 정리 포함)."""
        with self._lock:
            self._sweep_expired_locked()
            result = []
            for slot in self._slots.values():
                info: dict[str, Any] = {
                    "slot_id": slot.slot_id,
                    "assigned_agent": slot.assigned_agent,
                    "connection_index": slot.connection_index,
                    "session_index": slot.session_index,
                    "system_id": slot.system_id,
                    "created_at": slot.created_at,
                    "last_used_at": slot.last_used_at,
                    "connected": False,
                }
                try:
                    info["connected"] = slot.wrapper.is_connected
                    if info["connected"]:
                        session_info = slot.wrapper.get_session_info()
                        info["system_id"] = session_info.system_name
                        info["transaction"] = session_info.transaction
                        info["user"] = session_info.user
                except Exception:
                    pass
                result.append(info)
            return result

    def update_slot_connection(
        self,
        slot_id: str,
        connection_index: int,
        session_index: int,
        system_id: str | None = None,
    ) -> None:
        """슬롯의 연결 정보 갱신 (connect 후 호출)."""
        with self._lock:
            slot = self._slots.get(slot_id)
            if slot:
                slot.connection_index = connection_index
                slot.session_index = session_index
                slot.system_id = system_id

    def has_slot(self, slot_id: str) -> bool:
        with self._lock:
            return slot_id in self._slots

    def slot_count(self) -> int:
        with self._lock:
            return len(self._slots)

    def sweep_expired(self) -> list[str]:
        """TTL 초과 슬롯을 자동 해제. 해제된 slot_id 목록 반환."""
        with self._lock:
            return self._sweep_expired_locked()

    def clear(self) -> None:
        """모든 슬롯 해제 (테스트용)."""
        with self._lock:
            self._slots.clear()
