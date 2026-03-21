from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any


def _load_eval_run_module() -> Any:
    repo_root = Path(__file__).resolve().parents[2]
    module_path = repo_root / "eval" / "run.py"
    spec = importlib.util.spec_from_file_location("repo_eval_run_correctness", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# pass_at_k — pure unit tests
# ---------------------------------------------------------------------------


def test_pass_at_k_all_correct() -> None:
    module = _load_eval_run_module()
    assert module.pass_at_k(n=5, c=5, k=1) == 1.0


def test_pass_at_k_none_correct() -> None:
    module = _load_eval_run_module()
    assert module.pass_at_k(n=5, c=0, k=1) == 0.0


def test_pass_at_k_partial_correct_k1() -> None:
    module = _load_eval_run_module()
    # With n=5, c=3, k=1: 1 - C(2,1)/C(5,1) = 1 - 2/5 = 0.6
    result = module.pass_at_k(n=5, c=3, k=1)
    assert abs(result - 0.6) < 1e-9


def test_pass_at_k_large_k() -> None:
    module = _load_eval_run_module()
    result = module.pass_at_k(n=10, c=5, k=5)
    assert 0.0 < result < 1.0


def test_pass_at_k_k_equals_n() -> None:
    module = _load_eval_run_module()
    # k == n is permitted; at least one must be correct when c > 0
    result = module.pass_at_k(n=5, c=3, k=5)
    assert 0.0 < result <= 1.0


def test_pass_at_k_raises_when_k_exceeds_n() -> None:
    import pytest

    module = _load_eval_run_module()
    with pytest.raises(ValueError, match="k"):
        module.pass_at_k(n=4, c=2, k=5)


def test_pass_at_k_k1_one_of_one_correct() -> None:
    module = _load_eval_run_module()
    assert module.pass_at_k(n=1, c=1, k=1) == 1.0


def test_pass_at_k_k1_zero_of_one_correct() -> None:
    module = _load_eval_run_module()
    assert module.pass_at_k(n=1, c=0, k=1) == 0.0


# ---------------------------------------------------------------------------
# CLI — --pass-at-k 1 runs each task once
# ---------------------------------------------------------------------------


def test_cli_pass_at_k_1_runs_once(tmp_path: Path, capsys: object) -> None:
    module = _load_eval_run_module()

    task_path = tmp_path / "canary.json"
    task_path.write_text(
        json.dumps(
            {
                "id": "canary-001",
                "request": "Summarize the repository structure.",
                "expected_intent": "analysis",
            }
        ),
        encoding="utf-8",
    )

    call_count = 0

    def _fake_run_task(task: Any, **kwargs: Any) -> dict[str, Any]:
        nonlocal call_count
        call_count += 1
        return {
            "intent": "analysis",
            "halt_reason": "",
            "final": "done",
            "tool_results": [],
            "verification": {"acceptance_ok": True, "ok": True},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
        }

    original = module.run_task
    module.run_task = _fake_run_task
    try:
        rc = module.main(["--tasks-dir", str(tmp_path), "--pass-at-k", "1"])
    finally:
        module.run_task = original

    assert rc == 0
    assert call_count == 1


# ---------------------------------------------------------------------------
# CLI — --runner-enabled propagates to run_task
# ---------------------------------------------------------------------------


def test_cli_runner_enabled_flag(tmp_path: Path) -> None:
    module = _load_eval_run_module()

    task_path = tmp_path / "canary.json"
    task_path.write_text(
        json.dumps(
            {
                "id": "canary-001",
                "request": "Summarize.",
                "expected_intent": "analysis",
            }
        ),
        encoding="utf-8",
    )

    captured_kwargs: dict[str, Any] = {}

    def _fake_run_task(task: Any, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "intent": "analysis",
            "halt_reason": "",
            "final": "done",
            "tool_results": [],
            "verification": {"acceptance_ok": True, "ok": True},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
        }

    original = module.run_task
    module.run_task = _fake_run_task
    try:
        rc = module.main(["--tasks-dir", str(tmp_path), "--runner-enabled"])
    finally:
        module.run_task = original

    assert rc == 0
    assert captured_kwargs.get("runner_enabled") is True


# ---------------------------------------------------------------------------
# CLI — --temperature propagates to run_task
# ---------------------------------------------------------------------------


def test_cli_temperature_propagates(tmp_path: Path) -> None:
    module = _load_eval_run_module()

    task_path = tmp_path / "canary.json"
    task_path.write_text(
        json.dumps(
            {
                "id": "canary-001",
                "request": "Summarize.",
                "expected_intent": "analysis",
            }
        ),
        encoding="utf-8",
    )

    captured_kwargs: dict[str, Any] = {}

    def _fake_run_task(task: Any, **kwargs: Any) -> dict[str, Any]:
        captured_kwargs.update(kwargs)
        return {
            "intent": "analysis",
            "halt_reason": "",
            "final": "done",
            "tool_results": [],
            "verification": {"acceptance_ok": True, "ok": True},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
        }

    original = module.run_task
    module.run_task = _fake_run_task
    try:
        rc = module.main(["--tasks-dir", str(tmp_path), "--temperature", "0.5"])
    finally:
        module.run_task = original

    assert rc == 0
    assert abs(float(captured_kwargs.get("temperature", -1)) - 0.5) < 1e-9


# ---------------------------------------------------------------------------
# CLI — --pass-at-k K > 1 auto-sets temperature to 0.8
# ---------------------------------------------------------------------------


def test_cli_pass_at_k_gt1_auto_temperature(tmp_path: Path, capsys: object) -> None:
    module = _load_eval_run_module()

    task_path = tmp_path / "canary.json"
    task_path.write_text(
        json.dumps(
            {
                "id": "canary-001",
                "request": "Summarize.",
                "expected_intent": "analysis",
            }
        ),
        encoding="utf-8",
    )

    captured_temps: list[float] = []

    def _fake_run_task(task: Any, **kwargs: Any) -> dict[str, Any]:
        captured_temps.append(float(kwargs.get("temperature", -1.0)))
        return {
            "intent": "analysis",
            "halt_reason": "",
            "final": "done",
            "tool_results": [],
            "verification": {"acceptance_ok": True, "ok": True},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
        }

    original = module.run_task
    module.run_task = _fake_run_task
    try:
        rc = module.main(["--tasks-dir", str(tmp_path), "--pass-at-k", "3"])
    finally:
        module.run_task = original

    assert rc == 0
    # Called 3 times (k=3), all with temperature=0.8
    assert len(captured_temps) == 3
    for t in captured_temps:
        assert abs(t - 0.8) < 1e-9


# ---------------------------------------------------------------------------
# CLI — --pass-at-k K > 1 prints structured table
# ---------------------------------------------------------------------------


def test_cli_pass_at_k_table_output(tmp_path: Path, capsys: object) -> None:
    module = _load_eval_run_module()

    task_path = tmp_path / "canary.json"
    task_path.write_text(
        json.dumps(
            {
                "id": "canary-001",
                "request": "Summarize.",
                "expected_intent": "analysis",
            }
        ),
        encoding="utf-8",
    )

    def _fake_run_task(task: Any, **kwargs: Any) -> dict[str, Any]:
        return {
            "intent": "analysis",
            "halt_reason": "",
            "final": "done",
            "tool_results": [],
            "verification": {"acceptance_ok": True, "ok": True},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
        }

    original = module.run_task
    module.run_task = _fake_run_task
    try:
        rc = module.main(["--tasks-dir", str(tmp_path), "--pass-at-k", "2"])
    finally:
        module.run_task = original

    assert rc == 0
    out = capsys.readouterr().out
    assert "pass@k" in out
    assert "canary-001" in out


# ---------------------------------------------------------------------------
# evaluate_golden_assertions — operator unit tests
# ---------------------------------------------------------------------------


def test_evaluate_golden_assertions_eq_pass() -> None:
    module = _load_eval_run_module()
    golden = {"assertions": [{"field": "status", "op": "eq", "value": "success"}]}
    result = {"status": "success"}
    passed, total, failures = module.evaluate_golden_assertions(result, golden)
    assert passed == 1
    assert total == 1
    assert failures == []


def test_evaluate_golden_assertions_eq_fail() -> None:
    module = _load_eval_run_module()
    golden = {"assertions": [{"field": "status", "op": "eq", "value": "success"}]}
    result = {"status": "failure"}
    passed, _total, failures = module.evaluate_golden_assertions(result, golden)
    assert passed == 0
    assert _total == 1
    assert len(failures) == 1
    assert "status" in failures[0]


def test_evaluate_golden_assertions_lte() -> None:
    module = _load_eval_run_module()
    golden = {"assertions": [{"field": "loop_count", "op": "lte", "value": 3}]}
    # passes when actual <= expected
    passed, _total, failures = module.evaluate_golden_assertions({"loop_count": 2}, golden)
    assert passed == 1 and failures == []
    # fails when actual > expected
    passed2, _total2, failures2 = module.evaluate_golden_assertions({"loop_count": 5}, golden)
    assert passed2 == 0 and len(failures2) == 1


def test_evaluate_golden_assertions_in() -> None:
    module = _load_eval_run_module()
    golden = {
        "assertions": [
            {"field": "approval_outcome", "op": "in", "value": ["approved", "rejected"]}
        ]
    }
    passed, _total, failures = module.evaluate_golden_assertions(
        {"approval_outcome": "approved"}, golden
    )
    assert passed == 1 and failures == []
    passed2, _, failures2 = module.evaluate_golden_assertions(
        {"approval_outcome": "pending"}, golden
    )
    assert passed2 == 0 and len(failures2) == 1


def test_evaluate_golden_assertions_contains() -> None:
    module = _load_eval_run_module()
    # list containment
    golden = {"assertions": [{"field": "tool_calls", "op": "contains", "value": "apply_patch"}]}
    passed, _, failures = module.evaluate_golden_assertions(
        {"tool_calls": ["apply_patch", "read_file"]}, golden
    )
    assert passed == 1 and failures == []
    # string containment
    passed2, _, failures2 = module.evaluate_golden_assertions(
        {"tool_calls": "apply_patch,read_file"}, golden
    )
    assert passed2 == 1 and failures2 == []
    # fails when not present
    passed3, _, failures3 = module.evaluate_golden_assertions(
        {"tool_calls": ["read_file"]}, golden
    )
    assert passed3 == 0 and len(failures3) == 1


def test_evaluate_golden_assertions_nested_path() -> None:
    module = _load_eval_run_module()
    golden = {"assertions": [{"path": "verification.ok", "op": "eq", "value": True}]}
    result = {"verification": {"ok": True, "acceptance_ok": False}}
    passed, total, failures = module.evaluate_golden_assertions(result, golden)
    assert passed == 1 and total == 1 and failures == []
    # fails when nested value is False
    result2 = {"verification": {"ok": False}}
    passed2, _, failures2 = module.evaluate_golden_assertions(result2, golden)
    assert passed2 == 0 and len(failures2) == 1


# ---------------------------------------------------------------------------
# load_golden — graceful None on missing file
# ---------------------------------------------------------------------------


def test_load_golden_returns_none_for_missing() -> None:
    module = _load_eval_run_module()
    result = module.load_golden("task-that-does-not-exist-xyz")
    assert result is None


# ---------------------------------------------------------------------------
# score_task integration — golden assertions affect overall passed flag
# ---------------------------------------------------------------------------


def test_score_task_applies_golden_assertions(tmp_path: Path) -> None:
    """score_task sets passed=False when golden assertions fail."""
    module = _load_eval_run_module()

    # Write a golden file that will fail (expects status "success" but output is "failure")
    golden_dir = tmp_path / "golden"
    golden_dir.mkdir()
    golden_file = golden_dir / "my-task.json"
    golden_file.write_text(
        json.dumps({"assertions": [{"field": "status", "op": "eq", "value": "success"}]}),
        encoding="utf-8",
    )

    # Patch load_golden to return our temp golden
    original_load_golden = module.load_golden

    def _fake_load_golden(task_id: str) -> Any:
        if task_id.startswith("my-task"):
            return json.loads(golden_file.read_text(encoding="utf-8"))
        return original_load_golden(task_id)

    module.load_golden = _fake_load_golden
    try:
        task = module.EvalTask(
            id="my-task-001",
            request="do something",
            expected_intent="analysis",
            expected_acceptance_ok=False,
            require_final=False,
        )
        output: dict[str, Any] = {
            "intent": "analysis",
            "halt_reason": "",
            "final": "",
            "status": "failure",  # deliberately wrong — golden expects "success"
            "tool_results": [],
            "verification": {"acceptance_ok": False, "ok": False},
            "route": {"lane": "fast"},
            "telemetry": {"compression_summary": {"total_events": 1}},
            "loop_summaries": [{"acceptance_criteria": ["done"], "failure_fingerprint": "err"}],
        }
        result = module.score_task(task, output)
    finally:
        module.load_golden = original_load_golden

    assert result["golden_assertions_total"] == 1
    assert result["golden_assertions_passed"] == 0
    assert len(result["golden_assertion_failures"]) == 1
    assert result["golden_passed"] is False
    assert result["passed"] is False
