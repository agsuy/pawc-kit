"""Integration tests that hit paid LLM APIs.

These are opt-in only: excluded from default runs by the ``llm_paid`` marker
and ``addopts = "-m 'not llm_paid'"`` in pyproject.toml.

Run explicitly with::

    scripts/test.sh -m llm_paid

Requires the ``openai`` package and ``OPENAI_API_KEY`` env var.
"""

from __future__ import annotations

import asyncio
import os

import pytest

from pawc_kit.contracts import RoleConfig
from pawc_kit.llm.roles import AsyncLLMExecutorRole
from tests.llm.conftest import make_exec_ctx

_OPENAI_KEY = os.environ.get("OPENAI_API_KEY", "")

_missing_openai = False
try:
    from openai import AsyncOpenAI as _AO  # noqa: F401  # pyright: ignore[reportMissingImports]
except ImportError:
    _missing_openai = True


@pytest.mark.llm_paid
@pytest.mark.skipif(not _OPENAI_KEY, reason="OPENAI_API_KEY not set")
@pytest.mark.skipif(_missing_openai, reason="openai package not installed")
def test_executor_produces_valid_output_with_role_config() -> None:
    """Smoke test: real paid LLM call with role_config."""
    from pawc_kit.llm.backend import BackendCapabilities, CompletionResult, TokenUsage

    class _OpenAIBackend:
        """Thin async wrapper for the openai client used only in this test."""

        def __init__(self, api_key: str, model: str) -> None:
            from openai import AsyncOpenAI  # pyright: ignore[reportMissingImports]

            self._client = AsyncOpenAI(api_key=api_key)
            self._model = model

        def capabilities(self) -> BackendCapabilities:
            return BackendCapabilities(supports_structured_output=False)

        async def complete(
            self,
            system: str,
            user: str,
            *,
            response_schema=None,
            max_tokens: int = 4096,
        ) -> CompletionResult:
            resp = await self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                max_tokens=max_tokens,
            )
            text = resp.choices[0].message.content or ""
            usage = None
            if resp.usage:
                usage = TokenUsage(
                    prompt_tokens=resp.usage.prompt_tokens,
                    completion_tokens=resp.usage.completion_tokens,
                    total_tokens=resp.usage.total_tokens,
                )
            return CompletionResult(text=text, usage=usage)

    backend = _OpenAIBackend(api_key=_OPENAI_KEY, model="gpt-4o-mini")
    cfg = RoleConfig(
        name="Test Executor",
        version="0.1.0",
        expertise=["testing"],
        guidelines=["Respond with valid structured output"],
    )
    role = AsyncLLMExecutorRole(backend=backend, role_configs={"worker-role": cfg})
    result = asyncio.run(role.execute(make_exec_ctx()))

    assert result.confidence_score >= 0
    assert result.confidence_score <= 100
    assert result.summary
    assert role.last_system_prompt is not None
    assert "Test Executor" in role.last_system_prompt
