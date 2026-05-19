"""온톨로지 답변 포매터 — 질문 유형별 구조화된 답변 생성."""

from __future__ import annotations

from typing import Any

from .graph import OntologyGraph
from .question_parser import ParsedQuestion, QuestionType
from .store import OntologyStore


class OntologyAnswerFormatter:

    def __init__(self, store: OntologyStore, graph: OntologyGraph | None = None):
        self._store = store
        self._graph_instance = graph
        self._graph_built = graph is not None

    @property
    def _graph(self) -> OntologyGraph:
        if not self._graph_built:
            self._graph_instance = OntologyGraph(self._store)
            self._graph_instance.build_from_store()
            self._graph_built = True
        assert self._graph_instance is not None
        return self._graph_instance

    def answer(self, parsed: ParsedQuestion) -> dict[str, Any]:
        handlers = {
            QuestionType.TRANSACTION_SUMMARY: self._answer_transaction_summary,
            QuestionType.FIELD_MEANING: self._answer_field_meaning,
            QuestionType.FIELD_CROSS: self._answer_field_cross,
            QuestionType.REQUIRED_FIELDS: self._answer_required_fields,
            QuestionType.PROCESS: self._answer_process,
            QuestionType.FIELD_DEPENDENCY: self._answer_field_dependency,
            QuestionType.PATTERN: self._answer_pattern,
        }
        handler = handlers.get(parsed.question_type, self._answer_transaction_summary)
        try:
            result = handler(parsed)
        except Exception as e:
            return {"question": parsed.raw, "question_type": parsed.question_type.name, "error": str(e)}
        result["question"] = parsed.raw
        result["question_type"] = parsed.question_type.name
        return result

    def _answer_transaction_summary(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if not parsed.tcodes:
            return {"error": "트랜잭션 코드를 식별할 수 없습니다.", "hint": "예: VA01이 뭐야?"}

        results = []
        for tcode in parsed.tcodes:
            ont = self._store.load_transaction(tcode)
            if not ont:
                results.append({"tcode": tcode, "error": f"'{tcode}' 온톨로지가 없습니다. `sap learn`으로 학습할 수 있습니다."})
                continue

            screens = []
            for sn, sc in ont.screens.items():
                req_fields = [
                    {"label": f.label, "sap_field_name": f.semantic.sap_field_name, "description": f.semantic.description}
                    for f in sc.fields if f.required
                ]
                screens.append({"screen_number": sn, "title": sc.title, "required_fields": req_fields})

            results.append({
                "tcode": ont.tcode,
                "title": ont.title,
                "module": ont.module,
                "description": ont.description,
                "screens": screens,
                "related_transactions": ont.related_transactions,
            })

        return {"transactions": results} if len(results) > 1 else results[0]

    def _answer_field_meaning(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if not parsed.fields:
            return {"error": "필드명을 식별할 수 없습니다.", "hint": "예: MATNR이 뭐야?"}

        results = []
        for field_name in parsed.fields:
            occurrences = self._find_field_occurrences(field_name)
            if not occurrences:
                results.append({"field": field_name, "error": f"'{field_name}' 필드를 찾을 수 없습니다."})
                continue

            first = occurrences[0]
            results.append({
                "sap_field_name": field_name,
                "description": first.get("description", ""),
                "synonyms": first.get("synonyms", []),
                "validation": first.get("validation", ""),
                "domain_context": first.get("domain_context", ""),
                "occurrences": [
                    {"tcode": o["tcode"], "screen_number": o["screen_number"], "label": o["label"], "changeable": o["changeable"]}
                    for o in occurrences
                ],
            })

        return {"fields": results} if len(results) > 1 else results[0]

    def _answer_field_cross(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if not parsed.fields:
            return {"error": "필드명을 식별할 수 없습니다.", "hint": "예: WERKS가 어디 나와?"}

        results = []
        for field_name in parsed.fields:
            registry = self._store.load_field_registry()
            entry = registry.fields.get(field_name.upper())
            if entry and entry.occurrences:
                locs = [
                    {"tcode": o.tcode, "screen_number": o.screen_number, "label": o.label, "changeable": o.changeable}
                    for o in entry.occurrences
                ]
                results.append({"field": field_name, "description": entry.description, "locations": locs})
            else:
                fallback = self._find_field_occurrences(field_name)
                if fallback:
                    results.append({
                        "field": field_name,
                        "locations": [
                            {"tcode": o["tcode"], "screen_number": o["screen_number"], "label": o["label"], "changeable": o["changeable"]}
                            for o in fallback
                        ],
                    })
                else:
                    results.append({"field": field_name, "error": f"'{field_name}' 필드를 찾을 수 없습니다."})

        return {"fields": results} if len(results) > 1 else results[0]

    def _answer_required_fields(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if not parsed.tcodes:
            return {"error": "트랜잭션 코드를 식별할 수 없습니다.", "hint": "예: VA01 필수 필드"}

        results = []
        for tcode in parsed.tcodes:
            ont = self._store.load_transaction(tcode)
            if not ont:
                results.append({"tcode": tcode, "error": f"'{tcode}' 온톨로지가 없습니다."})
                continue

            screens = []
            for sn, sc in ont.screens.items():
                req = [
                    {
                        "label": f.label,
                        "sap_field_name": f.semantic.sap_field_name,
                        "description": f.semantic.description,
                        "validation": f.semantic.validation,
                    }
                    for f in sc.fields if f.required
                ]
                if req:
                    screens.append({"screen_number": sn, "title": sc.title, "required_fields": req})

            results.append({"tcode": tcode, "title": ont.title, "screens": screens})

        return {"transactions": results} if len(results) > 1 else results[0]

    def _answer_process(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if parsed.process_keywords:
            proc_ids = parsed.process_keywords
        else:
            proc_ids = self._store.list_processes()

        if not proc_ids:
            return {"error": "프로세스 데이터가 없습니다.", "hint": "`sap process-create`로 프로세스를 정의할 수 있습니다."}

        results = []
        for pid in proc_ids:
            proc = self._store.load_process(pid)
            if not proc:
                results.append({"process_id": pid, "error": f"'{pid}' 프로세스를 찾을 수 없습니다."})
                continue

            steps = [
                {
                    "step_number": s.step_number,
                    "tcode": s.tcode,
                    "description": s.description,
                    "input_fields": s.input_fields,
                    "expected_output": s.expected_output,
                }
                for s in proc.steps
            ]
            data_flows = [
                {
                    "source_step": df.source_step,
                    "target_step": df.target_step,
                    "source_field": df.source_field,
                    "target_field": df.target_field,
                    "description": df.description,
                }
                for df in proc.data_flows
            ]
            results.append({
                "process_id": pid,
                "name": proc.name,
                "module": proc.module,
                "description": proc.description,
                "steps": steps,
                "data_flows": data_flows,
            })

        return {"processes": results} if len(results) > 1 else results[0]

    def _answer_field_dependency(self, parsed: ParsedQuestion) -> dict[str, Any]:
        if not parsed.fields:
            return {"error": "필드명을 식별할 수 없습니다.", "hint": "예: AUART 바꾸면 뭐가 변해?"}

        results = []
        for field_name in parsed.fields:
            deps = self._graph.get_field_dependencies(field_name)
            if deps:
                determines = [d for d in deps if d["direction"] == "determines"]
                determined_by = [d for d in deps if d["direction"] == "determined_by"]
                results.append({
                    "field": field_name,
                    "determines": determines,
                    "determined_by": determined_by,
                })
            else:
                fallback = self._find_field_deps_from_store(field_name)
                if fallback:
                    results.append({"field": field_name, **fallback})
                else:
                    results.append({"field": field_name, "determines": [], "determined_by": [], "note": "종속성 데이터가 없습니다."})

        return {"fields": results} if len(results) > 1 else results[0]

    _PATTERN_ALIAS: dict[str, list[str]] = {
        "save_flow": ["저장", "save", "ctrl+s"],
        "search_help": ["F4", "검색 도움말", "search help", "검색도움말"],
        "macro_variable_registration": [
            "매크로",
            "macro",
            "변수",
            "변수화",
            "등록",
            "record-finish",
            "variable",
        ],
    }

    def _answer_pattern(self, parsed: ParsedQuestion) -> dict[str, Any]:
        pattern_names = self._store.list_patterns()
        if not pattern_names:
            return {"error": "패턴 데이터가 없습니다."}

        kw_lower = [k.lower() for k in parsed.pattern_keywords]
        matched = []
        for name in pattern_names:
            aliases = self._PATTERN_ALIAS.get(name, [name])
            if any(kw in alias.lower() or alias.lower() in kw for kw in kw_lower for alias in aliases):
                matched.append(name)
            elif any(kw in name.lower() for kw in kw_lower):
                matched.append(name)

        if not matched:
            matched = pattern_names

        results = []
        for name in matched:
            data = self._store.load_pattern(name)
            if data:
                results.append({"pattern_name": name, **data})

        return {"patterns": results} if len(results) > 1 else (results[0] if results else {"error": "패턴을 찾을 수 없습니다."})

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _find_field_occurrences(self, field_name: str) -> list[dict[str, Any]]:
        key = field_name.upper()
        occurrences: list[dict[str, Any]] = []
        for tcode in self._store.list_transactions():
            ont = self._store.load_transaction(tcode)
            if not ont:
                continue
            for sn, sc in ont.screens.items():
                for f in sc.fields:
                    if (f.semantic.sap_field_name or "").upper() == key or f.name.upper() == key:
                        occurrences.append({
                            "tcode": tcode,
                            "screen_number": sn,
                            "label": f.label,
                            "changeable": f.changeable,
                            "description": f.semantic.description,
                            "synonyms": f.semantic.synonyms,
                            "validation": f.semantic.validation,
                            "domain_context": f.semantic.domain_context,
                        })
        return occurrences

    def _find_field_deps_from_store(self, field_name: str) -> dict[str, Any] | None:
        key = field_name.upper()
        determines: list[dict[str, Any]] = []
        determined_by: list[dict[str, Any]] = []
        dep_graph = self._store.load_field_dependencies()
        for rel in dep_graph.relationships:
            if rel.source_field.upper() == key:
                determines.append({
                    "field": rel.target_field,
                    "direction": "determines",
                    "dependency_type": rel.dependency_type.value,
                    "description": rel.description,
                    "observed_in": rel.observed_in,
                })
            elif rel.target_field.upper() == key:
                determined_by.append({
                    "field": rel.source_field,
                    "direction": "determined_by",
                    "dependency_type": rel.dependency_type.value,
                    "description": rel.description,
                    "observed_in": rel.observed_in,
                })
        if determines or determined_by:
            return {"determines": determines, "determined_by": determined_by}
        return None
