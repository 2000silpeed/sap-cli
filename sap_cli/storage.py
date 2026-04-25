"""공통 저장 유틸리티.

작은 JSON 파일 저장소들이 같은 atomic write 패턴을 공유하도록 한다.
POSIX에서는 temp 파일을 쓰고 fsync한 뒤 os.replace로 교체하고, 부모
디렉터리를 best-effort fsync해 rename 내구성을 높인다. Windows에서는
디렉터리 fd open/fsync가 실패할 수 있으므로 해당 단계만 건너뛴다.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


def fsync_dir_best_effort(directory: str | Path) -> None:
    """디렉터리 fsync.

    POSIX에서는 rename 내구성 보강에 도움이 된다. Windows 등 디렉터리
    open이 불가능한 환경에서는 조용히 no-op 처리한다.
    """
    try:
        fd = os.open(str(directory), os.O_RDONLY)
    except OSError:
        return
    try:
        try:
            os.fsync(fd)
        except OSError:
            pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def atomic_write_text(path: str | Path, text: str, mode: int = 0o644) -> None:
    """UTF-8 텍스트를 atomic하게 저장한다.

    short-write를 허용하지 않도록 os.write 루프를 사용하고, 실패 시 temp
    파일을 cleanup한다. 파일 fsync 오류(EIO/ENOSPC 등)는 호출자에게
    전파되어야 하므로 삼키지 않는다.
    """
    final_path = Path(path)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = final_path.with_suffix(
        final_path.suffix + f".tmp-{os.getpid()}-{time.time_ns()}"
    )
    try:
        data = text.encode("utf-8")
        fd = os.open(str(tmp_path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
        try:
            view = memoryview(data)
            written = 0
            while written < len(view):
                n = os.write(fd, view[written:])
                if n <= 0:
                    raise OSError(f"short write: {written}/{len(view)} bytes")
                written += n
            if hasattr(os, "fsync"):
                os.fsync(fd)
        finally:
            os.close(fd)
        os.replace(tmp_path, final_path)
        fsync_dir_best_effort(final_path.parent)
    except Exception:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass
        raise


def atomic_write_json(path: str | Path, data: Any, mode: int = 0o644) -> None:
    """JSON 직렬화 후 atomic하게 저장한다."""
    atomic_write_text(
        path,
        json.dumps(data, ensure_ascii=False, indent=2),
        mode=mode,
    )


def safe_read_json(path: str | Path) -> Any | None:
    """JSON 파일을 안전하게 읽는다.

    I/O 오류, JSON 파싱 오류는 None으로 반환한다. 스키마 검증/로깅 정책은
    각 Store가 담당한다.
    """
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return None
    try:
        return json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return None
