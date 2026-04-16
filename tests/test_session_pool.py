"""Phase 17-1: SessionPool 테스트.

세션 풀 생성/조회/해제/최대 세션/하위 호환성 검증.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from sap_cli.com_wrapper import SapGuiError, SapGuiWrapper
from sap_cli.session_pool import DEFAULT_SLOT, DEFAULT_TTL_SECONDS, SessionPool, SessionSlot


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_session_info(system_name="PR1", client="100", user="TESTUSER"):
    info = SimpleNamespace(
        User=user,
        Language="KO",
        Transaction="SESSION_MANAGER",
        ScreenNumber=100,
        Program="SAPMSYST",
        SystemName=system_name,
        Client=client,
        ApplicationServer="sapserver01",
    )
    session = MagicMock()
    session.Info = info
    session.Id = "/app/con[0]/ses[0]"
    return session


def _make_connection(system_name="PR1", session_count=1):
    conn = MagicMock()
    conn.Description = f"{system_name} - SAP System"
    sessions = []
    for _ in range(session_count):
        sessions.append(_make_session_info(system_name))
    conn.Sessions.Count = session_count
    conn.Sessions.side_effect = lambda idx: sessions[idx]
    return conn


def _make_application(*connections):
    app = MagicMock()
    app.Connections.Count = len(connections)
    app.Connections.side_effect = lambda idx: connections[idx]
    return app


# ---------------------------------------------------------------------------
# SessionPool 기본 CRUD
# ---------------------------------------------------------------------------

class TestSessionPoolBasic:
    def test_get_or_create_default_slot(self):
        pool = SessionPool()
        wrapper = pool.get_or_create()
        assert wrapper is not None
        assert isinstance(wrapper, SapGuiWrapper)
        assert pool.has_slot(DEFAULT_SLOT)
        assert pool.slot_count() == 1

    def test_get_or_create_returns_same_wrapper(self):
        pool = SessionPool()
        w1 = pool.get_or_create("ses0")
        w2 = pool.get_or_create("ses0")
        assert w1 is w2

    def test_get_or_create_different_slots(self):
        pool = SessionPool()
        w1 = pool.get_or_create("ses0")
        w2 = pool.get_or_create("ses1")
        assert w1 is not w2
        assert pool.slot_count() == 2

    def test_get_existing_slot(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        assert pool.get("ses0") is not None

    def test_get_nonexistent_slot(self):
        pool = SessionPool()
        assert pool.get("ses99") is None

    def test_release_slot(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        assert pool.release("ses0") is True
        assert pool.has_slot("ses0") is False
        assert pool.slot_count() == 0

    def test_release_nonexistent(self):
        pool = SessionPool()
        assert pool.release("ses99") is False

    def test_close_slot(self):
        pool = SessionPool()
        wrapper = pool.get_or_create("ses0")
        # is_connected가 False면 send_vkey 호출 안 함
        assert pool.close("ses0") is True
        assert pool.has_slot("ses0") is False

    def test_close_nonexistent(self):
        pool = SessionPool()
        assert pool.close("ses99") is False

    def test_clear(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        pool.get_or_create("ses1")
        pool.clear()
        assert pool.slot_count() == 0


# ---------------------------------------------------------------------------
# 최대 세션 제한
# ---------------------------------------------------------------------------

class TestSessionPoolMaxSessions:
    def test_max_sessions_default(self):
        pool = SessionPool()
        assert pool.max_sessions == 6

    def test_max_sessions_custom(self):
        pool = SessionPool(max_sessions=3)
        assert pool.max_sessions == 3

    def test_exceed_max_sessions(self):
        pool = SessionPool(max_sessions=2)
        pool.get_or_create("ses0")
        pool.get_or_create("ses1")
        with pytest.raises(SapGuiError, match="최대 세션 수"):
            pool.get_or_create("ses2")

    def test_release_then_create(self):
        """해제 후 새 슬롯 생성 가능."""
        pool = SessionPool(max_sessions=2)
        pool.get_or_create("ses0")
        pool.get_or_create("ses1")
        pool.release("ses0")
        # 이제 다시 생성 가능
        wrapper = pool.get_or_create("ses2")
        assert wrapper is not None
        assert pool.slot_count() == 2


# ---------------------------------------------------------------------------
# 에이전트 할당
# ---------------------------------------------------------------------------

class TestSessionPoolAgent:
    def test_agent_assignment(self):
        pool = SessionPool()
        pool.get_or_create("ses0", agent_name="operator")
        slots = pool.list_slots()
        assert slots[0]["assigned_agent"] == "operator"

    def test_agent_update(self):
        pool = SessionPool()
        pool.get_or_create("ses0", agent_name="operator")
        pool.get_or_create("ses0", agent_name="verifier")
        slots = pool.list_slots()
        assert slots[0]["assigned_agent"] == "verifier"


# ---------------------------------------------------------------------------
# list_slots
# ---------------------------------------------------------------------------

class TestSessionPoolListSlots:
    def test_list_empty(self):
        pool = SessionPool()
        assert pool.list_slots() == []

    def test_list_multiple(self):
        pool = SessionPool()
        pool.get_or_create("ses0", agent_name="A")
        pool.get_or_create("ses1", agent_name="B")
        slots = pool.list_slots()
        assert len(slots) == 2
        ids = {s["slot_id"] for s in slots}
        assert ids == {"ses0", "ses1"}

    def test_list_includes_timestamps(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        slots = pool.list_slots()
        assert "created_at" in slots[0]
        assert "last_used_at" in slots[0]


# ---------------------------------------------------------------------------
# update_slot_connection
# ---------------------------------------------------------------------------

class TestUpdateSlotConnection:
    def test_update(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        pool.update_slot_connection("ses0", connection_index=1, session_index=2, system_id="ERP")
        slots = pool.list_slots()
        assert slots[0]["connection_index"] == 1
        assert slots[0]["session_index"] == 2

    def test_update_nonexistent(self):
        pool = SessionPool()
        # 존재하지 않는 슬롯 업데이트는 조용히 무시
        pool.update_slot_connection("ses99", connection_index=0, session_index=0)


# ---------------------------------------------------------------------------
# SapGuiWrapper.create_session
# ---------------------------------------------------------------------------

class TestCreateSession:
    def test_create_session_success(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1", session_count=1)
        # CreateSession 호출 후 Sessions.Count가 2로 증가하는 시뮬레이션
        original_count = 1
        call_count = [0]

        def mock_count_getter():
            return original_count + (1 if call_count[0] > 0 else 0)

        type(conn.Sessions).Count = property(lambda self: mock_count_getter())
        conn.Sessions(0).CreateSession = MagicMock(side_effect=lambda: call_count.__setitem__(0, 1))

        wrapper._connection = conn
        with patch("time.sleep"):
            idx = wrapper.create_session()
        assert idx == 1

    def test_create_session_no_connection(self):
        wrapper = SapGuiWrapper()
        with pytest.raises(SapGuiError, match="연결되지 않았습니다"):
            wrapper.create_session()

    def test_create_session_timeout(self):
        wrapper = SapGuiWrapper()
        conn = _make_connection("PR1", session_count=1)
        # Sessions.Count가 절대 증가하지 않음
        wrapper._connection = conn
        with patch("time.sleep"):
            with pytest.raises(SapGuiError, match="시간 초과"):
                wrapper.create_session()


# ---------------------------------------------------------------------------
# service.py 하위 호환
# ---------------------------------------------------------------------------

class TestServiceCompat:
    def test_get_sap_default_slot(self):
        """get_sap()가 기본 슬롯(ses0)의 wrapper를 반환."""
        from sap_cli import service
        # pool 초기화
        old_pool = service._pool
        service._pool = None
        try:
            pool = service.get_pool()
            assert pool is not None
            assert pool.slot_count() == 0
        finally:
            service._pool = old_pool

    def test_get_pool_singleton(self):
        from sap_cli import service
        old_pool = service._pool
        service._pool = None
        try:
            p1 = service.get_pool()
            p2 = service.get_pool()
            assert p1 is p2
        finally:
            service._pool = old_pool

    def test_get_sap_creates_slot(self):
        """get_sap("ses1")이 ses1 슬롯을 자동 생성."""
        from sap_cli import service
        old_pool = service._pool
        service._pool = SessionPool()
        try:
            # connect()가 호출되면 COM 연결 시도하므로 mock
            with patch.object(SapGuiWrapper, "is_connected", new_callable=lambda: property(lambda self: True)):
                wrapper = service.get_sap("ses1")
                assert wrapper is not None
                assert service.get_pool().has_slot("ses1")
        finally:
            service._pool = old_pool


# ---------------------------------------------------------------------------
# 스레드 안전성
# ---------------------------------------------------------------------------

class TestThreadSafety:
    def test_concurrent_create(self):
        """여러 스레드에서 동시에 슬롯 생성해도 안전."""
        import threading

        pool = SessionPool(max_sessions=10)
        errors = []

        def create_slot(slot_id):
            try:
                pool.get_or_create(slot_id)
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=create_slot, args=(f"ses{i}",)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0
        assert pool.slot_count() == 10

    def test_concurrent_max_sessions(self):
        """max_sessions를 초과하는 동시 요청 중 정확히 max_sessions개만 성공."""
        import threading

        pool = SessionPool(max_sessions=3)
        successes = []
        failures = []
        lock = threading.Lock()

        def create_slot(slot_id):
            try:
                pool.get_or_create(slot_id)
                with lock:
                    successes.append(slot_id)
            except SapGuiError:
                with lock:
                    failures.append(slot_id)

        threads = [threading.Thread(target=create_slot, args=(f"ses{i}",)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(successes) == 3
        assert len(failures) == 2


# ---------------------------------------------------------------------------
# Dispatcher 통합 테스트: slots, slot_allocate, slot_release
# ---------------------------------------------------------------------------

class TestDispatchSlots:
    def test_dispatch_slots_empty(self):
        from sap_cli import service
        from sap_cli.dispatcher import _dispatch_slots

        old_pool = service._pool
        service._pool = SessionPool()
        try:
            result = _dispatch_slots()
            assert result["slot_count"] == 0
            assert result["max_sessions"] == 6
            assert result["slots"] == []
        finally:
            service._pool = old_pool

    def test_dispatch_slots_with_entries(self):
        from sap_cli import service
        from sap_cli.dispatcher import _dispatch_slots

        old_pool = service._pool
        pool = SessionPool()
        pool.get_or_create("ses0", agent_name="op1")
        pool.get_or_create("ses1", agent_name="op2")
        service._pool = pool
        try:
            result = _dispatch_slots()
            assert result["slot_count"] == 2
            ids = {s["slot_id"] for s in result["slots"]}
            assert ids == {"ses0", "ses1"}
        finally:
            service._pool = old_pool


class TestDispatchSlotAllocate:
    def test_allocate_basic(self):
        from sap_cli import service
        from sap_cli.dispatcher import DispatchContext, _dispatch_slot_allocate

        old_pool = service._pool
        service._pool = SessionPool()
        try:
            ctx = DispatchContext(slot_id="ses0", agent_name="tester")
            result = _dispatch_slot_allocate({"slot_id": "ses2", "agent_name": "agent_a"}, ctx)
            assert result["allocated"] is True
            assert result["slot_id"] == "ses2"
            assert result["agent_name"] == "agent_a"
            assert "connected" not in result
            assert service.get_pool().has_slot("ses2")
        finally:
            service._pool = old_pool

    def test_allocate_uses_ctx_agent_fallback(self):
        from sap_cli import service
        from sap_cli.dispatcher import DispatchContext, _dispatch_slot_allocate

        old_pool = service._pool
        service._pool = SessionPool()
        try:
            ctx = DispatchContext(slot_id="ses0", agent_name="ctx_agent")
            result = _dispatch_slot_allocate({"slot_id": "ses3"}, ctx)
            assert result["agent_name"] == "ctx_agent"
        finally:
            service._pool = old_pool

    def test_allocate_missing_slot_id(self):
        from sap_cli.dispatcher import DispatchContext, _dispatch_slot_allocate

        ctx = DispatchContext()
        with pytest.raises(ValueError, match="slot_id가 필요합니다"):
            _dispatch_slot_allocate({}, ctx)


class TestDispatchSlotRelease:
    def test_release_existing(self):
        from sap_cli import service
        from sap_cli.dispatcher import _dispatch_slot_release

        old_pool = service._pool
        pool = SessionPool()
        pool.get_or_create("ses0")
        service._pool = pool
        try:
            result = _dispatch_slot_release({"slot_id": "ses0"})
            assert result["released"] is True
            assert result["slot_id"] == "ses0"
            assert result["closed"] is False
            assert not pool.has_slot("ses0")
        finally:
            service._pool = old_pool

    def test_release_nonexistent(self):
        from sap_cli import service
        from sap_cli.dispatcher import _dispatch_slot_release

        old_pool = service._pool
        service._pool = SessionPool()
        try:
            result = _dispatch_slot_release({"slot_id": "ses99"})
            assert result["released"] is False
        finally:
            service._pool = old_pool

    def test_release_with_close(self):
        from sap_cli import service
        from sap_cli.dispatcher import _dispatch_slot_release

        old_pool = service._pool
        pool = SessionPool()
        pool.get_or_create("ses0")
        service._pool = pool
        try:
            result = _dispatch_slot_release({"slot_id": "ses0", "close": True})
            assert result["released"] is True
            assert result["closed"] is True
            assert not pool.has_slot("ses0")
        finally:
            service._pool = old_pool

    def test_release_missing_slot_id(self):
        from sap_cli.dispatcher import _dispatch_slot_release

        with pytest.raises(ValueError, match="slot_id가 필요합니다"):
            _dispatch_slot_release({})


# ---------------------------------------------------------------------------
# TTL 기반 자동 해제
# ---------------------------------------------------------------------------

class TestSessionPoolTTL:
    def test_sweep_no_expired(self):
        pool = SessionPool(ttl_seconds=3600)
        pool.get_or_create("ses0")
        expired = pool.sweep_expired()
        assert expired == []
        assert pool.slot_count() == 1

    def test_sweep_expired_slot(self):
        from datetime import datetime, timedelta, timezone
        pool = SessionPool(ttl_seconds=60)
        pool.get_or_create("ses0")
        pool.get_or_create("ses1")
        # ses0의 last_used_at을 2시간 전으로 조작
        with pool._lock:
            pool._slots["ses0"].last_used_at = (
                datetime.now(timezone.utc) - timedelta(hours=2)
            ).isoformat()
        expired = pool.sweep_expired()
        assert "ses0" in expired
        assert "ses1" not in expired
        assert pool.slot_count() == 1

    def test_auto_sweep_on_max_sessions(self):
        from datetime import datetime, timedelta, timezone
        pool = SessionPool(max_sessions=2, ttl_seconds=60)
        pool.get_or_create("ses0")
        pool.get_or_create("ses1")
        # ses0을 만료시키면 ses2 생성 가능
        with pool._lock:
            pool._slots["ses0"].last_used_at = (
                datetime.now(timezone.utc) - timedelta(hours=2)
            ).isoformat()
        wrapper = pool.get_or_create("ses2")
        assert wrapper is not None
        assert pool.slot_count() == 2
        assert not pool.has_slot("ses0")
        assert pool.has_slot("ses2")

    def test_default_ttl(self):
        pool = SessionPool()
        assert pool._ttl_seconds == DEFAULT_TTL_SECONDS


# ---------------------------------------------------------------------------
# slot_id 검증
# ---------------------------------------------------------------------------

class TestSlotIdValidation:
    def test_valid_slot_ids(self):
        pool = SessionPool()
        pool.get_or_create("ses0")
        pool.get_or_create("agent-1")
        pool.get_or_create("my_slot_2")
        assert pool.slot_count() == 3

    def test_invalid_slot_id_empty(self):
        pool = SessionPool()
        with pytest.raises(ValueError, match="유효하지 않은 slot_id"):
            pool.get_or_create("")

    def test_invalid_slot_id_spaces(self):
        pool = SessionPool()
        with pytest.raises(ValueError, match="유효하지 않은 slot_id"):
            pool.get_or_create("ses 0")

    def test_invalid_slot_id_special_chars(self):
        pool = SessionPool()
        with pytest.raises(ValueError, match="유효하지 않은 slot_id"):
            pool.get_or_create("ses0;rm -rf")

    def test_slot_id_strip(self):
        pool = SessionPool()
        pool.get_or_create("  ses0  ")
        assert pool.has_slot("ses0")

    def test_invalid_slot_id_too_long(self):
        pool = SessionPool()
        with pytest.raises(ValueError, match="유효하지 않은 slot_id"):
            pool.get_or_create("a" * 33)
