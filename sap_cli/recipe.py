"""AI 네비게이션 레시피 - 성공한 SAP 조작 경로를 저장/검색.

매크로(VBS)와 다르게, AI에게 힌트를 주는 방식.
AI가 레시피를 참고하여 상황 판단하면서 따라감.
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field


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
        self._path(recipe.id).write_text(
            recipe.model_dump_json(indent=2), encoding="utf-8"
        )

    def load(self, recipe_id: str) -> Recipe | None:
        path = self._path(recipe_id)
        if not path.exists():
            return None
        return Recipe.model_validate_json(path.read_text(encoding="utf-8"))

    def delete(self, recipe_id: str) -> bool:
        path = self._path(recipe_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def list_all(self) -> list[dict[str, Any]]:
        results = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                r = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
                results.append({
                    "id": r.id,
                    "description": r.description,
                    "context": r.context,
                    "keywords": r.keywords,
                    "step_count": len(r.steps),
                    "success_count": r.success_count,
                })
            except Exception:
                continue
        return results

    def search(self, query: str) -> list[Recipe]:
        query_lower = query.lower()
        query_words = query_lower.split()
        results: list[tuple[int, Recipe]] = []

        for path in self._dir.glob("*.json"):
            try:
                r = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
            except Exception:
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
            try:
                r = Recipe.model_validate_json(path.read_text(encoding="utf-8"))
                linked = any(
                    s.tool in ("sap_run", "sap_command") and
                    tcode_upper in str(s.args.get("tcode", s.args.get("cmd", ""))).upper()
                    for s in r.steps
                )
                if linked or tcode_upper in r.id.upper():
                    results.append(r)
            except Exception:
                continue
        results.sort(key=lambda r: -r.success_count)
        return results
