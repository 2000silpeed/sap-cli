"""Ontology command dispatch helpers.

Ontology command implementations live here while :mod:`sap_cli.dispatcher`
keeps thin private wrappers for backward-compatible imports and monkeypatch-
based tests.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

StoreFactory = Callable[[], Any]
RecipeStoreFactory = Callable[[], Any]


ONTOLOGY_COMMANDS: frozenset[str] = frozenset(
    {
        "ontology_query",
        "ontology_ask",
        "ontology_relationships",
        "field_dependencies",
        "document_flow",
        "ontology_rebuild",
        "annotate",
        "annotate_dependency",
    }
)


def is_ontology_command(command: str) -> bool:
    """Return whether ``command`` belongs to the ontology dispatcher group."""
    return command in ONTOLOGY_COMMANDS


def dispatch_ontology_command(
    command: str,
    args: dict[str, Any],
    *,
    get_store: StoreFactory,
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    """Dispatch an ontology command with facade-provided compatibility hooks."""
    if command == "ontology_query":
        return dispatch_ontology_query(args, get_store, get_recipe_store)
    if command == "ontology_ask":
        return dispatch_ontology_ask(args, get_store)
    if command == "ontology_relationships":
        return dispatch_ontology_relationships(args, get_store)
    if command == "field_dependencies":
        return dispatch_field_dependencies(args, get_store)
    if command == "document_flow":
        return dispatch_document_flow(args, get_store)
    if command == "ontology_rebuild":
        return dispatch_ontology_rebuild(get_store)
    if command == "annotate":
        return dispatch_annotate(args, get_store)
    if command == "annotate_dependency":
        return dispatch_annotate_dependency(args, get_store)
    raise ValueError(f"미구현 ontology 명령: {command}")


def dispatch_ontology_ask(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """자연어 질문 → 온톨로지 그래프 탐색 → 구조화된 답변."""
    from ..ontology.answer_formatter import OntologyAnswerFormatter
    from ..ontology.question_parser import parse_question

    question = str(args.get("question", ""))
    if not question:
        raise ValueError("질문을 입력하세요. 예: sap ontology-ask 'VA01이 뭐야?'")

    store = get_store()
    parsed = parse_question(question)
    formatter = OntologyAnswerFormatter(store)
    return formatter.answer(parsed)


def dispatch_ontology_query_extended(
    store: Any, query_type: str, args: dict[str, Any]
) -> dict[str, Any]:
    """확장 쿼리 타입 핸들러: field_semantic, field_cross, process_guide, dependency_chain."""
    from ..ontology.semantic_search import semantic_field_search

    if query_type == "field_semantic":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("field_semantic 쿼리에는 field_search가 필요합니다.")
        matches = semantic_field_search(store, str(search_term))
        if not matches:
            return {
                "query_type": "field_semantic",
                "search": search_term,
                "count": 0,
                "results": [],
                "hint": "일치하는 필드가 없습니다. 다른 키워드나 SAP 필드명(예: MATNR)을 시도하세요.",
            }
        total = len(matches)
        truncated = total > 20
        results = [
            {
                "sap_field_name": m.sap_field_name,
                "tcode": m.tcode,
                "screen_number": m.screen_number,
                "label": m.label,
                "description": m.description,
                "synonyms": m.synonyms,
                "domain_context": m.domain_context,
                "validation": m.validation,
                "changeable": m.changeable,
                "score": round(m.score, 2),
                "match_source": m.match_source,
            }
            for m in matches[:20]
        ]
        return {
            "query_type": "field_semantic",
            "search": search_term,
            "count": len(results),
            "total_count": total,
            "truncated": truncated,
            "results": results,
        }

    if query_type == "field_cross":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("field_cross 쿼리에는 field_search가 필요합니다.")
        field_name = str(search_term).upper()
        registry = store.load_field_registry()
        entry = registry.fields.get(field_name)
        if entry and entry.occurrences:
            locations = [
                {
                    "tcode": o.tcode,
                    "screen_number": o.screen_number,
                    "label": o.label,
                    "changeable": o.changeable,
                }
                for o in entry.occurrences
            ]
            return {
                "query_type": "field_cross",
                "field": field_name,
                "description": entry.description,
                "transaction_count": len({o.tcode for o in entry.occurrences}),
                "total_occurrences": len(locations),
                "locations": locations,
            }
        matches = semantic_field_search(store, str(search_term))
        if matches:
            seen: set[str] = set()
            locations = []
            for m in matches:
                key = f"{m.tcode}:{m.screen_number}:{m.sap_field_name}"
                if key not in seen:
                    seen.add(key)
                    locations.append(
                        {
                            "tcode": m.tcode,
                            "screen_number": m.screen_number,
                            "sap_field_name": m.sap_field_name,
                            "label": m.label,
                            "changeable": m.changeable,
                        }
                    )
            return {
                "query_type": "field_cross",
                "search": search_term,
                "note": "정확한 필드명 대신 시맨틱 검색 결과를 반환합니다.",
                "transaction_count": len({l["tcode"] for l in locations}),
                "total_occurrences": len(locations),
                "locations": locations,
            }
        return {
            "query_type": "field_cross",
            "field": field_name,
            "count": 0,
            "locations": [],
            "hint": f"'{search_term}' 필드를 찾을 수 없습니다.",
        }

    if query_type == "process_guide":
        process_id = args.get("field_search") or args.get("tcode")
        if not process_id:
            all_procs = store.list_processes()
            if not all_procs:
                return {"query_type": "process_guide", "error": "프로세스 데이터가 없습니다."}
            return {
                "query_type": "process_guide",
                "available_processes": all_procs,
                "hint": "field_search에 프로세스 ID를 지정하세요.",
            }
        from ..ontology.question_parser import KOREAN_TO_PROCESS

        proc = None
        pid_str = str(process_id)
        resolved = KOREAN_TO_PROCESS.get(pid_str)
        if resolved:
            proc = store.load_process(resolved)
        if not proc:
            try:
                proc = store.load_process(pid_str)
            except ValueError:
                pass
        if not proc:
            for pid in store.list_processes():
                p = store.load_process(pid)
                if p and any(s.tcode.upper() == pid_str.upper() for s in p.steps):
                    proc = p
                    break
        if not proc:
            return {
                "query_type": "process_guide",
                "error": f"'{process_id}' 프로세스를 찾을 수 없습니다.",
                "available": store.list_processes(),
            }
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
        return {
            "query_type": "process_guide",
            "process_id": proc.id,
            "name": proc.name,
            "module": proc.module,
            "description": proc.description,
            "step_count": len(steps),
            "steps": steps,
            "data_flows": data_flows,
        }

    if query_type == "dependency_chain":
        search_term = args.get("field_search")
        if not search_term:
            raise ValueError("dependency_chain 쿼리에는 field_search가 필요합니다.")
        field_name = str(search_term).upper()
        from ..ontology.graph import OntologyGraph

        graph = OntologyGraph(store)
        graph.build_from_store()
        deps = graph.get_field_dependencies(field_name)
        if deps:
            determines = [d for d in deps if d["direction"] == "determines"]
            determined_by = [d for d in deps if d["direction"] == "determined_by"]
        else:
            determines = []
            determined_by = []
            dep_graph = store.load_field_dependencies()
            for rel in dep_graph.relationships:
                if rel.source_field.upper() == field_name:
                    determines.append(
                        {
                            "field": rel.target_field,
                            "dependency_type": rel.dependency_type.value,
                            "description": rel.description,
                            "observed_in": rel.observed_in,
                        }
                    )
                elif rel.target_field.upper() == field_name:
                    determined_by.append(
                        {
                            "field": rel.source_field,
                            "dependency_type": rel.dependency_type.value,
                            "description": rel.description,
                            "observed_in": rel.observed_in,
                        }
                    )
        dep_graph = store.load_field_dependencies()
        chain: list[str] = []
        build_chain(dep_graph, field_name, chain, set(), depth=0, max_depth=5)
        return {
            "query_type": "dependency_chain",
            "field": field_name,
            "determines": determines,
            "determined_by": determined_by,
            "downstream_chain": chain,
        }

    raise ValueError(f"알 수 없는 query_type: {query_type}")


def build_chain(
    dep_graph: Any,
    field_name: str,
    chain: list[str],
    visited: set[str],
    depth: int,
    max_depth: int,
) -> None:
    if depth >= max_depth or field_name in visited:
        return
    visited.add(field_name)
    for rel in dep_graph.relationships:
        if rel.source_field.upper() == field_name.upper():
            target = rel.target_field.upper()
            entry = f"{field_name} → {target} ({rel.dependency_type.value})"
            if entry not in chain:
                chain.append(entry)
                build_chain(dep_graph, target, chain, visited, depth + 1, max_depth)


def dispatch_ontology_query(
    args: dict[str, Any],
    get_store: StoreFactory,
    get_recipe_store: RecipeStoreFactory,
) -> dict[str, Any]:
    store = get_store()
    if args.get("list_all"):
        return {
            "transactions": store.list_transactions(),
            "patterns": store.list_patterns(),
            "processes": store.list_processes(),
        }

    query_type = args.get("query_type")
    if query_type:
        return dispatch_ontology_query_extended(store, query_type, args)

    tcode = args.get("tcode")
    if not tcode:
        raise ValueError("트랜잭션 코드를 지정하세요.")

    if field_search := args.get("field_search"):
        result = store.query_field(str(tcode), str(field_search))
        if result:
            return result
        return {"error": True, "message": f"필드 '{field_search}'를 찾을 수 없습니다."}

    if args.get("actions"):
        return {
            "transaction": tcode,
            "actions": store.query_actions(str(tcode)),
        }

    if action_search := args.get("action_search"):
        ontology = store.load_transaction(str(tcode))
        if not ontology:
            return {"error": True, "message": f"'{tcode}' 온톨로지가 없습니다."}
        matches = ontology.find_action_path(str(action_search))
        if not matches:
            return {
                "transaction": tcode,
                "action_search": action_search,
                "count": 0,
                "paths": [],
                "hint": "일치하는 경로가 없습니다. sap_recipe_search로 외부 레시피를 검색하거나, 직접 트리를 탐색하세요.",
            }
        if len(matches) == 1:
            path, ap = matches[0]
            execution_plan = [
                {"order": i + 1, "tool": s.tool, "args": s.args, **({"hint": s.hint} if s.hint else {})}
                for i, s in enumerate(ap.steps)
            ]
            return {
                "transaction": tcode,
                "action_search": action_search,
                "count": 1,
                "exact_match": True,
                "path": path,
                "context": ap.context,
                "execution_plan": execution_plan,
                "instruction": (
                    f"경로 '{path}'가 검색되었습니다. "
                    "execution_plan의 단계를 순서대로 정확히 실행하세요. "
                    "별도 탐색(sap_tree_read, sap_screen)하지 마세요. "
                    "완료 후 sap_recipe_success를 호출하세요."
                ),
            }
        return {
            "transaction": tcode,
            "action_search": action_search,
            "count": len(matches),
            "exact_match": False,
            "paths": [
                {"path": p, "context": a.context, "keywords": a.keywords, "success_count": a.success_count}
                for p, a in matches
            ],
            "hint": "여러 경로가 검색되었습니다. context를 비교하여 적합한 경로를 선택한 후, action_search에 정확한 경로명을 입력하세요.",
        }

    ontology = store.load_transaction(str(tcode))
    if ontology:
        result = ontology.model_dump(mode="json")
        if ontology.recipes:
            recipe_store = get_recipe_store()
            recipe_details = []
            for rid in ontology.recipes:
                r = recipe_store.load(rid)
                if r:
                    recipe_details.append(
                        {
                            "id": r.id,
                            "description": r.description,
                            "context": r.context,
                            "keywords": r.keywords,
                            "step_count": len(r.steps),
                            "success_count": r.success_count,
                        }
                    )
            recipe_details.sort(key=lambda x: -x["success_count"])
            if recipe_details:
                result["recipe_details"] = recipe_details
                result["recipe_hint"] = (
                    f"이 트랜잭션에 {len(recipe_details)}개의 레시피가 있습니다. "
                    "사용자의 요청과 description/context를 비교하여 적합한 레시피가 있으면 "
                    "sap_recipe_show(recipe_id=...)로 실행 계획을 조회하세요. "
                    "직접 탐색보다 레시피를 따르는 것이 빠르고 정확합니다."
                )
        if ontology.action_paths:
            result["available_actions"] = [
                {"path": p, "context": a.context, "step_count": len(a.steps), "success_count": a.success_count}
                for p, a in ontology.action_paths.items()
            ]
        return result
    return {"error": True, "message": f"'{tcode}' 온톨로지가 없습니다."}


def dispatch_annotate(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """필드 시맨틱 수동 입력/수정."""
    store = get_store()
    tcode = str(args["tcode"]).upper()
    field_query = str(args["field"])

    ontology = store.load_transaction(tcode)
    if ontology is None:
        raise ValueError(f"온톨로지가 없습니다: {tcode}. 먼저 sap learn으로 학습하세요.")

    updated = 0
    for screen in ontology.screens.values():
        for field in screen.fields:
            if field.id == field_query or field.name == field_query or field.semantic.sap_field_name == field_query:
                sem = field.semantic
                if "desc" in args and args["desc"]:
                    sem.description = args["desc"]
                if "desc_ko" in args and args["desc_ko"]:
                    sem.description_ko = args["desc_ko"]
                if "synonyms" in args and args["synonyms"]:
                    sem.synonyms = [s.strip() for s in args["synonyms"].split(",")]
                if "synonyms_ko" in args and args["synonyms_ko"]:
                    sem.synonyms_ko = [s.strip() for s in args["synonyms_ko"].split(",")]
                if "validation" in args and args["validation"]:
                    sem.validation = args["validation"]
                if "domain_context" in args and args["domain_context"]:
                    sem.domain_context = args["domain_context"]
                updated += 1

    if updated == 0:
        raise ValueError(f"필드를 찾을 수 없습니다: {field_query} (tcode={tcode})")

    store.save_transaction(ontology)
    return {
        "annotated": True,
        "tcode": tcode,
        "field": field_query,
        "screens_updated": updated,
    }


def dispatch_annotate_dependency(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """필드 종속 관계를 수동으로 추가."""
    from ..ontology.schema import FieldDependencyType, FieldRelationship

    store = get_store()
    source = str(args["source"]).upper()
    target = str(args["target"]).upper()
    dep_type_str = str(args.get("type") or "value_determines").lower()

    valid_types = {t.value: t for t in FieldDependencyType}
    if dep_type_str not in valid_types:
        raise ValueError(
            f"잘못된 종속성 유형: {dep_type_str}. "
            f"가능한 값: {', '.join(valid_types.keys())}"
        )

    dep_type = valid_types[dep_type_str]
    description = args.get("description") or ""
    observed_in_raw = args.get("observed_in") or ""
    observed_in = [t.strip().upper() for t in observed_in_raw.split(",") if t.strip()] if observed_in_raw else []

    graph = store.load_field_dependencies()

    existing = None
    for rel in graph.relationships:
        if (
            rel.source_field.upper() == source
            and rel.target_field.upper() == target
            and rel.dependency_type == dep_type
        ):
            existing = rel
            break

    if existing:
        if description:
            existing.description = description
        for tcode in observed_in:
            if tcode not in existing.observed_in:
                existing.observed_in.append(tcode)
        action = "updated"
    else:
        new_rel = FieldRelationship(
            source_field=source,
            target_field=target,
            dependency_type=dep_type,
            description=description,
            observed_in=observed_in,
        )
        graph.relationships.append(new_rel)
        action = "added"

    store.save_field_dependencies(graph)
    return {
        "annotated": True,
        "action": action,
        "source_field": source,
        "target_field": target,
        "dependency_type": dep_type_str,
        "description": description,
        "observed_in": observed_in if observed_in else (existing.observed_in if existing else []),
        "total_relationships": len(graph.relationships),
    }


def dispatch_ontology_relationships(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """필드 관계 조회 — 크로스 트랜잭션 필드 인덱스."""
    store = get_store()

    if args.get("rebuild"):
        registry = store.rebuild_field_registry()
        return {
            "rebuilt": True,
            "field_count": len(registry.fields),
            "last_rebuilt": registry.last_rebuilt,
        }

    field_name = args.get("field")
    registry = store.load_field_registry()

    if field_name:
        key = field_name.upper()
        entry = registry.fields.get(key)
        if entry is None:
            matches = [e for k, e in registry.fields.items() if key in k.upper()]
            if len(matches) == 1:
                entry = matches[0]
            elif matches:
                return {
                    "partial_matches": [
                        {
                            "sap_field_name": m.sap_field_name,
                            "description": m.description,
                            "transactions": sorted({o.tcode for o in m.occurrences}),
                        }
                        for m in matches
                    ]
                }
            else:
                return {"error": True, "message": f"필드 '{field_name}'를 레지스트리에서 찾을 수 없습니다."}
        return entry.model_dump(mode="json")

    return {
        "field_count": len(registry.fields),
        "last_rebuilt": registry.last_rebuilt,
        "fields": {
            k: {
                "description": v.description,
                "occurrence_count": len(v.occurrences),
                "transactions": sorted({o.tcode for o in v.occurrences}),
            }
            for k, v in sorted(registry.fields.items())
        },
    }


def dispatch_field_dependencies(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """필드 종속성 조회 — 글로벌 필드 종속성 그래프."""
    store = get_store()

    if args.get("rebuild"):
        graph = store.rebuild_field_dependencies()
        return {
            "rebuilt": True,
            "relationship_count": len(graph.relationships),
            "last_rebuilt": graph.last_rebuilt,
        }

    field_name = args.get("field")
    graph = store.load_field_dependencies()

    if not graph.relationships:
        return {"error": True, "message": "필드 종속성 데이터가 없습니다. --rebuild로 먼저 구축하세요."}

    if field_name:
        key = field_name.upper()
        matches = [
            r for r in graph.relationships
            if key in r.source_field.upper() or key in r.target_field.upper()
        ]
        if not matches:
            return {"error": True, "message": f"필드 '{field_name}'의 종속 관계를 찾을 수 없습니다."}
        return {
            "field": field_name,
            "relationships": [r.model_dump(mode="json") for r in matches],
        }

    return {
        "relationship_count": len(graph.relationships),
        "last_rebuilt": graph.last_rebuilt,
        "relationships": [
            {
                "source": r.source_field,
                "target": r.target_field,
                "type": r.dependency_type,
                "observed_in": r.observed_in,
            }
            for r in graph.relationships
        ],
    }


def dispatch_document_flow(args: dict[str, Any], get_store: StoreFactory) -> dict[str, Any]:
    """문서 흐름 조회 — 트랜잭션 간 문서 흐름 온톨로지."""
    store = get_store()

    if args.get("rebuild"):
        flow = store.rebuild_document_flow()
        return {
            "rebuilt": True,
            "edge_count": len(flow.edges),
            "last_rebuilt": flow.last_rebuilt,
        }

    tcode = args.get("tcode")
    flow = store.load_document_flow()

    if not flow.edges:
        return {"error": True, "message": "문서 흐름 데이터가 없습니다. --rebuild로 먼저 구축하세요."}

    if tcode:
        key = tcode.upper()
        matches = [
            e for e in flow.edges
            if key == e.source_tcode.upper() or key == e.target_tcode.upper()
        ]
        if not matches:
            return {"error": True, "message": f"트랜잭션 '{tcode}'의 문서 흐름을 찾을 수 없습니다."}
        return {
            "tcode": tcode,
            "edges": [e.model_dump(mode="json") for e in matches],
        }

    return {
        "edge_count": len(flow.edges),
        "last_rebuilt": flow.last_rebuilt,
        "edges": [
            {
                "source": e.source_tcode,
                "target": e.target_tcode,
                "flow_type": e.flow_type,
                "linking_field": e.linking_field,
                "confidence": e.confidence,
            }
            for e in flow.edges
        ],
    }


def dispatch_ontology_rebuild(get_store: StoreFactory) -> dict[str, Any]:
    """온톨로지 전체 재구축 — field_registry + field_dependencies + document_flow 일괄."""
    store = get_store()

    registry = store.rebuild_field_registry()
    deps = store.rebuild_field_dependencies()
    flow = store.rebuild_document_flow()

    return {
        "rebuilt": True,
        "field_registry": {
            "field_count": len(registry.fields),
            "last_rebuilt": registry.last_rebuilt,
        },
        "field_dependencies": {
            "relationship_count": len(deps.relationships),
            "last_rebuilt": deps.last_rebuilt,
        },
        "document_flow": {
            "edge_count": len(flow.edges),
            "last_rebuilt": flow.last_rebuilt,
        },
    }
