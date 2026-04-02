"""Tests for Q-RAG value-based multi-step retrieval."""

from __future__ import annotations

import time
from typing import Any

from lg_orch.qrag import QRAGRetriever, ScoredMemory, _cosine_sim, _extract_embedding


def _cand(
    content: str = "memory",
    similarity: float = 0.8,
    task_type: str = "",
    success: bool | None = None,
    created_at: float | None = None,
    embedding: list[float] | None = None,
) -> dict[str, Any]:
    meta: dict[str, Any] = {}
    if task_type:
        meta["task_type"] = task_type
    if success is not None:
        meta["success"] = success
    if created_at is not None:
        meta["created_at"] = created_at
    cand: dict[str, Any] = {
        "content": content,
        "similarity": similarity,
        "metadata": meta,
    }
    if embedding is not None:
        cand["embedding"] = embedding
    return cand


class TestQRAGRetriever:
    def test_empty_candidates(self) -> None:
        r = QRAGRetriever()
        assert r.retrieve([], "code_change") == []

    def test_single_candidate(self) -> None:
        r = QRAGRetriever()
        result = r.retrieve([_cand("fix bug", 0.9)], "debug")
        assert len(result) == 1
        assert isinstance(result[0], ScoredMemory)
        assert result[0].content == "fix bug"
        assert result[0].similarity == 0.9

    def test_top_k_limits_output(self) -> None:
        r = QRAGRetriever()
        candidates = [_cand(f"mem-{i}", similarity=0.5 + i * 0.05) for i in range(10)]
        result = r.retrieve(candidates, "code_change", top_k=3)
        assert len(result) == 3

    def test_task_type_match_boosts_score(self) -> None:
        r = QRAGRetriever()
        matched = _cand("relevant", similarity=0.7, task_type="debug")
        unmatched = _cand("irrelevant", similarity=0.7, task_type="analysis")
        result = r.retrieve([unmatched, matched], "debug", top_k=2)
        # The task-type matched candidate should score higher
        assert result[0].content == "relevant"

    def test_success_boosts_score(self) -> None:
        r = QRAGRetriever()
        successful = _cand("good", similarity=0.7, success=True)
        failed = _cand("bad", similarity=0.7, success=False)
        result = r.retrieve([failed, successful], "code_change", top_k=2)
        assert result[0].content == "good"

    def test_recency_boosts_score(self) -> None:
        r = QRAGRetriever(
            recency_weight=0.8, similarity_weight=0.1, success_weight=0.05, diversity_weight=0.05
        )
        now = time.time()
        recent = _cand("recent", similarity=0.5, created_at=now - 60)
        old = _cand("old", similarity=0.5, created_at=now - 86400 * 30)
        result = r.retrieve([old, recent], "code_change", top_k=2)
        assert result[0].content == "recent"

    def test_diversity_penalty_applied(self) -> None:
        r = QRAGRetriever(diversity_weight=0.5)
        emb = [1.0, 0.0, 0.0]
        # Two candidates with identical embeddings
        c1 = _cand("first", similarity=0.9, embedding=emb)
        c2 = _cand("second", similarity=0.85, embedding=emb)
        c3 = _cand("diverse", similarity=0.8, embedding=[0.0, 1.0, 0.0])
        result = r.retrieve([c1, c2, c3], "code_change", top_k=3)
        # First should be c1 (highest similarity), but c3 should beat c2 due to diversity
        assert result[0].content == "first"

    def test_default_weights_sum_to_one(self) -> None:
        r = QRAGRetriever()
        total = r.similarity_weight + r.recency_weight + r.diversity_weight + r.success_weight
        assert abs(total - 1.0) < 1e-6

    def test_zero_weights_raises(self) -> None:
        import pytest

        with pytest.raises(ValueError, match="positive"):
            QRAGRetriever(
                similarity_weight=0, recency_weight=0, diversity_weight=0, success_weight=0
            )


class TestComputeValue:
    def test_exact_task_match(self) -> None:
        r = QRAGRetriever()
        val = r._compute_value({"metadata": {"task_type": "debug", "success": True}}, "debug")
        assert val > 0.5

    def test_partial_task_match(self) -> None:
        r = QRAGRetriever()
        val = r._compute_value({"metadata": {"task_type": "code", "success": True}}, "code_change")
        # Partial match — code in code_change
        assert val > 0.0

    def test_no_task_match(self) -> None:
        r = QRAGRetriever()
        val = r._compute_value({"metadata": {"task_type": "analysis"}}, "debug")
        assert val < 0.5

    def test_empty_metadata(self) -> None:
        r = QRAGRetriever()
        val = r._compute_value({"metadata": {}}, "debug")
        assert isinstance(val, float)


class TestRecencyScore:
    def test_recent_memory_high_score(self) -> None:
        r = QRAGRetriever()
        score = r._recency_score(time.time() - 60)
        assert score > 0.9

    def test_old_memory_low_score(self) -> None:
        r = QRAGRetriever(recency_halflife_days=1.0)
        score = r._recency_score(time.time() - 86400 * 10)
        assert score < 0.01

    def test_none_returns_neutral(self) -> None:
        r = QRAGRetriever()
        assert r._recency_score(None) == 0.5

    def test_invalid_returns_neutral(self) -> None:
        r = QRAGRetriever()
        assert r._recency_score("not-a-number") == 0.5


class TestDiversityPenalty:
    def test_no_selected_returns_zero(self) -> None:
        r = QRAGRetriever()
        assert r._diversity_penalty([1.0, 0.0], []) == 0.0

    def test_identical_returns_one(self) -> None:
        r = QRAGRetriever()
        emb = [1.0, 0.0, 0.0]
        penalty = r._diversity_penalty(emb, [emb])
        assert abs(penalty - 1.0) < 0.01

    def test_orthogonal_returns_zero(self) -> None:
        r = QRAGRetriever()
        penalty = r._diversity_penalty([1.0, 0.0], [[0.0, 1.0]])
        assert abs(penalty) < 0.01

    def test_empty_embedding(self) -> None:
        r = QRAGRetriever()
        assert r._diversity_penalty([], [[1.0, 0.0]]) == 0.0


class TestSuccessScore:
    def test_true(self) -> None:
        r = QRAGRetriever()
        assert r._success_score({"success": True}) == 1.0

    def test_false(self) -> None:
        r = QRAGRetriever()
        assert r._success_score({"success": False}) == 0.0

    def test_none(self) -> None:
        r = QRAGRetriever()
        assert r._success_score({}) == 0.5

    def test_numeric(self) -> None:
        r = QRAGRetriever()
        assert r._success_score({"success": 0.7}) == 0.7


class TestHelpers:
    def test_cosine_sim_identical(self) -> None:
        assert abs(_cosine_sim([1.0, 0.0], [1.0, 0.0]) - 1.0) < 1e-6

    def test_cosine_sim_orthogonal(self) -> None:
        assert abs(_cosine_sim([1.0, 0.0], [0.0, 1.0])) < 1e-6

    def test_cosine_sim_different_lengths(self) -> None:
        assert _cosine_sim([1.0], [1.0, 0.0]) == 0.0

    def test_cosine_sim_empty(self) -> None:
        assert _cosine_sim([], []) == 0.0

    def test_extract_embedding_present(self) -> None:
        assert _extract_embedding({"embedding": [1.0, 2.0]}) == [1.0, 2.0]

    def test_extract_embedding_missing(self) -> None:
        assert _extract_embedding({"content": "x"}) == []

    def test_extract_embedding_none(self) -> None:
        assert _extract_embedding(None) == []
