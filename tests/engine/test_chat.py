"""Phase 6 built-in chat: providers (recorded responses), key storage, proposals and the apply gate."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_phase2 import _evidence, _plan, _project

from ageneditor import chat, credentials
from ageneditor.chat_providers import complete
from ageneditor.contracts import EngineException, ErrorCode
from ageneditor.contracts.chat import ChatProvider
from ageneditor.desktop_service import DesktopService

KEY = "sk-test-0123456789"


class MemoryCredentials:
    def __init__(self) -> None:
        self.items: dict[str, str] = {}

    def write(self, target: str, secret: str) -> None:
        self.items[target] = secret

    def read(self, target: str) -> str | None:
        return self.items.get(target)

    def delete(self, target: str) -> None:
        self.items.pop(target, None)


@pytest.fixture(autouse=True)
def memory_credentials():
    store = MemoryCredentials()
    credentials.use_backend(store)
    yield store
    credentials.use_backend(None)


# ---- providers --------------------------------------------------------------------------------------


def test_openai_and_gemini_requests_and_replies() -> None:
    sent = {}

    def transport(url, headers, body, timeout):
        sent.update(url=url, headers=headers, body=json.loads(body))
        if "openai" in url:
            reply = {
                "output": [
                    {"type": "message", "content": [{"type": "output_text", "text": '{"message": "hi"}'}]}
                ]
            }
        else:
            reply = {"candidates": [{"content": {"parts": [{"text": '{"message": "hola"}'}]}}]}
        return 200, json.dumps(reply).encode()

    messages = [("user", "cut it"), ("assistant", "ok"), ("user", "shorter")]
    assert complete(ChatProvider.OPENAI, "model-a", KEY, "sys", messages, transport) == '{"message": "hi"}'
    assert sent["url"] == "https://api.openai.com/v1/responses"
    assert sent["headers"]["Authorization"] == f"Bearer {KEY}"
    assert sent["body"]["instructions"] == "sys" and len(sent["body"]["input"]) == 3

    assert complete(ChatProvider.GEMINI, "model-b", KEY, "sys", messages, transport) == '{"message": "hola"}'
    assert sent["url"].endswith("/models/model-b:generateContent")
    assert sent["headers"]["x-goog-api-key"] == KEY
    assert [c["role"] for c in sent["body"]["contents"]] == ["user", "model", "user"]


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (401, "rejected the API key"),
        (429, "rate limit"),
        (503, "having trouble"),
        (400, "refused the request"),
    ],
)
def test_provider_errors_are_plain_and_never_contain_the_key(status: int, expected: str) -> None:
    def transport(url, headers, body, timeout):
        echoed = f"Incorrect API key {KEY} (ends ...{KEY[-8:]})"
        return status, json.dumps({"error": {"message": echoed}}).encode()

    with pytest.raises(EngineException) as failure:
        complete(ChatProvider.OPENAI, "model-a", KEY, "sys", [("user", "x")], transport)
    assert expected in failure.value.error.message
    assert KEY not in failure.value.error.message and KEY[-8:] not in failure.value.error.message


def test_claude_uses_the_sdk_with_effort_and_refusal_fallback() -> None:
    calls = []

    class Messages:
        def create(self, **request):
            calls.append(request)
            stop = "refusal" if "refuse" in request["messages"][-1]["content"] else "end_turn"
            return SimpleNamespace(
                stop_reason=stop, content=[SimpleNamespace(type="text", text='{"message": "ok"}')]
            )

    def factory(key: str):
        assert key == KEY
        return SimpleNamespace(beta=SimpleNamespace(messages=Messages()))

    reply = complete(ChatProvider.ANTHROPIC, "claude-opus-5-5", KEY, "sys", [("user", "cut")],
                     anthropic_client_factory=factory)  # fmt: skip
    assert reply == '{"message": "ok"}'
    request = calls[0]
    assert request["model"] == "claude-opus-5-5" and request["system"] == "sys"
    assert request["output_config"] == {"effort": "high"}
    assert request["fallbacks"] == "default" and request["betas"] == ["server-side-fallback-2026-07-01"]
    with pytest.raises(EngineException, match="declined"):
        complete(ChatProvider.ANTHROPIC, "claude-opus-5-5", KEY, "sys", [("user", "refuse")],
                 anthropic_client_factory=factory)  # fmt: skip


def test_claude_request_serializes_with_the_official_sdk() -> None:
    """Exercise the actual SDK serializer and parser without sending a network request."""
    anthropic = pytest.importorskip("anthropic")
    httpx2 = pytest.importorskip("httpx2")
    sent = {}

    def handle(request):
        sent["url"] = str(request.url)
        sent["headers"] = dict(request.headers)
        sent["body"] = json.loads(request.content)
        return httpx2.Response(
            200,
            json={
                "id": "msg_test",
                "type": "message",
                "role": "assistant",
                "content": [{"type": "text", "text": '{"message": "ok"}'}],
                "model": "claude-opus-5-5",
                "stop_reason": "end_turn",
                "stop_sequence": None,
                "usage": {"input_tokens": 10, "output_tokens": 5},
            },
        )

    def factory(key: str):
        return anthropic.Anthropic(
            api_key=key,
            max_retries=0,
            http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handle)),
        )

    reply = complete(
        ChatProvider.ANTHROPIC,
        "claude-opus-5-5",
        KEY,
        "system prompt",
        [("user", "cut this")],
        anthropic_client_factory=factory,
    )
    assert reply == '{"message": "ok"}'
    assert sent["url"] == "https://api.anthropic.com/v1/messages?beta=true"
    assert sent["headers"]["x-api-key"] == KEY
    assert sent["body"]["system"] == "system prompt"
    assert sent["body"]["messages"] == [{"role": "user", "content": "cut this"}]
    assert sent["body"]["output_config"] == {"effort": "high"}
    assert sent["body"]["fallbacks"] == "default"


def test_keys_are_validated_and_can_be_removed(memory_credentials: MemoryCredentials) -> None:
    with pytest.raises(EngineException):
        credentials.set_key("openai", "has space")
    credentials.set_key("openai", f"  {KEY}  ")
    assert credentials.get_key("openai") == KEY and credentials.has_key("openai")
    assert memory_credentials.items == {"AgenEditor/chat/openai": KEY}
    credentials.remove_key("openai")
    assert not credentials.has_key("openai")


# ---- turns, proposals and the apply gate -----------------------------------------------------------------


def _reply(store, asset_id: str, **plan_changes) -> str:
    plan = _plan(store, asset_id).model_dump(mode="json", exclude={"project_id", "schema_version"})
    plan.update(plan_changes)
    return "```json\n" + json.dumps({"message": "Two passages that explain it.", "plan": plan}) + "\n```"


@pytest.mark.media
def test_a_chat_plan_is_validated_and_applied_only_on_request(tmp_path: Path, media_dir: Path) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    replies = iter([_reply(store, asset_id)])
    seen = []

    def ask(system, messages):
        seen.append(messages)
        return next(replies)

    with pytest.raises(EngineException) as no_consent:
        chat.send(store, ChatProvider.OPENAI, "model-a", "Make a 40 second cut", ask)
    assert no_consent.value.error.code is ErrorCode.PERMISSION_DENIED
    store.set_setting("chat_consent", "1")

    turn = chat.send(store, ChatProvider.OPENAI, "model-a", "Make a 40 second cut", ask)
    evidence = seen[0][-1][1]
    assert "word_0000000" in evidence and "transcript_test" in evidence
    assert str(tmp_path) not in evidence  # no local paths leave the machine
    assert [m.role for m in turn.messages] == ["user", "assistant"]
    proposal = turn.proposal
    assert proposal is not None and proposal.status == "proposed" and proposal.result.valid
    assert store.current_revision() == 0  # nothing changes until a person applies it

    applied = chat.apply_proposal(store, proposal.id, 0)
    assert applied.status == "applied" and applied.applied_revision == 1
    assert store.timeline().provenance.tool == "chat_apply"
    with pytest.raises(EngineException, match="unapplied"):
        chat.apply_proposal(store, proposal.id, 1)
    assert [m.role for m in chat.history(store)] == ["user", "assistant"]


@pytest.mark.media
def test_an_invalid_plan_gets_one_retry_with_the_reasons(tmp_path: Path, media_dir: Path) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    store.set_setting("chat_consent", "1")
    good = _reply(store, asset_id)
    one_segment = json.loads(good.strip("`").removeprefix("json\n"))
    one_segment["plan"]["segments"] = one_segment["plan"]["segments"][:1]
    replies = iter([json.dumps(one_segment), good])
    seen = []

    def ask(system, messages):
        seen.append(messages)
        return next(replies)

    turn = chat.send(store, ChatProvider.GEMINI, "model-b", "Cut it", ask)
    assert len(seen) == 2 and "could not accept that plan" in seen[1][-1][1]
    assert turn.proposal is not None and turn.proposal.result.valid


@pytest.mark.media
def test_a_proposal_goes_stale_when_the_edit_changes(tmp_path: Path, media_dir: Path) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    store.set_setting("chat_consent", "1")
    turn = chat.send(
        store, ChatProvider.OPENAI, "m", "Cut it", lambda system, messages: _reply(store, asset_id)
    )
    seed = store.timeline()
    store.commit_timeline(turn.proposal.result.candidate_timeline, 0)  # someone else edits first
    with pytest.raises(EngineException) as conflict:
        chat.apply_proposal(store, turn.proposal.id, 0)
    assert conflict.value.error.code is ErrorCode.REVISION_CONFLICT
    assert chat.proposal(store, turn.proposal.id).status == "stale"
    assert seed.revision == 0


@pytest.mark.media
def test_desktop_chat_ops_never_return_a_key(
    tmp_path: Path, media_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store, asset_id = _project(tmp_path, media_dir)
    _evidence(store, asset_id)
    monkeypatch.setattr("ageneditor.chat_providers.complete", lambda *args, **kwargs: _reply(store, asset_id))
    service = DesktopService(tmp_path)
    try:
        call = service._desktop_call
        args = {"project_id": store.project_id}
        status = call("chat_status", args)
        assert status["keys"] == {"anthropic": False, "openai": False, "gemini": False}
        assert status["default_models"]["anthropic"] == "claude-opus-5-5" and status["data_sent"]
        with pytest.raises(EngineException, match="API key"):
            call("chat_send", {**args, "provider": "openai", "model": "m", "text": "Cut it"})
        assert call("chat_set_key", {"provider": "openai", "key": KEY}) == {
            "provider": "openai",
            "has_key": True,
        }
        call("chat_set_consent", {**args, "consent": True})
        turn = call("chat_send", {**args, "provider": "openai", "model": "m", "text": "Cut it"})
        assert "candidate_timeline" not in turn["proposal"]["result"]
        applied = call("chat_apply", {**args, "proposal_id": turn["proposal"]["id"], "expected_revision": 0})
        assert applied["applied_revision"] == 1
        history = call("chat_history", args)
        everything = json.dumps([status, turn, applied, history])
        assert KEY not in everything
    finally:
        service.http.server_close()
