"""Local LLM inference through Ollama (spec §18).

The LLM is optional infrastructure: when Ollama is not running or the model is
not pulled, every call raises an error whose message is the setup instruction
the user needs - never a stack trace, and never a crash of the analysis. The
client only ever talks to the configured local Ollama URL; repository content
does not leave the machine (spec §38).

Prompts and completions are deliberately not logged (spec §54): they contain
repository source.
"""
from __future__ import annotations

import logging
import re
import threading
import time
from typing import Literal

import httpx
from pydantic import BaseModel, Field

from backend.config import settings

logger = logging.getLogger(__name__)

# Some Qwen3 variants emit their reasoning inside <think> tags before the
# answer; the reasoning is not part of the answer shown to the user.
_THINK_BLOCK = re.compile(r"<think>.*?</think>\s*", re.DOTALL)
_HEALTH_TIMEOUT_SECONDS = 3.0


class LLMError(Exception):
    """Base error for LLM failures. Messages are safe to show to the user."""


class LLMUnavailableError(LLMError):
    """Ollama is not reachable at the configured URL."""


class LLMModelMissingError(LLMError):
    """Ollama runs, but the configured model has not been pulled."""


class LLMTimeoutError(LLMError):
    """The model did not answer within the configured timeout."""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(..., max_length=20_000)


class LLMHealth(BaseModel):
    """What the frontend needs to tell the user whether AI features work, and if not, why."""

    reachable: bool
    base_url: str
    model: str
    model_available: bool
    available_models: list[str] = Field(default_factory=list)
    ready: bool
    message: str  # human-readable status or setup instruction


class LLMClient:
    """Interface every LLM backend implements (spec §18)."""

    name: str

    def health_check(self) -> LLMHealth:
        raise NotImplementedError

    def model_available(self) -> bool:
        raise NotImplementedError

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[ChatMessage] | None = None,
    ) -> str:
        """Return the model's answer to ``prompt`` (with optional system prompt and prior turns)."""
        raise NotImplementedError


class OllamaClient(LLMClient):
    def __init__(
        self,
        base_url: str | None = None,
        model: str | None = None,
        timeout_seconds: float | None = None,
        transport: httpx.BaseTransport | None = None,  # tests inject a mock transport
    ) -> None:
        self.base_url = (base_url or settings.ollama_base_url).rstrip("/")
        self.name = model or settings.ollama_model
        self.timeout_seconds = timeout_seconds or settings.llm_timeout_seconds
        self._http = httpx.Client(base_url=self.base_url, transport=transport)

    # -- health ---------------------------------------------------------------

    def health_check(self) -> LLMHealth:
        try:
            models = self._installed_models()
        except LLMUnavailableError as exc:
            return LLMHealth(
                reachable=False,
                base_url=self.base_url,
                model=self.name,
                model_available=False,
                ready=False,
                message=str(exc),
            )
        available = self._matches_installed(models)
        return LLMHealth(
            reachable=True,
            base_url=self.base_url,
            model=self.name,
            model_available=available,
            available_models=models,
            ready=available,
            message=(
                f"Ollama is running and '{self.name}' is installed."
                if available
                else self._missing_model_message(models)
            ),
        )

    def model_available(self) -> bool:
        try:
            return self._matches_installed(self._installed_models())
        except LLMUnavailableError:
            return False

    def _installed_models(self) -> list[str]:
        try:
            response = self._http.get("/api/tags", timeout=_HEALTH_TIMEOUT_SECONDS)
            response.raise_for_status()
            data = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise LLMUnavailableError(self._unreachable_message()) from exc
        models = data.get("models", []) if isinstance(data, dict) else []
        return sorted(
            str(m.get("name")) for m in models if isinstance(m, dict) and m.get("name")
        )

    def _matches_installed(self, installed: list[str]) -> bool:
        """``qwen3-coder`` matches ``qwen3-coder:latest``; an explicit tag must match exactly."""
        wanted = self.name if ":" in self.name else f"{self.name}:latest"
        return wanted in installed or self.name in installed

    # -- generation -----------------------------------------------------------

    def generate(
        self,
        prompt: str,
        *,
        system: str | None = None,
        history: list[ChatMessage] | None = None,
    ) -> str:
        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        for turn in history or []:
            messages.append({"role": turn.role, "content": turn.content})
        messages.append({"role": "user", "content": prompt})

        payload = {
            "model": self.name,
            "messages": messages,
            "stream": False,
            "options": {"temperature": settings.llm_temperature, "num_ctx": settings.llm_num_ctx},
        }
        started = time.monotonic()
        try:
            response = self._http.post("/api/chat", json=payload, timeout=self.timeout_seconds)
        except httpx.TimeoutException as exc:
            logger.warning("LLM timed out after %.0fs (model %s)", self.timeout_seconds, self.name)
            raise LLMTimeoutError(
                f"The model did not answer within {self.timeout_seconds:.0f}s. A smaller model "
                f"or a higher LLM_TIMEOUT may help."
            ) from exc
        except httpx.HTTPError as exc:
            raise LLMUnavailableError(self._unreachable_message()) from exc

        if response.status_code == 404:
            raise LLMModelMissingError(self._missing_model_message())
        if response.status_code >= 400:
            logger.warning("Ollama returned HTTP %s for model %s", response.status_code, self.name)
            raise LLMError(
                f"Ollama returned an error (HTTP {response.status_code}): {_error_text(response)}"
            )
        try:
            content = response.json()["message"]["content"]
        except (ValueError, KeyError, TypeError) as exc:
            raise LLMError("Ollama returned an unexpected response.") from exc
        logger.info(
            "LLM answered in %.1fs (model %s, %d chars)",
            time.monotonic() - started,
            self.name,
            len(content),
        )
        return strip_thinking(str(content))

    # -- messages -------------------------------------------------------------

    def _unreachable_message(self) -> str:
        return (
            f"Ollama is not reachable at {self.base_url}. Install it from https://ollama.com, "
            f"start it (`ollama serve`), and pull the model (`ollama pull {self.name}`). "
            f"If Ollama runs elsewhere, set OLLAMA_BASE_URL."
        )

    def _missing_model_message(self, installed: list[str] | None = None) -> str:
        hint = f" Installed models: {', '.join(installed)}." if installed else ""
        return (
            f"Model '{self.name}' is not installed in Ollama. Run `ollama pull {self.name}` "
            f"or set OLLAMA_MODEL to an installed model.{hint}"
        )


def strip_thinking(text: str) -> str:
    """Remove ``<think>...</think>`` reasoning blocks some models emit."""
    return _THINK_BLOCK.sub("", text).strip()


def _error_text(response: httpx.Response) -> str:
    try:
        error = response.json().get("error")
        if isinstance(error, str) and error:
            return error[:200]
    except ValueError:
        pass
    return "no details"


_client_instance: LLMClient | None = None
_client_lock = threading.Lock()


def get_llm_client() -> LLMClient:
    """The process-wide LLM client for the configured Ollama URL and model."""
    global _client_instance
    with _client_lock:
        current = _client_instance
        if (
            not isinstance(current, OllamaClient)
            or current.base_url != settings.ollama_base_url.rstrip("/")
            or current.name != settings.ollama_model
        ):
            _client_instance = OllamaClient()
        return _client_instance
