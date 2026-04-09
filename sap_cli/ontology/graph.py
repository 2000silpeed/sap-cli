"""온톨로지 그래프 엔진 - 트랜잭션/화면/필드 관계 탐색.

SOS Tier 1 GraphRAG + Tier 3 NOMIK 원칙:
- 확률적 벡터 검색이 아닌 결정론적 그래프 탐색
- "grep이 문자열의 위치를 답한다면, 그래프는 변경 영향 범위를 답한다"
"""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from pydantic import BaseModel, Field

from .schema import (
    DocumentFlow,
    FieldDependencyGraph,
    FieldRegistry,
    LinkType,
    OntologyLink,
    SapField,
    ScreenOntology,
    TransactionOntology,
)
from .store import OntologyStore


class GraphNode(BaseModel):
    """그래프 노드."""
    id: str
    node_type: str  # system, transaction, screen, field, button, table
    label: str = ""
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphEdge(BaseModel):
    """그래프 엣지."""
    source: str
    target: str
    edge_type: str
    properties: dict[str, Any] = Field(default_factory=dict)


class OntologyGraph:
    """온톨로지 그래프 - 노드와 엣지로 SAP 도메인 지식을 표현.

    SOS GraphRAG: "확률적 추측에서 구조적 이해로"
    - 트랜잭션 간 관계 탐색
    - 화면 전환 경로 추적
    - 필드 영향도 분석 (인과공간)
    """

    def __init__(self, store: OntologyStore | None = None):
        self.store = store or OntologyStore()
        self._nodes: dict[str, GraphNode] = {}
        self._edges: list[GraphEdge] = []
        self._adjacency: dict[str, list[str]] = defaultdict(list)
        self._reverse_adjacency: dict[str, list[str]] = defaultdict(list)

    # -------------------------------------------------------------------
    # Graph Construction
    # -------------------------------------------------------------------

    def build_from_store(self) -> None:
        """저장소의 모든 온톨로지로부터 그래프 구축."""
        for tcode in self.store.list_transactions():
            ontology = self.store.load_transaction(tcode)
            if ontology:
                self._add_transaction(ontology)

        # Phase 11 데이터 통합
        self._add_field_registry_edges(self.store.load_field_registry())
        self._add_field_dependency_edges(self.store.load_field_dependencies())
        self._add_document_flow_edges(self.store.load_document_flow())

    def _add_transaction(self, ontology: TransactionOntology) -> None:
        tx_id = f"tx:{ontology.tcode}"
        self._add_node(GraphNode(
            id=tx_id,
            node_type="transaction",
            label=f"{ontology.tcode} - {ontology.title}",
            properties={
                "tcode": ontology.tcode,
                "module": ontology.module,
                "description": ontology.description,
            },
        ))

        for screen_num, screen in ontology.screens.items():
            self._add_screen(tx_id, screen_num, screen)

        for link in ontology.screen_flow:
            self._add_edge(GraphEdge(
                source=link.source_id,
                target=link.target_id,
                edge_type=link.link_type,
                properties=link.properties,
            ))

        for link in ontology.field_dependencies:
            self._add_edge(GraphEdge(
                source=link.source_id,
                target=link.target_id,
                edge_type=link.link_type,
                properties=link.properties,
            ))

        for related_tcode in ontology.related_transactions:
            self._add_edge(GraphEdge(
                source=tx_id,
                target=f"tx:{related_tcode}",
                edge_type=LinkType.TRANSACTION_CALLS,
            ))

    def _add_screen(self, tx_id: str, screen_num: int, screen: ScreenOntology) -> None:
        tcode = tx_id.removeprefix("tx:")
        screen_id = f"screen:{tcode}:{screen_num}"
        self._add_node(GraphNode(
            id=screen_id,
            node_type="screen",
            label=f"Screen {screen_num}: {screen.title}",
            properties={
                "screen_number": screen_num,
                "program": screen.program,
                "tcode": tcode,
            },
        ))
        self._add_edge(GraphEdge(
            source=tx_id,
            target=screen_id,
            edge_type=LinkType.TRANSACTION_HAS_SCREEN,
        ))

        for field in screen.fields:
            field_id = f"field:{tcode}:{screen_num}:{field.name}"
            self._add_node(GraphNode(
                id=field_id,
                node_type="field",
                label=field.label or field.name,
                properties={
                    "sap_field_name": field.semantic.sap_field_name or field.name,
                    "changeable": field.changeable,
                    "type": field.type,
                    "tcode": tcode,
                    "screen_number": screen_num,
                },
            ))
            self._add_edge(GraphEdge(
                source=screen_id,
                target=field_id,
                edge_type=LinkType.SCREEN_CONTAINS,
            ))

    def _add_node(self, node: GraphNode) -> None:
        self._nodes[node.id] = node

    def _add_edge(self, edge: GraphEdge) -> None:
        self._edges.append(edge)
        self._adjacency[edge.source].append(edge.target)
        self._reverse_adjacency[edge.target].append(edge.source)

    # -------------------------------------------------------------------
    # Graph Queries
    # -------------------------------------------------------------------

    def get_node(self, node_id: str) -> GraphNode | None:
        return self._nodes.get(node_id)

    def get_neighbors(self, node_id: str, edge_type: str | None = None) -> list[GraphNode]:
        """인접 노드 조회 (선택적 엣지 타입 필터)."""
        neighbor_ids = self._adjacency.get(node_id, [])
        nodes = []
        for nid in neighbor_ids:
            if edge_type:
                matching = any(
                    e.source == node_id and e.target == nid and e.edge_type == edge_type
                    for e in self._edges
                )
                if not matching:
                    continue
            if node := self._nodes.get(nid):
                nodes.append(node)
        return nodes

    def get_parents(self, node_id: str) -> list[GraphNode]:
        """역방향 인접 노드 (부모) 조회."""
        parent_ids = self._reverse_adjacency.get(node_id, [])
        return [self._nodes[pid] for pid in parent_ids if pid in self._nodes]

    def find_path(self, from_id: str, to_id: str, max_depth: int = 10) -> list[str] | None:
        """두 노드 간 최단 경로 (BFS)."""
        if from_id == to_id:
            return [from_id]

        visited = {from_id}
        queue: list[list[str]] = [[from_id]]

        while queue:
            path = queue.pop(0)
            if len(path) > max_depth:
                break

            current = path[-1]
            for neighbor_id in self._adjacency.get(current, []):
                if neighbor_id == to_id:
                    return path + [to_id]
                if neighbor_id not in visited:
                    visited.add(neighbor_id)
                    queue.append(path + [neighbor_id])

        return None

    def get_related_transactions(self, tcode: str) -> list[GraphNode]:
        """관련 트랜잭션 조회."""
        tx_id = f"tx:{tcode}"
        return self.get_neighbors(tx_id, edge_type=LinkType.TRANSACTION_CALLS)

    def get_transaction_screens(self, tcode: str) -> list[GraphNode]:
        """트랜잭션의 화면 목록."""
        tx_id = f"tx:{tcode}"
        return self.get_neighbors(tx_id, edge_type=LinkType.TRANSACTION_HAS_SCREEN)

    def get_screen_fields(self, tcode: str, screen_number: int) -> list[GraphNode]:
        """화면의 필드 목록."""
        screen_id = f"screen:{tcode}:{screen_number}"
        return self.get_neighbors(screen_id, edge_type=LinkType.SCREEN_CONTAINS)

    def get_field_impact(self, tcode: str, screen_number: int, field_name: str, max_depth: int = 5) -> list[GraphNode]:
        """필드 변경 시 영향받는 노드 목록 (인과 전파).

        SOS 인과공간: "변경 영향도 분석(Impact Analysis)"
        """
        field_id = f"field:{tcode}:{screen_number}:{field_name}"
        affected = []
        visited = {field_id}
        queue = [field_id]

        depth = 0
        while queue and depth < max_depth:
            next_queue = []
            for current in queue:
                for edge in self._edges:
                    if edge.source == current and edge.edge_type == LinkType.FIELD_DEPENDS_ON:
                        if edge.target not in visited:
                            visited.add(edge.target)
                            next_queue.append(edge.target)
                            if node := self._nodes.get(edge.target):
                                affected.append(node)
            queue = next_queue
            depth += 1

        return affected

    # -------------------------------------------------------------------
    # Phase 11: Cross-Transaction Edge Construction
    # -------------------------------------------------------------------

    def _add_field_registry_edges(self, registry: FieldRegistry) -> None:
        """FieldRegistry → SAME_FIELD 엣지: 동일 필드가 다른 트랜잭션에 존재."""
        for sfn, entry in registry.fields.items():
            if len(entry.occurrences) < 2:
                continue
            # 트랜잭션 단위로 그룹핑하여 트랜잭션 간 엣지 생성
            tcodes = sorted({o.tcode for o in entry.occurrences})
            for i, t1 in enumerate(tcodes):
                for t2 in tcodes[i + 1:]:
                    self._add_edge(GraphEdge(
                        source=f"tx:{t1}",
                        target=f"tx:{t2}",
                        edge_type=LinkType.SAME_FIELD,
                        properties={"field": sfn, "description": entry.description},
                    ))

    def _add_field_dependency_edges(self, graph: FieldDependencyGraph) -> None:
        """FieldDependencyGraph → FIELD_DETERMINES 엣지."""
        for rel in graph.relationships:
            self._add_edge(GraphEdge(
                source=f"field_global:{rel.source_field}",
                target=f"field_global:{rel.target_field}",
                edge_type=LinkType.FIELD_DETERMINES,
                properties={
                    "dependency_type": rel.dependency_type.value,
                    "observed_in": rel.observed_in,
                    "description": rel.description,
                },
            ))
            # 글로벌 필드 노드 추가
            for fname in (rel.source_field, rel.target_field):
                fid = f"field_global:{fname}"
                if fid not in self._nodes:
                    self._add_node(GraphNode(
                        id=fid,
                        node_type="field_global",
                        label=fname,
                        properties={"sap_field_name": fname},
                    ))

    def _add_document_flow_edges(self, flow: DocumentFlow) -> None:
        """DocumentFlow → DOCUMENT_FLOW 엣지."""
        for edge in flow.edges:
            self._add_edge(GraphEdge(
                source=f"tx:{edge.source_tcode}",
                target=f"tx:{edge.target_tcode}",
                edge_type=LinkType.DOCUMENT_FLOW,
                properties={
                    "flow_type": edge.flow_type.value,
                    "linking_field": edge.linking_field,
                    "confidence": edge.confidence,
                    "description": edge.description,
                },
            ))
            # 트랜잭션 노드가 없으면 추가
            for tc in (edge.source_tcode, edge.target_tcode):
                tid = f"tx:{tc}"
                if tid not in self._nodes:
                    self._add_node(GraphNode(
                        id=tid, node_type="transaction", label=tc,
                        properties={"tcode": tc},
                    ))

    # -------------------------------------------------------------------
    # Phase 11: Cross-Transaction Queries
    # -------------------------------------------------------------------

    def find_field_across_transactions(self, sap_field_name: str) -> list[GraphNode]:
        """필드가 사용되는 트랜잭션 목록 (SAME_FIELD 엣지 기반)."""
        key = sap_field_name.upper()
        result = []
        seen = set()
        for edge in self._edges:
            if edge.edge_type != LinkType.SAME_FIELD:
                continue
            if edge.properties.get("field", "").upper() != key:
                continue
            for nid in (edge.source, edge.target):
                if nid not in seen:
                    seen.add(nid)
                    if node := self._nodes.get(nid):
                        result.append(node)
        return result

    def get_field_dependencies(self, sap_field_name: str) -> list[dict[str, Any]]:
        """특정 필드의 종속성 관계 (FIELD_DETERMINES 엣지 기반)."""
        key = sap_field_name.upper()
        fid = f"field_global:{key}"
        result = []
        for edge in self._edges:
            if edge.edge_type != LinkType.FIELD_DETERMINES:
                continue
            if edge.source == fid or edge.target == fid:
                direction = "determines" if edge.source == fid else "determined_by"
                other = edge.target if edge.source == fid else edge.source
                result.append({
                    "field": other.removeprefix("field_global:"),
                    "direction": direction,
                    **edge.properties,
                })
        return result

    def get_document_flow(self, tcode: str) -> list[dict[str, Any]]:
        """특정 트랜잭션의 문서 흐름 (DOCUMENT_FLOW 엣지 기반)."""
        tid = f"tx:{tcode.upper()}"
        result = []
        for edge in self._edges:
            if edge.edge_type != LinkType.DOCUMENT_FLOW:
                continue
            if edge.source == tid or edge.target == tid:
                direction = "outgoing" if edge.source == tid else "incoming"
                other = edge.target if edge.source == tid else edge.source
                result.append({
                    "tcode": other.removeprefix("tx:"),
                    "direction": direction,
                    **edge.properties,
                })
        return result

    def get_process_path(self, from_tcode: str, to_tcode: str, max_depth: int = 10) -> list[str] | None:
        """두 트랜잭션 간 문서 흐름 경로 (BFS, DOCUMENT_FLOW 엣지만 사용).

        Returns: 트랜잭션 코드 리스트 (경로) 또는 None.
        """
        start = f"tx:{from_tcode.upper()}"
        end = f"tx:{to_tcode.upper()}"

        if start == end:
            return [from_tcode.upper()]

        # DOCUMENT_FLOW 엣지만으로 인접 리스트 구축
        flow_adj: dict[str, list[str]] = defaultdict(list)
        for edge in self._edges:
            if edge.edge_type == LinkType.DOCUMENT_FLOW:
                flow_adj[edge.source].append(edge.target)

        visited = {start}
        queue: list[list[str]] = [[start]]

        while queue:
            path = queue.pop(0)
            if len(path) > max_depth:
                break
            for neighbor in flow_adj.get(path[-1], []):
                if neighbor == end:
                    return [n.removeprefix("tx:") for n in path + [end]]
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])

        return None

    # -------------------------------------------------------------------
    # Graph Statistics
    # -------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """그래프 통계."""
        type_counts: dict[str, int] = defaultdict(int)
        for node in self._nodes.values():
            type_counts[node.node_type] += 1

        edge_type_counts: dict[str, int] = defaultdict(int)
        for edge in self._edges:
            edge_type_counts[edge.edge_type] += 1

        return {
            "total_nodes": len(self._nodes),
            "total_edges": len(self._edges),
            "node_types": dict(type_counts),
            "edge_types": dict(edge_type_counts),
        }

    def to_dict(self) -> dict[str, Any]:
        """그래프를 직렬화 가능한 딕셔너리로 변환."""
        return {
            "nodes": [n.model_dump() for n in self._nodes.values()],
            "edges": [e.model_dump() for e in self._edges],
            "stats": self.stats(),
        }
