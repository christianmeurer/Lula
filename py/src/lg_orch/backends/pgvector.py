# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christian Meurer — https://github.com/christianmeurer/Lula
"""PostgreSQL + pgvector backend for long-term memory.

Requires: psycopg[binary] and a PostgreSQL instance with pgvector extension.
Activated via: LG_MEMORY_BACKEND=pgvector LG_MEMORY_PGVECTOR_DSN=postgresql://...
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import structlog

_log = structlog.get_logger(__name__)

EmbedderFn = Callable[[str], list[float]]


@dataclass
class PgMemoryRecord:
    """A single record returned from the pgvector memory store."""

    id: int | None
    content: str
    metadata: dict[str, Any]
    created_at: float
    distance: float | None = None


class PgVectorMemoryStore:
    """PostgreSQL + pgvector backed long-term memory store.

    Implements the same interface as ``LongTermMemoryStore`` (store_semantic,
    search_semantic, close) but uses PostgreSQL with the pgvector extension for
    indexed vector similarity search.

    Parameters
    ----------
    dsn:
        PostgreSQL connection string (e.g. ``postgresql://user:pass@host:5432/db``).
    embedder:
        A callable that takes a string and returns a list of floats.
    embedding_dim:
        Dimensionality of the embedding vectors (default: 128).
    table_name:
        Name of the table to store memories in (default: ``semantic_memories``).
    """

    def __init__(
        self,
        dsn: str,
        embedder: EmbedderFn,
        embedding_dim: int = 128,
        table_name: str = "semantic_memories",
    ) -> None:
        import psycopg  # type: ignore[import-not-found]

        self._dsn = dsn
        self._embedder = embedder
        self._embedding_dim = embedding_dim
        self._table_name = table_name
        self._lock = threading.Lock()
        self._conn = psycopg.connect(dsn, autocommit=True)
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        """Create the pgvector extension and table if they don't exist."""
        with self._lock, self._conn.cursor() as cur:
            cur.execute("CREATE EXTENSION IF NOT EXISTS vector")
            cur.execute(
                f"""
                    CREATE TABLE IF NOT EXISTS {self._table_name} (
                        id         BIGSERIAL PRIMARY KEY,
                        content    TEXT NOT NULL,
                        metadata   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding  vector({self._embedding_dim}) NOT NULL,
                        created_at DOUBLE PRECISION NOT NULL
                    )
                    """
            )

    def store_semantic(
        self,
        content: str,
        metadata: dict[str, Any] | None = None,
        embedding: list[float] | None = None,
    ) -> int:
        """Insert a semantic memory. Returns the row id.

        If *embedding* is not provided, the configured embedder is called on
        *content* to generate one.
        """
        meta_json = json.dumps(metadata or {}, ensure_ascii=False, sort_keys=True)
        vec = embedding if embedding is not None else self._embedder(content)
        now = time.time()
        vec_str = f"[{','.join(str(v) for v in vec)}]"
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                f"""
                    INSERT INTO {self._table_name} (content, metadata, embedding, created_at)
                    VALUES (%s, %s::jsonb, %s::vector, %s)
                    RETURNING id
                    """,
                (content, meta_json, vec_str, now),
            )
            row = cur.fetchone()
            assert row is not None
            return int(row[0])

    def search_semantic(
        self,
        query_text: str,
        top_k: int = 5,
    ) -> list[PgMemoryRecord]:
        """Vector similarity search using pgvector's cosine distance operator.

        Returns up to *top_k* records ordered by ascending cosine distance
        (i.e. most similar first).
        """
        top_k = max(1, top_k)
        query_vec = self._embedder(query_text)
        vec_str = f"[{','.join(str(v) for v in query_vec)}]"
        with self._lock, self._conn.cursor() as cur:
            cur.execute(
                f"""
                    SELECT id, content, metadata, created_at,
                           embedding <=> %s::vector AS distance
                    FROM {self._table_name}
                    ORDER BY distance ASC
                    LIMIT %s
                    """,
                (vec_str, top_k),
            )
            rows = cur.fetchall()

        results: list[PgMemoryRecord] = []
        for row in rows:
            try:
                meta = json.loads(row[2]) if isinstance(row[2], str) else row[2]
            except (json.JSONDecodeError, TypeError):
                meta = {}
            results.append(
                PgMemoryRecord(
                    id=int(row[0]),
                    content=str(row[1]),
                    metadata=meta if isinstance(meta, dict) else {},
                    created_at=float(row[3]),
                    distance=float(row[4]) if row[4] is not None else None,
                )
            )
        return results

    def close(self) -> None:
        """Close the underlying database connection."""
        with self._lock:
            self._conn.close()


__all__ = [
    "PgMemoryRecord",
    "PgVectorMemoryStore",
]
