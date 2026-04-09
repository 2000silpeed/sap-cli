"""온톨로지 학습 엔진 - GetObjectTree() → ScreenOntology 자동 변환.

NOMIK 패턴: "Scan once, query everything"
SAP GUI 화면을 스캔하여 온톨로지를 자동으로 구축한다.
반복 사용 시 기존 온톨로지를 업데이트하며 시맨틱 컨텍스트를 풍부화한다.
"""

from __future__ import annotations

from typing import Any

from .com_wrapper import SapGuiWrapper
from .ontology.analysis_space import CrossSpace, TemporalSpace
from .ontology.schema import (
    ActionRecord,
    ActionType,
    OntologyLink,
    LinkType,
    ScreenOntology,
    SemanticContext,
    TransactionOntology,
)
from .ontology.store import OntologyStore


class OntologyLearner:
    """화면 스캔 → 온톨로지 학습 → 저장.

    SOS Tier 3 Active Metadata 개념:
    - 화면이 변경될 때마다 온톨로지를 자동 갱신
    - 반복 학습으로 시맨틱 컨텍스트 축적
    """

    def __init__(self, sap: SapGuiWrapper, store: OntologyStore | None = None):
        self.sap = sap
        self.store = store or OntologyStore()
        self.temporal = TemporalSpace()
        self._last_screen: int | None = None

    def learn_current_screen(self) -> ScreenOntology:
        """현재 화면을 스캔하여 온톨로지로 저장."""
        screen = self.sap.get_screen()
        self._enrich_semantics(screen)
        self.store.save_screen(screen.transaction, screen)
        self.store.update_field_registry(screen)

        if self._last_screen is not None and self._last_screen != screen.screen_number:
            self._record_navigation(screen)
        self._last_screen = screen.screen_number

        return screen

    def learn_transaction(self, tcode: str) -> TransactionOntology:
        """트랜잭션의 초기 화면을 학습. 이미 저장된 온톨로지가 있으면 병합."""
        existing = self.store.load_transaction(tcode)

        self.sap.run_transaction(tcode)
        screen = self.learn_current_screen()

        if existing:
            existing.add_screen(screen)
            self.store.save_transaction(existing)
            self.store.update_document_flow_hints(tcode)
            return existing

        ontology = TransactionOntology(
            tcode=tcode,
            title=screen.title,
        )
        ontology.add_screen(screen)
        self.store.save_transaction(ontology)
        self.store.update_document_flow_hints(tcode)
        return ontology

    def record_action(self, action_type: ActionType, target_id: str,
                      params: dict[str, Any] | None = None) -> ActionRecord:
        """액션 실행을 기록하고 인과관계를 학습."""
        before_info = self.sap.get_session_info()

        record = ActionRecord(
            action_type=action_type,
            target_id=target_id,
            params=params or {},
            before_screen=before_info.screen_number,
        )

        return record

    def complete_action_record(self, record: ActionRecord) -> ActionRecord:
        """액션 실행 후 결과를 기록."""
        after_info = self.sap.get_session_info()
        status = self.sap.get_status()

        record.after_screen = after_info.screen_number
        record.result_status = status

        self.temporal.record_action(record)

        if record.before_screen != record.after_screen:
            after_screen = self.learn_current_screen()

        return record

    def _enrich_semantics(self, screen: ScreenOntology) -> None:
        """기존 온톨로지의 시맨틱 정보를 새 스캔 결과에 병합.

        learn이 자동 추출한 값(description, raw_label 등)은 항상 최신으로 갱신하되,
        사용자가 수동 입력한 값(description_ko, synonyms_ko, synonyms, domain_context 등)은 보존.
        """
        existing = self.store.load_screen(screen.transaction, screen.screen_number)
        if existing is None:
            return

        existing_fields = {f.id: f for f in existing.fields}
        for field in screen.fields:
            if field.id not in existing_fields:
                continue
            old = existing_fields[field.id]
            sem = field.semantic
            old_sem = old.semantic

            # 수동 입력 필드 보존 (learn이 채우지 않는 값들)
            if old_sem.description_ko and not sem.description_ko:
                sem.description_ko = old_sem.description_ko
            if old_sem.synonyms_ko and not sem.synonyms_ko:
                sem.synonyms_ko = old_sem.synonyms_ko
            if old_sem.synonyms and not sem.synonyms:
                sem.synonyms = old_sem.synonyms
            if old_sem.validation and not sem.validation:
                sem.validation = old_sem.validation
            if old_sem.domain_context and not sem.domain_context:
                sem.domain_context = old_sem.domain_context

            # 자동 추출 실패 시 기존 값 유지
            if old_sem.description and not sem.description:
                sem.description = old_sem.description
            if old_sem.raw_label and not sem.raw_label:
                sem.raw_label = old_sem.raw_label

    def _record_navigation(self, new_screen: ScreenOntology) -> None:
        """화면 전환을 온톨로지의 screen_flow에 기록."""
        tcode = new_screen.transaction
        ontology = self.store.load_transaction(tcode)
        if ontology is None:
            return

        link = OntologyLink(
            link_type=LinkType.NAVIGATES_TO,
            source_id=f"screen:{self._last_screen}",
            target_id=f"screen:{new_screen.screen_number}",
            description=f"Navigation from screen {self._last_screen} to {new_screen.screen_number}",
        )

        existing_flows = {(l.source_id, l.target_id) for l in ontology.screen_flow}
        if (link.source_id, link.target_id) not in existing_flows:
            ontology.screen_flow.append(link)
            self.store.save_transaction(ontology)

    def get_next_screen_prediction(self) -> int | None:
        """시간공간 분석 기반 다음 화면 예측."""
        info = self.sap.get_session_info()
        return self.temporal.predict_next_screen(info.screen_number)
