"""LLM abstraction (see architecture.md §6).

The pipeline is provider-agnostic: a real `gateway` or `direct` client can be
dropped in here. Until one is configured, `complete()` raises `LLMUnavailable`,
which every caller treats as a signal to use its deterministic fallback. This
keeps the whole pipeline runnable (and reproducible) with no network or keys.
"""

from __future__ import annotations

from .config import Config


class LLMUnavailable(RuntimeError):
    """Raised when no LLM backend is reachable/configured."""


class LLMClient:
    def __init__(self, backend: str, model_id: str) -> None:
        self.backend = backend
        self.model_id = model_id

    def complete(self, prompt: str, schema: dict) -> dict:
        """Return a JSON object matching `schema`.

        Extension point: wire an AI gateway or provider SDK here.
        """
        if self.backend == "offline":
            raise LLMUnavailable("offline backend selected")
        raise LLMUnavailable(
            f"'{self.backend}' backend is not configured (no credentials/SDK wired)."
        )


def get_llm(config: Config) -> LLMClient:
    return LLMClient(config.llm_backend, config.model_id)
