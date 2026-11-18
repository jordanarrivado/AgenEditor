"""AgenEditor 2.0 through the shared engine service: desktop IPC operations, the `/scoped` agent
endpoint and the Claude Code host adapter (driven by a stand-in host executable, never the real CLI)."""

from __future__ import annotations

import sys
import textwrap
import threading
import time
from pathlib import Path

from production_helpers import PROFILE, make_project

from ageneditor.desktop_service import DesktopService, service_address, service_request
from ageneditor.orchestration.host_adapters import ClaudeCodeHostAdapter

FAKE_HOST = textwrap.dedent(
    """
    import json, sys
    from pathlib import Path
    from ageneditor.desktop_service import service_request

    args = sys.argv[1:]
    config = json.loads(Path(args[args.index("--mcp-config") + 1]).read_text(encoding="utf-8"))
    server = config["mcpServers"]["ageneditor"]
    assert "--scoped" in server["args"] and args[args.index("--tools") + 1] == ""
    workspace = Path(server["args"][server["args"].index("--workspace") + 1])
    token = server["env"]["AGENEDITOR_TASK_TOKEN"]
    sys.stdin.read()

    def call(tool, payload):
        body = {"tool": tool, "args": payload, "task_token": token}
        return service_request(workspace, "/scoped", body)["result"]

    packet = call("qa_get_review_packet", {})
    call("qa_submit_findings", {"submission": {
        "verdict": "pass", "summary": "Reviewed revision %d" % packet["reviewed_revision"],
        "evidence_basis": ["timeline", "transcript"], "findings": []}})
    call("production_task_complete", {"summary": "done"})
    print(json.dumps({"type": "result", "subtype": "success", "is_error": False, "total_cost_usd": 0.0123,
                      "usage": {"input_tokens": 900, "output_tokens": 120}, "result": "ok"}))
    """
)


def _desktop(workspace: Path, operation: str, **args: object) -> object:
    return service_request(workspace, "/desktop", {"operation": operation, "args": args})["result"]


def test_production_through_the_shared_service(
    tmp_path: Path, media_dir: Path, private_service_home: Path
) -> None:
    store, _ = make_project(tmp_path, media_dir)
    script = tmp_path / "fake_claude.py"
    script.write_text(FAKE_HOST, encoding="utf-8")
    service = DesktopService(tmp_path)
    service.orchestrator.adapters["claude_code"] = ClaudeCodeHostAdapter(
        tmp_path, executable=[sys.executable, str(script)]
    )
    thread = threading.Thread(target=service.serve, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 5
        while service_address(tmp_path) is None and time.monotonic() < deadline:
            time.sleep(0.05)
        client = _desktop(tmp_path, "production_create_client", name="Acme")
        _desktop(tmp_path, "production_bind_project", project_id=store.project_id, client_id=client["id"])
        providers = _desktop(
            tmp_path, "production_set_provider_enabled", provider_id="claude_code", enabled=True
        )
        assert providers["enabled"] is True
        created = _desktop(
            tmp_path,
            "production_create_batch",
            request={
                "project_id": store.project_id,
                "name": "Service batch",
                "policy": {
                    "target_mode": "count",
                    "target_count": 1,
                    "min_duration_us": 8_000_000,
                    "max_duration_us": 12_000_000,
                    "output_profile": PROFILE.model_dump(mode="json"),
                    "selection": "auto_top",
                    "allow_data_egress": True,
                },
            },
        )
        batch_id = created["batch"]["id"]
        assert created["batch"]["client_id"] == client["id"]
        _desktop(tmp_path, "production_start_discovery", batch_id=batch_id)
        deadline = time.monotonic() + 240
        status: dict = {}
        while time.monotonic() < deadline:
            status = _desktop(tmp_path, "production_status", batch_id=batch_id)  # type: ignore[assignment]
            if status["clips"] and status["clips"][0]["status"] in {
                "review_ready",
                "failed",
                "needs_human",
                "blocked",
            }:
                break
            time.sleep(0.3)
        clip = status["clips"][0]
        assert clip["status"] == "review_ready", status["tasks"]
        qa = next(t for t in status["tasks"] if t["type"] == "editorial_qa")
        assert qa["status"] == "succeeded" and qa["result"]["verdict"] == "pass"
        usage = _desktop(tmp_path, "production_costs", batch_id=batch_id)
        assert usage and usage[0]["cost_kind"] == "provider_reported" and usage[0]["cost_micros"] == 12300
        detail = _desktop(tmp_path, "production_clip_detail", clip_id=clip["id"])
        assert detail["reviews"][0]["reviewer"].startswith("claude_code:")
        overview = _desktop(tmp_path, "production_overview")
        assert overview["batches"][0]["review_ready"] == 1
        assert "team" in overview["resources"]["pools"]
        # Privileged operations never exist on the agent endpoint.
        try:
            service_request(
                tmp_path, "/scoped", {"tool": "production_approve", "args": {}, "task_token": "x" * 40}
            )
            raise AssertionError("expected refusal")
        except Exception as exc:
            assert "approve" not in str(exc) or "token" in str(exc).lower()
        approvals = _desktop(tmp_path, "production_approve", clip_ids=[clip["id"]], approved_by="Producer")
        assert approvals[0]["timeline_revision"] == clip["draft_revision"]
    finally:
        service.http.shutdown()
        thread.join(timeout=10)
