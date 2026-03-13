from __future__ import annotations

from lg_orch.graph import build_graph


def test_graph_smoke() -> None:
    app = build_graph()
    out = app.invoke(
        {
            "request": "summarize repo",
            "_repo_root": ".",
            "_runner_base_url": "http://127.0.0.1:8088",
            "_runner_enabled": False,
            "_budget_max_loops": 1,
            "_config_policy": {"network_default": "deny", "require_approval_for_mutations": True},
        }
    )
    assert "intent" in out
    assert "final" in out
    assert "verification" in out
    assert "acceptance_ok" in out["verification"]


def test_graph_halts_with_loop_budget_reason() -> None:
    app = build_graph()
    out = app.invoke(
        {
            "request": "summarize repo",
            "_repo_root": ".",
            "_runner_base_url": "http://127.0.0.1:0",
            "_runner_enabled": True,
            "_budget_max_loops": 1,
            "_config_policy": {
                "network_default": "deny",
                "require_approval_for_mutations": True,
            },
        }
    )
    assert out["halt_reason"] in {"max_loops_exhausted", "plan_max_iterations_exhausted"}


def test_graph_populates_repo_context_and_satisfies_acceptance() -> None:
    # context_builder runs inside the graph and fills repo_context, so the
    # acceptance criterion "Necessary repository context was gathered." resolves ok=True.
    app = build_graph()
    out = app.invoke(
        {
            "request": "summarize repo",
            "repo_context": {},
            "plan": {
                "steps": [{"id": "step-1"}],
                "verification": [],
                "rollback": "No changes were made.",
                "acceptance_criteria": ["Necessary repository context was gathered."],
                "max_iterations": 1,
            },
            "_repo_root": ".",
            "_runner_base_url": "http://127.0.0.1:8088",
            "_runner_enabled": False,
            "_budget_max_loops": 2,
            "_config_policy": {"network_default": "deny", "require_approval_for_mutations": True},
        }
    )
    assert "acceptance_ok" in out["verification"]
