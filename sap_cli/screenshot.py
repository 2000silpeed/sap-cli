"""SAP GUI 스크린샷 캡처 모듈.

SAP GUI 윈도우만 정확히 캡처하여 PNG로 저장.
매크로/프로세스 단계별 자동 캡처 및 사용자 설명서 소재 생성 용도.
"""

from __future__ import annotations

import logging
import platform
import time
from datetime import datetime
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_WIN32 = platform.system() == "Windows"

if _WIN32:
    import ctypes
    import ctypes.wintypes
    from PIL import ImageGrab  # type: ignore[import-untyped]


def _find_sap_window_from_session(session: Any = None) -> int | None:
    """COM 세션 객체에서 SAP GUI 윈도우 핸들을 직접 가져온다.

    session.ActiveWindow.Handle 또는 session.findById("wnd[0]").Handle로
    정확한 HWND를 얻는다. 추측 없이 현재 연결된 세션의 윈도우를 잡는다.
    """
    if session is None:
        return None
    for attr_path in ("ActiveWindow", ):
        try:
            window = getattr(session, attr_path, None)
            if window:
                hwnd = int(window.Handle)
                if hwnd:
                    return hwnd
        except Exception:
            pass
    # fallback: FindById
    try:
        wnd = session.FindById("wnd[0]")
        return int(wnd.Handle)
    except Exception:
        return None


def _find_sap_window_by_scan() -> int | None:
    """윈도우 스캔으로 SAP GUI 윈도우를 찾는다 (COM 세션 없을 때 fallback)."""
    if not _WIN32:
        return None

    import win32gui  # type: ignore[import-untyped]

    exact: list[int] = []
    fallback: list[int] = []

    def _enum_cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.IsWindowEnabled(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)
        if cls == "SAP_FRONTEND_SESSION":
            exact.append(hwnd)
        elif "SAP" in title.upper() and cls not in ("CabinetWClass", "Progman", "#32770"):
            fallback.append(hwnd)
        return True

    win32gui.EnumWindows(_enum_cb, None)
    return (exact or fallback or [None])[0]


def _get_window_rect(hwnd: int) -> tuple[int, int, int, int] | None:
    """윈도우의 실제 좌표(left, top, right, bottom) 반환. DPI 보정 포함."""
    if not _WIN32:
        return None

    try:
        # DPI awareness 설정 (고해상도 디스플레이 대응)
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass

    import win32gui  # type: ignore[import-untyped]

    try:
        rect = win32gui.GetWindowRect(hwnd)
        return rect  # (left, top, right, bottom)
    except Exception:
        return None


def capture_sap_window(
    output_path: str | Path | None = None,
    session: Any = None,
) -> Path | None:
    """SAP GUI 윈도우를 캡처하여 PNG로 저장.

    1순위: COM 세션에서 정확한 윈도우 핸들을 가져와서 캡처
    2순위: 윈도우 스캔으로 SAP GUI 찾기
    3순위: 전체 화면 캡처 (fallback)

    Args:
        output_path: 저장 경로. None이면 자동 생성.
        session: SapGuiWrapper의 COM 세션 객체. 있으면 정확한 윈도우를 잡음.

    Returns:
        저장된 파일 경로. 실패 시 None.
    """
    if not _WIN32:
        logger.warning("스크린샷은 Windows에서만 지원됩니다.")
        return None

    bbox = None

    # 1순위: COM 세션에서 직접 핸들 획득
    hwnd = _find_sap_window_from_session(session)
    if hwnd is None:
        # 2순위: 윈도우 스캔
        hwnd = _find_sap_window_by_scan()

    if hwnd is not None:
        rect = _get_window_rect(hwnd)
        if rect is not None:
            try:
                import win32gui  # type: ignore[import-untyped]
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception:
                pass
            bbox = rect
    else:
        logger.info("SAP GUI 윈도우를 찾을 수 없어 전체 화면을 캡처합니다.")

    try:
        img = ImageGrab.grab(bbox=bbox)

        if output_path is None:
            output_path = _default_path()
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        img.save(str(output_path), "PNG")
        logger.info("스크린샷 저장: %s", output_path)
        return output_path
    except Exception as e:
        logger.error("스크린샷 캡처 실패: %s", e)
        return None


def capture_for_step(
    tcode: str,
    step_number: int,
    screen_number: int,
    label: str = "",
    base_dir: str | Path | None = None,
    session: Any = None,
) -> Path | None:
    """매크로/프로세스 단계별 스크린샷 캡처.

    파일명 규칙: {tcode}_{step:03d}_screen{screen}_{label}.png
    예: VL03N_001_screen1000_initial.png
    """
    if base_dir is None:
        base_dir = Path.home() / ".sap_cli" / "screenshots"
    base_dir = Path(base_dir)

    safe_label = label.replace(" ", "_").replace("/", "_")[:30] if label else "step"
    filename = f"{tcode}_{step_number:03d}_screen{screen_number}_{safe_label}.png"
    output_path = base_dir / tcode / filename

    return capture_sap_window(output_path, session=session)


def _default_path() -> Path:
    """기본 스크린샷 저장 경로 생성."""
    base = Path.home() / ".sap_cli" / "screenshots"
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    return base / f"capture_{ts}.png"


def list_screenshots(tcode: str | None = None) -> list[dict[str, Any]]:
    """저장된 스크린샷 목록 조회."""
    base = Path.home() / ".sap_cli" / "screenshots"
    if not base.exists():
        return []

    if tcode:
        search_dir = base / tcode
        if not search_dir.exists():
            return []
        files = sorted(search_dir.glob("*.png"))
    else:
        files = sorted(base.rglob("*.png"))

    return [
        {
            "path": str(f),
            "name": f.name,
            "tcode": f.parent.name if f.parent != base else None,
            "size_kb": round(f.stat().st_size / 1024, 1),
        }
        for f in files
    ]
