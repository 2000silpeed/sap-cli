"""Hybrid recipe retrieval for AI-oriented recipe search.

This module intentionally has no third-party dependencies.  It combines:

- BM25 lexical retrieval over recipe text
- lightweight semantic/token-overlap scoring
- exact SAP tcode and step_kind boosts
- existing recipe quality signals for reranking
"""

from __future__ import annotations

import json
import math
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Any


_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[가-힣]+")
_STEP_KIND_ALIASES = {
    "excel": {"excel", "엑셀", "xlsx", "xls", "sheet", "시트"},
    "bash": {"bash", "shell", "cli", "터미널", "스크립트", "script"},
    "external": {"external", "외부", "파일", "file", "pdf", "csv"},
    "manual": {"manual", "수동", "확인", "승인", "approve"},
    "sap": {"sap", "tcode", "transaction", "트랜잭션"},
}


@dataclass(slots=True)
class RecipeSearchResult:
    recipe: Any
    final_score: float
    bm25_score: float
    semantic_score: float
    quality_score: float
    tcode_score: float
    step_kind_score: float
    match_reasons: list[str] = field(default_factory=list)
    matched_snippets: list[str] = field(default_factory=list)

    def metadata(self) -> dict[str, Any]:
        return {
            "mode": "hybrid_bm25",
            "final_score": round(self.final_score, 4),
            "bm25_score": round(self.bm25_score, 4),
            "semantic_score": round(self.semantic_score, 4),
            "quality_score": round(self.quality_score, 4),
            "tcode_score": round(self.tcode_score, 4),
            "step_kind_score": round(self.step_kind_score, 4),
            "match_reasons": self.match_reasons,
            "matched_snippets": self.matched_snippets,
        }


def hybrid_recipe_search(
    recipes: list[Any],
    *,
    tcode: str | None = None,
    keywords: str | None = None,
    include_disabled: bool = False,
    limit: int | None = None,
) -> list[RecipeSearchResult]:
    """Return hybrid-ranked recipe search results.

    The score is intentionally deterministic and local:

    final = BM25 + semantic overlap + exact boosts + quality reranking.
    """
    query = _build_query(tcode, keywords)
    query_tokens = _tokenize(query)
    if not query_tokens:
        return []

    candidates = [r for r in recipes if include_disabled or not getattr(r, "disabled", False)]
    if not candidates:
        return []

    docs = [_recipe_document(r) for r in candidates]
    tokenized_docs = [_tokenize(doc["text"]) for doc in docs]
    avgdl = sum(len(tokens) for tokens in tokenized_docs) / max(len(tokenized_docs), 1)
    df = _document_frequency(tokenized_docs)
    bm25_raw = [
        _bm25(query_tokens, tokens, df, doc_count=len(tokenized_docs), avgdl=avgdl)
        for tokens in tokenized_docs
    ]
    max_bm25 = max(bm25_raw) if bm25_raw else 0.0

    results: list[RecipeSearchResult] = []
    for recipe, doc, doc_tokens, raw_bm25 in zip(candidates, docs, tokenized_docs, bm25_raw):
        bm25_score = raw_bm25 / max_bm25 if max_bm25 > 0 else 0.0
        semantic_score = _semantic_overlap(query_tokens, doc_tokens, query, doc["text"])
        tcode_score = _tcode_score(recipe, tcode)
        step_kind_score = _step_kind_score(recipe, query_tokens)
        quality_score = _quality_score(recipe)

        has_retrieval_match = any(
            score > 0
            for score in (bm25_score, semantic_score, tcode_score, step_kind_score)
        )
        if not has_retrieval_match:
            continue

        final_score = (
            bm25_score * 0.40
            + semantic_score * 0.25
            + tcode_score * 0.15
            + step_kind_score * 0.08
            + quality_score * 0.12
        )
        if final_score <= 0:
            continue

        reasons = _match_reasons(
            bm25_score=bm25_score,
            semantic_score=semantic_score,
            tcode_score=tcode_score,
            step_kind_score=step_kind_score,
            quality_score=quality_score,
        )
        snippets = _matched_snippets(query_tokens, doc)
        results.append(
            RecipeSearchResult(
                recipe=recipe,
                final_score=round(final_score, 6),
                bm25_score=bm25_score,
                semantic_score=semantic_score,
                quality_score=quality_score,
                tcode_score=tcode_score,
                step_kind_score=step_kind_score,
                match_reasons=reasons,
                matched_snippets=snippets,
            )
        )

    results.sort(
        key=lambda r: (
            -r.final_score,
            -r.quality_score,
            -getattr(r.recipe, "success_count", 0),
            getattr(r.recipe, "id", ""),
        )
    )
    return results[:limit] if limit else results


def _build_query(tcode: str | None, keywords: str | None) -> str:
    parts = []
    if tcode:
        parts.append(str(tcode))
    if keywords:
        parts.append(str(keywords))
    return " ".join(parts).strip()


def _tokenize(text: str) -> list[str]:
    tokens: list[str] = []
    for raw in _TOKEN_RE.findall((text or "").lower()):
        if len(raw) < 2:
            continue
        tokens.append(raw)
        if re.fullmatch(r"[가-힣]+", raw) and len(raw) >= 3:
            tokens.extend(raw[i : i + 2] for i in range(len(raw) - 1))
    return tokens


def _recipe_document(recipe: Any) -> dict[str, Any]:
    fields: list[tuple[str, str]] = []
    fields.append(("id", str(getattr(recipe, "id", ""))))
    fields.append(("description", str(getattr(recipe, "description", ""))))
    fields.append(("context", str(getattr(recipe, "context", ""))))
    fields.append(("keywords", " ".join(str(k) for k in getattr(recipe, "keywords", []))))
    fields.append(("tcode_sequence", " ".join(_extract_tcodes(recipe))))

    step_texts: list[str] = []
    for step in getattr(recipe, "steps", []):
        args = getattr(step, "args", {}) or {}
        step_texts.append(
            " ".join(
                [
                    str(getattr(step, "tool", "")),
                    str(getattr(step, "hint", "")),
                    str(getattr(step, "step_kind", "")),
                    str(getattr(step, "cli_invocation", "")),
                    json.dumps(args, ensure_ascii=False, sort_keys=True),
                ]
            )
        )
    fields.append(("steps", "\n".join(step_texts)))

    # Repeat high-signal fields to approximate field weighting in BM25.
    weighted_text = " ".join(
        [
            str(getattr(recipe, "id", "")) * 1,
            (str(getattr(recipe, "description", "")) + " ") * 3,
            (" ".join(str(k) for k in getattr(recipe, "keywords", [])) + " ") * 4,
            (" ".join(_extract_tcodes(recipe)) + " ") * 5,
            str(getattr(recipe, "context", "")),
            "\n".join(step_texts),
        ]
    )
    return {
        "text": weighted_text,
        "fields": fields,
    }


def _extract_tcodes(recipe: Any) -> list[str]:
    tcodes: list[str] = []
    explicit = getattr(recipe, "tcode_sequence", None)
    if explicit:
        tcodes.extend(str(t).upper() for t in explicit if str(t).strip())
    for step in getattr(recipe, "steps", []):
        if getattr(step, "tool", "") not in {"sap_run", "sap_command"}:
            continue
        args = getattr(step, "args", {}) or {}
        value = str(args.get("tcode", args.get("cmd", ""))).upper().strip()
        if value:
            tcodes.append(value)
    return sorted(set(tcodes))


def _document_frequency(tokenized_docs: list[list[str]]) -> Counter[str]:
    df: Counter[str] = Counter()
    for tokens in tokenized_docs:
        df.update(set(tokens))
    return df


def _bm25(
    query_tokens: list[str],
    doc_tokens: list[str],
    df: Counter[str],
    *,
    doc_count: int,
    avgdl: float,
    k1: float = 1.5,
    b: float = 0.75,
) -> float:
    if not query_tokens or not doc_tokens:
        return 0.0
    tf = Counter(doc_tokens)
    dl = len(doc_tokens)
    score = 0.0
    for token in set(query_tokens):
        freq = tf.get(token, 0)
        if freq <= 0:
            continue
        n = df.get(token, 0)
        idf = math.log(1 + (doc_count - n + 0.5) / (n + 0.5))
        denom = freq + k1 * (1 - b + b * dl / max(avgdl, 1.0))
        score += idf * (freq * (k1 + 1)) / max(denom, 1e-9)
    return score


def _semantic_overlap(
    query_tokens: list[str],
    doc_tokens: list[str],
    query: str,
    doc_text: str,
) -> float:
    q = set(query_tokens)
    d = set(doc_tokens)
    if not q or not d:
        return 0.0
    overlap = len(q & d) / max(len(q), 1)
    jaccard = len(q & d) / max(len(q | d), 1)
    substring = 0.15 if query.lower() in doc_text.lower() else 0.0
    return min(1.0, overlap * 0.75 + jaccard * 0.25 + substring)


def _tcode_score(recipe: Any, tcode: str | None) -> float:
    if not tcode:
        return 0.0
    target = str(tcode).upper().strip()
    if not target:
        return 0.0
    tcodes = _extract_tcodes(recipe)
    if target in tcodes:
        return 1.0
    rid = str(getattr(recipe, "id", "")).upper()
    return 0.65 if target in rid else 0.0


def _step_kind_score(recipe: Any, query_tokens: list[str]) -> float:
    q = set(query_tokens)
    wanted = {
        kind
        for kind, aliases in _STEP_KIND_ALIASES.items()
        if q & aliases
    }
    if not wanted:
        return 0.0
    kinds = {str(getattr(step, "step_kind", "")).lower() for step in getattr(recipe, "steps", [])}
    return len(wanted & kinds) / max(len(wanted), 1)


def _quality_score(recipe: Any) -> float:
    try:
        from .recipe import efficiency_score

        return min(max(float(efficiency_score(recipe)), 0.0), 1.0)
    except Exception:
        return 0.0


def _match_reasons(
    *,
    bm25_score: float,
    semantic_score: float,
    tcode_score: float,
    step_kind_score: float,
    quality_score: float,
) -> list[str]:
    reasons: list[str] = []
    if tcode_score >= 1.0:
        reasons.append("tcode_exact")
    elif tcode_score > 0:
        reasons.append("tcode_partial")
    if bm25_score > 0:
        reasons.append("bm25")
    if semantic_score >= 0.5:
        reasons.append("semantic_overlap")
    if step_kind_score > 0:
        reasons.append("step_kind")
    if quality_score >= 0.5:
        reasons.append("quality")
    return reasons


def _matched_snippets(query_tokens: list[str], doc: dict[str, Any], limit: int = 3) -> list[str]:
    q = set(query_tokens)
    snippets: list[str] = []
    for name, value in doc["fields"]:
        tokens = set(_tokenize(value))
        if q & tokens:
            clean = " ".join(value.split())
            if clean:
                snippets.append(f"{name}: {clean[:180]}")
        if len(snippets) >= limit:
            break
    return snippets
