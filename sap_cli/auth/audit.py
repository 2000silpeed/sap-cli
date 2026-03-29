"""감사 로그 기록."""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .policy import AuditConfig

logger = logging.getLogger(__name__)


class AuditLogger:
    """SAP 조작 감사 로그."""

    def __init__(self, config: AuditConfig | None = None) -> None:
        if config is None:
            config = AuditConfig()
        self._config = config
        self._enabled = config.enabled
        self._log_dir = Path(config.log_dir).expanduser()
        self._log_level = config.log_level
        if self._enabled:
            self._log_dir.mkdir(parents=True, exist_ok=True)

    @property
    def enabled(self) -> bool:
        return self._enabled

    def log(
        self,
        agent: str,
        tool_name: str,
        arguments: dict[str, Any],
        allowed: bool,
        reason: str = "",
        permission: str = "",
        resource: str = "",
    ) -> None:
        if not self._enabled:
            return

        if self._log_level == "write_only" and permission == "read":
            return
        if self._log_level == "dangerous_only" and allowed:
            return

        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "agent": agent,
            "tool": tool_name,
            "resource": resource,
            "permission": permission,
            "allowed": allowed,
            "reason": reason,
        }

        # 인자에서 민감 정보 제거
        safe_args = {k: v for k, v in arguments.items() if k not in ("password", "secret")}
        entry["arguments"] = safe_args

        log_file = self._log_dir / f"audit-{datetime.now().strftime('%Y-%m-%d')}.jsonl"
        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False) + "\n")
        except Exception as e:
            logger.warning("감사 로그 기록 실패: %s", e)
