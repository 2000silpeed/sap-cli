# SAP CLI 작업 이력

## 2026-03-31: Codex 리뷰 기반 전면 개선

### Phase 1: Codex 코드 리뷰 (10건 발견)

| # | 이슈 | 분류 | 상태 |
|---|------|------|------|
| 1 | Path Traversal 취약점 (macro/store, ontology/store) | 보안 | 완료 |
| 2 | 매크로 녹화/재생 데이터 불일치 (run/vkey value) | 기능 버그 | 완료 |
| 3 | 화면 검증 로직 반전 (실행 전→후) | 기능 버그 | 완료 |
| 4 | 온톨로지 그래프 네임스페이스 충돌 | 아키텍처 | 완료 |
| 5 | AI 에이전트 SAP 조작 제한 없음 | 보안 | 완료 (ReBAC) |
| 6 | COM 에러 핸들링이 실패를 숨김 | 안정성 | 완료 |
| 7 | MCP 선택적 의존성 임포트 크래시 | 즉시 크래시 | 완료 |
| 8 | 연결 상태 모델 부정확 (liveness) | 안정성 | 완료 |
| 9 | 화면 추출이 온톨로지 스펙과 불일치 | 품질 | 완료 |
| 10 | 프로덕션 준비 미비 (저장경로, 테스트, 중복) | 운영 | 완료 |

### Phase 2: 추가 구현

| 작업 | 내용 | 상태 |
|------|------|------|
| ReBAC 설계 | DESIGN_REBAC.md 작성 | 완료 |
| ReBAC P0 구현 | auth 모듈 (policy, checker, audit) | 완료 |
| MCP 권한 게이트 | handle_tool에 권한 체크 삽입 | 완료 |
| 서비스 레이어 | service.py 추출 (CLI/MCP 중복 제거) | 완료 |
| 데이터 외부화 | ~/.sap_cli/ 경로로 변경 + 시드 복사 | 완료 |
| 테스트 68개 | macro_store, recorder, ontology_store, graph, auth | 완료 |
| pywin32 조건부 | Windows 전용 의존성으로 변경 | 완료 |

### Phase 3: 온톨로지 JSON 품질 개선

| 작업 | 내용 | 상태 |
|------|------|------|
| VA01 필드명 오류 | VBKD-BSTNK→VBAK-BSTNK, KUNNR_SH→KUNNR | 완료 |
| MM03 타입 오류 | MARA-MEINS GuiComboBox→GuiTextField | 완료 |
| MM03 화면 4004 | 빈 화면에 조직레벨 필드 4개 + 뷰 선택 테이블 추가 | 완료 |
| MM03 기본데이터1 | 필드 7→13개 (중량단위, 구부문, 제품계층, EAN 등) | 완료 |
| VA01 오버뷰 | 필드 4→7개 (PO Date, Payment Terms, Incoterms) | 완료 |
| field_dependencies | 가상노드 → 실제 노드 참조로 교체 | 완료 |
| screen_flow | tcode 네임스페이스 + Back 네비게이션 추가 | 완료 |

### Phase 4: 실제 SAP 환경 테스트 대응

| 작업 | 내용 | 상태 |
|------|------|------|
| GetObjectTree 파서 재작성 | properties에 id만 있는 실제 JSON 구조 대응 | 완료 |
| 화면 스캔 속도 최적화 | GetObjectTree 대신 수동 탐색 직접 사용 | 완료 |
| 수동 탐색 개선 | usr/tbar/mbar 분리 스캔, 깊이 제한, 불필요 타입 skip | 완료 |

### Phase 5: Codex 일관성 검증 기반 수정

| # | 이슈 | 긴급도 | 상태 |
|---|------|--------|------|
| 1 | MCP _ACTION_MAP 미정의 (런타임 에러) | P0 | 완료 |
| 2 | MCP 매크로 value 정규화 누락 | P0 | 완료 |
| 3 | connect 싱글턴 불일치 | P0 | 완료 |
| 4 | menus 미수집 (mbar 스캔 누락) | P1 | 완료 |
| 5 | field_dependencies 노드 ID 불일치 | P1 | 완료 |
| 6 | MM03 max_length 누락 | P2 | 완료 |
| 7 | MacroStore 독스트링 이전 경로 | P2 | 완료 |
| 8 | 시드 복사 로직 (patterns 독립 체크) | P2 | 완료 |

### Phase 6: 문서 업데이트

| 파일 | 변경 내용 | 상태 |
|------|-----------|------|
| README.md | ReBAC 섹션, 테스트 섹션, 아키텍처 다이어그램 갱신 | 완료 |
| SETUP.md | 클론 URL, pywin32 설명, 권한 정책 설정 섹션 | 완료 |

---

## 커밋 이력 (2026-03-31)

```
e340878 Codex 일관성 검증 기반 6건 수정
a631fd1 화면 스캔 속도 2차 최적화: 불필요 타입 건너뛰기 + 깊이 제한
fcb4b0c 화면 스캔 속도 개선: GetObjectTree 대신 수동 탐색 직접 사용
6578426 GetObjectTree 파서 속도 최적화: 리프 노드만 FindById 호출
1e289db GetObjectTree 파서 전면 재작성 (실제 SAP 환경 대응)
6164493 README/SETUP 문서 업데이트
7d49f05 온톨로지 JSON 품질 개선 (Codex 검증 기반)
399393c Merge PR #1: pytest 설정 추가
ec29b59 Codex 리뷰 기반 10개 이슈 수정 + ReBAC 권한관리 구현
```

### Phase 7: 데몬 모드 + Shell REPL (2026-03-31)

| 작업 | 내용 | 상태 |
|------|------|------|
| 데몬 서버 | TCP 소켓 기반 상주 프로세스, COM 연결 유지 | 완료 |
| Shell REPL | 대화형/파이프 모드, 데몬 자동 시작 | 완료 |
| 중앙 dispatcher | CLI/데몬/MCP 통합 실행 경로 | 완료 |
| command_parser | 트랜잭션 코드 추출 유틸리티 | 완료 |

### Phase 8: Codex CC 코드리뷰 기반 수정 (2026-04-01)

| # | 이슈 | 심각도 | 상태 |
|---|------|--------|------|
| 1 | MCP vkey 인자 불일치 (vkey→key KeyError) | major | 완료 |
| 2 | 데몬 SAP 인스턴스 상태 불일치 | minor | 완료 |
| 3 | Shell 파이프 모드 에러 시 exit code 미반환 | major | 완료 |
| 4 | 트랜잭션 제한 정책 우회 (raw command fail-open) | major | 완료 |
| 5 | modal/macro_show/macro_delete 권한 검사 우회 | major | 완료 |
| 6 | 명령 정의 4중 중복 → 단일 COMMAND_REGISTRY 통합 | major | 완료 |

## 커밋 이력 (2026-04-01)

```
2204d20 Codex 코드리뷰 6건 수정: registry 통합, 보안 강화, 버그 수정
34f9e33 데몬 모드 추가: COM 연결 유지로 명령 실행 속도 5초→0.2초
```

---

## 남은 작업

### ReBAC P1-P3 (설계만 완료, 구현 미착수)
- P1: 감사 로그 강화 + 위험 트랜잭션 경고 UI
- P2: 온톨로지 그래프 기반 관계 상속 (필드 레벨 세밀 제어)
- P3: 런타임 승인 워크플로우 (MCP sampling 활용)

### 사내 검증 (SAP 환경 필요) → VERIFICATION.md 참조
- [ ] 시나리오 1: 기본 연결 및 화면 탐색
- [ ] 시나리오 2: MM03 자재 조회 자동화
- [ ] 시나리오 3: 매크로 녹화 → 재생 사이클
- [ ] 시나리오 4: 온톨로지 학습
- [ ] 시나리오 5: MCP 서버 + Claude Code 연동
- [ ] 시나리오 6: 데몬 모드 + Shell

### 추가 온톨로지 보강 (SAP 환경 필요)
- MM03 추가 화면 (MRP, 구매, 판매, 저장위치 뷰)
- VA01 하위 탭 (가격조건, 출하, 대금청구, 파트너)
- ME21N, FB01 등 추가 트랜잭션 온톨로지
