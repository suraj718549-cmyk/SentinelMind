"""
Tests for src/llm_reasoner.py.

None of these tests make a real network call — the "LLM" calls are mocked
by monkeypatching src.llm_reasoner._CALLERS. This keeps the test suite fully
offline (same philosophy as the rest of this repo's tests) while still
exercising the real parsing, validation, and fallback logic.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.agent import Alert
import src.llm_reasoner as llm_reasoner


ALERT = Alert(
    "A1", "network_ioc", "Connection to 185.220.101.7",
    {"dest_ip": "185.220.101.7"}, "outbound TLS handshake",
)


def test_get_reasoner_falls_back_to_heuristic_when_provider_unset(monkeypatch):
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    reasoner = llm_reasoner.get_reasoner()
    assert reasoner is llm_reasoner.heuristic_reasoner


def test_get_reasoner_falls_back_when_api_key_missing(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    reasoner = llm_reasoner.get_reasoner()
    assert reasoner is llm_reasoner.heuristic_reasoner


def test_get_reasoner_falls_back_for_unsupported_provider(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "not-a-real-provider")
    monkeypatch.setenv("LLM_API_KEY", "sk-fake")
    reasoner = llm_reasoner.get_reasoner()
    assert reasoner is llm_reasoner.heuristic_reasoner


def test_llm_reasoner_parses_well_formed_response(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        return '{"verdict": "malicious", "confidence": 0.91, "rationale": "Matches a known-bad C2 indicator."}'

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(provider="anthropic", api_key="sk-fake", model="test-model")

    verdict, confidence, rationale = reasoner(ALERT, "prior incident: 185.220.101.7 confirmed malicious")
    assert verdict == "malicious"
    assert confidence == 0.91
    assert "known-bad" in rationale
    assert "[reasoner: anthropic:test-model]" in rationale


def test_llm_reasoner_handles_markdown_fenced_json(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        return '```json\n{"verdict": "benign", "confidence": 0.2, "rationale": "No indicators matched."}\n```'

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(provider="anthropic", api_key="sk-fake", model="test-model")

    verdict, confidence, rationale = reasoner(ALERT, "")
    assert verdict == "benign"
    assert confidence == 0.2


def test_llm_reasoner_falls_back_on_malformed_json(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        return "I think this alert looks fine, no JSON here."

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(
        provider="anthropic", api_key="sk-fake", model="test-model", max_retries=0
    )

    verdict, confidence, rationale = reasoner(ALERT, "")
    assert verdict in ("benign", "suspicious", "malicious")  # heuristic still returns a valid verdict
    assert "heuristic-fallback" in rationale


def test_llm_reasoner_falls_back_on_invalid_verdict(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        return '{"verdict": "extremely_bad", "confidence": 1.5, "rationale": "n/a"}'

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(
        provider="anthropic", api_key="sk-fake", model="test-model", max_retries=0
    )

    verdict, confidence, rationale = reasoner(ALERT, "")
    assert verdict in ("benign", "suspicious", "malicious")
    assert "heuristic-fallback" in rationale


def test_llm_reasoner_falls_back_when_api_raises(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        raise ConnectionError("simulated network failure")

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(
        provider="anthropic", api_key="sk-fake", model="test-model", max_retries=0
    )

    verdict, confidence, rationale = reasoner(ALERT, "")
    assert verdict in ("benign", "suspicious", "malicious")
    assert "heuristic-fallback" in rationale
    assert "simulated network failure" in rationale


def test_confidence_is_clamped_to_valid_range(monkeypatch):
    def fake_call(system, user, api_key, model, timeout):
        return '{"verdict": "suspicious", "confidence": 3.7, "rationale": "over-confident model"}'

    monkeypatch.setitem(llm_reasoner._CALLERS, "anthropic", fake_call)
    reasoner = llm_reasoner.make_llm_reasoner(provider="anthropic", api_key="sk-fake", model="test-model")

    verdict, confidence, rationale = reasoner(ALERT, "")
    assert 0.0 <= confidence <= 1.0
