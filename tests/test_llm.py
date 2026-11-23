import inspect
import json
from types import SimpleNamespace

import anthropic
import httpx2
import pytest
from anthropic.resources.messages import Messages

from triage import llm
from triage.llm import LLMError, TriageClient, build_system_prompt, cost_usd
from triage.llm import make_default_client as real_factory  # imported before conftest disables it
from triage.models import Extraction

VALID = {
    "sender_type": "CUSTOMER",
    "category": "RENTAL",
    "urgency": "LOW",
    "department": "FRONT_DESK",
    "reason": "Asks about renting a bike.",
    "intake": {"customer_name": "Alex", "bike_or_booking": None, "issue": "rental question"},
    "safety_risk": False,
    "escalation_threat": False,
    "secondary_requests": [],
    "draft_reply": "Hello Alex. Larkspur Cycle Works",
    "human_action": None,
}
USAGE = dict(input_tokens=100, output_tokens=50, cache_creation_input_tokens=0, cache_read_input_tokens=2000)


def response(parsed=VALID, stop_reason="end_turn", usage=USAGE, stop_details=None):
    return SimpleNamespace(
        parsed_output=Extraction.model_validate(parsed) if parsed else None,
        stop_reason=stop_reason,
        stop_details=stop_details,
        usage=SimpleNamespace(**usage),
        model="claude-opus-5-5",
    )


class RecordingMessages:
    """Stands in for `client.messages`; replays queued responses and records each call."""

    def __init__(self, *queued):
        self.queued = list(queued)
        self.calls = []

    def parse(self, **kwargs):
        self.calls.append(kwargs)
        item = self.queued.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def client_with(*queued, **kwargs):
    messages = RecordingMessages(*queued)
    tc = TriageClient("claude-opus-5-5", "SYSTEM PROMPT", client=SimpleNamespace(messages=messages), **kwargs)
    return tc, messages


# --------------------------------------------------------------------------- request shape
def test_request_matches_the_installed_sdk_signature():
    tc, messages = client_with(response())
    tc.extract("hello")
    kwargs = messages.calls[0]
    inspect.signature(Messages.parse).bind(None, **kwargs)  # TypeError if a kwarg does not exist
    assert kwargs["model"] == "claude-opus-5-5"
    assert kwargs["output_format"] is Extraction
    assert kwargs["output_config"] == {"effort": "low"}
    assert kwargs["system"] == [{"type": "text", "text": "SYSTEM PROMPT", "cache_control": {"type": "ephemeral"}}]
    assert kwargs["messages"] == [{"role": "user", "content": "hello"}]
    assert "thinking" not in kwargs  # thinking cannot be disabled on the default model


def test_real_sdk_builds_the_expected_http_request_and_parses_the_reply():
    seen = {}

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen["body"] = json.loads(request.content)
        return httpx2.Response(200, json={
            "id": "msg_offline", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": [{"type": "text", "text": json.dumps(VALID)}],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 120, "output_tokens": 80,
                      "cache_creation_input_tokens": 1500, "cache_read_input_tokens": 0},
        })

    sdk = anthropic.Anthropic(api_key="placeholder", max_retries=0,
                              http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    out = TriageClient("claude-opus-5-5", "SYSTEM PROMPT", client=sdk).extract("hello")

    assert out.parsed == Extraction.model_validate(VALID)
    assert out.usage["cache_creation_input_tokens"] == 1500
    body = seen["body"]
    assert body["output_config"]["effort"] == "low"
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert body["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert "thinking" not in body


def sdk_replying(*replies):
    """Real SDK client whose HTTP layer replays canned API responses; counts requests."""
    state = {"requests": 0}

    def handler(request):
        content, stop = replies[state["requests"]]
        state["requests"] += 1
        return httpx2.Response(200, json={
            "id": "msg_offline", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
            "content": content, "stop_reason": stop, "stop_sequence": None,
            "usage": {"input_tokens": 10, "output_tokens": 5},
        })

    sdk = anthropic.Anthropic(api_key="placeholder", max_retries=0,
                              http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    return sdk, state


TRUNCATED = ([{"type": "text", "text": '{"sender_type": "CUST'}], "max_tokens")
WRONG_SCHEMA = ([{"type": "text", "text": '{"foo": 1}'}], "end_turn")
GOOD = ([{"type": "text", "text": json.dumps(VALID)}], "end_turn")


def test_real_sdk_truncated_reply_is_retried_once_and_flagged_as_unmetered():
    sdk, state = sdk_replying(TRUNCATED, GOOD)
    out = TriageClient("claude-opus-5-5", "S", client=sdk).extract("hello")
    assert state["requests"] == 2
    assert out.api_calls == 2
    assert out.unmetered_calls == 1  # the SDK raises before we can read that reply's usage


def test_real_sdk_wrong_schema_twice_raises():
    sdk, state = sdk_replying(WRONG_SCHEMA, WRONG_SCHEMA)
    with pytest.raises(LLMError, match="schema"):
        TriageClient("claude-opus-5-5", "S", client=sdk).extract("hello")
    assert state["requests"] == 2


def test_real_sdk_refusal_is_not_retried():
    sdk, state = sdk_replying(([], "refusal"))
    with pytest.raises(LLMError, match="refus"):
        TriageClient("claude-opus-5-5", "S", client=sdk).extract("hello")
    assert state["requests"] == 1


# --------------------------------------------------------------------------- credentials
def test_default_client_requires_an_api_key_in_the_environment(monkeypatch):
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: pytest.fail("must not be built"))
    with pytest.raises(LLMError, match="ANTHROPIC_API_KEY"):
        real_factory()


def test_default_client_passes_the_key_explicitly(monkeypatch):
    seen = {}
    monkeypatch.setenv("ANTHROPIC_API_KEY", "placeholder")
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: seen.update(kw) or "client")
    assert real_factory() == "client"
    assert seen == {"api_key": "placeholder"}


def test_missing_key_becomes_a_per_email_error(monkeypatch):
    monkeypatch.setattr(llm, "make_default_client", real_factory)
    monkeypatch.setattr(llm.anthropic, "Anthropic", lambda **kw: pytest.fail("must not be built"))
    with pytest.raises(LLMError):
        TriageClient("claude-opus-5-5", "S").extract("hello")


# --------------------------------------------------------------------------- disk cache
def test_second_identical_call_is_served_from_disk(tmp_path):
    tc, messages = client_with(response(), cache_dir=tmp_path)
    first = tc.extract("hello")
    second = tc.extract("hello")
    assert len(messages.calls) == 1
    assert first.from_cache is False and second.from_cache is True
    assert second.parsed == first.parsed
    assert second.usage == first.usage


def test_cache_key_depends_on_model_system_and_message():
    a = TriageClient("m1", "S", client=object())
    assert a.cache_key("x") != TriageClient("m2", "S", client=object()).cache_key("x")
    assert a.cache_key("x") != TriageClient("m1", "S2", client=object()).cache_key("x")
    assert a.cache_key("x") != a.cache_key("y")


def test_corrupt_cache_file_is_ignored(tmp_path):
    tc, messages = client_with(response(), cache_dir=tmp_path)
    (tmp_path / f"{tc.cache_key('hello')}.json").write_text("{not json", encoding="utf-8")
    assert tc.extract("hello").from_cache is False
    assert len(messages.calls) == 1


# --------------------------------------------------------------------------- failures
def test_one_retry_when_the_reply_does_not_fit_the_schema():
    tc, messages = client_with(response(parsed=None), response())
    out = tc.extract("hello")
    assert out.api_calls == 2
    assert out.usage["output_tokens"] == 2 * USAGE["output_tokens"]  # the discarded reply was paid for too


def test_truncated_reply_counts_as_a_schema_failure():
    tc, _ = client_with(response(stop_reason="max_tokens"), response(stop_reason="max_tokens"))
    with pytest.raises(LLMError, match="max_tokens"):
        tc.extract("hello")


def test_refusal_is_reported_and_not_retried():
    details = SimpleNamespace(category="cyber", explanation="declined")
    tc, messages = client_with(response(parsed=None, stop_reason="refusal", stop_details=details))
    with pytest.raises(LLMError, match="refus"):
        tc.extract("hello")
    assert len(messages.calls) == 1


def test_api_errors_become_llm_errors():
    error = anthropic.APIConnectionError(request=httpx2.Request("POST", "https://api.invalid/v1/messages"))
    tc, _ = client_with(error)
    with pytest.raises(LLMError):
        tc.extract("hello")


def test_offline_mode_without_cache_raises(tmp_path):
    tc, messages = client_with(response(), cache_dir=tmp_path, offline=True)
    with pytest.raises(LLMError, match="offline"):
        tc.extract("hello")
    assert messages.calls == []


# --------------------------------------------------------------------------- cost and prompt
def test_cost_uses_the_list_price_of_the_model():
    usage = dict(input_tokens=1_000_000, output_tokens=1_000_000,
                 cache_creation_input_tokens=1_000_000, cache_read_input_tokens=1_000_000)
    assert cost_usd("claude-opus-5-5", usage) == pytest.approx(4 + 20 + 5 + 0.20)


def test_cost_of_an_unknown_model_is_none():
    assert cost_usd("some-other-model", USAGE) is None


def test_system_prompt_embeds_the_company_profile_and_is_long_enough_to_cache():
    system = build_system_prompt()
    assert "{{COMPANY}}" not in system
    assert "Larkspur Cycle Works" in system
    assert len(system) > 4 * 1024  # roughly above 1024 tokens; caching needs a minimum prefix length
