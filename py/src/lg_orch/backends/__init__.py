# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christian Meurer — https://github.com/christianmeurer/Lula
"""Checkpoint backend implementations for lg_orch.

Public API — import from here or from ``lg_orch.checkpointing`` (shim).
"""

from __future__ import annotations

from lg_orch.backends._base import (
    BaseCheckpointSaver,
    CheckpointBackendError,
    parse_config,
    resolve_checkpoint_db_path,
    stable_checkpoint_thread_id,
)
from lg_orch.backends.postgres import PostgresCheckpointSaver
from lg_orch.backends.redis import RedisCheckpointSaver
from lg_orch.backends.sqlite import SqliteCheckpointSaver


def create_checkpoint_saver(backend: str, **kwargs: object) -> BaseCheckpointSaver[object]:
    """Create a checkpoint saver for the given backend.

    Parameters
    ----------
    backend:
        One of ``"sqlite"``, ``"redis"``, or ``"postgres"``.
    **kwargs:
        Forwarded to the appropriate constructor:

        - ``sqlite``: ``db_path: Path``
        - ``redis``: ``redis_url: str``, ``key_prefix: str``, ``ttl_seconds: int``
        - ``postgres``: ``dsn: str``, ``table_name: str``
    """
    if backend == "sqlite":
        return SqliteCheckpointSaver(**kwargs)  # type: ignore[arg-type]
    if backend == "redis":
        return RedisCheckpointSaver(**kwargs)  # type: ignore[arg-type]
    if backend == "postgres":
        return PostgresCheckpointSaver(**kwargs)  # type: ignore[arg-type]
    raise ValueError(
        f"Unknown checkpoint backend: {backend!r}. Expected one of: 'sqlite', 'redis', 'postgres'."
    )


__all__ = [
    "BaseCheckpointSaver",
    "CheckpointBackendError",
    "PostgresCheckpointSaver",
    "RedisCheckpointSaver",
    "SqliteCheckpointSaver",
    "create_checkpoint_saver",
    "parse_config",
    "resolve_checkpoint_db_path",
    "stable_checkpoint_thread_id",
]
