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

from sap_cli.ui_action_catalog import get_ui_action_defs


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
    "sap_connections": Permission.READ,
    "sap_session": Permission.READ,
    "sap_screen": Permission.READ,
    "sap_map_scan": Permission.READ,
    "sap_map_list": Permission.READ,
    "sap_map_search": Permission.READ,
    "sap_map_path": Permission.READ,
    "sap_map_report": Permission.READ,
    "sap_map_view": Permission.READ,
    "sap_map_delete": Permission.WRITE,
    "sap_map_job_status": Permission.READ,
    "sap_map_job_cancel": Permission.WRITE,
    "sap_map_augment_start": Permission.WRITE,
    "sap_map_traverse_start": Permission.EXECUTE,
    "sap_map_traverse_resume": Permission.EXECUTE,
    "sap_plan_preview": Permission.READ,
    "sap_plan_approve": Permission.ADMIN,
    "sap_plan_execute": Permission.WRITE,
    "sap_job_status": Permission.READ,
    "sap_job_reconcile": Permission.ADMIN,
    "sap_inspect_element": Permission.READ,
    "sap_scripting_query": Permission.READ,
    "sap_combo_entries": Permission.READ,
    "sap_get_field": Permission.READ,
    "sap_get_status": Permission.READ,
    "sap_table_read": Permission.READ,
    "sap_ontology_query": Permission.READ,
    "sap_transcript_query": Permission.READ,
    "sap_transcript_plan": Permission.READ,
    "sap_run": Permission.EXECUTE,
    "sap_press": Permission.EXECUTE,
    "sap_send_vkey": Permission.EXECUTE,
    "sap_command": Permission.EXECUTE,
    "sap_ui_action": Permission.WRITE,
    "sap_api_object_action": Permission.ADMIN,
    "sap_api_live_probe": Permission.ADMIN,
    "sap_set_field": Permission.WRITE,
    "sap_fill_fields": Permission.WRITE,
    "sap_learn": Permission.ADMIN,
    "sap_annotate_field": Permission.WRITE,
    "sap_annotate_dependency": Permission.WRITE,
    "sap_macro_record_step": Permission.WRITE,
    "sap_macro_control": Permission.ADMIN,
    "sap_macro_play": Permission.EXECUTE,
    "sap_macro_list": Permission.READ,
    "sap_modal": Permission.READ,
    "sap_macro_show": Permission.READ,
    "sap_macro_delete": Permission.ADMIN,
    "sap_process_list": Permission.READ,
    "sap_process_show": Permission.READ,
    "sap_process_create": Permission.ADMIN,
    "sap_process_delete": Permission.ADMIN,
    "sap_process_start": Permission.EXECUTE,
    "sap_process_step": Permission.EXECUTE,
    "sap_process_status": Permission.READ,
    "sap_table_select": Permission.EXECUTE,
    "sap_table_doubleclick": Permission.EXECUTE,
    "sap_tree_read": Permission.READ,
    "sap_tree_select": Permission.EXECUTE,
    "sap_tree_doubleclick": Permission.EXECUTE,
    "sap_tree_expand": Permission.EXECUTE,
    "sap_tree_collapse": Permission.EXECUTE,
    "sap_tree_press": Permission.EXECUTE,
    "sap_ontology_ask": Permission.READ,
    "sap_ontology_relationships": Permission.READ,
    "sap_field_dependencies": Permission.READ,
    "sap_document_flow": Permission.READ,
    "sap_process_path": Permission.READ,
    "sap_ontology_rebuild": Permission.ADMIN,
    "sap_capture": Permission.READ,
    "sap_capture_list": Permission.READ,
    "sap_recipe_save": Permission.WRITE,
    "sap_recipe_search": Permission.READ,
    "sap_recipe_show": Permission.READ,
    "sap_recipe_list": Permission.READ,
    "sap_recipe_delete": Permission.ADMIN,
    "sap_recipe_success": Permission.WRITE,
    "sap_recipe_query": Permission.READ,
    "sap_recipe_pending": Permission.READ,
    "sap_recipe_failure": Permission.WRITE,
    "sap_recipe_enable": Permission.ADMIN,
    "sap_recipe_version_up": Permission.WRITE,
    "sap_recipe_history": Permission.READ,
    "sap_recipe_chain_search": Permission.READ,
    "sap_recipe_replay_plan": Permission.READ,
    "sap_recipe_replay_execute": Permission.WRITE,
    "sap_session_start": Permission.WRITE,
    "sap_session_end": Permission.WRITE,
    "sap_session_list": Permission.READ,
    "sap_session_show": Permission.READ,
    "sap_session_candidates": Permission.READ,
    "sap_session_approve": Permission.ADMIN,
    "sap_session_mark": Permission.WRITE,
    "sap_session_excel": Permission.WRITE,
    "sap_session_pool": Permission.READ,
    "sap_session_allocate": Permission.ADMIN,
    "sap_session_release": Permission.ADMIN,
    "sap_event_subscribe": Permission.ADMIN,
    "sap_event_poll": Permission.ADMIN,
    "sap_event_unsubscribe": Permission.ADMIN,
    "sap_event_list": Permission.ADMIN,
}

TOOL_PERMISSION_MAP.update(
    {
        definition.tool_name: Permission(definition.permission)
        for definition in get_ui_action_defs()
    }
)


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
    category: str = "sensitive_transaction"
    risk_level: str = "high"
    auth_action: str = "approval_required"
    preflight_code: str = "dangerous_transaction"

    def to_risk_metadata(self) -> dict[str, str]:
        return {
            "type": "transaction",
            "id": self.tcode.upper(),
            "reason": self.reason,
            "category": self.category,
            "risk_level": self.risk_level,
            "auth_action": self.auth_action,
            "preflight_code": self.preflight_code,
        }


class DangerousReport(BaseModel):
    report: str
    reason: str = ""
    category: str = "sensitive_report"
    risk_level: str = "high"
    auth_action: str = "approval_required"
    preflight_code: str = "dangerous_report"

    def to_risk_metadata(self) -> dict[str, str]:
        return {
            "type": "report",
            "id": self.report.upper(),
            "reason": self.reason,
            "category": self.category,
            "risk_level": self.risk_level,
            "auth_action": self.auth_action,
            "preflight_code": self.preflight_code,
        }


class AuditConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "~/.sap_cli/audit"
    log_level: str = "all"


class TranscriptConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "~/.sap_cli/transcripts"
    include_reads: bool = True
    include_screen_summary: bool = False


class SessionConfig(BaseModel):
    enabled: bool = True
    log_dir: str = "~/.sap_cli/sessions"
    auto_log_steps: bool = True


class Policy(BaseModel):
    version: int = 1
    agents: dict[str, AgentPolicy] = Field(default_factory=dict)
    relation_permissions: dict[str, list[str]] = Field(default_factory=dict)
    dangerous_transactions: list[DangerousTransaction] = Field(default_factory=list)
    dangerous_reports: list[DangerousReport] = Field(default_factory=list)
    audit: AuditConfig = Field(default_factory=AuditConfig)
    transcript: TranscriptConfig = Field(default_factory=TranscriptConfig)
    session: SessionConfig = Field(default_factory=SessionConfig)
    active_agent: str = ""

    def get_agent(self, agent_name: str) -> AgentPolicy | None:
        return self.agents.get(agent_name)

    def get_dangerous_tcodes(self) -> dict[str, str]:
        return {d.tcode.upper(): d.reason for d in self.dangerous_transactions}

    def get_dangerous_transaction_details(self) -> dict[str, DangerousTransaction]:
        return {d.tcode.upper(): d for d in self.dangerous_transactions}

    def get_dangerous_report_details(self) -> dict[str, DangerousReport]:
        return {d.report.upper(): d for d in self.dangerous_reports}

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
