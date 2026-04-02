# SPDX-License-Identifier: MIT
# Copyright (c) 2026 Christian Meurer — https://github.com/christianmeurer/Lula
"""Q-RAG — Value-Based Multi-Step Retrieval.

Enhances semantic search by re-ranking results using a value function
that estimates the downstream utility of each retrieved memory for
the current task context. This addresses the limitation of single-step
cosine similarity which may retrieve superficially similar but
low-utility memories.

Based on: Q-RAG (ICLR 2026) — Long Context Multi-Step Retrieval
via Value-Based Embedder Training.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from typing import Any

import structlog

_log = structlog.get_logger(__name__)


@dataclass
class ScoredMemory:
    """A memory with both similarity and value scores."""

    content: str
    metadata: dict[str, Any]
    similarity: float  # cosine similarity from vector search
    value: float  # estimated downstream utility
    combined_score: float  # weighted combination


class QRAGRetriever:
    """Multi-step retrieval with value-based re-ranking.

    Stage 1: Standard vector search (sqlite-vec or pgvector) returns top-k candidates
    Stage 2: Value function re-ranks candidates based on:
      - Task relevance: how well the memory matches the current task type
      - Recency: newer memories get a boost (exponential decay)
      - Diversity: penalize memories too similar to already-selected ones (MMR)
      - Success history: memories from successful runs score higher

    The architecture is designed to accept a learned value function later
    (replacing the heuristic signals with an RL-trained estimator).
    """

    def __init__(
        self,
        similarity_weight: float = 0.5,
        recency_weight: float = 0.2,
        diversity_weight: float = 0.15,
        success_weight: float = 0.15,
        recency_halflife_days: float = 7.0,
    ) -> None:
        total = similarity_weight + recency_weight + diversity_weight + success_weight
        if total <= 0:
            raise ValueError("weights must sum to a positive value")
        # Normalize weights
        self.similarity_weight = similarity_weight / total
        self.recency_weight = recency_weight / total
        self.diversity_weight = diversity_weight / total
        self.success_weight = success_weight / total
        self.recency_halflife_days = max(0.01, recency_halflife_days)

    def retrieve(
        self,
        candidates: list[dict[str, Any]],
        query_task_type: str,
        top_k: int = 5,
    ) -> list[ScoredMemory]:
        """Re-rank *candidates* using the value function and return top-k.

        Each candidate dict is expected to have:
          - content (str)
          - metadata (dict) — may contain ``task_type``, ``success``, ``created_at``
          - similarity (float) — cosine similarity from vector search
          - embedding (list[float], optional) — for diversity calculation
        """
        if not candidates:
            return []

        top_k = max(1, top_k)

        # Score every candidate
        scored: list[tuple[float, ScoredMemory]] = []
        for cand in candidates:
            similarity = float(cand.get("similarity", 0.0))
            metadata = dict(cand.get("metadata", {}))
            value = self._compute_value(cand, query_task_type)

            combined = (
                self.similarity_weight * similarity
                + self.recency_weight * self._recency_score(metadata.get("created_at"))
                + self.success_weight * self._success_score(metadata)
                + (1.0 - self.diversity_weight) * value  # placeholder before MMR
            )
            scored.append(
                (
                    combined,
                    ScoredMemory(
                        content=str(cand.get("content", "")),
                        metadata=metadata,
                        similarity=similarity,
                        value=value,
                        combined_score=combined,
                    ),
                )
            )

        # Sort by combined score descending
        scored.sort(key=lambda t: t[0], reverse=True)

        # Greedy MMR-style selection for diversity
        selected: list[ScoredMemory] = []
        selected_embeddings: list[list[float]] = []

        for _, mem in scored:
            if len(selected) >= top_k:
                break

            embedding = _extract_embedding(
                next((c for c in candidates if c.get("content") == mem.content), None)
            )

            if embedding and selected_embeddings:
                penalty = self._diversity_penalty(embedding, selected_embeddings)
                mem = ScoredMemory(
                    content=mem.content,
                    metadata=mem.metadata,
                    similarity=mem.similarity,
                    value=mem.value,
                    combined_score=mem.combined_score - self.diversity_weight * penalty,
                )

            selected.append(mem)
            if embedding:
                selected_embeddings.append(embedding)

        # Re-sort after diversity adjustment
        selected.sort(key=lambda m: m.combined_score, reverse=True)
        return selected

    def _compute_value(self, memory: dict[str, Any], query_task_type: str) -> float:
        """Heuristic value function estimating downstream utility.

        Combines task-type relevance and success history into a [0, 1] score.
        Designed to be replaced by a learned value function once reward signals
        are available.
        """
        metadata = dict(memory.get("metadata", {}))

        # Task-type match bonus
        mem_task_type = str(metadata.get("task_type", "")).strip().lower()
        query_lower = query_task_type.strip().lower()
        task_match = 1.0 if mem_task_type and mem_task_type == query_lower else 0.0

        # Partial match: check if either contains the other
        if (
            not task_match
            and mem_task_type
            and query_lower
            and (mem_task_type in query_lower or query_lower in mem_task_type)
        ):
            task_match = 0.5

        # Success signal
        success = self._success_score(metadata)

        # Weighted combination — task match is primary
        return 0.6 * task_match + 0.4 * success

    def _recency_score(self, created_at: Any) -> float:
        """Exponential decay based on age.  Returns [0, 1]."""
        if created_at is None:
            return 0.5  # unknown age gets neutral score
        try:
            age_seconds = max(0.0, time.time() - float(created_at))
        except (TypeError, ValueError):
            return 0.5
        halflife_seconds = self.recency_halflife_days * 86400.0
        return math.exp(-0.693 * age_seconds / halflife_seconds)

    def _diversity_penalty(self, embedding: list[float], selected: list[list[float]]) -> float:
        """Max cosine similarity to already-selected embeddings.

        Returns a value in [0, 1] where higher means more redundant.
        """
        if not selected or not embedding:
            return 0.0

        max_sim = 0.0
        for sel in selected:
            sim = _cosine_sim(embedding, sel)
            if sim > max_sim:
                max_sim = sim
        return max(0.0, min(1.0, max_sim))

    def _success_score(self, metadata: dict[str, Any]) -> float:
        """Score based on historical success.  Returns [0, 1]."""
        success = metadata.get("success")
        if success is None:
            return 0.5  # unknown
        if isinstance(success, bool):
            return 1.0 if success else 0.0
        try:
            return max(0.0, min(1.0, float(success)))
        except (TypeError, ValueError):
            return 0.5


def _extract_embedding(candidate: dict[str, Any] | None) -> list[float]:
    """Extract embedding from a candidate dict, returning [] if absent."""
    if candidate is None:
        return []
    raw = candidate.get("embedding")
    if isinstance(raw, list):
        return [float(v) for v in raw]
    return []


def _cosine_sim(a: list[float], b: list[float]) -> float:
    """Cosine similarity between two float lists."""
    if len(a) != len(b) or not a:
        return 0.0
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    norm_a = math.sqrt(sum(x * x for x in a))
    norm_b = math.sqrt(sum(x * x for x in b))
    if norm_a < 1e-9 or norm_b < 1e-9:
        return 0.0
    return dot / (norm_a * norm_b)


__all__ = [
    "QRAGRetriever",
    "ScoredMemory",
]
