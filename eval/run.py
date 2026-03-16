from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[1]


def _ensure_py_src_on_path() -> None:
    py_src = _repo_root() / "py" / "src"
    py_src_text = str(py_src)
    if py_src_text not in sys.path:
        sys.path.insert(0, py_src_text)


@dataclass(frozen=True)
class EvalTask:
    id: str
    request: str
    expected_intent: str
    expected_halt_reason: str = ""
    require_final: bool = True
    expected_acceptance_ok: bool = True
    budget_max_loops: int = 1
    expected_recovery_packet_present: bool = False
    description: str = ""


def load_tasks(tasks_dir: Path) -> list[EvalTask]:
    tasks: list[EvalTask] = []
    for path in sorted(tasks_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        tasks.append(
            EvalTask(
                id=str(data["id"]),
                request=str(data["request"]),
                expected_intent=str(data["expected_intent"]),
                expected_halt_reason=str(data.get("expected_halt_reason", "")),
                require_final=bool(data.get("require_final", True)),
                expected_acceptance_ok=bool(data.get("expected_acceptance_ok", True)),
                budget_max_loops=int(data.get("budget_max_loops", 1)),
                expected_recovery_packet_present=bool(data.get("expected_recovery_packet_present", False)),
                description=str(data.get("description", "")),
            )
        )
    return tasks


def run_task(task: EvalTask, *, repo_root: Path) -> dict[str, Any]:
    _ensure_py_src_on_path()
    from lg_orch.graph import build_graph

    app = build_graph()
    output = app.invoke(
        {
            "request": task.request,
            "_repo_root": str(repo_root),
            "_runner_base_url": "http://127.0.0.1:8088",
            "_runner_enabled": False,
            "_budget_max_loops": task.budget_max_loops,
            "_config_policy": {
                "network_default": "deny",
                "require_approval_for_mutations": True,
                "allowed_write_paths": [],
            },
        }
    )
    return dict(output)


def _score_recovery_packet(task: EvalTask, output: dict[str, Any]) -> bool:
    packet = output.get("recovery_packet")
    present = isinstance(packet, dict) and bool(packet)
    return present == task.expected_recovery_packet_present


def _score_loop_summary_quality(output: dict[str, Any]) -> bool:
    verification = output.get("verification", {})
    if isinstance(verification, dict) and bool(verification.get("ok", False)):
        return True
    loop_summaries = output.get("loop_summaries", [])
    return isinstance(loop_summaries, list) and len(loop_summaries) > 0


def _score_acceptance_criteria_tracking(output: dict[str, Any]) -> bool:
    loop_summaries_raw = output.get("loop_summaries", [])
    loop_summaries = loop_summaries_raw if isinstance(loop_summaries_raw, list) else []
    if not loop_summaries:
        verification = output.get("verification", {})
        return isinstance(verification, dict) and bool(verification.get("ok", False))
    for summary in loop_summaries:
        if not isinstance(summary, dict):
            continue
        criteria = summary.get("acceptance_criteria")
        if isinstance(criteria, list) and len(criteria) > 0:
            return True
    return False


def _score_failure_fingerprint_present(output: dict[str, Any]) -> bool:
    verification = output.get("verification", {})
    if isinstance(verification, dict) and bool(verification.get("ok", False)):
        return True
    loop_summaries_raw = output.get("loop_summaries", [])
    loop_summaries = loop_summaries_raw if isinstance(loop_summaries_raw, list) else []
    for summary in loop_summaries:
        if not isinstance(summary, dict):
            continue
        fingerprint = str(summary.get("failure_fingerprint", "")).strip()
        if fingerprint and fingerprint != "verification_failed":
            return True
    return False


def _score_compression_tracking(output: dict[str, Any]) -> bool:
    telemetry_raw = output.get("telemetry", {})
    telemetry = dict(telemetry_raw) if isinstance(telemetry_raw, dict) else {}
    compression_summary = telemetry.get("compression_summary", {})
    if not isinstance(compression_summary, dict):
        return False
    total_events = int(compression_summary.get("total_events", 0))
    return total_events > 0


def score_task(task: EvalTask, output: dict[str, Any]) -> dict[str, Any]:
    actual_intent = str(output.get("intent", "")).strip()
    halt_reason = str(output.get("halt_reason", "")).strip()
    final_present = bool(str(output.get("final", "")).strip())
    tool_results_raw = output.get("tool_results", [])
    tool_results = tool_results_raw if isinstance(tool_results_raw, list) else []
    verification_raw = output.get("verification", {})
    verification = dict(verification_raw) if isinstance(verification_raw, dict) else {}
    acceptance_ok = bool(verification.get("acceptance_ok", False))

    checks = {
        "intent_match": actual_intent == task.expected_intent,
        "halt_reason_match": halt_reason == task.expected_halt_reason,
        "final_present": final_present if task.require_final else True,
        "acceptance_ok_match": acceptance_ok == task.expected_acceptance_ok,
        "recovery_packet_match": _score_recovery_packet(task, output),
        "loop_summary_quality": _score_loop_summary_quality(output),
        "route_lane_set": bool(str(output.get("route", {}).get("lane", "")).strip()),
        "acceptance_criteria_tracking": _score_acceptance_criteria_tracking(output),
        "failure_fingerprint_present": _score_failure_fingerprint_present(output),
        "compression_tracking": _score_compression_tracking(output),
    }
    passed_checks = sum(1 for ok in checks.values() if ok)
    max_checks = len(checks)
    score = passed_checks / max_checks if max_checks > 0 else 0.0

    return {
        "id": task.id,
        "request": task.request,
        "expected_intent": task.expected_intent,
        "actual_intent": actual_intent,
        "expected_halt_reason": task.expected_halt_reason,
        "actual_halt_reason": halt_reason,
        "final_present": final_present,
        "acceptance_ok": acceptance_ok,
        "tool_results_count": len(tool_results),
        "checks": checks,
        "score": score,
        "passed": passed_checks == max_checks,
    }


def evaluate_tasks(
    tasks: list[EvalTask],
    *,
    repo_root: Path,
    evaluator: Callable[[EvalTask], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    run = evaluator if evaluator is not None else (lambda task: run_task(task, repo_root=repo_root))
    results = [score_task(task, run(task)) for task in tasks]

    total = len(results)
    passed = sum(1 for result in results if bool(result.get("passed", False)))
    avg_score = sum(float(result.get("score", 0.0)) for result in results) / total if total else 0.0
    intent_matches = sum(
        1
        for result in results
        if bool(result.get("checks", {}).get("intent_match", False))
    )
    avg_tool_results = (
        sum(int(result.get("tool_results_count", 0)) for result in results) / total if total else 0.0
    )
    recovery_packet_accuracy = sum(
        1 for result in results
        if bool(result.get("checks", {}).get("recovery_packet_match", False))
    ) / total if total else 0.0
    loop_summary_quality = sum(
        1 for result in results
        if bool(result.get("checks", {}).get("loop_summary_quality", False))
    ) / total if total else 0.0
    acceptance_criteria_tracking = sum(
        1 for result in results
        if bool(result.get("checks", {}).get("acceptance_criteria_tracking", False))
    ) / total if total else 0.0
    failure_fingerprint_present = sum(
        1 for result in results
        if bool(result.get("checks", {}).get("failure_fingerprint_present", False))
    ) / total if total else 0.0
    compression_tracking = sum(
        1 for result in results
        if bool(result.get("checks", {}).get("compression_tracking", False))
    ) / total if total else 0.0

    return {
        "summary": {
            "total_tasks": total,
            "passed_tasks": passed,
            "failed_tasks": total - passed,
            "pass_rate": passed / total if total else 0.0,
            "intent_accuracy": intent_matches / total if total else 0.0,
            "average_score": avg_score,
            "average_tool_results": avg_tool_results,
            "recovery_packet_accuracy": recovery_packet_accuracy,
            "loop_summary_quality": loop_summary_quality,
            "acceptance_criteria_tracking": acceptance_criteria_tracking,
            "failure_fingerprint_present": failure_fingerprint_present,
            "compression_tracking": compression_tracking,
        },
        "results": results,
    }


def _render_text_report(report: dict[str, Any]) -> str:
    summary_raw = report.get("summary", {})
    summary = summary_raw if isinstance(summary_raw, dict) else {}
    results_raw = report.get("results", [])
    results = results_raw if isinstance(results_raw, list) else []

    lines = [
        (
            "summary: "
            f"passed={int(summary.get('passed_tasks', 0))}/{int(summary.get('total_tasks', 0))} "
            f"pass_rate={float(summary.get('pass_rate', 0.0)):.2f} "
            f"intent_accuracy={float(summary.get('intent_accuracy', 0.0)):.2f} "
            f"avg_score={float(summary.get('average_score', 0.0)):.2f} "
            f"recovery_packet_acc={float(summary.get('recovery_packet_accuracy', 0.0)):.2f} "
            f"loop_summary_quality={float(summary.get('loop_summary_quality', 0.0)):.2f} "
            f"acceptance_criteria_track={float(summary.get('acceptance_criteria_tracking', 0.0)):.2f} "
            f"failure_fingerprint={float(summary.get('failure_fingerprint_present', 0.0)):.2f} "
            f"compression_track={float(summary.get('compression_tracking', 0.0)):.2f}"
        )
    ]
    for result in results:
        if not isinstance(result, dict):
            continue
        status = "PASS" if bool(result.get("passed", False)) else "FAIL"
        halt_reason = str(result.get("actual_halt_reason", "")).strip() or "(none)"
        lines.append(
            (
                f"[{status}] {str(result.get('id', ''))} "
                f"score={float(result.get('score', 0.0)):.2f} "
                f"intent={str(result.get('actual_intent', '')) or '(missing)'} "
                f"halt={halt_reason} "
                f"acceptance_ok={bool(result.get('acceptance_ok', False))} "
                f"tools={int(result.get('tool_results_count', 0))}"
            )
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="eval-run")
    parser.add_argument("--tasks-dir", default=str(Path(__file__).parent / "tasks"))
    parser.add_argument("--format", choices=["text", "json"], default="text")
    args = parser.parse_args(argv)

    tasks = load_tasks(Path(str(args.tasks_dir)))
    if not tasks:
        raise SystemExit("no tasks")
    for task in tasks:
        if not task.id or not task.request or not task.expected_intent:
            raise SystemExit(f"invalid task: {task}")

    report = evaluate_tasks(tasks, repo_root=_repo_root())
    if str(args.format) == "json":
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        print(_render_text_report(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
