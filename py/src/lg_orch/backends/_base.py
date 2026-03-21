# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christian Meurer — https://github.com/christianmeurer/Lula
"""Shared base types, helpers, and _parse_config for all checkpoint backends."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

from langchain_core.runnables.config import RunnableConfig
from langgraph.checkpoint.base import BaseCheckpointSaver  # re-exported for convenience


class CheckpointBackendError(RuntimeError):
    """Raised when the checkpoint backend fails in a non-recoverable way."""


def resolve_checkpoint_db_path(*, repo_root: Path, db_path: str) -> Path:
    candidate = Path(db_path)
    if candidate.is_absolute():
        return candidate.resolve()
    return (repo_root / candidate).resolve()


def stable_checkpoint_thread_id(*, request: str, thread_prefix: str, provided: str | None) -> str:
    if provided is not None:
        text = provided.strip()
        if text:
            return text
    digest = hashlib.sha256(request.encode("utf-8", errors="replace")).hexdigest()[:16]
    prefix = thread_prefix.strip() or "lg-orch"
    return f"{prefix}-{digest}"


def parse_config(config: RunnableConfig) -> tuple[str, str, str | None]:
    """Extract (thread_id, checkpoint_ns, checkpoint_id) from a RunnableConfig."""
    configurable = config.get("configurable", {})
    if not isinstance(configurable, dict):
        raise ValueError("configurable must be a dict")

    thread_id = configurable.get("thread_id")
    if not isinstance(thread_id, str) or not thread_id.strip():
        raise ValueError("missing configurable.thread_id")

    checkpoint_ns_raw = configurable.get("checkpoint_ns", "")
    checkpoint_ns = str(checkpoint_ns_raw)

    checkpoint_id_raw = configurable.get("checkpoint_id")
    checkpoint_id = str(checkpoint_id_raw) if checkpoint_id_raw is not None else None
    return thread_id.strip(), checkpoint_ns, checkpoint_id


__all__ = [
    "BaseCheckpointSaver",
    "CheckpointBackendError",
    "parse_config",
    "resolve_checkpoint_db_path",
    "stable_checkpoint_thread_id",
]


def _dump_typed_mixin(serde: Any, value: Any) -> tuple[str, bytes]:
    type_tag, payload = serde.dumps_typed(value)
    return str(type_tag), bytes(payload)


def _load_typed_mixin(serde: Any, *, type_tag: str, payload: bytes) -> Any:
    return serde.loads_typed((type_tag, payload))
