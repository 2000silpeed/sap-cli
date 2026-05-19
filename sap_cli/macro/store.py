"""매크로 JSON 저장/로드/관리."""

from __future__ import annotations

import json
import re
from pathlib import Path

from sap_cli.macro.schema import Macro
from sap_cli.storage import atomic_write_text

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_id(value: str, label: str = "id") -> str:
    if not value or not _SAFE_ID.match(value):
        raise ValueError(f"잘못된 {label}: {value!r} (영문, 숫자, _, - 만 허용)")
    return value


class MacroStore:
    """매크로 JSON 저장/로드/관리.

    저장 경로: ~/.sap_cli/macros/
    파일명: {macro_id}.json
    """

    def __init__(self, library_dir: str | Path | None = None) -> None:
        if library_dir is None:
            self._dir = Path.home() / ".sap_cli" / "macros"
        else:
            self._dir = Path(library_dir)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _macro_path(self, macro_id: str) -> Path:
        _validate_id(macro_id, "macro_id")
        return self._dir / f"{macro_id}.json"

    def name_exists(self, name: str, exclude_id: str | None = None) -> bool:
        """같은 이름의 매크로가 이미 존재하는지 확인."""
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if data.get("name", "") == name:
                    if exclude_id and data.get("id") == exclude_id:
                        continue
                    return True
            except Exception:
                continue
        return False

    def save(self, macro: Macro) -> Path:
        """매크로를 JSON으로 저장."""
        if self.name_exists(macro.name, exclude_id=macro.id):
            raise ValueError(f"이름이 '{macro.name}'인 매크로가 이미 존재합니다.")
        path = self._macro_path(macro.id)
        atomic_write_text(
            path,
            macro.model_dump_json(indent=2),
            mode=0o644,
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

    def resolve(self, identifier: str) -> Macro | None:
        """Resolve a macro by ID first, then by exact or unique partial name."""
        identifier = str(identifier).strip()
        if not identifier:
            return None

        try:
            macro = self.load(identifier)
        except ValueError:
            macro = None
        if macro:
            return macro

        exact_matches: list[Macro] = []
        partial_matches: list[Macro] = []
        identifier_lower = identifier.lower()
        for path in self._dir.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                macro = Macro.model_validate(data)
            except Exception:
                continue
            if macro.name == identifier:
                exact_matches.append(macro)
            elif identifier_lower in macro.name.lower():
                partial_matches.append(macro)

        if len(exact_matches) == 1:
            return exact_matches[0]
        if len(exact_matches) > 1:
            raise ValueError(f"매크로 이름 '{identifier}'가 여러 개입니다. macro_id를 사용하세요.")
        if len(partial_matches) == 1:
            return partial_matches[0]
        if len(partial_matches) > 1:
            choices = ", ".join(f"{macro.id}:{macro.name}" for macro in partial_matches[:5])
            raise ValueError(
                f"매크로 이름 '{identifier}'가 여러 개와 일치합니다. macro_id를 사용하세요: {choices}"
            )
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
