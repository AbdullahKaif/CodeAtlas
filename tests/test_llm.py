"""Tests for the Ollama client (offline: httpx mock transport) and its error mapping."""
from __future__ import annotations

import json

import httpx
import pytest

from backend.llm.ollama_client import (
    ChatMessage,
    LLMError,
    LLMModelMissingError,
    LLMTimeoutError,
    LLMUnavailableError,
    OllamaClient,
    strip_thinking,
)


def make_client(handler, model="qwen3-coder") -> OllamaClient:
    return OllamaClient(
        base_url="http://ollama.test:11434",
        model=model,
        timeout_seconds=5,
        transport=httpx.MockTransport(handler),
    )


def tags_response(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": n} for n in names]})


class TestHealth:
    def test_ready_when_model_installed(self):
        client = make_client(lambda request: tags_response("qwen3-coder:latest", "llama3:8b"))
        health = client.health_check()
        assert health.reachable and health.model_available and health.ready
        assert health.available_models == ["llama3:8b", "qwen3-coder:latest"]
        assert client.model_available() is True

    def test_missing_model_gives_pull_instruction(self):
        client = make_client(lambda request: tags_response("llama3:8b"))
        health = client.health_check()
        assert health.reachable and not health.model_available and not health.ready
        assert "ollama pull qwen3-coder" in health.message
        assert "llama3:8b" in health.message  # tells the user what IS installed

    def test_unreachable_gives_install_instruction(self):
        def handler(request):
            raise httpx.ConnectError("connection refused", request=request)

        client = make_client(handler)
        health = client.health_check()
        assert not health.reachable and not health.ready
        assert "ollama.com" in health.message and "OLLAMA_BASE_URL" in health.message
        assert client.model_available() is False

    def test_garbage_response_is_unreachable(self):
        client = make_client(lambda request: httpx.Response(200, text="<html>not ollama</html>"))
        assert client.health_check().reachable is False

    @pytest.mark.parametrize(
        ("configured", "installed", "expected"),
        [
            ("qwen3-coder", ["qwen3-coder:latest"], True),
            ("qwen3-coder", ["qwen3-coder:30b"], False),  # explicit tag differs from :latest
            ("qwen3-coder:30b", ["qwen3-coder:30b"], True),
            ("qwen3-coder:30b", ["qwen3-coder:latest"], False),
        ],
    )
    def test_tag_matching(self, configured, installed, expected):
        client = make_client(lambda request: tags_response(*installed), model=configured)
        assert client.model_available() is expected


class TestGenerate:
    def test_sends_system_history_and_prompt_in_order(self):
        seen: dict = {}

        def handler(request):
            seen.update(json.loads(request.content))
            assert request.url.path == "/api/chat"
            return httpx.Response(
                200, json={"message": {"role": "assistant", "content": "<think>hmm</think>Hello."}}
            )

        client = make_client(handler)
        answer = client.generate(
            "Q2", system="SYS", history=[ChatMessage(role="user", content="Q1"), ChatMessage(role="assistant", content="A1")]
        )
        assert answer == "Hello."  # reasoning block stripped
        assert [m["role"] for m in seen["messages"]] == ["system", "user", "assistant", "user"]
        assert seen["messages"][-1]["content"] == "Q2"
        assert seen["stream"] is False
        assert seen["model"] == "qwen3-coder"
        assert "num_ctx" in seen["options"] and "temperature" in seen["options"]

    def test_missing_model_is_model_error(self):
        client = make_client(lambda request: httpx.Response(404, json={"error": "model 'qwen3-coder' not found"}))
        with pytest.raises(LLMModelMissingError, match="ollama pull qwen3-coder"):
            client.generate("hi")

    def test_server_error_is_llm_error_with_details(self):
        client = make_client(lambda request: httpx.Response(500, json={"error": "out of memory"}))
        with pytest.raises(LLMError, match="out of memory"):
            client.generate("hi")

    def test_timeout_is_timeout_error(self):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        with pytest.raises(LLMTimeoutError, match="LLM_TIMEOUT"):
            make_client(handler).generate("hi")

    def test_connection_failure_is_unavailable(self):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        with pytest.raises(LLMUnavailableError, match="ollama.com"):
            make_client(handler).generate("hi")

    def test_unexpected_payload_is_llm_error(self):
        client = make_client(lambda request: httpx.Response(200, json={"nope": 1}))
        with pytest.raises(LLMError, match="unexpected"):
            client.generate("hi")


class TestStripThinking:
    def test_removes_think_blocks(self):
        assert strip_thinking("<think>a\nb</think>\n\nAnswer") == "Answer"
        assert strip_thinking("plain") == "plain"
        assert strip_thinking("<think>x</think>A<think>y</think>B") == "AB"
