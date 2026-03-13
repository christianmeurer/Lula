from __future__ import annotations

from typing import Any

from lg_orch.logging import get_logger
from lg_orch.trace import append_event


def reporter(state: dict[str, Any]) -> dict[str, Any]:
    log = get_logger()
    state = append_event(state, kind="node", data={"name": "reporter", "phase": "start"})
    try:
        repo_context = state.get("repo_context", {})
        tool_results = state.get("tool_results", [])
        verification_raw = state.get("verification", {})
        verification = dict(verification_raw) if isinstance(verification_raw, dict) else {}
        lines: list[str] = []
        lines.append(f"intent: {state.get('intent')}")
        lines.append(f"repo_root: {repo_context.get('repo_root')}")
        lines.append(f"top_level: {repo_context.get('top_level')}")
        if tool_results:
            lines.append(f"tool_calls: {len(tool_results)}")
        if "ok" in verification:
            lines.append(f"verification_ok: {verification.get('ok')}")
        if "acceptance_ok" in verification:
            lines.append(f"acceptance_ok: {verification.get('acceptance_ok')}")
        acceptance_checks_raw = verification.get("acceptance_checks", [])
        acceptance_checks = (
            [entry for entry in acceptance_checks_raw if isinstance(entry, dict)]
            if isinstance(acceptance_checks_raw, list)
            else []
        )
        unmet = [
            str(entry.get("criterion", "")).strip()
            for entry in acceptance_checks
            if bool(entry.get("ok", False)) is False and str(entry.get("criterion", "")).strip()
        ]
        if unmet:
            lines.append(f"acceptance_unmet: {unmet}")
        halt_reason = str(state.get("halt_reason", "")).strip()
        if halt_reason:
            lines.append(f"halt_reason: {halt_reason}")
        final = "\n".join(lines)
    except Exception as exc:
        log.error("reporter_failed", error=str(exc))
        final = f"error: reporter failed: {exc}"
    out = {**state, "final": final}
    return append_event(out, kind="node", data={"name": "reporter", "phase": "end"})
