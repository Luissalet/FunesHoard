"""A fake Hoard Link double for offline tests of the backend/day-narrative
routes (see CONTRACT_BACKEND.md: "injectable in tests"). Not a test module
itself -- pytest only collects files matching test_*.py.
"""
from __future__ import annotations

from typing import Optional

from funes_hoard.hoard_link import BackendError, ChatResult, LinkConfig, Resolution, Unavailable, Usage


class FakeLink:
    """Duck-types the slice of `Link` the app actually calls.

    `resolution` controls what `resolve("llm")`/`status()` report;
    `chat_result` or `chat_error` control what `chat()` does. Both are
    mutable after construction so a test can flip availability mid-way.
    """

    def __init__(self, config: LinkConfig, resolution: Optional[Resolution] = None, chat_result: Optional[ChatResult] = None, chat_error: Optional[BaseException] = None):
        self.config = config
        self.resolution = resolution or Resolution(
            capability="llm", provider=None, url=None, model=None, api=None,
            state="unavailable", reason="no fake resolution configured", details={"reasons": ["no fake resolution configured"]},
        )
        self.chat_result = chat_result
        self.chat_error = chat_error
        self.closed = False
        self.chat_calls: list[list[dict]] = []

    async def resolve(self, capability: str) -> Resolution:
        return self.resolution

    async def status(self) -> dict:
        return {self.resolution.capability: self.resolution.to_dict()}

    async def chat(self, messages, **kwargs) -> ChatResult:
        self.chat_calls.append(messages)
        if self.chat_error is not None:
            raise self.chat_error
        if self.chat_result is not None:
            return self.chat_result
        raise Unavailable("llm", ["no fake chat_result configured"])

    async def wait_idle(self, capability: str, max_wait_s: float = 30.0) -> bool:
        return True

    async def aclose(self) -> None:
        self.closed = True


def resolved_llm(model: str = "qwen3.8-27b-q8-llamacpp", provider: str = "llamacpp") -> Resolution:
    return Resolution(
        capability="llm", provider=provider, url="http://127.0.0.1:8081/v1/chat/completions", model=model,
        api="openai", state="resolved", reason=f"llm -> {provider} at 127.0.0.1:8081 ({model}), shared loopback server; resident",
        details={"source": "loopback"},
    )


def chat_text(text: str, model: str = "qwen3.8-27b-q8-llamacpp") -> ChatResult:
    return ChatResult(text=text, model=model, provider="llamacpp", usage=Usage(), elapsed_ms=12.0)
