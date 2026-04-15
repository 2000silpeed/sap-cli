---
name: sap-ontology-qa
description: SAP 온톨로지 Q&A 스킬. 저장된 온톨로지 JSON(transactions, processes, patterns, field_registry, field_relationships, document_flow)을 직접 읽어 SAP 도메인 질문에 구조화된 답변을 제공한다. 트랜잭션 요약, 필드 의미/교차조회, 필수 필드, 업무 프로세스 흐름, 필드 종속성, 공통 패턴 질문을 처리한다. MCP 서버나 SAP 연결 없이 동작. 한국어 키워드(자재, 수주, 플랜트, 필수필드 등)와 SAP 필드명(MATNR, AUART, WERKS 등)으로 트리거.
---

# SAP 온톨로지 Q&A 스킬

SAP 도메인 질문에 대해 **저장된 온톨로지 데이터를 직접 읽어** 구조화된 답변을 제공한다.
MCP 서버나 SAP 연결 없이 동작한다.

## 데이터 소스

모든 온톨로지 데이터는 `sap_cli/ontology/knowledge/` 하위에 JSON으로 저장되어 있다.

```
sap_cli/ontology/knowledge/
├── transactions/          # 항상 존재 — 트랜잭션별 화면/필드/시맨틱 구조
│   ├── MM03.json         # 자재 조회
│   ├── VA01.json         # 판매오더 생성
│   ├── ME21N.json        # 구매오더 생성
│   ├── MIGO.json         # 입고 처리
│   ├── MIRO.json         # 송장 검증
│   ├── VL01N.json        # 출하(납품) 생성
│   └── VF01.json         # 대금청구 생성
├── processes/             # 항상 존재 — 업무 프로세스 (트랜잭션 시퀀스)
│   ├── procurement_basic.json  # 자재조달: MM01→ME21N→MIGO→MIRO
│   └── sales_to_billing.json   # 판매: VA01→VL01N→VF01
├── patterns/              # 항상 존재 — 공통 패턴
│   ├── save_flow.json    # 저장 흐름 (Ctrl+S→상태확인→모달)
│   └── search_help.json  # F4 검색 도움말 패턴
├── field_registry.json    # 없을 수 있음 — rebuild 후 생성
├── field_relationships.json  # 없을 수 있음 — rebuild 후 생성
└── document_flow.json     # 없을 수 있음 — rebuild 후 생성
```

## 파일이 없을 때 대체 방법

| 파일 | 없을 때 대체 |
|------|------------|
| `field_registry.json` | `transactions/*.json`을 모두 읽고 `semantic.sap_field_name`으로 직접 수집 |
| `field_relationships.json` | 각 `transactions/{TCODE}.json`의 `field_dependencies` 배열에서 직접 추출 |
| `document_flow.json` | `processes/*.json`의 `data_flows` + 각 트랜잭션의 `related_transactions` 기반으로 제한 답변 |

## 질문 분류 우선순위

사용자 질문에 복수 유형이 겹칠 때 아래 순서로 분류한다:

1. **tcode + "필수"/"required"** → 필수 필드 조회 (유형 4)
2. **SAP 필드명 또는 한국어 필드명 + "뭐야"/"의미"/"설명"** → 필드 의미 (유형 2)
3. **필드명 + "어디"/"사용"/"나와"** → 필드 교차 조회 (유형 3)
4. **필드명 + "종속"/"영향"/"바꾸면"/"결정"** → 필드 종속성 (유형 6)
5. **"프로세스"/"흐름" + 업무 키워드** → 업무 프로세스 (유형 5), processes/*.json 우선 조회
6. **tcode만 언급** → 트랜잭션 요약 (유형 1)
7. **"저장"/"F4"/"검색 도움말" 등 패턴 키워드** → 공통 패턴 (유형 7)

## 질문 유형별 답변 방법

### 1. 트랜잭션 요약 ("VA01이 뭐야?", "MM03 화면 구조")

**데이터**: `transactions/{TCODE}.json` 읽기

**추출 항목**:
- `tcode`, `title`, `module`, `description` — 기본 정보
- `screens` — 각 화면의 `screen_number`와 `title`
- 각 화면의 `fields` 중 `required: true` — 필수 필드
- `related_transactions` — 관련 트랜잭션 (있으면)

**답변 형식**:
```
[TCODE] - [title]
모듈: [module]
설명: [description]

화면:
  1. 화면 [screen_number]: [title]
  2. ...

필수 필드:
  - 화면 [screen_number] > [label] ([sap_field_name]): [semantic.description]

관련 트랜잭션: [related_transactions]
```

### 2. 필드 의미 ("AUART가 뭐야?", "자재유형 필드")

**데이터**: `transactions/*.json`의 모든 필드에서 `semantic.sap_field_name`이 일치하는 항목 검색.

**추출 항목** (`field.semantic`):
- `sap_field_name` — SAP 기술 필드명
- `description` — 한국어 설명
- `synonyms` — 동의어 목록 (한/영)
- `validation` — 입력 규칙 (자릿수, 형식)
- `domain_context` — 도메인 맥락 (어디서 어떻게 사용되는지)

**답변 형식**:
```
[sap_field_name] — [description]
동의어: [synonyms 나열]
입력 규칙: [validation]
맥락: [domain_context]
사용 위치: [tcode] 화면 [screen_number] ([screen title])
```

### 3. 필드 교차 조회 ("WERKS가 어디어디 나와?", "MATNR 사용 트랜잭션")

**데이터 (우선)**: `field_registry.json` 읽기
- 실제 스키마: `{ "fields": { "MATNR": { "sap_field_name": "MATNR", "description": "...", "occurrences": [{ "tcode": "MM03", "screen_number": 4000, "element_id": "...", "label": "Material", "changeable": false }] } } }`

**데이터 (대체)**: `field_registry.json`이 없으면 `transactions/*.json`을 모두 읽고 해당 `sap_field_name`을 가진 필드를 직접 수집.

**답변 형식**:
```
[FIELD_NAME] 사용 위치:
  - [tcode] 화면 [screen_number]: [label] (changeable: [true/false])
  - ...
```

### 4. 필수 필드 조회 ("VA01 필수 필드", "MM03 초기화면 필수 입력")

**데이터**: `transactions/{TCODE}.json`의 각 화면에서 `required: true` 필드 필터링

**답변 형식**:
```
[TCODE] 필수 필드:

화면 [screen_number] ([title]):
  - [label] ([sap_field_name]): [semantic.description]
    입력 규칙: [semantic.validation]
  - ...
```

### 5. 업무 프로세스 ("조달 프로세스", "판매에서 납품까지 흐름")

**데이터**: `processes/*.json` 읽기

**추출 항목**:
- `name`, `module`, `description` — 프로세스 개요
- `steps[]` — 각 스텝의 `tcode`, `description`, `input_fields`, `expected_output`
- `data_flows[]` — 스텝 간 데이터 전달 관계

**답변 형식**:
```
[name] ([module])
[description]

스텝:
  0. [tcode] — [description]
     입력: [input_fields]
     출력: [expected_output]
  1. ...

데이터 흐름:
  스텝 0 → 스텝 1: [source_field] → [target_field] ([description])
  ...
```

### 6. 필드 종속성 ("AUART 바꾸면 뭐가 변해?", "WERKS 종속 필드")

**데이터 (우선)**: `field_relationships.json` 읽기
- 실제 스키마: `{ "relationships": [{ "source_field": "AUART", "target_field": "PSTYV", "dependency_type": "value_determines", "description": "...", "observed_in": ["VA01"] }] }`
- `source_field`가 `target_field`를 **결정**하는 방향이다 (source → target)

**데이터 (대체)**: 파일이 없으면 `transactions/{TCODE}.json` 내의 `field_dependencies` 배열에서 해당 필드의 종속 관계를 추출.

**종속성 유형**:
- `value_determines` — 값이 다른 필드 값을 결정 (KUNNR → ZTERM)
- `input_order` — 입력 순서 종속 (WERKS 먼저 → LGORT)
- `lookup` — 조회 종속 (MATNR → MAKTX)
- `field_activation` — 필드/탭 활성화 (MTART → 뷰 목록)

**답변 형식**:
```
[FIELD] 종속 관계:

[FIELD]가 결정하는 필드 (source → target):
  - → [target_field] ([dependency_type]): [description]

[FIELD]에 의존하는 필드 (source → [FIELD]):
  - [source_field] → ([dependency_type]): [description]
```

### 7. 공통 패턴 ("저장 흐름", "F4 검색 도움말")

**데이터**: `patterns/*.json` 읽기

**답변 형식**: 패턴의 `sequence` 단계별로 설명

## 키워드 → tcode 매핑

사용자가 한국어로 질문할 때 tcode를 추론하는 참조표:

| 한국어 키워드 | tcode | 설명 |
|-------------|-------|------|
| 자재 조회, 자재 마스터 | MM03 | Display Material |
| 판매오더, 수주 | VA01 | Create Sales Order |
| 구매오더, 발주 | ME21N | Create Purchase Order |
| 입고, 자재입고, 물품수령 | MIGO | Goods Receipt |
| 송장, 송장검증, 인보이스 | MIRO | Invoice Verification |
| 출하, 납품 | VL01N | Create Outbound Delivery |
| 대금청구, 빌링 | VF01 | Create Billing Document |

| 한국어 키워드 | 프로세스 |
|-------------|---------|
| 조달, 구매 흐름 | procurement_basic |
| 판매, SD 흐름 | sales_to_billing |

## 필드명 → SAP 필드 매핑

사용자가 한국어로 필드를 물어볼 때:

| 한국어 | SAP 필드 | 설명 |
|-------|---------|------|
| 자재번호, 품번 | MATNR | 자재 식별자 |
| 자재유형 | MTART | FERT/ROH/HALB 등 |
| 자재내역, 품명 | MAKTX | 자재 설명 |
| 기본단위 | MEINS | EA/KG 등 |
| 플랜트, 공장 | WERKS | 생산/물류 거점 |
| 저장위치, 창고 | LGORT | 재고 보관 위치 |
| 오더유형 | AUART | OR/SO/RE 등 |
| 판매조직 | VKORG | 판매 조직 코드 |
| 유통채널 | VTWEG | 10/20 등 |
| 제품군 | SPART | 제품 분류 |
| 고객번호, 주문자 | KUNNR | 판매처 코드 |
| 납품일 | KETDAT | 요청 납품일 |
| 구매오더번호 | EBELN | 구매문서 번호 |
| 판매오더번호 | VBELN | 판매문서 번호 |
| 자재문서번호 | MBLNR | 입고 문서 번호 |
| 전기일 | BUDAT | 회계 전기일 |
| 회사코드 | BUKRS | 법인 코드 |

## 답변 원칙

1. **온톨로지 데이터 기반**: 추측하지 않고 JSON에 있는 정보만 사용
2. **없으면 없다고 답변**: 해당 tcode/필드가 온톨로지에 없으면 "아직 학습되지 않았습니다. `sap_learn` 도구로 학습할 수 있습니다."로 안내
3. **파일 없으면 대체 경로**: field_registry/relationships/document_flow가 없으면 위 "대체 방법" 참조
4. **한국어 우선**: 답변은 한국어, 필드명은 SAP 원문 병기
5. **구조화된 답변**: 나열형이 아닌 화면/필드/프로세스 구조에 맞춘 답변
6. **교차 참조 제공**: 관련 트랜잭션, 프로세스, 패턴이 있으면 함께 안내
