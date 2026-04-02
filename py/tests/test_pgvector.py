# SPDX-License-Identifier: MIT
"""Tests for the pgvector long-term memory backend."""

from __future__ import annotations

import os
import sys
from unittest.mock import MagicMock, patch

import pytest

from lg_orch.backends.pgvector import PgMemoryRecord, PgVectorMemoryStore

# ---------------------------------------------------------------------------
# Mocked tests — verify SQL generation and interface without a real database
# ---------------------------------------------------------------------------


def _stub_embedder(text: str) -> list[float]:
    """Deterministic stub that returns a 4-dim vector."""
    return [0.1, 0.2, 0.3, 0.4]


def _make_mock_psycopg() -> tuple[MagicMock, MagicMock, MagicMock]:
    """Create a mock psycopg module with mock connection and cursor.

    Returns (mock_module, _mock_conn, mock_cursor).
    Callers that don't need all three should unpack with underscore prefixes.
    """
    mock_module = MagicMock()
    mock_conn = MagicMock()
    mock_cursor = MagicMock()
    mock_conn.cursor.return_value.__enter__ = MagicMock(return_value=mock_cursor)
    mock_conn.cursor.return_value.__exit__ = MagicMock(return_value=False)
    mock_module.connect.return_value = mock_conn
    return mock_module, mock_conn, mock_cursor


class TestPgVectorMemoryStoreMocked:
    """Tests using a mocked psycopg connection."""

    def test_ensure_schema_creates_extension_and_table(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            _store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
        calls = mock_cursor.execute.call_args_list
        sql_texts = [str(c[0][0]) for c in calls]
        assert any("CREATE EXTENSION" in s and "vector" in s for s in sql_texts)
        assert any("CREATE TABLE" in s and "vector(4)" in s for s in sql_texts)

    def test_store_semantic_inserts_row(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchone.return_value = (42,)
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            row_id = store.store_semantic("hello world", metadata={"source": "test"})
        assert row_id == 42
        insert_calls = [
            c for c in mock_cursor.execute.call_args_list if "INSERT INTO" in str(c[0][0])
        ]
        assert len(insert_calls) >= 1
        sql = str(insert_calls[-1][0][0])
        assert "RETURNING id" in sql

    def test_store_semantic_with_explicit_embedding(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchone.return_value = (7,)
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            row_id = store.store_semantic("test", embedding=[0.5, 0.5, 0.5, 0.5])
        assert row_id == 7

    def test_search_semantic_returns_records(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = [
            (1, "hello", {"source": "test"}, 1700000000.0, 0.1),
            (2, "world", {}, 1700000001.0, 0.5),
        ]
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            results = store.search_semantic("hello", top_k=2)
        assert len(results) == 2
        assert isinstance(results[0], PgMemoryRecord)
        assert results[0].content == "hello"
        assert results[0].distance == pytest.approx(0.1)
        assert results[1].content == "world"

        select_calls = [c for c in mock_cursor.execute.call_args_list if "<=>" in str(c[0][0])]
        assert len(select_calls) >= 1

    def test_search_semantic_empty_result(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = []
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            results = store.search_semantic("nonexistent")
        assert results == []

    def test_search_semantic_top_k_minimum_is_one(self) -> None:
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = []
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            store.search_semantic("test", top_k=0)
        select_calls = [c for c in mock_cursor.execute.call_args_list if "LIMIT" in str(c[0][0])]
        assert len(select_calls) >= 1
        args = select_calls[-1][0][1]
        assert args[-1] == 1

    def test_close_closes_connection(self) -> None:
        mock_module, mock_conn, _mock_cursor = _make_mock_psycopg()
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            store.close()
        mock_conn.close.assert_called_once()

    def test_search_handles_string_metadata(self) -> None:
        """When metadata is returned as a JSON string, it should be parsed."""
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = [
            (1, "hello", '{"key": "val"}', 1700000000.0, 0.1),
        ]
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            results = store.search_semantic("hello")
        assert results[0].metadata == {"key": "val"}

    def test_search_handles_invalid_metadata(self) -> None:
        """When metadata cannot be parsed, fallback to empty dict."""
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = [
            (1, "hello", "not-valid-json{", 1700000000.0, 0.1),
        ]
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            results = store.search_semantic("hello")
        assert results[0].metadata == {}

    def test_search_handles_none_distance(self) -> None:
        """When distance is None, record distance should be None."""
        mock_module, _mock_conn, mock_cursor = _make_mock_psycopg()
        mock_cursor.fetchall.return_value = [
            (1, "hello", {}, 1700000000.0, None),
        ]
        with patch.dict(sys.modules, {"psycopg": mock_module}):
            store = PgVectorMemoryStore(
                dsn="postgresql://test@localhost/db",
                embedder=_stub_embedder,
                embedding_dim=4,
            )
            results = store.search_semantic("hello")
        assert results[0].distance is None


class TestPgMemoryRecord:
    def test_fields(self) -> None:
        rec = PgMemoryRecord(
            id=1,
            content="hello",
            metadata={"key": "val"},
            created_at=1700000000.0,
            distance=0.1,
        )
        assert rec.id == 1
        assert rec.content == "hello"
        assert rec.metadata == {"key": "val"}
        assert rec.created_at == 1700000000.0
        assert rec.distance == 0.1

    def test_defaults(self) -> None:
        rec = PgMemoryRecord(id=None, content="", metadata={}, created_at=0.0)
        assert rec.distance is None


# ---------------------------------------------------------------------------
# Integration tests — skip unless LG_TEST_PGVECTOR_DSN is set
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    not os.environ.get("LG_TEST_PGVECTOR_DSN"),
    reason="pgvector not configured",
)
class TestPgVectorMemoryStoreIntegration:
    def test_round_trip(self) -> None:
        dsn = os.environ["LG_TEST_PGVECTOR_DSN"]
        store = PgVectorMemoryStore(
            dsn=dsn,
            embedder=_stub_embedder,
            embedding_dim=4,
            table_name="test_pgvector_integration",
        )
        try:
            row_id = store.store_semantic("hello world", metadata={"source": "test"})
            assert isinstance(row_id, int)
            results = store.search_semantic("hello", top_k=1)
            assert len(results) >= 1
            assert results[0].content == "hello world"
        finally:
            store.close()
