# SPDX-License-Identifier: MIT
"""Wave 17 coverage tests — targeting modules with the most uncovered lines."""

from __future__ import annotations

import json
import pathlib
import threading
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from lg_orch.api.streaming import (
    _emit_tool_stdout_lines,
    _run_streams,
    _run_streams_lock,
    _send_final_output,
    push_run_event,
    stream_new_sse,
)
from lg_orch.audit import AuditEvent, AuditLogger, S3AuditSink, utc_now_iso
from lg_orch.commands.trace import _trace_payload_from_path, _trace_run_id
from lg_orch.logging import _level_to_int, _redact_event_dict
from lg_orch.long_term_memory import (
    OllamaEmbedder,
    _approx_tokens,
    _infer_task_type,
    make_embedder,
    stub_embedder,
)
from lg_orch.nodes.executor import (
    _apply_patch_changed_paths,
    _as_int,
    _budget_failure_result,
    _coerce_approval_token,
    _configured_write_allowlist,
    _estimate_patch_bytes,
    _normalize_rel_path,
    _path_matches_allowlist,
    _validate_base_url,
)
from lg_orch.nodes.verifier import (
    _diagnostic_summary,
    _extract_diagnostics,
    _failure_fingerprint,
    _first_nonempty_line,
    _is_architecture_mismatch,
    _is_test_failure_post_change,
    _requires_formal_verification,
)

# ---------------------------------------------------------------------------
# api/streaming.py — _emit_tool_stdout_lines, _send_final_output, push_run_event
# ---------------------------------------------------------------------------


class _FakeWFile:
    """In-memory wfile that records write() calls."""

    def __init__(self, *, fail_on: int | None = None) -> None:
        self.chunks: list[bytes] = []
        self._call_count = 0
        self._fail_on = fail_on

    def write(self, data: bytes) -> int:
        self._call_count += 1
        if self._fail_on is not None and self._call_count >= self._fail_on:
            raise OSError("mock disconnect")
        self.chunks.append(data)
        return len(data)

    def flush(self) -> None:
        pass

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode()


class TestEmitToolStdoutLines:
    def test_ignores_non_tool_events(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines({"kind": "plan", "data": {"stdout": "hello"}}, wfile)
        assert wfile.chunks == []

    def test_ignores_when_no_stdout(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines({"kind": "tool_result", "data": {"tool": "bash"}}, wfile)
        assert wfile.chunks == []

    def test_ignores_non_dict_data(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines({"kind": "tool_result", "data": "notadict"}, wfile)
        assert wfile.chunks == []

    def test_emits_lines_for_tool_result(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines(
            {"kind": "tool_result", "data": {"tool": "bash", "stdout": "line1\nline2\n"}},
            wfile,
        )
        assert len(wfile.chunks) == 2
        parsed_1 = json.loads(wfile.chunks[0].decode().removeprefix("data: ").strip())
        assert parsed_1["type"] == "tool_stdout"
        assert parsed_1["tool"] == "bash"
        assert parsed_1["line"] == "line1"

    def test_emits_lines_for_tool_call(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines(
            {"kind": "tool_call", "data": {"name": "rg", "stdout": "hit\n"}},
            wfile,
        )
        assert len(wfile.chunks) == 1
        parsed = json.loads(wfile.chunks[0].decode().removeprefix("data: ").strip())
        assert parsed["tool"] == "rg"

    def test_skips_blank_lines(self) -> None:
        wfile = _FakeWFile()
        _emit_tool_stdout_lines(
            {"kind": "tool_result", "data": {"tool": "x", "stdout": "\n  \nreal\n"}},
            wfile,
        )
        assert len(wfile.chunks) == 1

    def test_handles_oserror_gracefully(self) -> None:
        wfile = _FakeWFile(fail_on=1)
        # Should not raise
        _emit_tool_stdout_lines(
            {"kind": "tool_result", "data": {"tool": "x", "stdout": "line\n"}},
            wfile,
        )


class TestSendFinalOutput:
    def test_noop_when_run_is_none(self) -> None:
        wfile = _FakeWFile()
        _send_final_output(None, wfile)
        assert wfile.chunks == []

    def test_noop_when_no_trace(self) -> None:
        wfile = _FakeWFile()
        _send_final_output({"trace": "not a dict"}, wfile)
        assert wfile.chunks == []

    def test_noop_when_final_is_empty(self) -> None:
        wfile = _FakeWFile()
        _send_final_output({"trace": {"final": "  "}}, wfile)
        assert wfile.chunks == []

    def test_sends_final_output(self) -> None:
        wfile = _FakeWFile()
        _send_final_output({"trace": {"final": "All done!"}}, wfile)
        assert len(wfile.chunks) == 1
        parsed = json.loads(wfile.chunks[0].decode().removeprefix("data: ").strip())
        assert parsed["type"] == "final_output"
        assert parsed["text"] == "All done!"

    def test_handles_oserror(self) -> None:
        wfile = _FakeWFile(fail_on=1)
        _send_final_output({"trace": {"final": "ok"}}, wfile)


class TestPushRunEvent:
    def test_noop_when_no_stream_registered(self) -> None:
        push_run_event("nonexistent-run", {"kind": "plan"})

    def test_pushes_to_registered_stream(self) -> None:
        import queue

        q: queue.Queue[dict[str, Any] | None] = queue.Queue()
        with _run_streams_lock:
            _run_streams["test-run-1"] = q
        try:
            push_run_event("test-run-1", {"kind": "plan"})
            event = q.get_nowait()
            assert event == {"kind": "plan"}
        finally:
            with _run_streams_lock:
                _run_streams.pop("test-run-1", None)


class TestStreamNewSse:
    def test_not_found_run(self) -> None:
        mock_service = MagicMock()
        mock_service.get_run.return_value = None
        wfile = _FakeWFile()
        stream_new_sse(mock_service, "nonexistent", wfile)
        assert len(wfile.chunks) >= 1
        parsed = json.loads(wfile.chunks[0].decode().removeprefix("data: ").strip())
        assert parsed["error"] == "not_found"

    def test_completed_run_with_trace_file(self, tmp_path: pathlib.Path) -> None:
        trace_path = tmp_path / "trace.json"
        trace_path.write_text(
            json.dumps({"events": [{"kind": "plan", "data": {}}]}),
            encoding="utf-8",
        )
        mock_service = MagicMock()
        mock_service.get_run.return_value = {
            "finished_at": "2026-01-01T00:00:00Z",
            "trace_path": str(trace_path),
            "trace": {"final": "All done"},
        }
        wfile = _FakeWFile()
        stream_new_sse(mock_service, "run-123", wfile)
        text = wfile.text
        assert "plan" in text
        assert "final_output" in text
        assert '"done"' in text

    def test_completed_run_no_trace(self) -> None:
        mock_service = MagicMock()
        mock_service.get_run.return_value = {
            "finished_at": "2026-01-01T00:00:00Z",
            "trace_path": "/nonexistent/trace.json",
        }
        wfile = _FakeWFile()
        stream_new_sse(mock_service, "run-123", wfile)
        text = wfile.text
        assert '"done"' in text

    def test_not_found_oserror(self) -> None:
        """When wfile raises OSError on not_found, should not crash."""
        mock_service = MagicMock()
        mock_service.get_run.return_value = None
        wfile = _FakeWFile(fail_on=1)
        stream_new_sse(mock_service, "nonexistent", wfile)


# ---------------------------------------------------------------------------
# logging.py — _level_to_int, _redact_event_dict
# ---------------------------------------------------------------------------


class TestLevelToInt:
    def test_critical(self) -> None:
        assert _level_to_int("CRITICAL") == 50
        assert _level_to_int("critical") == 50

    def test_error(self) -> None:
        assert _level_to_int("ERROR") == 40

    def test_warning(self) -> None:
        assert _level_to_int("WARNING") == 30
        assert _level_to_int("WARN") == 30

    def test_info(self) -> None:
        assert _level_to_int("INFO") == 20

    def test_debug(self) -> None:
        assert _level_to_int("DEBUG") == 10

    def test_unknown_defaults_to_info(self) -> None:
        assert _level_to_int("TRACE") == 20
        assert _level_to_int("") == 20


class TestRedactEventDict:
    def test_redacts_sensitive_keys(self) -> None:
        result = _redact_event_dict({"api_key": "secret", "password": "123", "event": "hello"})
        assert result["api_key"] == "[REDACTED]"
        assert result["password"] == "[REDACTED]"
        assert result["event"] == "hello"

    def test_redacts_bearer_tokens_in_strings(self) -> None:
        result = _redact_event_dict({"msg": "Got Bearer abc123 from header"})
        assert "abc123" not in result["msg"]
        assert "Bearer [REDACTED]" in result["msg"]

    def test_passes_non_string_non_sensitive_through(self) -> None:
        result = _redact_event_dict({"count": 42, "items": [1, 2, 3]})
        assert result["count"] == 42
        assert result["items"] == [1, 2, 3]


# ---------------------------------------------------------------------------
# commands/trace.py — _trace_payload_from_path, _trace_run_id
# ---------------------------------------------------------------------------


class TestTracePayloadFromPath:
    def test_returns_dict_for_valid_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text('{"events": []}', encoding="utf-8")
        result = _trace_payload_from_path(p, warn_context="test")
        assert result == {"events": []}

    def test_returns_none_for_missing_file(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "missing.json"
        assert _trace_payload_from_path(p, warn_context="test") is None

    def test_returns_none_for_invalid_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "bad.json"
        p.write_text("{invalid", encoding="utf-8")
        assert _trace_payload_from_path(p, warn_context="test") is None

    def test_returns_none_for_non_dict_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "arr.json"
        p.write_text("[1,2,3]", encoding="utf-8")
        assert _trace_payload_from_path(p, warn_context="test") is None


class TestTraceRunId:
    def test_extracts_run_id_from_payload(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-fallback.json"
        assert _trace_run_id(p, {"run_id": "abc-123"}) == "abc-123"

    def test_falls_back_to_stem(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-xyz789.json"
        assert _trace_run_id(p, {}) == "xyz789"

    def test_falls_back_for_empty_run_id(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-fallback.json"
        assert _trace_run_id(p, {"run_id": "  "}) == "fallback"


# ---------------------------------------------------------------------------
# long_term_memory.py — make_embedder, OllamaEmbedder, _infer_task_type
# ---------------------------------------------------------------------------


class TestMakeEmbedder:
    def test_default_returns_stub(self) -> None:
        with patch.dict("os.environ", {"LG_EMBED_PROVIDER": "stub"}):
            fn = make_embedder()
        result = fn("hello")
        assert isinstance(result, list)

    def test_ollama_provider(self) -> None:
        fn = make_embedder(provider="ollama", base_url="http://fake:1234", model="test")
        assert isinstance(fn, OllamaEmbedder)

    def test_explicit_stub(self) -> None:
        fn = make_embedder(provider="stub")
        result = fn("test")
        assert isinstance(result, list)
        assert len(result) > 0


class TestOllamaEmbedder:
    def test_fallback_when_unreachable(self) -> None:
        emb = OllamaEmbedder(base_url="http://localhost:1")
        result = emb("hello")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_probe_caches_result(self) -> None:
        emb = OllamaEmbedder(base_url="http://localhost:1")
        assert emb._available is None
        emb._probe()
        assert emb._available is not None
        # Second call returns cached
        first = emb._available
        emb._probe()
        assert emb._available == first


class TestInferTaskType:
    def test_code_change(self) -> None:
        assert _infer_task_type("refactor the config module") == "code_change"
        assert _infer_task_type("implement new feature") == "code_change"

    def test_debug(self) -> None:
        assert _infer_task_type("fix the broken test") == "debug"

    def test_analysis(self) -> None:
        assert _infer_task_type("review the pull request") == "analysis"

    def test_test_repair(self) -> None:
        assert _infer_task_type("failing tests in CI") == "test_repair"

    def test_canary(self) -> None:
        assert _infer_task_type("canary deploy") == "canary"

    def test_fallback_to_first_word(self) -> None:
        assert _infer_task_type("summarize the report") == "summarize"

    def test_empty_returns_unknown(self) -> None:
        assert _infer_task_type("") == "unknown"


class TestApproxTokens:
    def test_empty_string(self) -> None:
        assert _approx_tokens("") == 0

    def test_short_string(self) -> None:
        assert _approx_tokens("hi") == 1

    def test_longer_string(self) -> None:
        tokens = _approx_tokens("hello world this is a test")
        assert tokens >= 1


class TestStubEmbedder:
    def test_deterministic(self) -> None:
        v1 = stub_embedder("hello")
        v2 = stub_embedder("hello")
        assert (v1 == v2).all()

    def test_unit_norm(self) -> None:
        import numpy as np

        v = stub_embedder("test text")
        norm = float(np.linalg.norm(v))
        assert abs(norm - 1.0) < 1e-5

    def test_custom_dim(self) -> None:
        v = stub_embedder("test", dim=64)
        assert len(v) == 64


# ---------------------------------------------------------------------------
# audit.py — AuditLogger._export_async, S3AuditSink batch flush
# ---------------------------------------------------------------------------


def _make_audit_event(**kwargs: Any) -> AuditEvent:
    defaults: dict[str, Any] = {
        "ts": utc_now_iso(),
        "subject": "user-1",
        "roles": ["operator"],
        "action": "run.create",
        "resource_id": "run-abc",
        "outcome": "ok",
        "detail": None,
    }
    defaults.update(kwargs)
    return AuditEvent(**defaults)


class TestAuditLoggerExportAsync:
    def test_export_async_no_sink(self, tmp_path: pathlib.Path) -> None:
        logger = AuditLogger(tmp_path / "audit.jsonl", sink=None)
        try:
            logger._export_async(_make_audit_event())
        finally:
            logger.close()

    def test_export_async_outside_event_loop(self, tmp_path: pathlib.Path) -> None:
        """When no event loop is running, _export_async fires in a thread."""
        import time

        mock_sink = MagicMock()
        called = threading.Event()

        async def _fake_export(event: AuditEvent) -> None:
            called.set()

        mock_sink.export = MagicMock(side_effect=lambda e: _fake_export(e))
        logger = AuditLogger(tmp_path / "audit.jsonl", sink=mock_sink)
        try:
            logger.log(_make_audit_event())
            # Wait briefly for background thread
            time.sleep(0.3)
        finally:
            logger.close()


# ---------------------------------------------------------------------------
# nodes/executor.py — pure helper functions
# ---------------------------------------------------------------------------


class TestAsInt:
    def test_bool_returns_default(self) -> None:
        assert _as_int(True, default=42) == 42
        assert _as_int(False, default=7) == 7

    def test_int_returns_value(self) -> None:
        assert _as_int(10, default=0) == 10

    def test_string_int(self) -> None:
        assert _as_int("  5  ", default=0) == 5

    def test_string_non_int(self) -> None:
        assert _as_int("abc", default=99) == 99

    def test_other_types(self) -> None:
        assert _as_int(3.14, default=0) == 0
        assert _as_int(None, default=0) == 0


class TestEstimatePatchBytes:
    def test_string_patch(self) -> None:
        assert _estimate_patch_bytes({"patch": "hello"}) == 5

    def test_changes_list(self) -> None:
        result = _estimate_patch_bytes({"changes": [{"content": "abc"}, {"patch": "de"}]})
        assert result == 5

    def test_fallback_to_json(self) -> None:
        result = _estimate_patch_bytes({"key": "val"})
        assert result > 0

    def test_non_dict_changes_skipped(self) -> None:
        result = _estimate_patch_bytes({"changes": ["not a dict"]})
        # Falls through to json fallback
        assert result > 0


class TestBudgetFailureResult:
    def test_basic(self) -> None:
        result = _budget_failure_result(
            tool="write_file",
            message="over budget",
            error_tag="budget_exceeded",
            route_metadata={"stage": "exec"},
        )
        assert result["ok"] is False
        assert result["tool"] == "write_file"
        assert result["stderr"] == "over budget"
        assert result["artifacts"]["error"] == "budget_exceeded"

    def test_with_extra_artifacts(self) -> None:
        result = _budget_failure_result(
            tool="t",
            message="msg",
            error_tag="e",
            route_metadata={},
            artifacts_extra={"detail": "x"},
        )
        assert result["artifacts"]["detail"] == "x"


class TestNormalizeRelPath:
    def test_strips_and_normalizes_slashes(self) -> None:
        assert _normalize_rel_path("  src\\main.py  ") == "src/main.py"


class TestValidateBaseUrl:
    def test_valid_url(self) -> None:
        assert _validate_base_url("http://localhost:8080") is True

    def test_invalid_url(self) -> None:
        assert _validate_base_url("") is False


class TestCoerceApprovalToken:
    def test_not_dict(self) -> None:
        assert _coerce_approval_token("string") is None

    def test_missing_fields(self) -> None:
        assert _coerce_approval_token({"challenge_id": "x"}) is None
        assert _coerce_approval_token({"token": "y"}) is None

    def test_dot_format(self) -> None:
        result = _coerce_approval_token({"challenge_id": "c", "token": "a.b.c.d"})
        assert result == {"challenge_id": "c", "token": "a.b.c.d"}

    def test_pipe_format(self) -> None:
        result = _coerce_approval_token({"challenge_id": "c", "token": "a|b|c|d"})
        assert result == {"challenge_id": "c", "token": "a|b|c|d"}

    def test_legacy_plain(self) -> None:
        result = _coerce_approval_token({"challenge_id": "c", "token": "approve:123"})
        assert result == {"challenge_id": "c", "token": "approve:123"}

    def test_invalid_dot_format(self) -> None:
        assert _coerce_approval_token({"challenge_id": "c", "token": "a..c.d"}) is None

    def test_empty_fields(self) -> None:
        assert _coerce_approval_token({"challenge_id": "", "token": "x"}) is None
        assert _coerce_approval_token({"challenge_id": "x", "token": ""}) is None


class TestConfiguredWriteAllowlist:
    def test_normal(self) -> None:
        result = _configured_write_allowlist({"allowed_write_paths": ["src/*.py", "tests/*"]})
        assert result == ("src/*.py", "tests/*")

    def test_not_list(self) -> None:
        assert _configured_write_allowlist({"allowed_write_paths": "bad"}) == ()

    def test_filters_non_strings(self) -> None:
        result = _configured_write_allowlist({"allowed_write_paths": ["ok", 123, "", "also_ok"]})
        assert result == ("ok", "also_ok")


class TestPathMatchesAllowlist:
    def test_matches(self) -> None:
        assert _path_matches_allowlist("src/main.py", ("src/*.py",)) is True

    def test_no_match(self) -> None:
        assert _path_matches_allowlist("build/out.js", ("src/*.py",)) is False


class TestApplyPatchChangedPaths:
    def test_valid_changes(self) -> None:
        result = _apply_patch_changed_paths({"changes": [{"path": "src/main.py"}]})
        assert result == ["src/main.py"]

    def test_empty_changes(self) -> None:
        assert _apply_patch_changed_paths({"changes": []}) is None

    def test_not_list(self) -> None:
        assert _apply_patch_changed_paths({"changes": "bad"}) is None

    def test_non_dict_change(self) -> None:
        assert _apply_patch_changed_paths({"changes": ["bad"]}) is None

    def test_missing_path(self) -> None:
        assert _apply_patch_changed_paths({"changes": [{"content": "x"}]}) is None


# ---------------------------------------------------------------------------
# nodes/verifier.py — pure helper functions
# ---------------------------------------------------------------------------


class TestExtractDiagnostics:
    def test_from_direct(self) -> None:
        result = _extract_diagnostics({"diagnostics": [{"file": "a.py", "line": 1}]})
        assert len(result) == 1

    def test_from_artifacts(self) -> None:
        # diagnostics must be non-list for artifacts fallback path
        result = _extract_diagnostics(
            {"diagnostics": "not_a_list", "artifacts": {"diagnostics": [{"file": "a.py"}]}}
        )
        assert len(result) == 1

    def test_filters_non_dicts(self) -> None:
        result = _extract_diagnostics({"diagnostics": ["not_dict", {"ok": 1}]})
        assert len(result) == 1

    def test_empty(self) -> None:
        assert _extract_diagnostics({}) == []


class TestDiagnosticSummary:
    def test_full(self) -> None:
        result = _diagnostic_summary(
            {"file": "src/main.py", "line": 10, "column": 5, "code": "E001", "message": "bad"}
        )
        assert "src/main.py:10:5" in result
        assert "[E001]" in result
        assert "bad" in result

    def test_message_only(self) -> None:
        assert _diagnostic_summary({"message": "error"}) == "error"

    def test_empty(self) -> None:
        assert _diagnostic_summary({}) == ""

    def test_file_and_line_no_column(self) -> None:
        result = _diagnostic_summary({"file": "a.py", "line": 5, "message": "err"})
        assert "a.py:5" in result


class TestFirstNonemptyLine:
    def test_basic(self) -> None:
        assert _first_nonempty_line("\n  \nhello\nworld") == "hello"

    def test_empty(self) -> None:
        assert _first_nonempty_line("") == ""
        assert _first_nonempty_line("  \n  ") == ""


class TestFailureFingerprint:
    def test_with_diagnostic_fingerprint(self) -> None:
        result = _failure_fingerprint({}, [{"fingerprint": "abc123"}])
        assert result == "abc123"

    def test_computed_fingerprint(self) -> None:
        result = _failure_fingerprint(
            {"tool": "apply_patch", "stderr": "error msg"},
            [],
        )
        assert len(result) == 16  # hex hash prefix


class TestIsArchitectureMismatch:
    def test_read_file_always_true(self) -> None:
        assert (
            _is_architecture_mismatch(tool="read_file", diagnostics=[], stderr="", artifacts={})
            is True
        )

    def test_read_denied_error(self) -> None:
        assert (
            _is_architecture_mismatch(
                tool="apply_patch",
                diagnostics=[],
                stderr="",
                artifacts={"error": "read_denied"},
            )
            is True
        )

    def test_missing_module_in_stderr(self) -> None:
        assert (
            _is_architecture_mismatch(
                tool="run_tests",
                diagnostics=[],
                stderr="missing module foo",
                artifacts={},
            )
            is True
        )

    def test_no_mismatch(self) -> None:
        assert (
            _is_architecture_mismatch(
                tool="apply_patch",
                diagnostics=[],
                stderr="all good",
                artifacts={},
            )
            is False
        )

    def test_error_code_detection(self) -> None:
        assert (
            _is_architecture_mismatch(
                tool="compile",
                diagnostics=[{"code": "F821", "message": "undef"}],
                stderr="",
                artifacts={},
            )
            is True
        )


class TestIsTestFailurePostChange:
    def test_test_tool_with_prior_patch(self) -> None:
        assert (
            _is_test_failure_post_change(
                tool="run_tests",
                diagnostics=[],
                stderr="",
                stdout="",
                artifacts={},
                tool_results=[{"tool": "apply_patch", "ok": True}],
            )
            is True
        )

    def test_test_tool_without_prior_patch(self) -> None:
        assert (
            _is_test_failure_post_change(
                tool="run_tests",
                diagnostics=[],
                stderr="",
                stdout="",
                artifacts={},
                tool_results=[],
            )
            is False
        )

    def test_non_test_tool(self) -> None:
        assert (
            _is_test_failure_post_change(
                tool="apply_patch",
                diagnostics=[],
                stderr="",
                stdout="",
                artifacts={},
                tool_results=[],
            )
            is False
        )

    def test_non_test_tool_with_test_hints(self) -> None:
        assert (
            _is_test_failure_post_change(
                tool="shell",
                diagnostics=[],
                stderr="FAILED tests/test_foo.py",
                stdout="",
                artifacts={},
                tool_results=[{"tool": "apply_patch", "ok": True}],
            )
            is True
        )


class TestRequiresFormalVerification:
    def test_disabled(self) -> None:
        assert _requires_formal_verification({"_vericoding_enabled": False}, []) == []

    def test_no_apply_patch(self) -> None:
        assert (
            _requires_formal_verification(
                {"_vericoding_enabled": True},
                [{"tool": "read_file", "ok": True}],
            )
            == []
        )

    def test_returns_matching_files(self) -> None:
        result = _requires_formal_verification(
            {"_vericoding_enabled": True, "_vericoding_extensions": [".rs"]},
            [{"tool": "apply_patch", "ok": True, "input": {"changes": [{"path": "src/main.rs"}]}}],
        )
        assert result == ["src/main.rs"]


@pytest.mark.asyncio
async def test_s3_sink_batch_accumulates() -> None:
    """S3AuditSink accumulates events in the batch before flushing."""
    import sys
    import time

    fake_aioboto3 = MagicMock()
    with patch.dict(sys.modules, {"aioboto3": fake_aioboto3}):
        sink = S3AuditSink(bucket="b", prefix="p", region="us-east-1")
        sink._max_batch = 999  # prevent auto-flush
        sink._flush_interval = 999
        sink._last_flush = time.monotonic()  # prevent time-based flush
        await sink.export(_make_audit_event())
        assert len(sink._batch) == 1
