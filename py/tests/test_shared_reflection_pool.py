# SPDX-License-Identifier: MIT
"""Tests for the SYMPHONY SharedReflectionPool."""

from __future__ import annotations

import threading

from lg_orch.model_routing import FailureReflection, SharedReflectionPool


class TestFailureReflection:
    def test_fields(self) -> None:
        r = FailureReflection(
            loop_index=1,
            model_used="gpt-4o",
            failure_class="syntax_error",
            reflection="The plan used invalid SQL syntax.",
        )
        assert r.loop_index == 1
        assert r.model_used == "gpt-4o"
        assert r.failure_class == "syntax_error"
        assert r.reflection == "The plan used invalid SQL syntax."
        assert r.timestamp > 0

    def test_custom_timestamp(self) -> None:
        r = FailureReflection(
            loop_index=0,
            model_used="claude",
            failure_class="timeout",
            reflection="...",
            timestamp=123.0,
        )
        assert r.timestamp == 123.0


class TestSharedReflectionPool:
    def test_empty_pool_returns_empty_context(self) -> None:
        pool = SharedReflectionPool()
        assert pool.get_context() == ""

    def test_add_and_get_context(self) -> None:
        pool = SharedReflectionPool()
        pool.add_reflection(
            FailureReflection(
                loop_index=1,
                model_used="gpt-4o",
                failure_class="syntax_error",
                reflection="Used invalid SQL.",
            )
        )
        ctx = pool.get_context()
        assert "Previous failure reflections:" in ctx
        assert "Loop 1 (gpt-4o): syntax_error" in ctx
        assert "Used invalid SQL." in ctx

    def test_multiple_reflections(self) -> None:
        pool = SharedReflectionPool()
        for i in range(3):
            pool.add_reflection(
                FailureReflection(
                    loop_index=i,
                    model_used=f"model-{i}",
                    failure_class="error",
                    reflection=f"Reflection {i}",
                )
            )
        ctx = pool.get_context()
        assert "Loop 0" in ctx
        assert "Loop 1" in ctx
        assert "Loop 2" in ctx

    def test_max_cap_evicts_oldest(self) -> None:
        pool = SharedReflectionPool(max_reflections=3)
        for i in range(5):
            pool.add_reflection(
                FailureReflection(
                    loop_index=i,
                    model_used="m",
                    failure_class="e",
                    reflection=f"r{i}",
                )
            )
        ctx = pool.get_context()
        # Should only have the last 3 (indices 2, 3, 4)
        assert "Loop 0" not in ctx
        assert "Loop 1" not in ctx
        assert "Loop 2" in ctx
        assert "Loop 3" in ctx
        assert "Loop 4" in ctx

    def test_clear(self) -> None:
        pool = SharedReflectionPool()
        pool.add_reflection(
            FailureReflection(
                loop_index=0,
                model_used="m",
                failure_class="e",
                reflection="r",
            )
        )
        assert pool.get_context() != ""
        pool.clear()
        assert pool.get_context() == ""

    def test_thread_safety(self) -> None:
        pool = SharedReflectionPool(max_reflections=100)
        n_threads = 20
        n_per_thread = 10
        errors: list[Exception] = []

        def _worker(offset: int) -> None:
            try:
                for i in range(n_per_thread):
                    pool.add_reflection(
                        FailureReflection(
                            loop_index=offset * n_per_thread + i,
                            model_used="m",
                            failure_class="e",
                            reflection=f"r{offset}-{i}",
                        )
                    )
                    # Interleave reads
                    pool.get_context()
            except Exception as exc:
                errors.append(exc)

        threads = [threading.Thread(target=_worker, args=(t,)) for t in range(n_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert errors == []
        ctx = pool.get_context()
        assert "Previous failure reflections:" in ctx

    def test_default_max_is_20(self) -> None:
        pool = SharedReflectionPool()
        assert pool._max == 20

    def test_custom_max(self) -> None:
        pool = SharedReflectionPool(max_reflections=5)
        assert pool._max == 5
