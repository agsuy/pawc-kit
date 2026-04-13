"""Unit tests: role_config prompt wiring using MockBackend (no API calls)."""

from __future__ import annotations

import asyncio

from pawc_kit.contracts import RoleConfig
from pawc_kit.llm.mock import AsyncMockBackend, MockBackend
from pawc_kit.llm.roles import (
    AsyncLLMExecutorRole,
    AsyncLLMReviewerRole,
    LLMExecutorRole,
    LLMReviewerRole,
)
from tests.llm.conftest import make_exec_ctx, make_review_ctx


def _sec(name: str, content: str = "") -> str:
    return f'<pawc-section name="{name}">{content}</pawc-section>'


def _executor_md() -> str:
    return (
        _sec("CONFIDENCE", "\n90\n")
        + _sec("SUMMARY", "\nDone\n")
        + _sec("HANDOFF", "\nhandoff\n")
        + _sec("ARTIFACTS")
    )


def _reviewer_md() -> str:
    return (
        _sec("DECISION", "\nAPPROVE\n")
        + _sec("CONFIDENCE", "\n88\n")
        + _sec("COUNTS_VERIFIED", "\ntrue\n")
        + _sec("SUMMARY", "\nGood\n")
        + _sec("FINDINGS")
        + _sec("TARGET_PHASE")
    )


# ---------------------------------------------------------------------------
# Sync executor: role_config appears in system prompt
# ---------------------------------------------------------------------------


class TestSyncExecutorRoleConfigInPrompt:
    def test_role_config_fields_appear_in_system_prompt(self) -> None:
        backend = MockBackend()
        backend.queue(_executor_md())
        cfg = RoleConfig(
            name="Alpha Executor",
            version="0.1.0",
            expertise=["implementation", "architecture"],
            guidelines=["Always justify decisions"],
        )
        role = LLMExecutorRole(backend, role_configs={"worker-role": cfg})
        role.execute(make_exec_ctx())

        assert backend.last_system is not None
        assert "Alpha Executor" in backend.last_system
        assert "implementation" in backend.last_system
        assert "Always justify decisions" in backend.last_system

    def test_empty_role_configs_omit_role_section(self) -> None:
        backend = MockBackend()
        backend.queue(_executor_md())
        role = LLMExecutorRole(backend, role_configs={})
        role.execute(make_exec_ctx())

        assert backend.last_system is not None
        assert "Alpha Executor" not in backend.last_system

    def test_last_prompt_attributes_match_backend(self) -> None:
        backend = MockBackend()
        backend.queue(_executor_md())
        cfg = RoleConfig(name="Exec", version="0.1.0")
        role = LLMExecutorRole(backend, role_configs={"worker-role": cfg})
        role.execute(make_exec_ctx())

        assert role.last_system_prompt == backend.last_system
        assert role.last_user_prompt == backend.last_user


# ---------------------------------------------------------------------------
# Sync reviewer: role_config appears in system prompt
# ---------------------------------------------------------------------------


class TestSyncReviewerRoleConfigInPrompt:
    def test_role_config_fields_appear_in_system_prompt(self) -> None:
        backend = MockBackend()
        backend.queue(_reviewer_md())
        cfg = RoleConfig(
            name="Quality Reviewer",
            version="0.2.0",
            expertise=["quality-assurance"],
            review_criteria=["Meets requirements"],
        )
        role = LLMReviewerRole(backend, role_configs={"reviewer-role": cfg})
        role.review(make_review_ctx())

        assert backend.last_system is not None
        assert "Quality Reviewer" in backend.last_system
        assert "Meets requirements" in backend.last_system

    def test_last_prompt_attributes_match_backend(self) -> None:
        backend = MockBackend()
        backend.queue(_reviewer_md())
        cfg = RoleConfig(name="Rev", version="0.1.0")
        role = LLMReviewerRole(backend, role_configs={"reviewer-role": cfg})
        role.review(make_review_ctx())

        assert role.last_system_prompt == backend.last_system
        assert role.last_user_prompt == backend.last_user


# ---------------------------------------------------------------------------
# Async executor: role_config appears in system prompt
# ---------------------------------------------------------------------------


class TestAsyncExecutorRoleConfigInPrompt:
    def test_role_config_fields_appear_in_system_prompt(self) -> None:
        backend = AsyncMockBackend()
        backend.queue(_executor_md())
        cfg = RoleConfig(
            name="Async Executor",
            version="1.0.0",
            focus=["correctness"],
            guidelines=["Check edge cases"],
        )
        role = AsyncLLMExecutorRole(backend, role_configs={"worker-role": cfg})
        asyncio.run(role.execute(make_exec_ctx()))

        assert backend.last_system is not None
        assert "Async Executor" in backend.last_system
        assert "correctness" in backend.last_system
        assert "Check edge cases" in backend.last_system

    def test_last_prompt_attributes_match_backend(self) -> None:
        backend = AsyncMockBackend()
        backend.queue(_executor_md())
        cfg = RoleConfig(name="AExec", version="0.1.0")
        role = AsyncLLMExecutorRole(backend, role_configs={"worker-role": cfg})
        asyncio.run(role.execute(make_exec_ctx()))

        assert role.last_system_prompt == backend.last_system
        assert role.last_user_prompt == backend.last_user


# ---------------------------------------------------------------------------
# Async reviewer: role_config appears in system prompt
# ---------------------------------------------------------------------------


class TestAsyncReviewerRoleConfigInPrompt:
    def test_role_config_fields_appear_in_system_prompt(self) -> None:
        backend = AsyncMockBackend()
        backend.queue(_reviewer_md())
        cfg = RoleConfig(
            name="Async Reviewer",
            version="2.0.0",
            review_criteria=["All tests pass", "Coverage above 80%"],
        )
        role = AsyncLLMReviewerRole(backend, role_configs={"reviewer-role": cfg})
        asyncio.run(role.review(make_review_ctx()))

        assert backend.last_system is not None
        assert "Async Reviewer" in backend.last_system
        assert "All tests pass" in backend.last_system

    def test_last_prompt_attributes_match_backend(self) -> None:
        backend = AsyncMockBackend()
        backend.queue(_reviewer_md())
        cfg = RoleConfig(name="ARev", version="0.1.0")
        role = AsyncLLMReviewerRole(backend, role_configs={"reviewer-role": cfg})
        asyncio.run(role.review(make_review_ctx()))

        assert role.last_system_prompt == backend.last_system
        assert role.last_user_prompt == backend.last_user


# ---------------------------------------------------------------------------
# role_overrides merge on top of base config
# ---------------------------------------------------------------------------


class TestRoleOverridesMerge:
    def test_phase_overrides_merge_into_role_config(self) -> None:
        backend = MockBackend()
        backend.queue(_executor_md())
        base = RoleConfig(
            name="Base Exec",
            version="0.1.0",
            expertise=["general"],
            guidelines=["Be thorough"],
        )
        role = LLMExecutorRole(backend, role_configs={"worker-role": base})
        req = make_exec_ctx(role_overrides={"focus": ["speed"]})
        role.execute(req)

        assert backend.last_system is not None
        assert "Base Exec" in backend.last_system
        assert "speed" in backend.last_system
