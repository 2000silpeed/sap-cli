"""AI 네비게이션 레시피 - 성공한 SAP 조작 경로를 저장/검색.

매크로(VBS)와 다르게, AI에게 힌트를 주는 방식.
AI가 레시피를 참고하여 상황 판단하면서 따라감.
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError

_log = logging.getLogger(__name__)


def _fsync_dir_best_effort(directory: Path) -> None:
    """디렉토리 fsync. POSIX에선 rename durability 보장, Windows에선 no-op."""
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return  # Windows는 디렉토리 열기 불가 → skip
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


class RecipeStep(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    hint: str = ""


class Recipe(BaseModel):
    id: str
    description: str
    context: str = ""
    keywords: list[str] = Field(default_factory=list)
    steps: list[RecipeStep] = Field(default_factory=list)
    created_at: float = Field(default_factory=time.time)
    updated_at: float = Field(default_factory=time.time)
    success_count: int = 0
    source_session: str = ""
    approved_by: str = ""


class RecipeStore:
    """레시피 JSON 저장/로드/관리.

    저장 경로: ~/.sap_cli/recipes/
    """

    def __init__(self, store_dir: str | Path | None = None) -> None:
        if store_dir is None:
            self._dir = Path.home() / ".sap_cli" / "recipes"
        else:
            self._dir = Path(store_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, recipe_id: str) -> Path:
        if not recipe_id or not _SAFE_ID.match(recipe_id):
            raise ValueError(f"잘못된 recipe_id: {recipe_id!r}")
        return self._dir / f"{recipe_id}.json"

    def save(self, recipe: Recipe) -> None:
        recipe.updated_at = time.time()
        final_path = self._path(recipe.id)
        tmp_path = final_path.with_suffix(final_path.suffix + f".tmp-{os.getpid()}-{time.time_ns()}")
        try:
            data = recipe.model_dump_json(indent=2).encode("utf-8")
            fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
            try:
                view = memoryview(data)
                written = 0
                while written < len(view):
                    n = os.write(fd, view[written:])
                    if n <= 0:
                        raise OSError(f"short write: {written}/{len(view)} bytes")
                    written += n
                if hasattr(os, "fsync"):
                    os.fsync(fd)  # real flush error (EIO/ENOSPC) must surface
            finally:
                os.close(fd)
            os.replace(tmp_path, final_path)
            _fsync_dir_best_effort(self._dir)
        except Exception:
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except OSError:
                    pass
            raise

    def load(self, recipe_id: str) -> Recipe | None:
        path = self._path(recipe_id)
        if not path.exists():
            return None
        return self._safe_read(path)

    def delete(self, recipe_id: str) -> bool:
        path = self._path(recipe_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def _safe_read(self, path: Path) -> Recipe | None:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            _log.warning("I/O error reading recipe %s: %s", path, exc)
            return None
        try:
            return Recipe.model_validate_json(text)
        except (ValidationError, json.JSONDecodeError, ValueError) as exc:
            _log.warning("corrupt recipe JSON at %s: %s", path, exc)
            return None

    def list_all(self) -> list[dict[str, Any]]:
        results = []
        for path in sorted(self._dir.glob("*.json")):
            r = self._safe_read(path)
            if r is None:
                continue
            results.append({
                "id": r.id,
                "description": r.description,
                "context": r.context,
                "keywords": r.keywords,
                "step_count": len(r.steps),
                "success_count": r.success_count,
            })
        return results

    def search(self, query: str) -> list[Recipe]:
        query_lower = query.lower()
        query_words = query_lower.split()
        results: list[tuple[int, Recipe]] = []

        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is None:
                continue

            score = 0
            desc_lower = r.description.lower()
            ctx_lower = r.context.lower()
            kw_lower = [k.lower() for k in r.keywords]

            for word in query_words:
                if any(word in k for k in kw_lower):
                    score += 3
                if word in desc_lower:
                    score += 1
                if word in ctx_lower:
                    score += 2
                if word in r.id.lower():
                    score += 2

            if score > 0:
                results.append((score, r))

        results.sort(key=lambda x: (-x[0], -x[1].success_count))
        return [r for _, r in results]

    def increment_success(self, recipe_id: str) -> None:
        recipe = self.load(recipe_id)
        if recipe:
            recipe.success_count += 1
            self.save(recipe)

    def find_by_tcode(self, tcode: str) -> list[Recipe]:
        """특정 트랜잭션과 연관된 레시피 검색."""
        results = []
        tcode_upper = tcode.upper()
        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is None:
                continue
            linked = any(
                s.tool in ("sap_run", "sap_command") and
                tcode_upper in str(s.args.get("tcode", s.args.get("cmd", ""))).upper()
                for s in r.steps
            )
            if linked or tcode_upper in r.id.upper():
                results.append(r)
        results.sort(key=lambda r: -r.success_count)
        return results

    def query_for_ai(
        self,
        tcode: str | None = None,
        keywords: str | None = None,
    ) -> list[tuple[int, Recipe]]:
        """tcode + 키워드 통합 검색. (score, Recipe) 반환."""
        all_recipes = self._load_all()
        results: list[tuple[int, Recipe]] = []
        tcode_upper = tcode.upper() if tcode else None
        kw_words = keywords.lower().split() if keywords else []

        for r in all_recipes:
            score = 0
            if tcode_upper:
                for s in r.steps:
                    if s.tool in ("sap_run", "sap_command"):
                        val = str(s.args.get("tcode", s.args.get("cmd", ""))).upper()
                        if tcode_upper in val:
                            score += 5
                            break
                if tcode_upper in r.id.upper():
                    score += 3

            if kw_words:
                desc_lower = r.description.lower()
                ctx_lower = r.context.lower()
                kw_lower = [k.lower() for k in r.keywords]
                for word in kw_words:
                    if any(word in k for k in kw_lower):
                        score += 3
                    if word in desc_lower:
                        score += 1
                    if word in ctx_lower:
                        score += 2

            if score > 0:
                results.append((score, r))

        results.sort(key=lambda x: (-x[0], -x[1].success_count))
        return results

    def _load_all(self) -> list[Recipe]:
        recipes = []
        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is not None:
                recipes.append(r)
        return recipes

    def find_similar(
        self,
        description: str,
        context: str,
        keywords: list[str],
        steps: list[RecipeStep],
        exclude_id: str | None = None,
        threshold: float = 0.6,
    ) -> list[tuple[float, Recipe]]:
        """새 레시피와 유사한 기존 레시피 탐지. (similarity, Recipe) 반환.

        유사도 = 키워드 Jaccard(0.35) + tcode 시퀀스 일치(0.35) + 설명/컨텍스트 단어 겹침(0.30)
        """
        new_kw = {k.lower() for k in keywords if k.strip()}
        new_tcodes = _extract_tcode_sequence(steps)
        new_words = _tokenize(description) | _tokenize(context)

        results: list[tuple[float, Recipe]] = []
        for r in self._load_all():
            if exclude_id and r.id == exclude_id:
                continue
            sim = _compute_similarity(
                new_kw, new_tcodes, new_words,
                {k.lower() for k in r.keywords if k.strip()},
                _extract_tcode_sequence(r.steps),
                _tokenize(r.description) | _tokenize(r.context),
            )
            if sim >= threshold:
                results.append((sim, r))
        results.sort(key=lambda x: (-x[0], -x[1].success_count))
        return results


def _tokenize(text: str) -> set[str]:
    if not text:
        return set()
    lowered = text.lower()
    tokens = re.findall(r"[\w가-힣]+", lowered)
    return {t for t in tokens if len(t) >= 2}


def _extract_tcode_sequence(steps: list[RecipeStep]) -> tuple[str, ...]:
    seq: list[str] = []
    for s in steps:
        if s.tool in ("sap_run", "sap_command"):
            val = str(s.args.get("tcode", s.args.get("cmd", ""))).upper().strip()
            if val:
                seq.append(val)
    return tuple(seq)


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _sequence_match(a: tuple[str, ...], b: tuple[str, ...]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    set_a, set_b = set(a), set(b)
    overlap = len(set_a & set_b) / max(len(set_a | set_b), 1)
    order_bonus = 0.0
    if a == b:
        order_bonus = 0.2
    elif a and b and a[0] == b[0]:
        order_bonus = 0.1
    return min(1.0, overlap + order_bonus)


def _compute_similarity(
    new_kw: set[str], new_tcodes: tuple[str, ...], new_words: set[str],
    old_kw: set[str], old_tcodes: tuple[str, ...], old_words: set[str],
) -> float:
    kw_score = _jaccard(new_kw, old_kw)
    tcode_score = _sequence_match(new_tcodes, old_tcodes)
    word_score = _jaccard(new_words, old_words)
    return round(kw_score * 0.35 + tcode_score * 0.35 + word_score * 0.30, 3)


def efficiency_score(recipe: Recipe) -> float:
    """레시피 효율성 점수. 단계 적음(+), success_count 높음(+), context 풍부함(+), 최신(+)."""
    step_score = 1.0 / (1 + len(recipe.steps))
    success_score = min(recipe.success_count / 10.0, 1.0)
    context_score = min(len(recipe.context) / 500.0, 1.0)
    recency_score = min(max(recipe.updated_at, 0) / 1e10, 1.0)
    return round(
        step_score * 0.35
        + success_score * 0.35
        + context_score * 0.20
        + recency_score * 0.10,
        4,
    )
