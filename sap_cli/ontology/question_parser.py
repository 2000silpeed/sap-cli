"""온톨로지 질문 파서 — 자연어 질문에서 tcode/필드명/질문 유형 추출."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any


class QuestionType(IntEnum):
    REQUIRED_FIELDS = 4
    FIELD_MEANING = 2
    FIELD_CROSS = 3
    FIELD_DEPENDENCY = 6
    PROCESS = 5
    TRANSACTION_SUMMARY = 1
    PATTERN = 7


@dataclass
class ParsedQuestion:
    question_type: QuestionType
    tcodes: list[str] = field(default_factory=list)
    fields: list[str] = field(default_factory=list)
    process_keywords: list[str] = field(default_factory=list)
    pattern_keywords: list[str] = field(default_factory=list)
    raw: str = ""


KOREAN_TO_TCODE: dict[str, str] = {
    "자재 조회": "MM03", "자재조회": "MM03", "자재 마스터": "MM03", "자재마스터": "MM03",
    "판매오더": "VA01", "판매 오더": "VA01", "수주": "VA01",
    "구매오더": "ME21N", "구매 오더": "ME21N", "발주": "ME21N",
    "입고": "MIGO", "자재입고": "MIGO", "자재 입고": "MIGO", "물품수령": "MIGO",
    "송장": "MIRO", "송장검증": "MIRO", "송장 검증": "MIRO", "인보이스": "MIRO",
    "출하": "VL01N", "납품": "VL01N",
    "대금청구": "VF01", "대금 청구": "VF01", "빌링": "VF01",
}

KOREAN_TO_FIELD: dict[str, str] = {
    "자재번호": "MATNR", "품번": "MATNR",
    "자재유형": "MTART",
    "자재내역": "MAKTX", "품명": "MAKTX",
    "기본단위": "MEINS",
    "플랜트": "WERKS", "공장": "WERKS",
    "저장위치": "LGORT", "창고": "LGORT",
    "오더유형": "AUART",
    "판매조직": "VKORG",
    "유통채널": "VTWEG",
    "제품군": "SPART",
    "고객번호": "KUNNR", "주문자": "KUNNR",
    "납품일": "KETDAT",
    "구매오더번호": "EBELN",
    "판매오더번호": "VBELN",
    "자재문서번호": "MBLNR",
    "전기일": "BUDAT",
    "회사코드": "BUKRS",
}

KOREAN_TO_PROCESS: dict[str, str] = {
    "조달": "procurement_basic", "구매 흐름": "procurement_basic", "구매흐름": "procurement_basic",
    "판매 흐름": "sales_to_billing", "판매흐름": "sales_to_billing",
    "SD 흐름": "sales_to_billing", "SD흐름": "sales_to_billing",
    "판매 프로세스": "sales_to_billing", "판매프로세스": "sales_to_billing",
    "조달 프로세스": "procurement_basic", "조달프로세스": "procurement_basic",
    "구매 프로세스": "procurement_basic", "구매프로세스": "procurement_basic",
}

_ASCII_BOUND_L = r"(?<![A-Za-z0-9])"
_ASCII_BOUND_R = r"(?![A-Za-z0-9])"
_TCODE_PATTERN = re.compile(_ASCII_BOUND_L + r"([A-Z]{2,4}\d{1,4}[A-Z]?N?)" + _ASCII_BOUND_R, re.IGNORECASE)
_SAP_FIELD_PATTERN = re.compile(_ASCII_BOUND_L + r"([A-Z]{3,}[A-Z0-9]*)" + _ASCII_BOUND_R)

_REQUIRED_KW = re.compile(r"필수|required|반드시|꼭\s*입력")
_MEANING_KW = re.compile(r"뭐야|뭔가요|의미|설명|무엇|뭘|무슨")
_CROSS_KW = re.compile(r"어디|사용|나와|나오|위치|어떤\s*트랜잭션")
_DEPENDENCY_KW = re.compile(r"종속|영향|바꾸면|결정|depends|impact|변경")
_PROCESS_KW = re.compile(r"프로세스|흐름|절차|순서|단계")
_PATTERN_KW = re.compile(r"저장|F4|검색\s*도움말|패턴|save|search\s*help")


def parse_question(question: str) -> ParsedQuestion:
    raw = question.strip()
    upper = raw.upper()

    tcodes = _extract_tcodes(raw)
    fields = _extract_fields(raw)
    process_kw = _extract_process_keywords(raw)
    pattern_kw = _extract_pattern_keywords(raw)

    qtype = _classify(raw, upper, tcodes, fields, process_kw, pattern_kw)

    return ParsedQuestion(
        question_type=qtype,
        tcodes=tcodes,
        fields=fields,
        process_keywords=process_kw,
        pattern_keywords=pattern_kw,
        raw=raw,
    )


def _extract_tcodes(text: str) -> list[str]:
    found: list[str] = []
    for kw, tcode in KOREAN_TO_TCODE.items():
        if kw in text:
            if tcode not in found:
                found.append(tcode)
    for m in _TCODE_PATTERN.finditer(text):
        tc = m.group(1).upper()
        if tc not in found and len(tc) >= 3:
            found.append(tc)
    return found


def _extract_fields(text: str) -> list[str]:
    found: list[str] = []
    for kw, fld in KOREAN_TO_FIELD.items():
        if kw in text:
            if fld not in found:
                found.append(fld)
    for m in _SAP_FIELD_PATTERN.finditer(text):
        candidate = m.group(1)
        if candidate in KOREAN_TO_TCODE.values():
            continue
        if len(candidate) >= 4 and candidate not in found:
            found.append(candidate)
    return found


def _extract_process_keywords(text: str) -> list[str]:
    found: list[str] = []
    for kw, proc_id in KOREAN_TO_PROCESS.items():
        if kw in text:
            if proc_id not in found:
                found.append(proc_id)
    return found


def _extract_pattern_keywords(text: str) -> list[str]:
    found: list[str] = []
    for m in _PATTERN_KW.finditer(text):
        kw = m.group(0)
        if kw not in found:
            found.append(kw)
    return found


def _classify(
    raw: str,
    upper: str,
    tcodes: list[str],
    fields: list[str],
    process_kw: list[str],
    pattern_kw: list[str],
) -> QuestionType:
    # 우선순위 순서대로 매칭
    if tcodes and _REQUIRED_KW.search(raw):
        return QuestionType.REQUIRED_FIELDS
    if fields and _MEANING_KW.search(raw):
        return QuestionType.FIELD_MEANING
    if fields and _CROSS_KW.search(raw):
        return QuestionType.FIELD_CROSS
    if fields and _DEPENDENCY_KW.search(raw):
        return QuestionType.FIELD_DEPENDENCY
    if pattern_kw and not process_kw:
        return QuestionType.PATTERN
    if _PROCESS_KW.search(raw) or process_kw:
        return QuestionType.PROCESS
    if tcodes and not fields:
        return QuestionType.TRANSACTION_SUMMARY
    if fields:
        return QuestionType.FIELD_MEANING
    if tcodes:
        return QuestionType.TRANSACTION_SUMMARY
    return QuestionType.TRANSACTION_SUMMARY
