"""Provider API keys in the Windows Credential Manager (Phase 6 built-in chat).

Keys are written and read only by the engine; the desktop page can set, remove or ask whether a key
exists, never read one back. Nothing is written to project files, logs or the workspace. The backend is
swappable so tests never touch the real credential store.
"""

from __future__ import annotations

import ctypes
import sys
from typing import Protocol

from ageneditor.contracts import EngineException, ErrorCode

TARGET_PREFIX = "AgenEditor/chat/"
MAX_KEY_LENGTH = 512


class CredentialBackend(Protocol):
    def write(self, target: str, secret: str) -> None: ...
    def read(self, target: str) -> str | None: ...
    def delete(self, target: str) -> None: ...


class WindowsCredentialBackend:
    """Generic credentials via advapi32 (CredWriteW / CredReadW / CredDeleteW)."""

    CRED_TYPE_GENERIC = 1
    CRED_PERSIST_LOCAL_MACHINE = 2
    ERROR_NOT_FOUND = 1168

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise EngineException(ErrorCode.INVALID_ARGUMENT, "saved API keys need Windows")
        from ctypes import wintypes

        class FILETIME(ctypes.Structure):
            _fields_ = [("dwLowDateTime", wintypes.DWORD), ("dwHighDateTime", wintypes.DWORD)]

        class CREDENTIAL(ctypes.Structure):
            _fields_ = [
                ("Flags", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("TargetName", wintypes.LPWSTR),
                ("Comment", wintypes.LPWSTR),
                ("LastWritten", FILETIME),
                ("CredentialBlobSize", wintypes.DWORD),
                ("CredentialBlob", ctypes.POINTER(ctypes.c_ubyte)),
                ("Persist", wintypes.DWORD),
                ("AttributeCount", wintypes.DWORD),
                ("Attributes", ctypes.c_void_p),
                ("TargetAlias", wintypes.LPWSTR),
                ("UserName", wintypes.LPWSTR),
            ]

        self._credential = CREDENTIAL
        advapi = ctypes.WinDLL("advapi32", use_last_error=True)
        self._write = advapi.CredWriteW
        self._write.argtypes = [ctypes.POINTER(CREDENTIAL), wintypes.DWORD]
        self._write.restype = wintypes.BOOL
        self._read = advapi.CredReadW
        self._read.argtypes = [
            wintypes.LPCWSTR,
            wintypes.DWORD,
            wintypes.DWORD,
            ctypes.POINTER(ctypes.POINTER(CREDENTIAL)),
        ]
        self._read.restype = wintypes.BOOL
        self._delete = advapi.CredDeleteW
        self._delete.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD]
        self._delete.restype = wintypes.BOOL
        self._free = advapi.CredFree
        self._free.argtypes = [ctypes.c_void_p]

    def write(self, target: str, secret: str) -> None:
        blob = secret.encode("utf-16-le")
        buffer = (ctypes.c_ubyte * len(blob)).from_buffer_copy(blob)
        credential = self._credential()
        credential.Type = self.CRED_TYPE_GENERIC
        credential.TargetName = target
        credential.CredentialBlobSize = len(blob)
        credential.CredentialBlob = ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte))
        credential.Persist = self.CRED_PERSIST_LOCAL_MACHINE
        credential.UserName = "AgenEditor"
        if not self._write(ctypes.byref(credential), 0):
            raise EngineException(
                ErrorCode.PERMISSION_DENIED, "could not save the key in Windows Credential Manager"
            )

    def read(self, target: str) -> str | None:
        pointer = ctypes.POINTER(self._credential)()
        if not self._read(target, self.CRED_TYPE_GENERIC, 0, ctypes.byref(pointer)):
            if ctypes.get_last_error() == self.ERROR_NOT_FOUND:
                return None
            raise EngineException(ErrorCode.PERMISSION_DENIED, "could not read Windows Credential Manager")
        try:
            size = pointer.contents.CredentialBlobSize
            data = ctypes.string_at(pointer.contents.CredentialBlob, size)
            return data.decode("utf-16-le")
        finally:
            self._free(pointer)

    def delete(self, target: str) -> None:
        if (
            not self._delete(target, self.CRED_TYPE_GENERIC, 0)
            and ctypes.get_last_error() != self.ERROR_NOT_FOUND
        ):
            raise EngineException(ErrorCode.PERMISSION_DENIED, "could not remove the key")


_backend: CredentialBackend | None = None


def backend() -> CredentialBackend:
    global _backend
    if _backend is None:
        _backend = WindowsCredentialBackend()
    return _backend


def use_backend(replacement: CredentialBackend | None) -> None:
    """Tests swap in an in-memory backend; ``None`` restores the Windows one on next use."""
    global _backend
    _backend = replacement


def set_key(provider: str, key: str) -> None:
    key = key.strip()
    if not key or len(key) > MAX_KEY_LENGTH or any(c.isspace() for c in key):
        raise EngineException(ErrorCode.INVALID_ARGUMENT, "that does not look like an API key")
    backend().write(TARGET_PREFIX + provider, key)


def get_key(provider: str) -> str | None:
    return backend().read(TARGET_PREFIX + provider)


def has_key(provider: str) -> bool:
    return get_key(provider) is not None


def remove_key(provider: str) -> None:
    backend().delete(TARGET_PREFIX + provider)
