# Windows SAP GUI 실기 검증 리포트 템플릿

`WINDOWS_SAP_VERIFICATION.md`를 실행한 뒤 결과를 공유하기 위한 복사용 템플릿입니다.

민감정보를 남기지 않도록 아래 항목은 반드시 마스킹합니다.

- 사용자 계정, 사번, 고객명
- 내부 서버명, 상세 호스트명, IP
- 실제 자재번호/전표번호/구매오더 등 업무 데이터
- 운영 화면의 금액, 수량, 거래처 등 업무상 민감값

---

## 1. 기본 정보

```text
검증 일시:
검증자:
PC/OS:
SAP GUI 버전:
SAP SID/Client:
Python 버전:
sap_cli commit:
설치 방식:
SAP GUI Scripting 클라이언트 활성화 여부:
SAP GUI Scripting 서버 허용 여부:
```

## 2. 최종 판정

```text
최종 판정: PASS / PARTIAL / FAIL / SKIP
운영 전 차단 이슈: 있음 / 없음
```

판정 기준:

- `PASS`: 운영 예정 방식 기준으로 필수 항목이 통과했고 차단 이슈가 없다.
- `PARTIAL`: 핵심 조회 플로우는 가능하지만 일부 컨트롤/통합 기능의 후속 확인이 필요하다.
- `FAIL`: SAP GUI Scripting 활성 환경에서도 기본 연결, 화면 읽기, 상태 확인, 세션 선택 중 하나가 지속 실패한다.
- `SKIP`: 권한/데이터/환경 부재로 주요 검증을 수행하지 못했다.

## 3. 결과 요약

```text
PASS:
FAIL:
PARTIAL:
SKIP:
```

## 4. 항목별 결과

각 항목은 `PASS`, `FAIL`, `PARTIAL`, `SKIP` 중 하나로 기록합니다.

```text
5.1 sap connect:
5.2 sap connections:
5.3 sap screen:
5.4 sap status:
6.x MM03 run/fill/get/status:
7.x table control:
8.x ALV Grid:
9.x tree:
10.x capture:
11.x macro record/play:
12.x session start/end:
13.x recipe suggestion/replay:
14.x MCP 연결:
15.x daemon 모드:
16.x multi-session slot:
```

## 5. 실패/부분 성공 상세

실패나 부분 성공 항목마다 아래 블록을 복사해 작성합니다.

```text
항목:
결과: FAIL / PARTIAL
재현 명령:
기대 결과:
실제 결과:
상태바 메시지:
modal 여부:
스크린/필드 증거:
관련 로그/파일:
임시 우회 방법:
```

## 6. 원인 분류

가장 가까운 원인을 선택합니다.

```text
주된 원인:
- 없음 - 모두 통과
- 환경 - Windows/Python/pywin32/SAP GUI 설치 문제
- SAP GUI 설정 - scripting/보안 팝업/테마 문제
- SAP 권한 - tcode/table/object 권한 부족
- SAP 화면 차이 - 커스텀 dynpro/variant/언어/테마 차이
- sap_cli 버그 - 재현 가능한 CLI/COM 처리 문제
- 판단 보류 - 추가 로그 필요
```

## 7. 증거 파일

민감정보를 제거한 뒤 경로나 요약만 남깁니다.

```text
로그:
JSON 출력:
스크린샷:
capture:
기타:
```

## 8. 후속 조치

```text
- [ ] 운영 전 차단 이슈 해결
- [ ] 재검증 필요 항목 정리
- [ ] sap_cli 버그 이슈 분리
- [ ] 문서/체크리스트 보완
```
