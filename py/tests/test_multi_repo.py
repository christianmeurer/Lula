"""Tests for py/src/lg_orch/multi_repo.py (Wave 9)."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lg_orch.meta_graph import MetaRunResult, SubAgentTask
from lg_orch.multi_repo import CrossRepoHandoff, MultiRepoScheduler, RepoConfig
from lg_orch.scip_index import ScipIndex


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_task(task_id: str, extra: dict[str, Any] | None = None) -> SubAgentTask:
    return SubAgentTask(
        task_id=task_id,
        description=f"Task {task_id}",
        depends_on=[],
        input_state={"task_id": task_id, **(extra or {})},
    )


def _empty_result(tasks: list[SubAgentTask]) -> MetaRunResult:
    for t in tasks:
        t.status = "success"
    return MetaRunResult(
        tasks=tasks,
        total_duration_s=0.0,
        succeeded=len(tasks),
        failed=0,
        skipped=0,
    )


def _repo(
    name: str = "svc",
    root: str = "/repos/svc",
    runner: str = "http://runner.svc:8080",
    scip_index: ScipIndex | None = None,
) -> RepoConfig:
    return RepoConfig(
        name=name,
        root_path=root,
        runner_url=runner,
        scip_index=scip_index,
    )


# ---------------------------------------------------------------------------
# 1. repo_root injected into task input_state
# ---------------------------------------------------------------------------


def test_multi_repo_scheduler_injects_repo_root() -> None:
    """A task must receive ``repo_root`` and ``runner_url`` from its RepoConfig."""
    repo = _repo(name="auth", root="/repos/auth", runner="http://auth-runner:9000")
    task = _make_task("task-a")
    # Pre-load an empty SCIP index so load_scip_index is not called.
    repo.scip_index = ScipIndex(repo_root="/repos/auth")

    scheduler = MultiRepoScheduler(
        repos=[repo],
        task_repo_map={"task-a": "auth"},
        concurrency=1,
    )

    # Patch the inner MetaGraphScheduler.run so no real execution happens.
    dummy_result = _empty_result([task])
    with patch(
        "lg_orch.multi_repo.MetaGraphScheduler.run",
        new_callable=AsyncMock,
        return_value=dummy_result,
    ):
        asyncio.run(scheduler.run([task]))

    # The enrichment happens before delegation, so input_state is already modified.
    assert task.input_state["repo_root"] == "/repos/auth"
    assert task.input_state["runner_url"] == "http://auth-runner:9000"
    assert "scip_summary" in task.input_state
    assert isinstance(task.input_state["scip_summary"], list)


# ---------------------------------------------------------------------------
# 2. CrossRepoHandoff applied to the target task
# ---------------------------------------------------------------------------


def test_multi_repo_scheduler_applies_cross_repo_handoff() -> None:
    """A CrossRepoHandoff targeting repo B must appear in task B's active_handoff."""
    repo_a = _repo(name="api-gateway", root="/repos/api-gateway", runner="http://gw:8080")
    repo_b = _repo(name="auth-service", root="/repos/auth", runner="http://auth:9000")
    repo_a.scip_index = ScipIndex(repo_root="/repos/api-gateway")
    repo_b.scip_index = ScipIndex(repo_root="/repos/auth")

    task_a = _make_task("task-a")
    task_b = _make_task("task-b")

    handoff = CrossRepoHandoff(
        source_repo="api-gateway",
        target_repo="auth-service",
        shared_symbols=["AuthService", "validate_token"],
        objective="Implement token validation endpoint",
        context_patch={"priority": "high"},
    )

    scheduler = MultiRepoScheduler(
        repos=[repo_a, repo_b],
        task_repo_map={"task-a": "api-gateway", "task-b": "auth-service"},
        concurrency=2,
    )

    dummy_result = _empty_result([task_a, task_b])
    with patch(
        "lg_orch.multi_repo.MetaGraphScheduler.run",
        new_callable=AsyncMock,
        return_value=dummy_result,
    ):
        asyncio.run(scheduler.run([task_a, task_b], handoffs=[handoff]))

    # task_a targets "api-gateway"; the handoff targets "auth-service" → no handoff for A.
    assert "active_handoff" not in task_a.input_state

    # task_b targets "auth-service" → handoff must be present.
    assert "active_handoff" in task_b.input_state
    injected = task_b.input_state["active_handoff"]
    assert isinstance(injected, dict)
    assert injected["source_repo"] == "api-gateway"
    assert injected["target_repo"] == "auth-service"
    assert injected["shared_symbols"] == ["AuthService", "validate_token"]
    assert injected["objective"] == "Implement token validation endpoint"
    assert injected["context_patch"] == {"priority": "high"}


# ---------------------------------------------------------------------------
# 3. Unknown repo raises ValueError
# ---------------------------------------------------------------------------


def test_multi_repo_scheduler_unknown_repo_raises() -> None:
    """A task_id absent from task_repo_map must raise ValueError immediately."""
    repo = _repo(name="svc", root="/repos/svc", runner="http://svc:8080")
    task = _make_task("task-x")

    scheduler = MultiRepoScheduler(
        repos=[repo],
        task_repo_map={},  # task-x not mapped
        concurrency=1,
    )

    with pytest.raises(ValueError, match="task-x"):
        asyncio.run(scheduler.run([task]))


def test_multi_repo_scheduler_repo_name_not_in_repos_raises() -> None:
    """A task_id that maps to a non-existent repo name must raise ValueError."""
    task = _make_task("task-y")

    scheduler = MultiRepoScheduler(
        repos=[],  # no repos registered
        task_repo_map={"task-y": "missing-service"},
        concurrency=1,
    )

    with pytest.raises(ValueError, match="missing-service"):
        asyncio.run(scheduler.run([task]))


# ---------------------------------------------------------------------------
# 4. scip_summary uses top-20 symbols sorted by name
# ---------------------------------------------------------------------------


def test_multi_repo_scheduler_scip_summary_truncated() -> None:
    """scip_summary must contain at most 20 symbols, sorted by name."""
    from lg_orch.scip_index import ScipSymbol

    symbols = [
        ScipSymbol(
            name=f"sym_{i:03d}",
            kind="function",
            file_path="src/mod.py",
            start_line=i,
            end_line=i + 1,
            references=[],
        )
        for i in range(30)
    ]
    index = ScipIndex(repo_root="/repos/big", symbols=symbols)
    repo = _repo(name="big", root="/repos/big", runner="http://big:8080", scip_index=index)
    task = _make_task("task-big")

    scheduler = MultiRepoScheduler(
        repos=[repo],
        task_repo_map={"task-big": "big"},
        concurrency=1,
    )

    dummy_result = _empty_result([task])
    with patch(
        "lg_orch.multi_repo.MetaGraphScheduler.run",
        new_callable=AsyncMock,
        return_value=dummy_result,
    ):
        asyncio.run(scheduler.run([task]))

    summary = task.input_state["scip_summary"]
    assert len(summary) == 20
    names = [s["name"] for s in summary]
    assert names == sorted(names), "scip_summary must be sorted by name"
    # All entries must be the first 20 alphabetically.
    assert names[0] == "sym_000"
    assert names[-1] == "sym_019"
