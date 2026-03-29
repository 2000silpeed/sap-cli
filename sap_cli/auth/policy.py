"""ReBAC 정책 정의 및 로드."""

from __future__ import annotations

import fnmatch
from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

try:
    import yaml
    HAS_YAML = True
except ImportError:
    HAS_YAML = False

import json


class Permission(str, Enum):
    READ = "read"
    EXECUTE = "execute"
    WRITE = "write"
    ADMIN = "admin"


class Relation(str, Enum):
    VIEWER = "viewer"
    OPERATOR = "operator"
    SUPERVISOR = "supervisor"
    OWNER = "owner"


RELATION_PERMISSIONS: dict[Relation, set[Permission]] = {
    Relation.VIEWER: {Permission.READ},
    Relation.OPERATOR: {Permission.READ, Permission.EXECUTE, Permission.WRITE},
    Relation.SUPERVISOR: {Permission.READ, Permission.EXECUTE, Permission.WRITE, Permission.ADMIN},
    Relation.OWNER: {Permission.READ, Permission.EXECUTE, Permission.WRITE, Permission.ADMIN},
}

TOOL_PERMISSION_MAP: dict[str, Permission] = {
    "sap_connect": Permission.ADMIN,
    "sap_session": Permission.READ,
    "sap_screen": Permission.READ,
    "sap_get_field": Permission.READ,
    "sap_get_status": Permission.READ,
    "sap_table_read": Permission.READ,
    "sap_ontology_query": Permission.READ,
    "sap_run": Permission.EXECUTE,
    "sap_press": Permission.EXECUTE,
    "sap_send_vkey": Permission.EXECUTE,
    "sap_set_field": Permission.WRITE,
    "sap_learn": Permission.ADMIN,
    "sap_macro_record_step": Permission.WRITE,
    "sap_macro_control": Permission.ADMIN,
    "sap_macro_play": Permission.EXECUTE,
    "sap_macro_list": Permission.READ,
}


class RelationBinding(BaseModel):
    resource: str
    relation: Relation


class ApprovalRule(BaseModel):
    permission: Permission
    transactions: list[str] = Field(default_factory=list)


class AgentPolicy(BaseModel):
    description: str = ""
    relations: list[RelationBinding] = Field(default_factory=list)
    requires_approval: list[ApprovalRule] = Field(default_factory=list)


class DangerousTransaction(BaseModel):
    tcode: str
    reason: str = ""


class AuditConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "~/.sap_cli/audit"
    log_level: str = "all"


class Policy(BaseModel):
    version: int = 1
    agents: dict[str, AgentPolicy] = Field(default_factory=dict)
    relation_permissions: dict[str, list[str]] = Field(default_factory=dict)
    dangerous_transactions: list[DangerousTransaction] = Field(default_factory=list)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    active_agent: str = ""

    def get_agent(self, agent_name: str) -> AgentPolicy | None:
        return self.agents.get(agent_name)

    def get_dangerous_tcodes(self) -> dict[str, str]:
        return {d.tcode.upper(): d.reason for d in self.dangerous_transactions}

    def resolve_relation_permissions(self, relation: Relation) -> set[Permission]:
        custom = self.relation_permissions.get(relation.value)
        if custom:
            return {Permission(p) for p in custom}
        return RELATION_PERMISSIONS.get(relation, set())


def load_policy(path: str | Path | None = None) -> Policy | None:
    """정책 파일 로드. 없으면 None (전체 허용 모드)."""
    if path is None:
        path = Path.home() / ".sap_cli" / "policy.yaml"
    path = Path(path)

    if not path.exists():
        json_path = path.with_suffix(".json")
        if json_path.exists():
            path = json_path
        else:
            return None

    text = path.read_text(encoding="utf-8")

    if path.suffix in (".yaml", ".yml"):
        if not HAS_YAML:
            raise ImportError("pyyaml이 필요합니다: pip install pyyaml")
        data = yaml.safe_load(text)
    else:
        data = json.loads(text)

    return Policy.model_validate(data)


def match_resource(pattern: str, resource: str) -> bool:
    """와일드카드 패턴으로 리소스 매칭. 예: 'tx:MM*' matches 'tx:MM03'."""
    return fnmatch.fnmatch(resource, pattern)
