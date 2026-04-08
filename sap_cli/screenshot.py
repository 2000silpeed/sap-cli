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


def _find_sap_window() -> int | None:
    """SAP GUI 메인 윈도우 핸들(HWND) 탐색.

    SAP GUI 윈도우 클래스명 패턴:
    - SAP_FRONTEND_SESSION (메인 세션 윈도우)
    - Afx:400000:... (일부 SAP GUI 버전)
    타이틀에 "SAP" 또는 트랜잭션 코드가 포함됨.
    """
    if not _WIN32:
        return None

    import win32gui  # type: ignore[import-untyped]

    # 1순위: 클래스명으로 정확 매칭
    exact: list[int] = []
    # 2순위: 타이틀에 SAP 포함
    fallback: list[int] = []

    def _enum_cb(hwnd: int, _: Any) -> bool:
        if not win32gui.IsWindowVisible(hwnd) or not win32gui.IsWindowEnabled(hwnd):
            return True
        cls = win32gui.GetClassName(hwnd)
        title = win32gui.GetWindowText(hwnd)

        # SAP GUI 세션 윈도우 클래스 (가장 정확)
        if cls.startswith("SAP_FRONTEND"):
            exact.append(hwnd)
        # 타이틀 기반 fallback (대소문자 무시)
        elif "SAP" in title.upper() and cls not in ("CabinetWClass", "Progman"):
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


def capture_sap_window(output_path: str | Path | None = None) -> Path | None:
    """SAP GUI 윈도우를 캡처하여 PNG로 저장.

    1순위: SAP GUI 윈도우 핸들을 찾아서 해당 영역만 캡처
    2순위: 윈도우를 못 찾으면 (Citrix/RDP 등) 전체 화면 캡처로 fallback

    Args:
        output_path: 저장 경로. None이면 자동 생성.

    Returns:
        저장된 파일 경로. 실패 시 None.
    """
    if not _WIN32:
        logger.warning("스크린샷은 Windows에서만 지원됩니다.")
        return None

    bbox = None  # (left, top, right, bottom) or None for full screen

    hwnd = _find_sap_window()
    if hwnd is not None:
        rect = _get_window_rect(hwnd)
        if rect is not None:
            # SAP 윈도우를 전경으로 가져오기
            try:
                import win32gui  # type: ignore[import-untyped]
                win32gui.SetForegroundWindow(hwnd)
                time.sleep(0.3)
            except Exception:
                pass
            bbox = rect
    else:
        logger.info("SAP GUI 윈도우를 직접 찾을 수 없어 전체 화면을 캡처합니다 (Citrix/RDP 환경).")

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

    return capture_sap_window(output_path)


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
