"""Authenticated request envelopes for the loopback daemon transport."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import stat
import threading
import time
from pathlib import Path
from typing import Any, Callable, Mapping


AUTH_VERSION = "sap-cli-ipc-v1"
_NONCE = re.compile(r"^[A-Za-z0-9_-]{16,128}$")
_SIGNATURE = re.compile(r"^[0-9a-f]{64}$")


class IpcAuthError(RuntimeError):
    """Value-safe authentication failure; never contains key material."""


def default_secret_path() -> Path:
    configured = os.getenv("SAP_CLI_DAEMON_SECRET_FILE", "").strip()
    return Path(configured or "~/.sap_cli/daemon.secret").expanduser().resolve()


def load_or_create_secret(path: str | Path | None = None) -> bytes:
    """Load a 256-bit daemon secret from an owner-only file, creating it once."""

    target = Path(path).expanduser().resolve() if path is not None else default_secret_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    _secure_directory(target.parent)
    try:
        descriptor = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        descriptor = None
    if descriptor is not None:
        secret = secrets.token_bytes(32)
        try:
            with os.fdopen(descriptor, "wb", closefd=True) as stream:
                stream.write(secret)
                stream.flush()
                os.fsync(stream.fileno())
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError:
                pass
            raise
    _secure_secret_file(target)
    try:
        secret = target.read_bytes()
    except OSError as exc:
        raise IpcAuthError("daemon IPC secret could not be read") from exc
    if len(secret) != 32:
        raise IpcAuthError("daemon IPC secret has an invalid length")
    return secret


class IpcAuthenticator:
    """Create and verify HMAC-authenticated, replay-resistant request envelopes."""

    def __init__(
        self,
        secret: bytes,
        *,
        clock: Callable[[], float] = time.time,
        nonce_factory: Callable[[], str] = lambda: secrets.token_urlsafe(24),
        max_clock_skew_seconds: float = 30.0,
        max_nonces: int = 10_000,
    ) -> None:
        if not isinstance(secret, bytes) or len(secret) < 32:
            raise ValueError("IPC authentication secret must be at least 256 bits")
        if max_clock_skew_seconds <= 0 or max_nonces < 1:
            raise ValueError("invalid IPC authentication limits")
        self._secret = secret
        self._clock = clock
        self._nonce_factory = nonce_factory
        self._max_skew = float(max_clock_skew_seconds)
        self._max_nonces = int(max_nonces)
        self._seen_nonces: dict[str, int] = {}
        self._owner_thread_id = threading.get_ident()

    def sign_request(
        self,
        cmd: str,
        args: Mapping[str, Any] | None = None,
        context: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        timestamp = int(self._clock())
        nonce = str(self._nonce_factory())
        if not _NONCE.fullmatch(nonce):
            raise IpcAuthError("IPC nonce factory returned an invalid nonce")
        payload: dict[str, Any] = {
            "cmd": str(cmd),
            "args": dict(args or {}),
        }
        if context:
            payload["context"] = dict(context)
        signature = self._signature(payload, timestamp, nonce)
        payload["auth"] = {
            "version": AUTH_VERSION,
            "timestamp": timestamp,
            "nonce": nonce,
            "signature": signature,
        }
        return payload

    def verify_request(self, request: Mapping[str, Any]) -> None:
        self._assert_owner_thread()
        auth = request.get("auth")
        if not isinstance(auth, Mapping):
            raise IpcAuthError("unsigned daemon IPC request")
        if auth.get("version") != AUTH_VERSION:
            raise IpcAuthError("unsupported daemon IPC authentication version")
        timestamp = auth.get("timestamp")
        if isinstance(timestamp, bool) or not isinstance(timestamp, int):
            raise IpcAuthError("invalid daemon IPC timestamp")
        nonce = str(auth.get("nonce", ""))
        signature = str(auth.get("signature", ""))
        if not _NONCE.fullmatch(nonce) or not _SIGNATURE.fullmatch(signature):
            raise IpcAuthError("invalid daemon IPC authentication envelope")
        now = self._clock()
        if abs(now - timestamp) > self._max_skew:
            raise IpcAuthError("expired daemon IPC request")
        payload = {
            "cmd": request.get("cmd", ""),
            "args": request.get("args", {}),
        }
        if "context" in request:
            payload["context"] = request.get("context")
        expected = self._signature(payload, timestamp, nonce)
        if not hmac.compare_digest(signature, expected):
            raise IpcAuthError("daemon IPC signature mismatch")
        self._purge_nonces(now)
        if nonce in self._seen_nonces:
            raise IpcAuthError("replayed daemon IPC request")
        if len(self._seen_nonces) >= self._max_nonces:
            raise IpcAuthError("daemon IPC nonce capacity exhausted")
        self._seen_nonces[nonce] = timestamp

    def _signature(self, payload: Mapping[str, Any], timestamp: int, nonce: str) -> str:
        signed = {
            "version": AUTH_VERSION,
            "timestamp": timestamp,
            "nonce": nonce,
            "request": dict(payload),
        }
        try:
            canonical = json.dumps(
                signed,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise IpcAuthError("daemon IPC request is not canonical JSON") from exc
        return hmac.new(self._secret, canonical, hashlib.sha256).hexdigest()

    def _purge_nonces(self, now: float) -> None:
        cutoff = now - self._max_skew
        for nonce, timestamp in list(self._seen_nonces.items()):
            if timestamp < cutoff:
                self._seen_nonces.pop(nonce, None)

    def _assert_owner_thread(self) -> None:
        if threading.get_ident() != self._owner_thread_id:
            raise IpcAuthError("IPC verifier must run on its daemon owner thread")


def _secure_directory(path: Path) -> None:
    if os.name == "nt":
        _secure_windows_acl(path)
        return
    try:
        os.chmod(path, 0o700)
    except OSError as exc:
        raise IpcAuthError("daemon IPC secret directory permissions failed") from exc
    info = path.stat()
    if hasattr(os, "getuid") and info.st_uid != os.getuid():
        raise IpcAuthError("daemon IPC secret directory is owned by another user")
    if stat.S_IMODE(info.st_mode) & 0o077:
        raise IpcAuthError("daemon IPC secret directory permissions are too broad")


def _secure_secret_file(path: Path) -> None:
    try:
        info = path.stat()
    except OSError as exc:
        raise IpcAuthError("daemon IPC secret metadata could not be read") from exc
    if not stat.S_ISREG(info.st_mode):
        raise IpcAuthError("daemon IPC secret is not a regular file")
    if os.name != "nt":
        if hasattr(os, "getuid") and info.st_uid != os.getuid():
            raise IpcAuthError("daemon IPC secret is owned by another user")
        if stat.S_IMODE(info.st_mode) & 0o077:
            raise IpcAuthError("daemon IPC secret permissions are too broad")
        try:
            os.chmod(path, 0o600)
        except OSError as exc:
            raise IpcAuthError("daemon IPC secret permissions failed") from exc
        return
    _secure_windows_acl(path)


def _secure_windows_acl(path: Path) -> None:
    """Restrict the secret DACL to the current Windows user (pywin32)."""

    try:
        import ntsecuritycon  # type: ignore[import-not-found]
        import win32api  # type: ignore[import-not-found]
        import win32security  # type: ignore[import-not-found]

        token = win32security.OpenProcessToken(
            win32api.GetCurrentProcess(), win32security.TOKEN_QUERY
        )
        user_sid = win32security.GetTokenInformation(
            token, win32security.TokenUser
        )[0]
        dacl = win32security.ACL()
        access = ntsecuritycon.FILE_ALL_ACCESS
        dacl.AddAccessAllowedAce(win32security.ACL_REVISION, access, user_sid)
        descriptor = win32security.SECURITY_DESCRIPTOR()
        descriptor.SetSecurityDescriptorOwner(user_sid, False)
        descriptor.SetSecurityDescriptorDacl(True, dacl, False)
        win32security.SetFileSecurity(
            str(path),
            win32security.OWNER_SECURITY_INFORMATION
            | win32security.DACL_SECURITY_INFORMATION,
            descriptor,
        )
    except Exception as exc:
        raise IpcAuthError(
            "current-user Windows ACL could not be applied to daemon IPC secret"
        ) from exc


__all__ = [
    "AUTH_VERSION",
    "IpcAuthError",
    "IpcAuthenticator",
    "default_secret_path",
    "load_or_create_secret",
]
