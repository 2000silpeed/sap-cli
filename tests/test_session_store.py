"""Session store 단위 테스트."""

from __future__ import annotations

import json

from sap_cli.auth.policy import SessionConfig
from sap_cli.session_store import SessionStore


def _make_store(tmp_path):
    config = SessionConfig(log_dir=str(tmp_path / "sessions"))
    return SessionStore(config)


def test_create_session(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(
        user_request="VL33N에서 배송 처리",
        ai_reasoning="VL33N 접속 → 아이템 선택 → 배송",
        agent="operator",
        source="mcp",
    )
    assert session.id
    assert session.status == "running"
    assert session.user_request == "VL33N에서 배송 처리"
    assert session.ai_reasoning.startswith("VL33N")


def test_load_session(tmp_path):
    store = _make_store(tmp_path)
    created = store.create_session(user_request="테스트")
    loaded = store.load(created.id)
    assert loaded is not None
    assert loaded.id == created.id
    assert loaded.user_request == "테스트"


def test_load_nonexistent(tmp_path):
    store = _make_store(tmp_path)
    assert store.load("000000000000") is None


def test_add_step(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="테스트")

    step = store.add_step(
        session.id,
        command="run",
        tool_name="sap_run",
        arguments={"tcode": "VL33N"},
        outcome="success",
        before_state={
            "session": {"transaction": "SESSION_MANAGER", "screen_number": 100},
        },
        after_state={
            "session": {"transaction": "VL33N", "screen_number": 1000},
            "status": {"message": "출하 문서 표시"},
        },
    )
    assert step is not None
    assert step.step_number == 0
    assert step.command == "run"
    assert step.before_transaction == "SESSION_MANAGER"
    assert step.after_transaction == "VL33N"
    assert step.status_message == "출하 문서 표시"

    reloaded = store.load(session.id)
    assert len(reloaded.steps) == 1
    assert reloaded.steps[0].arguments == {"tcode": "VL33N"}


def test_add_multiple_steps(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="테스트")

    store.add_step(session.id, command="run", outcome="success")
    store.add_step(session.id, command="screen", outcome="success")
    store.add_step(session.id, command="set", outcome="success")

    reloaded = store.load(session.id)
    assert len(reloaded.steps) == 3
    assert [s.step_number for s in reloaded.steps] == [0, 1, 2]


def test_add_step_to_finished_session_returns_none(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="테스트")
    store.finish_session(session.id, outcome="success")

    result = store.add_step(session.id, command="run", outcome="success")
    assert result is None


def test_finish_session_success(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="테스트")
    store.add_step(session.id, command="run", outcome="success")

    finished = store.finish_session(
        session.id,
        outcome="success",
        outcome_summary="배송 처리 완료",
    )
    assert finished is not None
    assert finished.status == "completed"
    assert finished.outcome == "success"
    assert finished.outcome_summary == "배송 처리 완료"
    assert finished.finished_at is not None


def test_finish_session_failed(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="테스트")

    finished = store.finish_session(session.id, outcome="failed")
    assert finished.status == "failed"
    assert finished.outcome == "failed"


def test_finish_nonexistent_returns_none(tmp_path):
    store = _make_store(tmp_path)
    assert store.finish_session("000000000000") is None


def test_list_sessions(tmp_path):
    store = _make_store(tmp_path)
    store.create_session(user_request="첫번째")
    store.create_session(user_request="두번째")

    sessions = store.list_sessions()
    assert len(sessions) == 2
    assert all("id" in s for s in sessions)
    assert all("user_request" in s for s in sessions)


def test_list_sessions_with_status_filter(tmp_path):
    store = _make_store(tmp_path)
    s1 = store.create_session(user_request="진행중")
    s2 = store.create_session(user_request="완료됨")
    store.finish_session(s2.id, outcome="success")

    running = store.list_sessions(status="running")
    assert len(running) == 1
    assert running[0]["id"] == s1.id

    completed = store.list_sessions(status="completed")
    assert len(completed) == 1
    assert completed[0]["id"] == s2.id


def test_list_sessions_with_limit(tmp_path):
    store = _make_store(tmp_path)
    for i in range(5):
        store.create_session(user_request=f"세션 {i}")

    sessions = store.list_sessions(limit=3)
    assert len(sessions) == 3


def test_delete_session(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="삭제 테스트")
    assert store.delete(session.id) is True
    assert store.load(session.id) is None


def test_delete_nonexistent(tmp_path):
    store = _make_store(tmp_path)
    assert store.delete("000000000000") is False


def test_session_json_persistence(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(
        user_request="JSON 테스트",
        agent="operator",
        sap_user="TESTUSER",
        sap_system="DEV",
    )
    store.add_step(
        session.id,
        command="run",
        tool_name="sap_run",
        arguments={"tcode": "MM03"},
        outcome="success",
    )
    store.finish_session(session.id, outcome="success", outcome_summary="완료")

    path = store._find_session_path(session.id)
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["agent"] == "operator"
    assert data["sap_user"] == "TESTUSER"
    assert data["sap_system"] == "DEV"
    assert len(data["steps"]) == 1
    assert data["status"] == "completed"


def test_step_extracts_status_from_after_state(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="상태바 테스트")

    store.add_step(
        session.id,
        command="set",
        outcome="success",
        after_state={
            "session": {"transaction": "MM03", "screen_number": 4000},
            "status": {"message": "자재 100-100 표시됨"},
        },
    )
    reloaded = store.load(session.id)
    assert reloaded.steps[0].status_message == "자재 100-100 표시됨"
    assert reloaded.steps[0].after_screen == 4000


def test_auto_log_steps_disabled(tmp_path):
    config = SessionConfig(log_dir=str(tmp_path / "sessions"), auto_log_steps=False)
    store = SessionStore(config)
    assert store.auto_log_steps is False


def test_path_traversal_blocked(tmp_path):
    import pytest
    store = _make_store(tmp_path)
    with pytest.raises(ValueError, match="잘못된 session_id"):
        store.load("../../../etc/passwd")
    with pytest.raises(ValueError, match="잘못된 session_id"):
        store.delete("../malicious")
    with pytest.raises(ValueError, match="잘못된 session_id"):
        store.add_step("not-hex-id!", command="run")


def test_valid_session_id_format(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="ID 형식 테스트")
    import re
    assert re.match(r"^[a-f0-9]{12}$", session.id)


def test_atomic_write_survives_concurrent_reads(tmp_path):
    store = _make_store(tmp_path)
    session = store.create_session(user_request="동시성 테스트")
    for i in range(10):
        store.add_step(session.id, command=f"cmd_{i}", outcome="success")
    reloaded = store.load(session.id)
    assert len(reloaded.steps) == 10
