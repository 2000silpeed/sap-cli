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
    failure_count: int = 0
    last_failed_at: float = 0.0
    disabled: bool = False
    disabled_reason: str = ""
    version: int = 1
    previous_version_archive: str = ""
    tcode_sequence: list[str] = Field(default_factory=list)
    source_session: str = ""
    approved_by: str = ""


# 자동 비활성화 임계값 — total≥3 AND failure_rate≥0.5
AUTO_DISABLE_MIN_TOTAL = 3
AUTO_DISABLE_FAILURE_RATE = 0.5


def should_auto_disable(success: int, failure: int) -> bool:
    total = success + failure
    if total < AUTO_DISABLE_MIN_TOTAL:
        return False
    return (failure / total) >= AUTO_DISABLE_FAILURE_RATE


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
        self._archive_dir = self._dir / "_archive"

    def _ensure_archive_dir(self) -> None:
        self._archive_dir.mkdir(parents=True, exist_ok=True)

    def _path(self, recipe_id: str) -> Path:
        if not recipe_id or not _SAFE_ID.match(recipe_id):
            raise ValueError(f"잘못된 recipe_id: {recipe_id!r}")
        return self._dir / f"{recipe_id}.json"

    def save(self, recipe: Recipe) -> None:
        recipe.updated_at = time.time()
        recipe.tcode_sequence = list(_extract_tcode_sequence(recipe.steps))
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

    def list_all(self, include_disabled: bool = True) -> list[dict[str, Any]]:
        results = []
        for path in sorted(self._dir.glob("*.json")):
            r = self._safe_read(path)
            if r is None:
                continue
            if not include_disabled and r.disabled:
                continue
            results.append({
                "id": r.id,
                "description": r.description,
                "context": r.context,
                "keywords": r.keywords,
                "step_count": len(r.steps),
                "success_count": r.success_count,
                "failure_count": r.failure_count,
                "disabled": r.disabled,
                "disabled_reason": r.disabled_reason,
            })
        return results

    def search(self, query: str, include_disabled: bool = False) -> list[Recipe]:
        query_lower = query.lower()
        query_words = query_lower.split()
        results: list[tuple[int, Recipe]] = []

        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is None:
                continue
            if r.disabled and not include_disabled:
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

    def report_failure(self, recipe_id: str, reason: str = "") -> Recipe | None:
        """레시피 실행 실패 보고. 임계 도달 시 자동 비활성화."""
        recipe = self.load(recipe_id)
        if recipe is None:
            return None
        recipe.failure_count += 1
        recipe.last_failed_at = time.time()
        if not recipe.disabled and should_auto_disable(recipe.success_count, recipe.failure_count):
            recipe.disabled = True
            total = recipe.success_count + recipe.failure_count
            rate = recipe.failure_count / total
            base = f"auto: failure_rate={rate:.2f} ({recipe.failure_count}/{total})"
            recipe.disabled_reason = f"{base} | {reason}" if reason else base
        self.save(recipe)
        return recipe

    def _archive_path(self, recipe_id: str, version: int) -> Path:
        if not _SAFE_ID.match(recipe_id):
            raise ValueError(f"잘못된 recipe_id: {recipe_id!r}")
        ts = int(time.time() * 1000)
        return self._archive_dir / f"{recipe_id}__v{version}__{ts}.json"

    def _archive_current(self, recipe: Recipe) -> str:
        """현재 활성 레시피를 _archive로 이동(rename). 반환: archive 파일명."""
        self._ensure_archive_dir()
        archive = self._archive_path(recipe.id, recipe.version)
        current = self._path(recipe.id)
        if current.exists():
            os.replace(current, archive)
            _fsync_dir_best_effort(self._dir)
            _fsync_dir_best_effort(self._archive_dir)
        return archive.name

    def version_up(
        self,
        recipe_id: str,
        new_steps: list[RecipeStep],
        new_context: str = "",
        change_note: str = "",
    ) -> Recipe | None:
        """현재 버전을 _archive로 옮기고 version+1로 신규 저장.

        description, keywords, success/failure 등 메타는 승계. context/steps만 갱신.
        """
        current = self.load(recipe_id)
        if current is None:
            return None

        archive_name = self._archive_current(current)

        new_recipe = current.model_copy(deep=True)
        new_recipe.steps = list(new_steps)
        if new_context:
            new_recipe.context = new_context
        new_recipe.version = current.version + 1
        new_recipe.previous_version_archive = archive_name
        new_recipe.disabled = False
        new_recipe.disabled_reason = ""
        new_recipe.failure_count = 0
        new_recipe.last_failed_at = 0.0
        if change_note:
            note_line = f"[v{new_recipe.version} change_note] {change_note}"
            new_recipe.context = (
                f"{new_recipe.context}\n{note_line}" if new_recipe.context else note_line
            )
        self.save(new_recipe)
        return new_recipe

    def history(self, recipe_id: str) -> list[dict[str, Any]]:
        """과거 버전 목록 (archive 메타). 최신 archived 먼저."""
        if not _SAFE_ID.match(recipe_id):
            raise ValueError(f"잘못된 recipe_id: {recipe_id!r}")
        prefix = f"{recipe_id}__v"
        items: list[dict[str, Any]] = []
        if not self._archive_dir.exists():
            return items
        for path in self._archive_dir.glob(f"{prefix}*.json"):
            try:
                text = path.read_text(encoding="utf-8")
                data = Recipe.model_validate_json(text)
            except (ValidationError, OSError, ValueError, json.JSONDecodeError):
                continue
            items.append({
                "archive_file": path.name,
                "version": data.version,
                "step_count": len(data.steps),
                "context": data.context,
                "success_count": data.success_count,
                "failure_count": data.failure_count,
                "updated_at": data.updated_at,
            })
        items.sort(key=lambda x: (-x["version"], -x["updated_at"]))
        return items

    def load_archive(self, archive_file: str) -> Recipe | None:
        """archive 파일명을 받아 Recipe 로드. 디렉토리 탈출 차단."""
        if "/" in archive_file or "\\" in archive_file or ".." in archive_file:
            raise ValueError(f"잘못된 archive 파일명: {archive_file!r}")
        if not self._archive_dir.exists():
            return None
        path = self._archive_dir / archive_file
        if not path.exists():
            return None
        return self._safe_read(path)

    def enable(self, recipe_id: str, reset_failure_count: bool = True) -> Recipe | None:
        """레시피 수동 재활성화. 기본적으로 failure_count도 0으로 초기화."""
        recipe = self.load(recipe_id)
        if recipe is None:
            return None
        recipe.disabled = False
        recipe.disabled_reason = ""
        if reset_failure_count:
            recipe.failure_count = 0
            recipe.last_failed_at = 0.0
        self.save(recipe)
        return recipe

    def _tcode_seq(self, recipe: Recipe) -> tuple[str, ...]:
        """레시피의 tcode_sequence 반환. 비어있으면 steps에서 즉석 추출 (레거시 호환)."""
        if recipe.tcode_sequence:
            return tuple(recipe.tcode_sequence)
        return _extract_tcode_sequence(recipe.steps)

    def find_chain(
        self,
        tcodes: list[str],
        match: str = "all",
        include_disabled: bool = False,
    ) -> list[Recipe]:
        """여러 tcode가 모두/하나라도 포함된 레시피 검색. match='all'|'any'."""
        if match not in ("all", "any"):
            raise ValueError(f"match는 'all'|'any'여야 합니다: {match!r}")
        targets = {t.upper().strip() for t in tcodes if t and t.strip()}
        if not targets:
            return []

        results: list[Recipe] = []
        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is None:
                continue
            if r.disabled and not include_disabled:
                continue
            seq = set(self._tcode_seq(r))
            if not seq:
                continue
            if match == "all" and targets.issubset(seq):
                results.append(r)
            elif match == "any" and (targets & seq):
                results.append(r)
        # cross-tcode 우선 (시퀀스가 길수록 위로), 그다음 success
        results.sort(key=lambda r: (-len(self._tcode_seq(r)), -r.success_count))
        return results

    def find_by_tcode(self, tcode: str, include_disabled: bool = False) -> list[Recipe]:
        """특정 트랜잭션과 연관된 레시피 검색."""
        results = []
        tcode_upper = tcode.upper()
        for path in self._dir.glob("*.json"):
            r = self._safe_read(path)
            if r is None:
                continue
            if r.disabled and not include_disabled:
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
        include_disabled: bool = False,
    ) -> list[tuple[int, Recipe]]:
        """tcode + 키워드 통합 검색. (score, Recipe) 반환."""
        all_recipes = self._load_all()
        results: list[tuple[int, Recipe]] = []
        tcode_upper = tcode.upper() if tcode else None
        kw_words = keywords.lower().split() if keywords else []

        for r in all_recipes:
            if r.disabled and not include_disabled:
                continue
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
        include_disabled: bool = False,
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
            if r.disabled and not include_disabled:
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
