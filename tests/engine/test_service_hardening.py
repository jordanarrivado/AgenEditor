"""Credential isolation and responsiveness at the actual loopback boundary."""

from __future__ import annotations

import os
import socket
import threading
import time
from pathlib import Path

import pytest

from ageneditor.contracts import EngineException
from ageneditor.desktop_service import DesktopService, _single_service_lock, service_address
from ageneditor.service_credentials import discovery_path, prepare_discovery
from ageneditor.storage import ProjectStore


def test_credentials_private_and_slow_client_does_not_block(
    tmp_path: Path, private_service_home: Path
) -> None:
    workspace = tmp_path / "shared-workspace"
    service = DesktopService(workspace)
    thread = threading.Thread(target=service.serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while service_address(workspace) is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert service_address(workspace)
        path = discovery_path(workspace)
        assert path.is_relative_to(private_service_home)
        assert not (workspace / ".ageneditor-service.json").exists()
        if os.name == "nt":
            import win32api
            import win32con
            import win32security

            token = win32security.OpenProcessToken(win32api.GetCurrentProcess(), win32con.TOKEN_QUERY)
            try:
                user = win32security.GetTokenInformation(token, win32security.TokenUser)[0]
            finally:
                token.Close()
            permitted = {str(user), str(win32security.CreateWellKnownSid(win32security.WinLocalSystemSid))}
            for secured in (path, path.parent, path.parent.parent):
                descriptor = win32security.GetNamedSecurityInfo(
                    str(secured), win32security.SE_FILE_OBJECT, win32security.DACL_SECURITY_INFORMATION
                )
                acl = descriptor.GetSecurityDescriptorDacl()
                assert acl is not None and acl.GetAceCount() == 2
                assert {str(acl.GetAce(i)[2]) for i in range(acl.GetAceCount())} == permitted
        else:
            assert path.stat().st_mode & 0o077 == 0
        with socket.create_connection(("127.0.0.1", service.port), timeout=2) as stalled:
            stalled.sendall(b"POST /desktop HTTP/1.1\r\n")
            assert service_address(workspace)  # completes while first client withholds headers
        with pytest.raises(EngineException), _single_service_lock(workspace):
            pytest.fail("a second service acquired the same workspace")
    finally:
        if thread.is_alive():
            service.http.shutdown()
        thread.join(timeout=5)
    assert not discovery_path(workspace).exists()


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction check")
def test_service_discovery_refuses_junction(tmp_path: Path, private_service_home: Path) -> None:
    import _winapi

    outside = tmp_path / "outside"
    outside.mkdir()
    _winapi.CreateJunction(str(outside), str(private_service_home / "AgenEditor"))
    with pytest.raises(PermissionError):
        prepare_discovery(tmp_path)
    assert list(outside.iterdir()) == []


@pytest.mark.skipif(os.name != "nt", reason="NTFS junction check")
def test_workspace_does_not_open_projects_through_junctions(tmp_path: Path) -> None:
    import _winapi

    outside = ProjectStore.create(tmp_path, "outside")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "redirected"
    _winapi.CreateJunction(str(outside.root), str(link))
    with pytest.raises(EngineException):
        ProjectStore(link)
    service = DesktopService(workspace)
    try:
        assert service._desktop_call("list_projects", {}) == []
    finally:
        service.http.server_close()
