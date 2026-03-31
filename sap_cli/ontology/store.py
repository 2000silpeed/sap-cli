"""온톨로지 저장소 - JSON 기반 지식 영속화.

NOMIK 패턴: "Scan once, query everything"
- 트랜잭션별 화면 구조를 JSON으로 저장
- 메타엣지 규칙을 별도 관리
- 공통 패턴(메타온톨로지)을 추출하여 재사용
"""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

from .analysis_space import CrossSpace
from .meta_edge import MetaEdge, MetaEdgeRegistry, create_default_registry
from .schema import ScreenOntology, TransactionOntology

_SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def _validate_id(value: str, label: str = "id") -> str:
    if not value or not _SAFE_ID.match(value):
        raise ValueError(f"잘못된 {label}: {value!r} (영문, 숫자, _, - 만 허용)")
    return value


class OntologyStore:
    """온톨로지 저장소.

    파일 구조:
        knowledge/
        ├── transactions/    # 트랜잭션별 온톨로지
        │   ├── MM03.json
        │   └── VA01.json
        ├── meta_edges/      # 메타엣지 규칙
        │   └── registry.json
        └── patterns/        # 공통 패턴 (메타온톨로지)
            └── common.json
    """

    def __init__(self, knowledge_dir: str | Path | None = None):
        if knowledge_dir is None:
            knowledge_dir = Path.home() / ".sap_cli" / "knowledge"
        self.knowledge_dir = Path(knowledge_dir)
        self.transactions_dir = self.knowledge_dir / "transactions"
        self.meta_edges_dir = self.knowledge_dir / "meta_edges"
        self.patterns_dir = self.knowledge_dir / "patterns"

        self._ensure_dirs()
        self._seed_if_empty()

        self._meta_registry: MetaEdgeRegistry | None = None
        self._cross_space = CrossSpace()

    def _ensure_dirs(self) -> None:
        for d in [self.transactions_dir, self.meta_edges_dir, self.patterns_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def _seed_if_empty(self) -> None:
        """외부 디렉토리가 비어있으면 패키지 내 시드 데이터를 복사."""
        seed_dir = Path(__file__).parent / "knowledge"
        if not seed_dir.exists():
            return
        for subdir in ("transactions", "patterns"):
            src = seed_dir / subdir
            dst = self.knowledge_dir / subdir
            if not src.exists():
                continue
            if list(dst.glob("*.json")):
                continue  # 이미 데이터가 있으면 건너뜀
            for f in src.glob("*.json"):
                dest_file = dst / f.name
                if not dest_file.exists():
                    shutil.copy2(f, dest_file)

    # -------------------------------------------------------------------
    # Transaction Ontology CRUD
    # -------------------------------------------------------------------

    def save_transaction(self, ontology: TransactionOntology) -> Path:
        """트랜잭션 온톨로지를 JSON으로 저장."""
        _validate_id(ontology.tcode, "tcode")
        path = self.transactions_dir / f"{ontology.tcode}.json"
        data = ontology.model_dump(mode="json", exclude_none=True)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_transaction(self, tcode: str) -> TransactionOntology | None:
        """트랜잭션 온톨로지를 JSON에서 로드."""
        _validate_id(tcode, "tcode")
        path = self.transactions_dir / f"{tcode.upper()}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return TransactionOntology.model_validate(data)

    def list_transactions(self) -> list[str]:
        """저장된 트랜잭션 코드 목록."""
        return [p.stem for p in self.transactions_dir.glob("*.json")]

    def delete_transaction(self, tcode: str) -> bool:
        """트랜잭션 온톨로지 삭제."""
        _validate_id(tcode, "tcode")
        path = self.transactions_dir / f"{tcode.upper()}.json"
        if path.exists():
            path.unlink()
            return True
        return False

    # -------------------------------------------------------------------
    # Screen Ontology (트랜잭션 내 화면 단위)
    # -------------------------------------------------------------------

    def save_screen(self, tcode: str, screen: ScreenOntology) -> None:
        """화면 온톨로지를 트랜잭션에 추가/업데이트."""
        ontology = self.load_transaction(tcode)
        if ontology is None:
            ontology = TransactionOntology(tcode=tcode)
        ontology.add_screen(screen)
        self.save_transaction(ontology)

    def load_screen(self, tcode: str, screen_number: int) -> ScreenOntology | None:
        """특정 화면의 온톨로지 로드."""
        ontology = self.load_transaction(tcode)
        if ontology is None:
            return None
        return ontology.get_screen(screen_number)

    # -------------------------------------------------------------------
    # Meta Edge Registry
    # -------------------------------------------------------------------

    @property
    def meta_registry(self) -> MetaEdgeRegistry:
        if self._meta_registry is None:
            self._meta_registry = self._load_meta_registry()
        return self._meta_registry

    def _load_meta_registry(self) -> MetaEdgeRegistry:
        path = self.meta_edges_dir / "registry.json"
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            return MetaEdgeRegistry.model_validate(data)
        return create_default_registry()

    def save_meta_registry(self) -> Path:
        path = self.meta_edges_dir / "registry.json"
        data = self.meta_registry.model_dump(mode="json")
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def add_meta_edge(self, edge: MetaEdge) -> None:
        self.meta_registry.add(edge)
        self.save_meta_registry()

    def evaluate_meta_edges(self, tcode: str, context: dict[str, Any]) -> list[dict[str, Any]]:
        """특정 트랜잭션에서 현재 컨텍스트에 대한 메타엣지 효과 평가."""
        effects = self.meta_registry.evaluate_all(tcode, context)
        return [e.model_dump() for e in effects]

    # -------------------------------------------------------------------
    # Cross Space (6공간 분석 엔진)
    # -------------------------------------------------------------------

    @property
    def cross_space(self) -> CrossSpace:
        return self._cross_space

    def analyze_current_state(
        self,
        session_info: dict[str, Any],
        screen: ScreenOntology,
    ) -> dict[str, Any]:
        """현재 상태에 대한 6공간 통합 분석."""
        analysis = self._cross_space.analyze(session_info, screen)
        return analysis.model_dump(mode="json")

    # -------------------------------------------------------------------
    # Patterns (메타온톨로지 - 공통 패턴)
    # -------------------------------------------------------------------

    def save_pattern(self, name: str, pattern: dict[str, Any]) -> Path:
        _validate_id(name, "pattern name")
        path = self.patterns_dir / f"{name}.json"
        path.write_text(json.dumps(pattern, ensure_ascii=False, indent=2), encoding="utf-8")
        return path

    def load_pattern(self, name: str) -> dict[str, Any] | None:
        _validate_id(name, "pattern name")
        path = self.patterns_dir / f"{name}.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def list_patterns(self) -> list[str]:
        return [p.stem for p in self.patterns_dir.glob("*.json")]

    # -------------------------------------------------------------------
    # Query (온톨로지 검색)
    # -------------------------------------------------------------------

    def query_field(self, tcode: str, field_name: str) -> dict[str, Any] | None:
        """트랜잭션에서 필드 검색 (이름, 라벨, 시맨틱 모두 검색)."""
        ontology = self.load_transaction(tcode)
        if ontology is None:
            return None

        for screen_num, screen in ontology.screens.items():
            field = (
                screen.get_field_by_name(field_name)
                or screen.get_field_by_label(field_name)
            )
            if field:
                return {
                    "screen_number": screen_num,
                    "field": field.model_dump(mode="json"),
                }
        return None

    def query_actions(self, tcode: str, screen_number: int | None = None) -> list[dict[str, Any]]:
        """트랜잭션의 가용 액션 검색."""
        ontology = self.load_transaction(tcode)
        if ontology is None:
            return []

        actions = []
        screens = ontology.screens
        if screen_number is not None:
            screen = screens.get(screen_number)
            if screen:
                screens = {screen_number: screen}
            else:
                return []

        for screen in screens.values():
            for action in screen.get_available_actions():
                actions.append(action.model_dump(mode="json"))
        return actions
