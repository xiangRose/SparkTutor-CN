"""AI compatibility and credential tests use synthetic responses, never models."""

from copy import deepcopy
from io import BytesIO
import json
import os
from types import SimpleNamespace
from urllib.error import HTTPError, URLError
from urllib.request import Request

import pytest

from sparktutor.config.settings import Settings
from sparktutor.web.ai import MAX_RESPONSE, WebAiError, WebAiManager, _SameOriginRedirect
from sparktutor.web.secret_store import LocalSecretStore


@pytest.fixture(autouse=True)
def isolate_provider_environment(monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "SPARKTUTOR_CLAUDE_MODEL", "SPARKTUTOR_AI_PROVIDER",
                 "SPARKTUTOR_OPENAI_BASE_URL", "SPARKTUTOR_OPENAI_MODEL",
                 "SPARKTUTOR_OPENAI_API_KEY", "OPENAI_API_KEY"):
        monkeypatch.delenv(name, raising=False)


class MemorySecureStore:
    supported = True
    name = "Test secure store"

    def __init__(self):
        self.values = {}

    def save(self, values):
        self.values = deepcopy(values)
        return True

    def load(self):
        return deepcopy(self.values)

    def delete(self):
        self.values = {}


def response(text="OK", **choice):
    return {"choices": [{"message": {"content": text}, **choice}]}


def fake_opener(outcomes, calls):
    def open_request(request, timeout):
        calls.append((request, timeout))
        outcome = outcomes.pop(0)
        if isinstance(outcome, BaseException):
            raise outcome
        return BytesIO(outcome if isinstance(outcome, bytes) else json.dumps(outcome).encode())
    return open_request


def compatible(manager, **changes):
    ai = manager.new_session()
    manager.configure(ai, {"provider": "openai-compatible", "baseUrl": "http://localhost:11434/v1",
                           "model": "test-model", "apiKey": "test-secret", **changes})
    return ai


def test_session_only_configuration_does_not_write_or_leak_credentials(tmp_path):
    manager = WebAiManager(Settings(data_dir=tmp_path))
    first = compatible(manager, extraHeaders={"X-Api-Key": "header-secret"})
    public = manager.public(first)
    assert public["hasApiKey"] and public["hasExtraHeaders"]
    assert public["extraHeaderNames"] == ["X-Api-Key"]
    assert "test-secret" not in json.dumps(public)
    assert "header-secret" not in json.dumps(public)
    assert manager.new_session()["baseUrl"] == ""
    assert not list(tmp_path.iterdir())


def test_explicit_remember_uses_secure_store_and_public_json_contains_no_secrets(tmp_path):
    vault = MemorySecureStore()
    manager = WebAiManager(Settings(data_dir=tmp_path), secret_store=vault)
    ai = compatible(manager, remember=True, extraHeaders={"Authorization": "custom-secret"})
    public_json = manager.path.read_text(encoding="utf-8")
    assert "test-secret" not in public_json and "custom-secret" not in public_json
    assert "extraHeaders" not in public_json
    restored = WebAiManager(Settings(data_dir=tmp_path), secret_store=vault).new_session()
    assert restored["apiKey"] == "test-secret" and restored["extraHeaders"]["Authorization"] == "custom-secret"
    manager.configure(ai, {"clearKey": True, "clearExtraHeaders": True})
    assert not manager.new_session()["apiKey"] and not manager.new_session()["extraHeaders"]
    manager.configure(ai, {"remember": False})
    assert not manager.path.exists() and vault.values == {}


def test_unsupported_secure_storage_never_writes_plaintext_fallback(tmp_path):
    class Unsupported(MemorySecureStore):
        supported = False
        name = "memory only"

        def save(self, values):
            return False

    manager = WebAiManager(Settings(data_dir=tmp_path), secret_store=Unsupported())
    ai = compatible(manager, remember=True, extraHeaders={"X-Api-Key": "extra-private"})
    assert manager.public(ai)["storageWarning"]
    assert all("test-secret" not in path.read_text() and "extra-private" not in path.read_text()
               for path in tmp_path.iterdir())
    assert not manager.new_session()["apiKey"] and not manager.new_session()["extraHeaders"]


def test_partial_settings_write_cannot_bind_new_credentials_to_old_endpoint(tmp_path):
    vault = MemorySecureStore()
    manager = WebAiManager(Settings(data_dir=tmp_path), secret_store=vault)
    compatible(manager, remember=True)
    # A config-file write failure could leave a newer encrypted secret file.
    vault.values["parametersHash"] = "not-the-remembered-endpoint"
    restored = WebAiManager(Settings(data_dir=tmp_path), secret_store=vault).new_session()
    assert restored["apiKey"] == "" and restored["_storageWarning"]


@pytest.mark.parametrize("changes", [
    {"baseUrl": "file:///private/file"}, {"baseUrl": "https://user:secret@example.invalid"},
    {"baseUrl": "https://example.invalid/v1?api-key=secret"}, {"provider": "made-up"},
    {"extraHeaders": {"Host": "elsewhere"}}, {"extraHeaders": {"X-Key": "bad\r\nHost: injected"}},
    {"extraHeaders": []}, {"remember": "yes"},
])
def test_invalid_configuration_does_not_mutate_current_session(tmp_path, changes):
    manager = WebAiManager(Settings(data_dir=tmp_path))
    ai = compatible(manager)
    before = deepcopy(ai)
    with pytest.raises(ValueError):
        manager.configure(ai, changes)
    assert ai == before


def test_invalid_environment_url_does_not_expose_embedded_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SPARKTUTOR_OPENAI_BASE_URL", "https://user:private-key@example.invalid")
    manager = WebAiManager(Settings(data_dir=tmp_path))
    assert "private-key" not in json.dumps(manager.public(manager.new_session()))


@pytest.mark.asyncio
async def test_compatible_headers_text_blocks_and_probe_do_not_write_learning_data(tmp_path):
    calls = []
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([
        response([{"type": "text", "text": "O"}, {"type": "text", "text": "K"}]),
    ], calls))
    ai = compatible(manager, extraHeaders={"authorization": "Custom header-secret"})
    result = await manager.check_connection(ai)
    assert result["ok"] and result["state"] == "ready"
    request, timeout = calls[0]
    assert request.get_header("Authorization") == "Custom header-secret"
    assert json.loads(request.data)["max_tokens"] == 256
    assert 0 < timeout <= 30
    assert not list(tmp_path.iterdir())
    assert "header-secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_endpoint_fallback_and_max_tokens_retry_are_bounded(tmp_path):
    calls = []
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([
        HTTPError("http://local/api", 404, "not found", {}, BytesIO()),
        HTTPError("http://local/api", 400, "bad", {}, BytesIO(b"max_tokens not supported private-key")),
        response("ready"),
    ], calls))
    ai = compatible(manager, baseUrl="http://localhost:11434/api")
    assert await manager.complete(ai, [{"role": "user", "content": "hello"}]) == "ready"
    assert [item[0].full_url for item in calls] == [
        "http://localhost:11434/api/chat/completions",
        "http://localhost:11434/api/v1/chat/completions",
        "http://localhost:11434/api/v1/chat/completions",
    ]
    assert "max_tokens" not in json.loads(calls[-1][0].data)


@pytest.mark.parametrize("payload,kind", [
    ({"choices": [{"message": {"content": "", "reasoning_content": "private thought"}}]}, "incomplete"),
    (response("partial", finish_reason="length"), "incomplete"),
    (response(""), "empty"), ([], "invalid-format"), ({"choices": []}, "invalid-format"),
    (response(42), "invalid-format"),
])
def test_response_classification_never_exposes_private_reasoning(payload, kind):
    with pytest.raises(WebAiError) as caught:
        WebAiManager._extract_compatible(payload)
    assert caught.value.kind == kind
    assert "private thought" not in str(caught.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("status,kind", [(401, "authentication"), (403, "authentication"),
                                         (429, "rate-limit"), (503, "service"),
                                         (408, "timeout"), (504, "timeout")])
async def test_http_failures_are_sanitized_and_do_not_retry_another_path(tmp_path, status, kind):
    calls = []
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([
        HTTPError("http://local", status, "secret error", {}, BytesIO(b"private-secret-body")),
    ], calls))
    ai = compatible(manager, baseUrl="http://localhost:11434/api")
    result = await manager.check_connection(ai)
    assert not result["ok"] and result["kind"] == kind and len(calls) == 1
    assert "private-secret-body" not in json.dumps(result)


@pytest.mark.asyncio
@pytest.mark.parametrize("error,kind", [(TimeoutError("secret timeout"), "timeout"),
                                        (URLError("secret network"), "transport")])
async def test_transport_failures_return_fixed_categories(tmp_path, error, kind):
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([error], []))
    result = await manager.check_connection(compatible(manager))
    assert not result["ok"] and result["kind"] == kind
    assert "secret" not in json.dumps(result)


@pytest.mark.asyncio
async def test_empty_probe_does_not_disable_provider_and_manual_check_retries(tmp_path):
    calls = []
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([response(""), response()], calls))
    ai = compatible(manager, provider="auto")
    assert (await manager.check_connection(ai))["state"] == "unverified"
    assert manager.resolve(ai) == "openai-compatible"
    assert (await manager.check_connection(ai))["ok"] and len(calls) == 2


@pytest.mark.asyncio
async def test_automatic_main_fallback_and_explicit_coach_are_independent(tmp_path):
    anthropic_calls = []

    class Client:
        def __init__(self, **kwargs):
            self.messages = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def create(self, **kwargs):
            anthropic_calls.append(kwargs)
            error = RuntimeError("private Anthropic body")
            error.status_code = 401
            raise error

    calls = []
    manager = WebAiManager(Settings(data_dir=tmp_path), anthropic_factory=Client,
                           opener=fake_opener([response("fallback"), response("coach")], calls))
    ai = compatible(manager, provider="auto", anthropicApiKey="anthropic-secret")
    assert manager.resolve(ai) == "anthropic"
    assert await manager.complete(ai, [{"role": "user", "content": "hello"}]) == "fallback"
    assert manager.resolve(ai) == "openai-compatible"
    ai["provider"] = "anthropic"
    assert await manager.complete(ai, [{"role": "user", "content": "coach"}], coach=True) == "coach"
    assert len(anthropic_calls) == 1


@pytest.mark.asyncio
async def test_anthropic_preserves_system_messages_and_selected_model(tmp_path):
    requests = []

    class Client:
        def __init__(self, **kwargs):
            assert kwargs["api_key"] == "fake-anthropic-key"
            self.messages = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def create(self, **kwargs):
            requests.append(kwargs)
            return SimpleNamespace(stop_reason="end_turn", content=[SimpleNamespace(type="text", text="完成")])

    manager = WebAiManager(Settings(data_dir=tmp_path), anthropic_factory=Client)
    ai = manager.new_session()
    manager.configure(ai, {"provider": "anthropic", "anthropicModel": "selected-model", "anthropicApiKey": "fake-anthropic-key"})
    assert await manager.complete(ai, [{"role": "system", "content": "中文"}, {"role": "user", "content": "问题"}]) == "完成"
    assert requests[0]["system"] == "中文" and requests[0]["model"] == "selected-model"
    assert requests[0]["messages"] == [{"role": "user", "content": "问题"}]


@pytest.mark.asyncio
async def test_copilot_is_explicitly_unsupported_without_network(tmp_path):
    manager = WebAiManager(Settings(data_dir=tmp_path), opener=lambda *args, **kwargs: pytest.fail("No request expected"))
    ai = manager.new_session()
    manager.configure(ai, {"provider": "copilot"})
    result = await manager.check_connection(ai)
    assert not result["ok"] and result["kind"] == "unsupported"
    assert "VS Code" in result["message"]


@pytest.mark.asyncio
async def test_large_and_invalid_json_responses_are_rejected(tmp_path):
    for content in (b"not json", b"x" * (MAX_RESPONSE + 1)):
        manager = WebAiManager(Settings(data_dir=tmp_path), opener=fake_opener([content], []))
        result = await manager.check_connection(compatible(manager))
        assert result["kind"] == "invalid-format"


def test_model_redirect_does_not_forward_credentials_to_another_origin():
    request = Request("https://models.example/v1/chat/completions", data=b"{}",
                      headers={"Authorization": "Bearer private-key"})
    with pytest.raises(WebAiError, match="其他来源") as caught:
        _SameOriginRedirect().redirect_request(request, None, 302, "Found", {}, "https://other.example/chat")
    assert "private-key" not in str(caught.value)


@pytest.mark.skipif(os.name != "nt", reason="DPAPI is a Windows user-account service")
def test_windows_dpapi_ciphertext_roundtrip_uses_fake_credentials(tmp_path):
    vault = LocalSecretStore(tmp_path / "credentials.dpapi")
    assert vault.save({"apiKey": "fake-key-for-dpapi-test"})
    assert b"fake-key-for-dpapi-test" not in vault.path.read_bytes()
    assert vault.load() == {"apiKey": "fake-key-for-dpapi-test"}
    vault.delete()
    assert not vault.path.exists()
