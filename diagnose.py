"""SAP GUI 화면 스캔 진단 스크립트.

SAP에 로그인 + 트랜잭션 진입 후 실행:
    python diagnose.py
"""

import json
import platform
import sys

if platform.system() != "Windows":
    print("이 스크립트는 Windows에서만 실행 가능합니다.")
    sys.exit(1)

import win32com.client


def main():
    print("=" * 60)
    print("SAP GUI 화면 스캔 진단")
    print("=" * 60)

    # 1. SAP 연결
    try:
        rot = win32com.client.GetObject("SAPGUI")
        app = rot.GetScriptingEngine
        conn = app.Connections(0)
        session = conn.Sessions(0)
        print(f"\n[1] 연결 성공")
    except Exception as e:
        print(f"\n[1] 연결 실패: {e}")
        return

    # 2. 세션 정보
    info = session.Info
    print(f"    User: {info.User}")
    print(f"    Transaction: {info.Transaction}")
    print(f"    ScreenNumber: {info.ScreenNumber}")
    print(f"    Program: {info.Program}")
    print(f"    SystemName: {info.SystemName}")

    # 3. GetObjectTree 테스트
    print(f"\n[2] GetObjectTree 테스트")
    try:
        tree_json = session.GetObjectTree("wnd[0]", None)
        tree = json.loads(tree_json)

        # --- 핵심: JSON 구조 샘플 (첫 500자) ---
        print(f"\n    === JSON 샘플 (첫 500자) ===")
        print(f"    {tree_json[:500]}")
        print(f"    === 끝 ===\n")

        print(f"    최상위 키: {list(tree.keys())}")
        print(f"    JSON 총 길이: {len(tree_json)} bytes")

        # 첫 번째 자식의 전체 키 구조
        children = tree.get("children", [])
        print(f"    자식 수: {len(children)}")

        if children:
            first = children[0]
            print(f"\n    --- 첫 번째 자식 ---")
            # 자식 노드의 모든 키 (children 제외)
            keys_without_children = {k: type(v).__name__ for k, v in first.items() if k != "children"}
            print(f"    키(children 제외): {keys_without_children}")

            gc = first.get("children", [])
            print(f"    손자 수: {len(gc)}")
            if gc:
                g = gc[0]
                gkeys = {k: type(v).__name__ for k, v in g.items() if k != "children"}
                print(f"    첫 손자 키: {gkeys}")

                # 손자의 자식 (실제 필드가 여기 있을 수 있음)
                ggc = g.get("children", [])
                print(f"    첫 손자의 자식 수: {len(ggc)}")
                if ggc:
                    for i, gg in enumerate(ggc[:5]):
                        ggkeys = {k: v for k, v in gg.items() if k != "children"}
                        print(f"      [{i}] {ggkeys}")

        # 전체 JSON에서 키 패턴 검색
        raw = tree_json
        for keyword in ["GuiCTextField", "GuiButton", "GuiTextField", "MATNR", "Type", "type"]:
            count = raw.count(keyword)
            if count > 0:
                # 첫 출현 위치 주변 50자
                idx = raw.find(keyword)
                snippet = raw[max(0, idx-30):idx+50]
                print(f"    '{keyword}' 출현 {count}회, 첫 위치 주변: ...{snippet}...")

    except AttributeError:
        print("    GetObjectTree 메서드 없음 (SAP GUI 버전이 낮음)")
    except Exception as e:
        print(f"    실패: {type(e).__name__}: {e}")

    # 4. 윈도우 직접 탐색
    print(f"\n[3] wnd[0] Children 직접 탐색")
    try:
        wnd = session.FindById("wnd[0]")
        children = wnd.Children
        print(f"    Children 수: {children.Count}")
        for i in range(children.Count):
            child = children(i)
            print(f"      [{i}] Type={child.Type}, Id={child.Id}")
    except Exception as e:
        print(f"    실패: {type(e).__name__}: {e}")

    # 5. usr 영역 탐색
    print(f"\n[4] wnd[0]/usr Children 탐색")
    try:
        usr = session.FindById("wnd[0]/usr")
        print(f"    usr Type: {usr.Type}")
        children = usr.Children
        print(f"    Children 수: {children.Count}")
        for i in range(min(children.Count, 20)):
            child = children(i)
            child_type = str(child.Type)
            child_id = str(child.Id)
            text = ""
            try:
                text = str(child.Text)[:40]
            except:
                pass
            changeable = ""
            try:
                changeable = f", Changeable={child.Changeable}"
            except:
                pass
            print(f"      [{i}] Type={child_type}, Id={child_id}, Text='{text}'{changeable}")

            # 한 단계 더 깊이
            try:
                if hasattr(child, "Children") and child.Children.Count > 0:
                    for j in range(min(child.Children.Count, 5)):
                        gc = child.Children(j)
                        gc_text = ""
                        try:
                            gc_text = str(gc.Text)[:30]
                        except:
                            pass
                        print(f"        [{i}.{j}] Type={gc.Type}, Id={gc.Id}, Text='{gc_text}'")
            except:
                pass
    except Exception as e:
        print(f"    실패: {type(e).__name__}: {e}")

    # 6. tbar 탐색
    for t in range(3):
        print(f"\n[5.{t}] wnd[0]/tbar[{t}] 탐색")
        try:
            tbar = session.FindById(f"wnd[0]/tbar[{t}]")
            children = tbar.Children
            print(f"    Children 수: {children.Count}")
            for i in range(min(children.Count, 10)):
                child = children(i)
                tooltip = ""
                try:
                    tooltip = str(child.Tooltip)[:30]
                except:
                    pass
                print(f"      [{i}] Type={child.Type}, Id={child.Id}, Tooltip='{tooltip}'")
        except Exception as e:
            print(f"    없음 또는 실패: {e}")
            break

    print(f"\n{'=' * 60}")
    print("진단 완료. 위 출력을 그대로 보내주세요.")


if __name__ == "__main__":
    main()
