"""Private per-user service discovery, separate from user-selected project directories."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path


def discovery_path(workspace: Path) -> Path:
    normalized = workspace.resolve().as_posix()
    if os.name == "nt":
        normalized = normalized.lower()
        base = Path(os.environ["LOCALAPPDATA"])
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    key = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    return base / "AgenEditor" / "services" / key / "service.json"


def make_private(path: Path) -> None:
    """Remove inherited access, allowing only the current user (and Windows SYSTEM)."""
    if path.is_symlink() or path.is_junction():
        raise PermissionError("service credentials must not use a linked path")
    if os.name == "nt":
        import ntsecuritycon
        import win32api
        import win32con
        import win32security

        token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
        try:
            user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
        finally:
            token.Close()
        acl = win32security.ACL()
        inheritance = 3 if path.is_dir() else 0  # OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
        for sid in (user, win32security.CreateWellKnownSid(win32security.WinLocalSystemSid)):
            acl.AddAccessAllowedAceEx(
                win32security.ACL_REVISION, inheritance, ntsecuritycon.FILE_ALL_ACCESS, sid
            )
        win32security.SetNamedSecurityInfo(
            str(path),
            win32security.SE_FILE_OBJECT,
            win32security.DACL_SECURITY_INFORMATION | win32security.PROTECTED_DACL_SECURITY_INFORMATION,
            None,
            None,
            acl,
            None,
        )
    else:
        path.chmod(0o700 if path.is_dir() else 0o600)


def prepare_discovery(workspace: Path) -> Path:
    path = discovery_path(workspace)
    # Secure each app-owned directory before creating children or writing credentials.
    for directory in (path.parents[2], path.parents[1], path.parent):
        directory.mkdir(exist_ok=True)
        make_private(directory)
    return path
