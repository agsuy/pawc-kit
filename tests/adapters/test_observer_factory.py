"""Tests for ObservabilityConfig, the observer factory, and session auto-construction."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from pawc_kit.adapters.factory import build_async_observer, build_sync_observer
from pawc_kit.adapters.logging import AsyncLoggingWorkflowObserver, LoggingWorkflowObserver
from pawc_kit.adapters.otel import (
    AsyncOpenTelemetryWorkflowObserver,
    OpenTelemetryWorkflowObserver,
)
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.contracts.config import (
    ObservabilityConfig,
    PhaseDefConfig,
    RootConfig,
    SkillConfig,
    WorkflowConfig,
)
from pawc_kit.contracts.errors import ConfigurationError
from pawc_kit.session import WorkflowSession

# ---------------------------------------------------------------------------
# ObservabilityConfig defaults and validation
# ---------------------------------------------------------------------------


def test_observability_config_defaults() -> None:
    cfg = ObservabilityConfig.model_validate({})
    assert cfg.observer == "none"
    assert cfg.meter_name == "pawc_kit.workflow"
    assert cfg.tracer_name == "pawc_kit.workflow"
    assert cfg.logger_name == "pawc_kit.workflow"


def test_observability_config_explicit_values() -> None:
    cfg = ObservabilityConfig(
        observer="otel",
        meter_name="my.meter",
        tracer_name="my.tracer",
        logger_name="my.logger",
    )
    assert cfg.observer == "otel"
    assert cfg.meter_name == "my.meter"
    assert cfg.tracer_name == "my.tracer"
    assert cfg.logger_name == "my.logger"


def test_observability_config_rejects_unknown_observer() -> None:
    with pytest.raises(Exception):
        ObservabilityConfig(observer="unknown")  # type: ignore[arg-type]


def test_root_config_includes_observability() -> None:
    cfg = RootConfig(skill=SkillConfig(name="s", version="1.0.0"))
    assert cfg.observability.observer == "none"


# ---------------------------------------------------------------------------
# Factory -- build_sync_observer
# ---------------------------------------------------------------------------


def test_factory_sync_none_returns_none() -> None:
    cfg = ObservabilityConfig(observer="none")
    assert build_sync_observer(cfg) is None


def test_factory_sync_logging_returns_logging_observer() -> None:
    cfg = ObservabilityConfig(observer="logging")
    obs = build_sync_observer(cfg)
    assert isinstance(obs, LoggingWorkflowObserver)


def test_factory_sync_logging_uses_logger_name() -> None:
    cfg = ObservabilityConfig(observer="logging", logger_name="custom.logger")
    obs = build_sync_observer(cfg)
    assert isinstance(obs, LoggingWorkflowObserver)
    assert obs._logger.name == "custom.logger"


def test_factory_sync_otel_returns_otel_observer() -> None:
    cfg = ObservabilityConfig(observer="otel")
    obs = build_sync_observer(cfg)
    assert isinstance(obs, OpenTelemetryWorkflowObserver)


def test_factory_sync_otel_raises_when_not_installed() -> None:
    cfg = ObservabilityConfig(observer="otel")
    with patch.dict(sys.modules, {"opentelemetry": None, "opentelemetry.metrics": None}):
        # Re-import factory to exercise the ImportError path

        import pawc_kit.adapters.otel as otel_module

        original_init = otel_module.OpenTelemetryWorkflowObserver.__init__

        def _fail_init(self, **kwargs):
            raise ImportError("mocked missing otel")

        otel_module.OpenTelemetryWorkflowObserver.__init__ = _fail_init  # type: ignore[method-assign]
        try:
            with pytest.raises((ImportError, ConfigurationError)):
                build_sync_observer(cfg)
        finally:
            otel_module.OpenTelemetryWorkflowObserver.__init__ = original_init  # type: ignore[method-assign]


# ---------------------------------------------------------------------------
# Factory -- build_async_observer
# ---------------------------------------------------------------------------


def test_factory_async_none_returns_none() -> None:
    cfg = ObservabilityConfig(observer="none")
    assert build_async_observer(cfg) is None


def test_factory_async_logging_returns_async_logging_observer() -> None:
    cfg = ObservabilityConfig(observer="logging")
    obs = build_async_observer(cfg)
    assert isinstance(obs, AsyncLoggingWorkflowObserver)


def test_factory_async_otel_returns_async_otel_observer() -> None:
    cfg = ObservabilityConfig(observer="otel")
    obs = build_async_observer(cfg)
    assert isinstance(obs, AsyncOpenTelemetryWorkflowObserver)


# ---------------------------------------------------------------------------
# Session -- auto-construction from config.observability
# ---------------------------------------------------------------------------


def _minimal_config(*, observer: str = "none") -> RootConfig:
    obs_cfg = ObservabilityConfig(observer=observer)
    phases = [
        PhaseDefConfig(phase_id="work", role_id="worker", kind="executor", on_complete=[]),
    ]
    wf = WorkflowConfig(phases=phases)
    return RootConfig(
        skill=SkillConfig(name="test-skill", version="1.0.0"),
        workflow=wf,
        observability=obs_cfg,
    )


def test_session_omitted_observer_uses_none_config() -> None:
    session = WorkflowSession(config=_minimal_config(observer="none"))
    assert session._observer is None


def test_session_omitted_observer_auto_constructs_from_logging_config() -> None:
    session = WorkflowSession(config=_minimal_config(observer="logging"))
    assert isinstance(session._observer, LoggingWorkflowObserver)


def test_session_omitted_observer_auto_constructs_from_otel_config() -> None:
    session = WorkflowSession(config=_minimal_config(observer="otel"))
    assert isinstance(session._observer, OpenTelemetryWorkflowObserver)


def test_session_explicit_none_overrides_config_observer() -> None:
    session = WorkflowSession(config=_minimal_config(observer="logging"), observer=None)
    assert session._observer is None


def test_session_explicit_instance_overrides_config_observer() -> None:
    custom_obs = LoggingWorkflowObserver()
    session = WorkflowSession(config=_minimal_config(observer="otel"), observer=custom_obs)
    assert session._observer is custom_obs


# ---------------------------------------------------------------------------
# AsyncWorkflowSession -- same observer resolution
# ---------------------------------------------------------------------------


def test_async_session_omitted_observer_uses_none_config() -> None:
    session = AsyncWorkflowSession(config=_minimal_config(observer="none"))
    assert session._observer is None


def test_async_session_omitted_observer_auto_constructs_from_logging_config() -> None:
    session = AsyncWorkflowSession(config=_minimal_config(observer="logging"))
    assert isinstance(session._observer, AsyncLoggingWorkflowObserver)


def test_async_session_omitted_observer_auto_constructs_from_otel_config() -> None:
    session = AsyncWorkflowSession(config=_minimal_config(observer="otel"))
    assert isinstance(session._observer, AsyncOpenTelemetryWorkflowObserver)


def test_async_session_explicit_none_overrides_config_observer() -> None:
    session = AsyncWorkflowSession(config=_minimal_config(observer="logging"), observer=None)
    assert session._observer is None


def test_async_session_explicit_instance_overrides_config_observer() -> None:
    custom_obs = AsyncLoggingWorkflowObserver()
    session = AsyncWorkflowSession(config=_minimal_config(observer="otel"), observer=custom_obs)
    assert session._observer is custom_obs
