"""pawc_kit: workflow orchestration library.

Canonical public API lives in subpackages (``pawc_kit.contracts``, ``pawc_kit.ports``,
``pawc_kit.workflow``, ``pawc_kit.llm``, ``pawc_kit.adapters``, ``pawc_kit.context``).
This root module exposes a small entry-point surface only.
"""

import logging

from pawc_kit._time import utc_now
from pawc_kit.async_session import AsyncWorkflowSession
from pawc_kit.config import load_role_config, load_root_config, load_yaml_config
from pawc_kit.session import WorkflowSession

__version__ = "0.4.0"

logging.getLogger("pawc_kit").addHandler(logging.NullHandler())

__all__ = [
    "__version__",
    "AsyncWorkflowSession",
    "WorkflowSession",
    "load_role_config",
    "load_root_config",
    "load_yaml_config",
    "utc_now",
]
