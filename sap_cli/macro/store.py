"""매크로 JSON 저장/로드/관리."""

from __future__ import annotations

import json
from pathlib import Path

from sap_cli.macro.schema import Macro


class MacroStore:
    """매크로 JSON 저장/로드/관리.

    저장 경로: sap_cli/macro/library/
    파일명: {macro_id}.json
    """

    def __init__(self, library_dir: str | Path | None = None) -> None:
        if library_dir is None:
            self._dir = Path(__file__).parent / "library"
        else:
            self._dir = Path(library_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _macro_path(self, macro_id: str) -> Path:
        return self._dir / f"{macro_id}.json"

    def save(self, macro: Macro) -> Path:
        """매크로를 JSON으로 저장."""
        path = self._macro_path(macro.id)
        path.write_text(
            macro.model_dump_json(indent=2),
            encoding="utf-8",
        )
        return path

    def load(self, macro_id: str) -> Macro | None:
        """매크로 로드."""
        path = self._macro_path(macro_id)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Macro.model_validate(data)
        except Exception:
            return None

    def list_macros(self) -> list[dict]:
        """저장된 매크로 목록 (id, name, tcode, description만)."""
        result: list[dict] = []
        for path in sorted(self._dir.glob("*.json")):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                result.append({
                    "id": data.get("id", path.stem),
                    "name": data.get("name", ""),
                    "tcode": data.get("tcode", ""),
                    "description": data.get("description", ""),
                })
            except Exception:
                continue
        return result

    def delete(self, macro_id: str) -> bool:
        """매크로 삭제."""
        path = self._macro_path(macro_id)
        if path.exists():
            path.unlink()
            return True
        return False

    def find_by_tcode(self, tcode: str) -> list[Macro]:
        """트랜잭션 코드로 매크로 검색."""
        results: list[Macro] = []
        tcode_upper = tcode.upper()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("tcode", "").upper() == tcode_upper:
                    results.append(Macro.model_validate(data))
            except Exception:
                continue
        return results

    def find_by_name(self, name: str) -> list[Macro]:
        """이름으로 매크로 검색 (부분 일치)."""
        results: list[Macro] = []
        name_lower = name.lower()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if name_lower in data.get("name", "").lower():
                    results.append(Macro.model_validate(data))
            except Exception:
                continue
        return results
