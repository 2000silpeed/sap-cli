"""시맨틱 검색 — synonyms + domain_context 기반 퍼지 매칭."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .question_parser import KOREAN_TO_FIELD, KOREAN_TO_TCODE
from .store import OntologyStore


@dataclass
class SemanticMatch:
    sap_field_name: str
    score: float
    match_source: str
    tcode: str = ""
    screen_number: int = 0
    label: str = ""
    description: str = ""
    domain_context: str = ""
    synonyms: list[str] = field(default_factory=list)
    validation: str = ""
    changeable: bool = True


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def _token_overlap_score(query_tokens: set[str], target: str) -> float:
    if not target:
        return 0.0
    target_tokens = set(_normalize(target).split())
    if not target_tokens:
        return 0.0
    overlap = query_tokens & target_tokens
    return len(overlap) / max(len(query_tokens), 1)


def _substring_score(query: str, target: str) -> float:
    if not target:
        return 0.0
    q = _normalize(query)
    t = _normalize(target)
    if q in t or t in q:
        return 1.0
    return 0.0


def semantic_field_search(store: OntologyStore, query: str) -> list[SemanticMatch]:
    """synonyms, description, domain_context를 활용한 퍼지 필드 검색."""
    q_norm = _normalize(query)
    if not q_norm:
        return []
    q_tokens = set(q_norm.split())

    resolved = KOREAN_TO_FIELD.get(query.strip()) or KOREAN_TO_FIELD.get(q_norm)
    if resolved:
        q_tokens.add(resolved.lower())

    matches: dict[str, SemanticMatch] = {}

    for tcode in store.list_transactions():
        ont = store.load_transaction(tcode)
        if not ont:
            continue
        for sn, sc in ont.screens.items():
            for f in sc.fields:
                sem = f.semantic
                fname = (sem.sap_field_name or f.name).upper()

                best_score = 0.0
                best_source = ""

                if q_norm == fname.lower() or q_norm == f.name.lower():
                    best_score, best_source = 1.0, "exact_name"
                elif resolved and resolved.upper() == fname:
                    best_score, best_source = 0.95, "korean_mapping"
                elif _substring_score(q_norm, f.label) > 0:
                    best_score, best_source = 0.9, "label"
                else:
                    for syn in sem.synonyms + sem.synonyms_ko:
                        if _substring_score(q_norm, syn) > 0:
                            best_score, best_source = 0.85, f"synonym:{syn}"
                            break

                    if best_score == 0:
                        s = _substring_score(q_norm, sem.description)
                        if s > 0:
                            best_score, best_source = 0.7, "description"

                    if best_score == 0:
                        if _substring_score(q_norm, sem.domain_context) > 0:
                            best_score, best_source = 0.6, "domain_context"
                        else:
                            s = _token_overlap_score(q_tokens, sem.domain_context)
                            if s > 0.3:
                                best_score, best_source = 0.5 + s * 0.3, "domain_context"

                    if best_score == 0:
                        s = _token_overlap_score(q_tokens, sem.description)
                        if s > 0.3:
                            best_score, best_source = 0.4 + s * 0.2, "description_partial"

                if best_score > 0:
                    key = f"{tcode}:{sn}:{fname}"
                    if key not in matches or matches[key].score < best_score:
                        matches[key] = SemanticMatch(
                            sap_field_name=fname,
                            score=best_score,
                            match_source=best_source,
                            tcode=tcode,
                            screen_number=int(sn),
                            label=f.label,
                            description=sem.description,
                            domain_context=sem.domain_context,
                            synonyms=sem.synonyms + sem.synonyms_ko,
                            validation=sem.validation,
                            changeable=f.changeable,
                        )

    result = sorted(matches.values(), key=lambda m: -m.score)
    return result


def semantic_tcode_search(store: OntologyStore, query: str) -> list[dict[str, Any]]:
    """트랜잭션 코드를 한국어 키워드/설명으로 검색."""
    q_norm = _normalize(query)
    if not q_norm:
        return []

    resolved = KOREAN_TO_TCODE.get(query.strip()) or KOREAN_TO_TCODE.get(q_norm)
    if resolved:
        ont = store.load_transaction(resolved)
        if ont:
            return [{"tcode": ont.tcode, "title": ont.title, "module": ont.module,
                      "description": ont.description, "score": 1.0, "match_source": "korean_keyword"}]

    results: list[dict[str, Any]] = []
    for tcode in store.list_transactions():
        ont = store.load_transaction(tcode)
        if not ont:
            continue

        if q_norm == tcode.lower():
            results.append({"tcode": tcode, "title": ont.title, "module": ont.module,
                            "description": ont.description, "score": 1.0, "match_source": "exact_tcode"})
            continue

        score = 0.0
        source = ""
        if _substring_score(q_norm, ont.title) > 0:
            score, source = 0.8, "title"
        elif _substring_score(q_norm, ont.description or "") > 0:
            score, source = 0.6, "description"
        elif ont.module and _substring_score(q_norm, ont.module) > 0:
            score, source = 0.5, "module"

        if score > 0:
            results.append({"tcode": tcode, "title": ont.title, "module": ont.module,
                            "description": ont.description, "score": score, "match_source": source})

    return sorted(results, key=lambda r: -r["score"])
