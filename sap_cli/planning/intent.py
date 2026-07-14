"""Deterministic fallback parser for Korean/English SAP operation requests."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


_EXPLICIT_TCODE = re.compile(
    r"(?:\b(?:tcode|transaction|티코드|트랜잭션)\s*[:=]?\s*|/(?:n|o)|=)"
    r"(?P<tcode>/[A-Z0-9_]+(?:/[A-Z0-9_]+)+|[A-Z][A-Z0-9_/-]{1,30})",
    re.I,
)
_EXECUTE_TCODE = re.compile(
    r"\b(?P<tcode>[A-Z][A-Z0-9_/-]{1,20})\s*(?:을|를)?\s*"
    r"(?:실행|열어|오픈|run|execute)\b",
    re.I,
)
_PAIR = re.compile(
    r"(?P<key>[A-Za-z가-힣_][A-Za-z0-9가-힣_.-]*)\s*[:=]\s*"
    r"(?P<value>\"[^\"]*\"|'[^']*'|[^,\s]+)"
)
_LABELED_VALUE = re.compile(
    r"(?P<key>자재번호|회사코드|플랜트|판매조직|구매조직|문서번호|사용자|테이블|"
    r"material|company(?:\s+code)?|plant|sales(?:\s+org)?|document|table)\s*"
    r"(?:은|는|을|를|로|으로)?\s*[\"'](?P<value>[^\"']+)[\"']",
    re.I,
)
_SENSITIVE = re.compile(r"password|passwd|pwd|secret|token|pin|비밀번호|암호", re.I)


@dataclass(frozen=True)
class ParsedIntent:
    raw_text: str = field(repr=False)
    tcode: str = ""
    operation: str = "unknown"
    risk: str = "medium"
    inputs: dict[str, Any] = field(default_factory=dict, repr=False)
    sensitive_inputs: tuple[str, ...] = ()
    goal_terms: tuple[str, ...] = ()
    success_criteria: tuple[str, ...] = ()
    ambiguities: tuple[str, ...] = ()

    def redacted_summary(self) -> dict[str, Any]:
        sensitive = set(self.sensitive_inputs)
        return {
            "tcode": self.tcode,
            "operation": self.operation,
            "risk": self.risk,
            "inputs": {
                key: "<redacted>" if key in sensitive else value
                for key, value in self.inputs.items()
            },
            "goal_terms": list(self.goal_terms),
            "success_criteria": list(self.success_criteria),
            "ambiguities": list(self.ambiguities),
        }


def parse_intent(text: str) -> ParsedIntent:
    raw = str(text or "").strip()
    if not raw:
        return ParsedIntent(raw_text="", ambiguities=("request_text_missing",))
    tcode = _extract_tcode(raw)
    operation, risk = _operation(raw)
    inputs: dict[str, Any] = {}
    for pattern in (_PAIR, _LABELED_VALUE):
        for match in pattern.finditer(raw):
            key = _normalize_key(match.group("key"))
            value = match.group("value").strip("\"'")
            inputs[key] = value
    sensitive = tuple(sorted(key for key in inputs if _SENSITIVE.search(key)))
    ambiguities = []
    if not tcode:
        ambiguities.append("tcode_missing")
    if operation == "unknown":
        ambiguities.append("operation_ambiguous")
    return ParsedIntent(
        raw_text=raw,
        tcode=tcode,
        operation=operation,
        risk=risk,
        inputs=inputs,
        sensitive_inputs=sensitive,
        goal_terms=_goal_terms(raw, tcode, inputs),
        success_criteria=_success_criteria(raw),
        ambiguities=tuple(ambiguities),
    )


def _extract_tcode(text: str) -> str:
    for pattern in (_EXPLICIT_TCODE, _EXECUTE_TCODE):
        match = pattern.search(text)
        if match:
            value = match.group("tcode").strip().upper()
            return value
    return ""


def _operation(text: str) -> tuple[str, str]:
    lowered = text.casefold()
    rules = (
        ("delete", "critical", ("삭제", "delete")),
        ("post", "high", ("전기", "posting", "post ", "post하")),
        ("release", "high", ("승인", "릴리즈", "release")),
        ("save", "high", ("저장", "save")),
        ("create", "high", ("생성", "등록", "create", "신규")),
        ("change", "medium", ("변경", "수정", "change", "edit")),
        ("export", "medium", ("내보내", "다운로드", "export")),
        ("display", "low", ("조회", "표시", "확인", "display", "show", "read")),
    )
    for operation, risk, keywords in rules:
        if any(keyword in lowered for keyword in keywords):
            return operation, risk
    return "unknown", "medium"


def _normalize_key(key: str) -> str:
    aliases = {
        "자재번호": "material",
        "회사코드": "company_code",
        "플랜트": "plant",
        "판매조직": "sales_organization",
        "구매조직": "purchasing_organization",
        "문서번호": "document_number",
        "테이블": "table",
        "비밀번호": "password",
    }
    normalized = "_".join(key.strip().casefold().split())
    return aliases.get(normalized, normalized)


def _goal_terms(text: str, tcode: str, inputs: dict[str, Any]) -> tuple[str, ...]:
    scrubbed = text
    if tcode:
        scrubbed = re.sub(re.escape(tcode), " ", scrubbed, flags=re.I)
    for value in inputs.values():
        scrubbed = scrubbed.replace(str(value), " ")
    tokens = re.findall(r"[A-Za-z가-힣][A-Za-z0-9가-힣_-]{1,30}", scrubbed)
    stop = {
        "tcode", "티코드", "트랜잭션", "실행", "열어", "오픈", "조회", "표시",
        "확인", "변경", "수정", "생성", "등록", "저장", "삭제", "해줘", "해주세요",
        "전기", "승인", "릴리즈", "내보내기", "내보내", "다운로드",
        "에서", "으로", "하고", "run", "execute", "display", "show", "change", "create",
        "save", "delete", "edit", "read", "post", "posting", "release", "export", "download",
    }
    return tuple(dict.fromkeys(token for token in tokens if token.casefold() not in stop))[:12]


def _success_criteria(text: str) -> tuple[str, ...]:
    """Extract only explicitly stated completion conditions.

    The deterministic parser intentionally avoids inventing criteria.  More
    complex requests can still pass structured ``success_criteria`` through
    ``NaturalLanguageRequest``.
    """

    criteria: list[str] = []
    labeled = re.search(
        r"(?:성공\s*조건|완료\s*조건|success\s+criteria)\s*[:=]\s*(?P<body>.+)$",
        text,
        flags=re.I,
    )
    if labeled:
        criteria.extend(
            part.strip(" .")
            for part in re.split(r"\s*[;,]\s*|\s*그리고\s*", labeled.group("body"))
            if part.strip(" .")
        )
    for match in re.finditer(r"(?P<body>[^.!?]{2,100}?)(?:이면|하면)\s*성공", text):
        body = match.group("body").strip(" ,.;")
        if body:
            criteria.append(body)
    return tuple(dict.fromkeys(criteria))


__all__ = ["ParsedIntent", "parse_intent"]
