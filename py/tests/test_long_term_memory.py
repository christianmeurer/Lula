"""Tests for lg_orch.long_term_memory (Wave 9 – Tripartite Persistent Memory)."""
from __future__ import annotations

import time

import numpy as np
import pytest

from lg_orch.long_term_memory import LongTermMemoryStore, MemoryRecord, stub_embedder


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(tmp_path: pytest.TempPathFactory) -> LongTermMemoryStore:
    return LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))


# ---------------------------------------------------------------------------
# stub_embedder
# ---------------------------------------------------------------------------


def test_stub_embedder_is_deterministic() -> None:
    v1 = stub_embedder("hello world")
    v2 = stub_embedder("hello world")
    assert np.allclose(v1, v2), "same input must yield identical vectors"


def test_stub_embedder_different_strings_differ() -> None:
    v1 = stub_embedder("deploy script is at scripts/do_deploy.sh")
    v2 = stub_embedder("the database password is stored in .env")
    assert not np.allclose(v1, v2), "different strings must produce different vectors"


def test_stub_embedder_unit_norm() -> None:
    v = stub_embedder("unit norm check", dim=64)
    assert abs(float(np.linalg.norm(v)) - 1.0) < 1e-5


# ---------------------------------------------------------------------------
# Semantic tier
# ---------------------------------------------------------------------------


def test_store_and_search_semantic(tmp_path: pytest.TempPathFactory) -> None:
    """Store facts with a controlled embedder; verify top-1 is the closest fact."""

    def _embedder(text: str) -> "np.ndarray[object, np.dtype[np.float32]]":
        # Deploy-related text → dimension 0 dominant
        # Credential-related text → dimension 1 dominant
        # Test-related text → dimension 2 dominant
        v = np.zeros(128, dtype=np.float32)
        tl = text.lower()
        if "deploy" in tl:
            v[0] = 1.0
        elif "credential" in tl or ".env" in tl or "database" in tl or "password" in tl:
            v[1] = 1.0
        else:
            v[2] = 1.0
        return v

    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"), embedder=_embedder)
    store.store_semantic("the deploy script is at scripts/do_deploy.sh")
    store.store_semantic("database credentials are in .env")
    store.store_semantic("the test suite uses pytest")

    results = store.search_semantic("deploy script location", top_k=3)
    assert len(results) >= 1
    assert isinstance(results[0], MemoryRecord)
    assert results[0].tier == "semantic"
    # The most relevant result should mention "deploy"
    assert "deploy" in results[0].content.lower()
    store.close()


def test_semantic_search_cosine_ordering(tmp_path: pytest.TempPathFactory) -> None:
    """Two facts: one closely related to the query, one unrelated. Verify rank."""

    def _controlled_embedder(text: str) -> "np.ndarray[object, np.dtype[np.float32]]":
        # Embed "deploy" queries toward [1,0,...] and others toward [0,1,...]
        if "deploy" in text.lower():
            v = np.zeros(128, dtype=np.float32)
            v[0] = 1.0
        else:
            v = np.zeros(128, dtype=np.float32)
            v[1] = 1.0
        return v

    store = LongTermMemoryStore(
        db_path=str(tmp_path / "ltm2.db"),
        embedder=_controlled_embedder,
    )
    store.store_semantic("deploy script is at scripts/do_deploy.sh")
    store.store_semantic("the database password is stored in .env")

    results = store.search_semantic("deploy the application", top_k=2)
    assert len(results) == 2
    assert "deploy" in results[0].content.lower(), (
        "deploy fact must rank first for a deploy query"
    )
    store.close()


# ---------------------------------------------------------------------------
# Episodic tier
# ---------------------------------------------------------------------------


def test_store_and_get_episodes(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    store.store_episode("run-001", "Fixed import error in handler.py", "success")
    time.sleep(0.01)
    store.store_episode("run-002", "Repaired failing test in test_calculator.py", "success")

    episodes = store.get_episodes(limit=10)
    assert len(episodes) == 2
    # Reverse-chronological: most recent first
    assert "run-002" in (episodes[0].run_id or "")
    assert "run-001" in (episodes[1].run_id or "")
    store.close()


def test_get_episodes_filtered_by_run_id(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    store.store_episode("run-A", "summary A1", "success")
    store.store_episode("run-A", "summary A2", "failure")
    store.store_episode("run-B", "summary B1", "success")

    episodes = store.get_episodes(limit=20, run_id="run-A")
    assert len(episodes) == 2
    assert all(ep.run_id == "run-A" for ep in episodes)
    store.close()


# ---------------------------------------------------------------------------
# Procedural tier
# ---------------------------------------------------------------------------


def test_store_and_get_procedures(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    store.store_procedure("test_repair", ["read_file", "apply_patch", "run_tests"], success=True)
    store.store_procedure("test_repair", ["read_file", "run_tests"], success=False)

    # successful_only=True should return only the first
    procs = store.get_procedures("test_repair", successful_only=True)
    assert len(procs) == 1
    assert "apply_patch" in procs[0].content

    # successful_only=False should return both
    all_procs = store.get_procedures("test_repair", successful_only=False)
    assert len(all_procs) == 2
    store.close()


# ---------------------------------------------------------------------------
# Cross-tier retrieval
# ---------------------------------------------------------------------------


def test_retrieve_for_context_respects_token_budget(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))

    # Insert many large records across all tiers
    for i in range(20):
        store.store_semantic(f"semantic fact {i}: " + "x" * 200)
        store.store_episode(f"run-{i:03d}", "summary " + "y" * 200, "success")
        store.store_procedure("deploy", ["step_" + str(j) for j in range(10)], success=True)

    max_tokens = 500
    result = store.retrieve_for_context("semantic fact query", max_tokens=max_tokens)

    # Approximate token count: len(result) / 4 should be <= max_tokens (with small margin)
    approx_tokens = len(result) // 4
    assert approx_tokens <= max_tokens + 10, (
        f"result too long: {approx_tokens} tokens (budget {max_tokens})"
    )
    store.close()


def test_retrieve_for_context_contains_all_tiers(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    store.store_semantic("deploy script is scripts/do_deploy.sh")
    store.store_episode("run-001", "deployed successfully", "success")
    store.store_procedure("deploy", ["read_file", "apply_patch"], success=True)

    result = store.retrieve_for_context("deploy", max_tokens=2000)
    assert "[long_term:semantic]" in result
    assert "[long_term:episodic]" in result
    store.close()


def test_retrieve_for_context_empty_store_returns_empty(tmp_path: pytest.TempPathFactory) -> None:
    store = LongTermMemoryStore(db_path=str(tmp_path / "ltm.db"))
    result = store.retrieve_for_context("anything", max_tokens=1000)
    assert result == ""
    store.close()
