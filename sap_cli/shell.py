"""SAP CLI 대화형 셸 (REPL).

sap shell        → 대화형 모드
echo 'run MM03' | sap shell  → 파이프 모드 (비대화형)

데몬이 실행 중이어야 한다. 미실행 시 자동 시작.
Python 재시작 없이 명령을 연속 실행하므로
매 명령당 오버헤드가 사실상 0이다.
"""

from __future__ import annotations

import json
import shlex
import sys

try:
    import readline  # noqa: F401 — 방향키/히스토리 지원 (Windows에는 없음)
except ImportError:
    pass

from . import daemon

HELP_TEXT = """\
사용 가능한 명령:
  connect [-c N] [-s N]   SAP GUI 연결 (connection/session 인덱스)
  session                 세션 정보
  screen [-f|-a]          화면 구조 (-f: 필드만, -a: 액션만)
  run <TCODE>             트랜잭션 실행
  set <ID> <VALUE>        필드 값 설정
  get <ID>                필드 값 읽기
  press <ID>              버튼 클릭
  vkey <N>                VKey 전송 (0=Enter, 3=Back)
  status                  상태바 메시지
  table <ID>              테이블 읽기
  command <CMD>           커맨드 실행 (/nMM03 등)
  modal                   모달 팝업 확인
  transcript [options]    최근 transcript 조회
  transcript-plan [opts]  transcript 재실행 검토 계획 생성
  help                    이 도움말
  exit / quit             셸 종료
"""


def _parse_line(line: str) -> tuple[str, dict] | None:
    """사용자 입력을 (cmd, args) 튜플로 파싱."""
    line = line.strip()
    if not line or line.startswith("#"):
        return None

    try:
        tokens = shlex.split(line)
    except ValueError:
        tokens = line.split()

    cmd = tokens[0].lower()
    rest = tokens[1:]

    if cmd in ("exit", "quit", "q"):
        return ("__exit__", {})

    if cmd in ("help", "?"):
        return ("__help__", {})

    if cmd == "connect":
        args: dict = {}
        i = 0
        try:
            while i < len(rest):
                if rest[i] in ("-c", "--connection") and i + 1 < len(rest):
                    args["connection"] = int(rest[i + 1])
                    i += 2
                elif rest[i] in ("-s", "--session") and i + 1 < len(rest):
                    args["session"] = int(rest[i + 1])
                    i += 2
                else:
                    i += 1
        except ValueError:
            print(json.dumps({"error": True, "message": "숫자를 입력하세요. 예: connect -c 0 -s 1"}))
            return None
        return ("connect", args)

    if cmd == "screen":
        if "-f" in rest or "--fields-only" in rest:
            return ("screen", {"fields_only": True})
        if "-a" in rest or "--actions-only" in rest:
            return ("screen", {"actions_only": True})
        return ("screen", {})

    if cmd == "run":
        if not rest:
            return None
        return ("run", {"tcode": rest[0]})

    if cmd == "set":
        if len(rest) < 2:
            return None
        return ("set", {"element_id": rest[0], "value": " ".join(rest[1:])})

    if cmd == "get":
        if not rest:
            return None
        return ("get", {"element_id": rest[0]})

    if cmd == "press":
        if not rest:
            return None
        return ("press", {"element_id": rest[0]})

    if cmd == "vkey":
        if not rest:
            return None
        try:
            return ("vkey", {"key": int(rest[0])})
        except ValueError:
            print(json.dumps({"error": True, "message": f"VKey는 숫자여야 합니다: {rest[0]}"}))
            return None

    if cmd == "table":
        if not rest:
            return None
        return ("table", {"element_id": rest[0]})

    if cmd == "command":
        if not rest:
            return None
        return ("command", {"cmd": rest[0]})

    if cmd in ("status", "session", "modal"):
        return (cmd, {})

    if cmd == "transcript":
        args: dict = {"limit": 20}
        i = 0
        while i < len(rest):
            token = rest[i]
            if token in ("-n", "--limit") and i + 1 < len(rest):
                try:
                    args["limit"] = int(rest[i + 1])
                except ValueError:
                    print(json.dumps({"error": True, "message": "limit는 숫자여야 합니다."}))
                    return None
                i += 2
                continue
            if token in ("-o", "--outcome") and i + 1 < len(rest):
                args["outcome"] = rest[i + 1]
                i += 2
                continue
            if token in ("-c", "--command") and i + 1 < len(rest):
                args["command"] = rest[i + 1]
                i += 2
                continue
            if token in ("-t", "--tool") and i + 1 < len(rest):
                args["tool"] = rest[i + 1]
                i += 2
                continue
            if token in ("-a", "--agent") and i + 1 < len(rest):
                args["agent"] = rest[i + 1]
                i += 2
                continue
            if token == "--text" and i + 1 < len(rest):
                args["text"] = rest[i + 1]
                i += 2
                continue
            if token == "--full":
                args["full"] = True
                i += 1
                continue
            i += 1
        return ("transcript_query", args)

    if cmd == "transcript-plan":
        args = {"limit": 20, "index": 0}
        i = 0
        while i < len(rest):
            token = rest[i]
            if token in ("-n", "--limit") and i + 1 < len(rest):
                try:
                    args["limit"] = int(rest[i + 1])
                except ValueError:
                    print(json.dumps({"error": True, "message": "limit는 숫자여야 합니다."}))
                    return None
                i += 2
                continue
            if token in ("-i", "--index") and i + 1 < len(rest):
                try:
                    args["index"] = int(rest[i + 1])
                except ValueError:
                    print(json.dumps({"error": True, "message": "index는 숫자여야 합니다."}))
                    return None
                i += 2
                continue
            if token in ("-o", "--outcome") and i + 1 < len(rest):
                args["outcome"] = rest[i + 1]
                i += 2
                continue
            if token in ("-c", "--command") and i + 1 < len(rest):
                args["command"] = rest[i + 1]
                i += 2
                continue
            if token in ("-t", "--tool") and i + 1 < len(rest):
                args["tool"] = rest[i + 1]
                i += 2
                continue
            if token in ("-a", "--agent") and i + 1 < len(rest):
                args["agent"] = rest[i + 1]
                i += 2
                continue
            if token == "--text" and i + 1 < len(rest):
                args["text"] = rest[i + 1]
                i += 2
                continue
            i += 1
        return ("transcript_plan", args)

    # JSON 직접 입력 지원: {"cmd":"run","args":{"tcode":"MM03"}}
    if line.startswith("{"):
        try:
            obj = json.loads(line)
            return (obj["cmd"], obj.get("args", {}))
        except (json.JSONDecodeError, KeyError):
            pass

    print(json.dumps({"error": True, "message": f"알 수 없는 명령: {cmd}. help 입력"}))
    return None


def _execute(cmd: str, args: dict) -> bool:
    """데몬에 명령을 보내고 결과를 JSON으로 출력. 성공 시 True."""
    result = daemon.send_command(cmd, args)
    if result is None:
        print(json.dumps({"error": True, "message": "데몬 응답 없음"}))
        return False
    if result.get("ok"):
        print(json.dumps(result["data"], ensure_ascii=False, indent=2))
        return True
    elif result.get("approval_required"):
        print(json.dumps({
            "approval_required": True,
            "tool": result.get("tool"),
            "reason": result.get("reason", "approval required"),
        }, ensure_ascii=False, indent=2))
        return False
    else:
        print(json.dumps({"error": True, "message": result.get("error", "unknown")}))
        return False


def run_shell() -> None:
    """REPL 메인 루프."""
    # 데몬 확인/자동 시작
    if not daemon.is_running():
        sys.stderr.write("데몬 시작 중...")
        sys.stderr.flush()
        if daemon.start_daemon():
            sys.stderr.write(" OK\n")
        else:
            sys.stderr.write(" 실패\n")
            print(json.dumps({"error": True, "message": "데몬 시작 실패"}))
            sys.exit(1)

    interactive = sys.stdin.isatty()

    if interactive:
        sys.stderr.write("SAP CLI Shell (help로 명령 확인, exit로 종료)\n")

    while True:
        try:
            if interactive:
                line = input("sap> ")
            else:
                line = input()
        except (EOFError, KeyboardInterrupt):
            if interactive:
                sys.stderr.write("\n")
            break

        parsed = _parse_line(line)
        if parsed is None:
            continue

        cmd, args = parsed
        if cmd == "__exit__":
            break
        if cmd == "__help__":
            sys.stderr.write(HELP_TEXT)
            continue

        ok = _execute(cmd, args)
        if not ok and not interactive:
            sys.exit(1)
