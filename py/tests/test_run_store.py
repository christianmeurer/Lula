from __future__ import annotations

from pathlib import Path

import pytest

from lg_orch.run_store import RunStore


def _make_record(run_id: str = "run1", status: str = "running") -> dict:
    return {
        "run_id": run_id,
        "request": "do something",
        "status": status,
        "created_at": "2026-01-01T00:00:00Z",
        "started_at": "2026-01-01T00:00:00Z",
        "finished_at": None,
        "exit_code": None,
        "trace_out_dir": "artifacts/runs",
        "trace_path": f"artifacts/runs/run-{run_id}.json",
        "request_id": "req-abc",
        "auth_subject": "",
        "client_ip": "127.0.0.1",
    }


def test_create_table_and_upsert(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        store.upsert(_make_record())
        rows = store.list_runs()
        assert len(rows) == 1
        assert rows[0]["run_id"] == "run1"
    finally:
        store.close()


def test_get_run(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        store.upsert(_make_record("r2"))
        row = store.get_run("r2")
        assert row is not None
        assert row["run_id"] == "r2"
        assert row["request"] == "do something"
    finally:
        store.close()


def test_get_run_missing(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        assert store.get_run("nonexistent") is None
    finally:
        store.close()


def test_list_runs_empty(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        assert store.list_runs() == []
    finally:
        store.close()


def test_list_runs_ordered_by_created_at_desc(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        r1 = _make_record("r1")
        r1["created_at"] = "2026-01-01T00:00:00Z"
        r2 = _make_record("r2")
        r2["created_at"] = "2026-01-02T00:00:00Z"
        store.upsert(r1)
        store.upsert(r2)
        rows = store.list_runs()
        assert rows[0]["run_id"] == "r2"
        assert rows[1]["run_id"] == "r1"
    finally:
        store.close()


def test_upsert_idempotent_update(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        store.upsert(_make_record("r3", status="running"))
        updated = _make_record("r3", status="succeeded")
        updated["exit_code"] = 0
        updated["finished_at"] = "2026-01-01T00:01:00Z"
        store.upsert(updated)
        row = store.get_run("r3")
        assert row is not None
        assert row["status"] == "succeeded"
        assert row["exit_code"] == 0
        assert row["finished_at"] == "2026-01-01T00:01:00Z"
    finally:
        store.close()


def test_upsert_unknown_keys_ignored(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        record = _make_record("r4")
        record["log_lines"] = 42  # type: ignore[assignment]  # not a DB column
        record["cancel_requested"] = True  # type: ignore[assignment]
        store.upsert(record)
        row = store.get_run("r4")
        assert row is not None
        assert row["run_id"] == "r4"
    finally:
        store.close()


def test_db_created_on_disk(tmp_path: Path) -> None:
    db_path = tmp_path / "sub" / "runs.sqlite"
    store = RunStore(db_path=db_path)
    store.close()
    assert db_path.exists()


# ---------------------------------------------------------------------------
# recovery_facts / episodic memory
# ---------------------------------------------------------------------------

def _make_fact(
    fingerprint: str = "fp1",
    failure_class: str = "lint",
    summary: str = "test failed",
    loop: int = 1,
    salience: int = 5,
) -> dict:
    return {
        "failure_fingerprint": fingerprint,
        "failure_class": failure_class,
        "summary": summary,
        "loop": loop,
        "salience": salience,
        "last_check": "ruff",
        "context_scope": "py/",
        "plan_action": "retry",
    }


def test_upsert_recovery_facts_stores_rows(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        facts = [_make_fact("fp1"), _make_fact("fp2", failure_class="typecheck")]
        store.upsert_recovery_facts("run-A", facts)
        rows = store.get_recent_recovery_facts()
        fingerprints = {r["fingerprint"] for r in rows}
        assert "fp1" in fingerprints
        assert "fp2" in fingerprints
    finally:
        store.close()


def test_get_recent_recovery_facts_by_fingerprint(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        store.upsert_recovery_facts("run-B", [_make_fact("target_fp"), _make_fact("other_fp")])
        rows = store.get_recent_recovery_facts(fingerprint="target_fp")
        assert len(rows) == 1
        assert rows[0]["fingerprint"] == "target_fp"
    finally:
        store.close()


def test_get_recent_recovery_facts_by_class(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        store.upsert_recovery_facts("run-C", [
            _make_fact("fp3", failure_class="mypy"),
            _make_fact("fp4", failure_class="lint"),
        ])
        # fingerprint lookup yields nothing for "fp_nope", falls back to failure_class
        rows = store.get_recent_recovery_facts(fingerprint="fp_nope", failure_class="mypy")
        assert len(rows) == 1
        assert rows[0]["failure_class"] == "mypy"
    finally:
        store.close()


def test_upsert_recovery_facts_skips_empty_fingerprint(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        bad_fact: dict = {"failure_fingerprint": "", "summary": "should be ignored"}
        store.upsert_recovery_facts("run-D", [bad_fact])
        rows = store.get_recent_recovery_facts()
        assert rows == []
    finally:
        store.close()


def test_get_episodic_context_returns_empty_when_no_match(tmp_path: Path) -> None:
    store = RunStore(db_path=tmp_path / "runs.sqlite")
    try:
        result = store.get_episodic_context(
            failure_fingerprint="no_such_fp",
            failure_class="no_such_class",
        )
        assert result == []
    finally:
        store.close()
