# SAP CLI 스킬 설치·사용 설명서

이 폴더는 Claude Code / Codex가 SAP CLI를 올바르게 쓰도록 하는 스킬 묶음이다.
스킬은 "언제 무엇을 어떤 순서로 실행할지"를 에이전트에게 알려주는 지식 파일이며,
코드 실행은 전부 `sap` CLI와 MCP 도구가 담당한다.

## 구성

| 경로 | 이름 | 역할 | 주 사용 상황 |
|------|------|------|------|
| `SKILL.md` | `sap` | MCP 도구 레퍼런스 + SAP 도메인 지식 (메인) | MCP로 SAP를 조작하는 모든 작업 |
| `sap-cli-usage/` | `sap-cli-usage` | 개별 화면 조작 CLI 사용법 | bash에서 `sap connect/run/screen/set/...`으로 수동 조작 |
| `sap-map-build/` | `sap-map-build` | UI 지도 수집·보강·점검 (`sap map-*`) | 트랜잭션 화면 지도를 새로 만들거나 확장할 때 |
| `sap-plan-author/` | `sap-plan-author` | 자연어 요청 → 실행 계획 컴파일 (`sap plan-preview`) | 지도가 있는 트랜잭션에서 실행 계획을 만들 때 |
| `sap-plan-run/` | `sap-plan-run` | 승인·실행·결과 판정 (`sap plan-approve/execute`) | 컴파일된 계획을 실제로 실행할 때 |
| `sap-ontology-qa/` | `sap-ontology-qa` | 저장된 온톨로지 JSON Q&A | SAP 연결 없이 "MM03에 어떤 필드 있어?" 같은 질문 |
| `agents/claude/sap-map-planner.md` | `sap-map-planner` | 위 셋(build→author→run)을 묶어 자연어 요청 하나로 수행하는 에이전트 정의 | "MM03에서 자재 조회해줘" 같은 요청을 통째로 맡길 때 |
| `agents/codex/sap-map-planner/` | `sap-map-planner` | 같은 에이전트의 Codex CLI용 스킬 버전 | Codex에서 동일 워크플로 |

## 설치

### Claude Code

스킬 하나당 `~/.claude/skills/<스킬이름>/SKILL.md` 형태로 배치한다.

```powershell
# 전역 설치 (모든 프로젝트에서 사용)
mkdir %USERPROFILE%\.claude\skills
xcopy /E /I skill\sap-cli-usage %USERPROFILE%\.claude\skills\sap-cli-usage
xcopy /E /I skill\sap-map-build %USERPROFILE%\.claude\skills\sap-map-build
xcopy /E /I skill\sap-plan-author %USERPROFILE%\.claude\skills\sap-plan-author
xcopy /E /I skill\sap-plan-run %USERPROFILE%\.claude\skills\sap-plan-run
xcopy /E /I skill\sap-ontology-qa %USERPROFILE%\.claude\skills\sap-ontology-qa

# 메인 스킬 (MCP 사용 시)
mkdir %USERPROFILE%\.claude\skills\sap
copy skill\SKILL.md %USERPROFILE%\.claude\skills\sap\SKILL.md

# 에이전트 정의
mkdir %USERPROFILE%\.claude\agents
copy skill\agents\claude\sap-map-planner.md %USERPROFILE%\.claude\agents\
```

프로젝트에만 적용하려면 `%USERPROFILE%\.claude` 대신 프로젝트 루트의
`.claude\skills\`, `.claude\agents\`에 같은 구조로 넣는다.

macOS/Linux는 `~/.claude/skills/`, `~/.claude/agents/`에 `cp -r`로 동일하게 복사한다.

설치 확인: Claude Code 재시작 후 대화창에서 스킬 이름이 자동 완성되거나,
"SAP 지도 만들어줘" 같은 요청에 해당 스킬 절차를 따르면 정상이다.

### Codex

`skill/agents/codex/sap-map-planner/`를 Codex 스킬 디렉토리에 복사한다.
나머지 스킬 문서는 Codex가 리포지토리를 직접 읽으므로 별도 설치가 없어도
`skill/` 경로를 컨텍스트로 지정해 쓸 수 있다.

## 무엇부터 쓰나 — 추천 워크플로

```
[0] 환경 준비        sap daemon status → 실패 시 sap daemon doctor
[1] 지도 만들기      sap-map-build   (트랜잭션별 1회, 이후 재사용)
[2] 계획 세우기      sap-plan-author (자연어 요청 → plan-preview)
[3] 실행하기         sap-plan-run    (승인 → plan-execute → 결과 판정)
```

- **[1]~[3]을 한 번에**: `sap-map-planner` 에이전트에게 자연어 요청을 통째로 맡긴다.
  예: "MM03에서 자재 100-100 기본 정보 조회해줘"
- **지도 없이 한 화면만 조작**: `sap-cli-usage` (bash) 또는 메인 `sap` 스킬 (MCP)
- **SAP 연결 없이 구조 질문**: `sap-ontology-qa`

### 전제 조건

| 스킬 | SAP GUI 로그인 | daemon | 지도(map) |
|------|:---:|:---:|:---:|
| `sap-cli-usage` / 메인 `sap` | 필요 | 권장 (자동 기동) | 불필요 |
| `sap-map-build` | 필요 | **필수** (traverse/augment) | 만드는 대상 |
| `sap-plan-author` | 필요 | **필수** | 필요 |
| `sap-plan-run` | 필요 | **필수** | 필요 |
| `sap-ontology-qa` | 불필요 | 불필요 | 불필요 |

`passive` 외 모든 지도 수집 모드와 계획 실행은 **테스트 클라이언트에서만** 쓴다
(`--test-client-confirmed` 필요).

## 문제가 생기면

1. `sap daemon doctor` (MCP: `sap_daemon_status`) — 인터프리터/venv, `sap_cli`·
   `pywin32`·`mcp` import 가능 여부, `~/.sap_cli/daemon.log` tail을 한 번에 진단
2. `sap daemon restart` — 원인 수정 후 재기동
3. 각 스킬 문서의 "오류 코드와 대응" / "job 상태와 대응" 표 참조

## 스킬 파일 검증

스킬 문서가 참조하는 CLI 명령·옵션·MCP 도구는 코드와 함께 움직인다. 명령/도구를
추가·변경했다면 스킬 문서의 예시가 실제 CLI와 일치하는지 확인한다:

```bash
# 문서 속 `sap <명령> --옵션` 예시를 실제 CLI --help와 대조
python - <<'PY'
import re, glob, subprocess, sys
examples = {}
for f in glob.glob('skill/**/*.md', recursive=True):
    text = open(f, encoding='utf-8').read()
    for block in re.findall(r'```(?:powershell|bash|sh)?\n(.*?)```', text, re.S):
        for line in block.split('\n'):
            m = re.match(r'\s*sap ([a-z][a-z0-9-]+)\b(.*)', line)
            if m:
                examples.setdefault(m.group(1), set()).update(re.findall(r'(--[a-z0-9-]+)', m.group(2)))
for cmd, opts in sorted(examples.items()):
    r = subprocess.run([sys.executable, '-m', 'sap_cli.cli', cmd, '--help'], capture_output=True, text=True)
    if r.returncode != 0:
        print(f'없는 명령: sap {cmd}')
        continue
    # 참고: rich --help가 긴 옵션명을 자를 수 있어 접두 일치로 확인한다
    shown = re.findall(r'(--[a-z0-9-]+)', r.stdout)
    for o in opts:
        if not any(s == o or o.startswith(s) for s in shown):
            print(f'sap {cmd}: 문서에만 있는 옵션 {o}')
print('검증 완료')
PY
```
