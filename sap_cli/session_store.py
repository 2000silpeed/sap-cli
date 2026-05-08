"""MCP Session Store — AI 실행 세션 CRUD."""

from __future__ import annotations

import json
import logging
import re
import threading
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .auth.policy import SessionConfig
from .ontology.schema import McpSession, SessionStep
from .storage import atomic_write_json

logger = logging.getLogger(__name__)

_SAFE_SESSION_ID = re.compile(r"^[a-f0-9]{12}$")


class SessionStore:
    """세션 저장소 — ~/.sap_cli/sessions/{date}/{session_id}.json"""

    def __init__(self, config: SessionConfig | None = None):
        cfg = config or SessionConfig()
        self._base_dir = Path(cfg.log_dir).expanduser()
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._auto_log_steps = cfg.auto_log_steps
        self._lock = threading.RLock()

    @property
    def auto_log_steps(self) -> bool:
        return self._auto_log_steps

    @staticmethod
    def _validate_id(session_id: str) -> None:
        if not _SAFE_SESSION_ID.match(session_id):
            raise ValueError(f"잘못된 session_id: {session_id!r}")

    def _date_dir(self, iso_ts: str) -> Path:
        date_str = iso_ts[:10]
        d = self._base_dir / date_str
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _find_session_path(self, session_id: str) -> Path | None:
        self._validate_id(session_id)
        for date_dir in sorted(self._base_dir.iterdir(), reverse=True):
            if not date_dir.is_dir():
                continue
            p = date_dir / f"{session_id}.json"
            if p.exists():
                return p
        return None

    def create_session(
        self,
        *,
        user_request: str = "",
        ai_reasoning: str = "",
        agent: str = "",
        sap_user: str = "",
        sap_system: str = "",
        source: str = "mcp",
    ) -> McpSession:
        now = datetime.now(timezone.utc).isoformat()
        session = McpSession(
            id=uuid.uuid4().hex[:12],
            started_at=now,
            status="running",
            agent=agent,
            sap_user=sap_user,
            sap_system=sap_system,
            source=source,
            user_request=user_request,
            ai_reasoning=ai_reasoning,
        )
        with self._lock:
            self._save(session)
        return session

    def add_step(
        self,
        session_id: str,
        *,
        command: str,
        tool_name: str = "",
        arguments: dict[str, Any] | None = None,
        source: str = "",
        cli_invocation: str = "",
        raw_argv: list[str] | None = None,
        intent: str = "",
        outcome: str = "",
        error: str = "",
        before_state: dict[str, Any] | None = None,
        after_state: dict[str, Any] | None = None,
        status_message: str = "",
    ) -> SessionStep | None:
        with self._lock:
            session = self.load(session_id)
            if session is None or session.status != "running":
                return None

            before_tx, before_scr = "", 0
            if before_state and isinstance(before_state.get("session"), dict):
                s = before_state["session"]
                before_tx = s.get("transaction", "")
                before_scr = s.get("screen_number", 0)

            after_tx, after_scr = "", 0
            if after_state and isinstance(after_state.get("session"), dict):
                s = after_state["session"]
                after_tx = s.get("transaction", "")
                after_scr = s.get("screen_number", 0)

            if not status_message and after_state and isinstance(after_state.get("status"), dict):
                status_message = after_state["status"].get("message", "")

            step = SessionStep(
                step_number=len(session.steps),
                timestamp=datetime.now(timezone.utc).isoformat(),
                command=command,
                tool_name=tool_name,
                arguments=arguments or {},
                source=source,
                cli_invocation=cli_invocation,
                raw_argv=raw_argv or [],
                intent=intent,
                outcome=outcome,
                error=error,
                before_transaction=before_tx,
                before_screen=before_scr,
                after_transaction=after_tx,
                after_screen=after_scr,
                status_message=status_message,
            )
            session.steps.append(step)
            self._save(session)
            return step

    def finish_session(
        self,
        session_id: str,
        *,
        outcome: str = "success",
        outcome_summary: str = "",
    ) -> McpSession | None:
        with self._lock:
            session = self.load(session_id)
            if session is None:
                return None

            session.status = "completed" if outcome == "success" else outcome
            session.outcome = outcome
            session.outcome_summary = outcome_summary
            session.finished_at = datetime.now(timezone.utc).isoformat()
            self._save(session)
            return session

    def load(self, session_id: str) -> McpSession | None:
        path = self._find_session_path(session_id)
        if path is None:
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return McpSession.model_validate(data)

    def list_sessions(
        self,
        *,
        date: str | None = None,
        status: str | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        dirs = sorted(self._base_dir.iterdir(), reverse=True) if self._base_dir.exists() else []

        for date_dir in dirs:
            if not date_dir.is_dir():
                continue
            if date and date_dir.name != date:
                continue
            for p in sorted(date_dir.glob("*.json"), reverse=True):
                try:
                    data = json.loads(p.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if status and data.get("status") != status:
                    continue
                results.append({
                    "id": data.get("id", p.stem),
                    "started_at": data.get("started_at", ""),
                    "status": data.get("status", ""),
                    "outcome": data.get("outcome", ""),
                    "user_request": data.get("user_request", "")[:100],
                    "step_count": len(data.get("steps", [])),
                    "candidate": data.get("candidate", False),
                })
                if len(results) >= limit:
                    return results
        return results

    def delete(self, session_id: str) -> bool:
        path = self._find_session_path(session_id)
        if path is None:
            return False
        path.unlink()
        return True

    def _save(self, session: McpSession) -> Path:
        date_dir = self._date_dir(session.started_at)
        target = date_dir / f"{session.id}.json"
        data = session.model_dump(mode="json", exclude_none=True)
        atomic_write_json(target, data, mode=0o644)
        return target
