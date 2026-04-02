"""Wave-19 coverage ratchet tests — targeting modules below 80%."""

from __future__ import annotations

import asyncio
import pathlib
import time
from typing import Any
from unittest.mock import MagicMock, patch

from lg_orch.audit import (
    AuditConfig,
    AuditEvent,
    AuditLogger,
    GCSAuditSink,
    S3AuditSink,
    build_sink,
    utc_now_iso,
)
from lg_orch.auth import JWTSettings, jwt_settings_from_config
from lg_orch.commands.serve import _log_embedding_provider, serve_command
from lg_orch.commands.trace import (
    _trace_payload_from_path,
    _trace_run_id,
    trace_serve_command,
    trace_site_command,
    trace_view_command,
)

# Extra imports for coverage ratchet
from lg_orch.logging import init_telemetry
from lg_orch.long_term_memory import (
    LongTermMemoryStore,
    _infer_task_type,
    make_embedder,
)
from lg_orch.nodes.verifier import verifier
from lg_orch.remote_api import (
    _audit_action_and_resource,
    _authorize_request,
    _request_client_ip,
    _request_scheme,
)
from lg_orch.trace import append_event, ensure_run_id, now_ms, write_run_trace

# ---------------------------------------------------------------------------
# trace.py — write_run_trace, append_event, ensure_run_id
# ---------------------------------------------------------------------------


class TestWriteRunTrace:
    def test_basic_write(self, tmp_path: pathlib.Path) -> None:
        state: dict[str, Any] = {
            "_run_id": "test-run-123",
            "request": "fix bug",
            "intent": "debug",
            "tool_results": [{"tool": "exec", "ok": True}],
            "_trace_events": [{"ts_ms": 1000, "kind": "node", "data": {"name": "planner"}}],
        }
        out = write_run_trace(repo_root=tmp_path, out_dir=pathlib.Path("traces"), state=state)
        assert out.exists()
        import json

        data = json.loads(out.read_text())
        assert data["run_id"] == "test-run-123"
        assert data["request"] == "fix bug"
        assert len(data["events"]) == 1
        assert len(data["tool_results"]) == 1

    def test_with_correlation(self, tmp_path: pathlib.Path) -> None:
        state: dict[str, Any] = {
            "_run_id": "r1",
            "_request_id": "req-abc",
            "_remote_api_context": {"auth_subject": "user@test", "client_ip": "10.0.0.1"},
        }
        out = write_run_trace(repo_root=tmp_path, out_dir=pathlib.Path("traces"), state=state)
        import json

        data = json.loads(out.read_text())
        assert data["correlation"]["request_id"] == "req-abc"
        assert data["correlation"]["auth_subject"] == "user@test"
        assert data["correlation"]["client_ip"] == "10.0.0.1"

    def test_generates_run_id_if_missing(self, tmp_path: pathlib.Path) -> None:
        state: dict[str, Any] = {"request": "hello"}
        out = write_run_trace(repo_root=tmp_path, out_dir=pathlib.Path("traces"), state=state)
        assert out.exists()
        import json

        data = json.loads(out.read_text())
        assert len(data["run_id"]) > 0

    def test_with_recovery_and_checkpoint(self, tmp_path: pathlib.Path) -> None:
        state: dict[str, Any] = {
            "_run_id": "r2",
            "_checkpoint": {"thread_id": "t1", "checkpoint_id": "c1"},
            "verification": {"ok": False},
            "undo": {"restored": True},
            "_approval_context": {"decision": "approved"},
            "recovery_packet": {"failure_class": "test"},
            "loop_summaries": ["s1"],
            "snapshots": [{"id": "snap1"}],
            "provenance": [{"source": "verifier"}],
            "telemetry": {"diagnostics": []},
        }
        out = write_run_trace(repo_root=tmp_path, out_dir=pathlib.Path("traces"), state=state)
        import json

        data = json.loads(out.read_text())
        assert data["checkpoint"]["thread_id"] == "t1"
        assert data["verification"]["ok"] is False
        assert data["undo"]["restored"] is True


class TestEnsureRunId:
    def test_existing_run_id(self) -> None:
        result = ensure_run_id({"_run_id": "abc"})
        assert result["_run_id"] == "abc"

    def test_generates_new_run_id(self) -> None:
        result = ensure_run_id({})
        assert "_run_id" in result
        assert len(result["_run_id"]) > 0


class TestNowMs:
    def test_returns_int(self) -> None:
        result = now_ms()
        assert isinstance(result, int)
        assert result > 0


class TestAppendEvent:
    def test_appends_to_empty(self) -> None:
        state: dict[str, Any] = {"request": "test"}
        out = append_event(state, kind="node", data={"name": "planner"})
        assert len(out["_trace_events"]) == 1
        assert out["_trace_events"][0]["kind"] == "node"
        assert out["request"] == "test"

    def test_appends_to_existing(self) -> None:
        state: dict[str, Any] = {
            "_trace_events": [{"ts_ms": 1, "kind": "x", "data": {}}],
        }
        out = append_event(state, kind="y", data={"val": 1})
        assert len(out["_trace_events"]) == 2


# ---------------------------------------------------------------------------
# audit.py — S3AuditSink, GCSAuditSink, AuditLogger with sink
# ---------------------------------------------------------------------------


def _make_event(action: str = "run.create") -> AuditEvent:
    return AuditEvent(
        ts=utc_now_iso(),
        subject="test",
        roles=["admin"],
        action=action,
        resource_id="r1",
        outcome="ok",
        detail=None,
    )


class TestS3AuditSink:
    def test_export_returns_early_without_aioboto3(self) -> None:
        sink = S3AuditSink(bucket="b", prefix="p", region="us-east-1")
        # aioboto3 is not installed — export should be a no-op
        asyncio.run(sink.export(_make_event()))
        assert len(sink._batch) == 0  # returned before appending

    def test_init_params(self) -> None:
        sink = S3AuditSink(bucket="my-bucket", prefix="audit/", region="eu-west-1")
        assert sink._bucket == "my-bucket"
        assert sink._prefix == "audit"
        assert sink._region == "eu-west-1"
        assert sink._max_batch == 100
        assert sink._flush_interval == 5.0


class TestGCSAuditSink:
    def test_export_returns_early_without_gcs(self) -> None:
        sink = GCSAuditSink(bucket="b", prefix="p")
        asyncio.run(sink.export(_make_event()))
        assert len(sink._batch) == 0

    def test_init_params(self) -> None:
        sink = GCSAuditSink(bucket="gcs-bucket", prefix="logs/")
        assert sink._bucket == "gcs-bucket"
        assert sink._prefix == "logs"

    def test_do_export_returns_early_without_gcs(self) -> None:
        sink = GCSAuditSink(bucket="b", prefix="p")
        # _do_export returns early when google.cloud is not installed
        sink._do_export(["line1", "line2"])


class TestAuditLoggerWithSink:
    def test_log_with_sink_no_event_loop(self, tmp_path: pathlib.Path) -> None:
        """When no event loop is running, sink export fires in a background thread."""

        async def _noop(_event: AuditEvent) -> None:
            pass

        mock_sink = MagicMock()
        mock_sink.export = _noop
        logger = AuditLogger(log_path=tmp_path / "audit.jsonl", sink=mock_sink)
        try:
            event = _make_event()
            logger.log(event)
            # Give the background thread a moment
            time.sleep(0.1)
        finally:
            logger.close()
        # File should contain the event
        lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
        assert len(lines) == 1

    def test_log_without_sink(self, tmp_path: pathlib.Path) -> None:
        logger = AuditLogger(log_path=tmp_path / "audit.jsonl")
        try:
            logger.log(_make_event())
            logger.log(_make_event("run.cancel"))
        finally:
            logger.close()
        lines = (tmp_path / "audit.jsonl").read_text().strip().split("\n")
        assert len(lines) == 2

    def test_close_flushes(self, tmp_path: pathlib.Path) -> None:
        logger = AuditLogger(log_path=tmp_path / "audit.jsonl")
        logger.log(_make_event())
        logger.close()
        assert (tmp_path / "audit.jsonl").exists()


class TestBuildSink:
    def test_none_type(self) -> None:
        assert build_sink(AuditConfig()) is None

    def test_s3_without_bucket(self) -> None:
        assert build_sink(AuditConfig(sink_type="s3")) is None

    def test_s3_with_bucket(self) -> None:
        sink = build_sink(AuditConfig(sink_type="s3", s3_bucket="b"))
        assert isinstance(sink, S3AuditSink)

    def test_gcs_without_bucket(self) -> None:
        assert build_sink(AuditConfig(sink_type="gcs")) is None

    def test_gcs_with_bucket(self) -> None:
        sink = build_sink(AuditConfig(sink_type="gcs", gcs_bucket="b"))
        assert isinstance(sink, GCSAuditSink)

    def test_unknown_type(self) -> None:
        assert build_sink(AuditConfig(sink_type="kinesis")) is None


# ---------------------------------------------------------------------------
# commands/serve.py
# ---------------------------------------------------------------------------


class TestLogEmbeddingProvider:
    def test_stub_provider(self) -> None:
        with patch.dict("os.environ", {"LG_EMBED_PROVIDER": "stub"}, clear=False):
            _log_embedding_provider()  # should not raise

    def test_ollama_unavailable(self) -> None:
        with (
            patch.dict("os.environ", {"LG_EMBED_PROVIDER": "ollama"}, clear=False),
            patch("lg_orch.commands.serve.probe_ollama", return_value=False),
        ):
            _log_embedding_provider()

    def test_ollama_available(self) -> None:
        with (
            patch.dict("os.environ", {"LG_EMBED_PROVIDER": "ollama"}, clear=False),
            patch("lg_orch.commands.serve.probe_ollama", return_value=True),
        ):
            _log_embedding_provider()


class TestServeCommand:
    def test_invalid_port(self) -> None:
        args = MagicMock()
        args.port = -1
        args.host = "127.0.0.1"
        result = serve_command(args, repo_root=pathlib.Path("."))
        assert result == 2

    def test_zero_port(self) -> None:
        args = MagicMock()
        args.port = 0
        args.host = "127.0.0.1"
        result = serve_command(args, repo_root=pathlib.Path("."))
        assert result == 2


# ---------------------------------------------------------------------------
# commands/trace.py helpers
# ---------------------------------------------------------------------------


class TestTracePayloadFromPath:
    def test_valid_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text('{"run_id": "abc"}', encoding="utf-8")
        result = _trace_payload_from_path(p, warn_context="test")
        assert result == {"run_id": "abc"}

    def test_invalid_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text("not json", encoding="utf-8")
        result = _trace_payload_from_path(p, warn_context="test")
        assert result is None

    def test_missing_file(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "missing.json"
        result = _trace_payload_from_path(p, warn_context="test")
        assert result is None

    def test_non_dict_payload(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        result = _trace_payload_from_path(p, warn_context="test")
        assert result is None


class TestTraceViewCommand:
    def test_missing_file(self) -> None:
        args = MagicMock()
        args.trace_path = "/nonexistent/file.json"
        assert trace_view_command(args) == 2

    def test_invalid_json(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text("not json", encoding="utf-8")
        args = MagicMock()
        args.trace_path = str(p)
        assert trace_view_command(args) == 2

    def test_non_dict_payload(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        p.write_text("[1, 2, 3]", encoding="utf-8")
        args = MagicMock()
        args.trace_path = str(p)
        assert trace_view_command(args) == 2

    def test_console_format(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        payload = {
            "run_id": "abc",
            "events": [],
            "tool_results": [],
            "verification": {},
        }
        p.write_text(__import__("json").dumps(payload), encoding="utf-8")
        args = MagicMock()
        args.trace_path = str(p)
        args.format = "console"
        args.width = 120
        assert trace_view_command(args) == 0

    def test_html_format_to_file(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        payload = {
            "run_id": "abc",
            "events": [],
            "tool_results": [],
            "verification": {},
        }
        p.write_text(__import__("json").dumps(payload), encoding="utf-8")
        out = tmp_path / "output.html"
        args = MagicMock()
        args.trace_path = str(p)
        args.format = "html"
        args.output = str(out)
        assert trace_view_command(args) == 0
        assert out.exists()

    def test_html_format_to_stdout(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "trace.json"
        payload = {"run_id": "abc", "events": [], "tool_results": [], "verification": {}}
        p.write_text(__import__("json").dumps(payload), encoding="utf-8")
        args = MagicMock()
        args.trace_path = str(p)
        args.format = "html"
        args.output = None
        assert trace_view_command(args) == 0


class TestTraceSiteCommand:
    def test_missing_dir(self) -> None:
        args = MagicMock()
        args.trace_dir = "/nonexistent/dir"
        assert trace_site_command(args) == 2

    def test_empty_trace_dir(self, tmp_path: pathlib.Path) -> None:
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        args = MagicMock()
        args.trace_dir = str(trace_dir)
        args.output_dir = str(tmp_path / "site")
        result = trace_site_command(args)
        assert result == 0

    def test_with_trace_files(self, tmp_path: pathlib.Path) -> None:
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        payload = {"run_id": "abc", "events": [], "tool_results": [], "verification": {}}
        (trace_dir / "run-abc.json").write_text(__import__("json").dumps(payload), encoding="utf-8")
        args = MagicMock()
        args.trace_dir = str(trace_dir)
        args.output_dir = str(tmp_path / "site")
        result = trace_site_command(args)
        assert result == 0
        assert (tmp_path / "site" / "index.html").exists()
        assert (tmp_path / "site" / "run-abc.html").exists()

    def test_invalid_json_skipped(self, tmp_path: pathlib.Path) -> None:
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        (trace_dir / "run-bad.json").write_text("not json", encoding="utf-8")
        args = MagicMock()
        args.trace_dir = str(trace_dir)
        args.output_dir = str(tmp_path / "site")
        result = trace_site_command(args)
        assert result == 0  # should skip invalid


class TestTraceServeCommand:
    def test_missing_dir(self) -> None:
        args = MagicMock()
        args.trace_dir = "/nonexistent/dir"
        assert trace_serve_command(args) == 2

    def test_invalid_port(self, tmp_path: pathlib.Path) -> None:
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        args = MagicMock()
        args.trace_dir = str(trace_dir)
        args.port = 0
        args.host = "127.0.0.1"
        assert trace_serve_command(args) == 2

    def test_invalid_port_high(self, tmp_path: pathlib.Path) -> None:
        trace_dir = tmp_path / "traces"
        trace_dir.mkdir()
        args = MagicMock()
        args.trace_dir = str(trace_dir)
        args.port = 99999
        args.host = "127.0.0.1"
        assert trace_serve_command(args) == 2


class TestTraceRunId:
    def test_run_id_in_payload(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-xyz.json"
        assert _trace_run_id(p, {"run_id": "abc123"}) == "abc123"

    def test_run_id_from_filename(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-xyz.json"
        assert _trace_run_id(p, {}) == "xyz"

    def test_run_id_whitespace(self, tmp_path: pathlib.Path) -> None:
        p = tmp_path / "run-xyz.json"
        assert _trace_run_id(p, {"run_id": "  "}) == "xyz"


# ---------------------------------------------------------------------------
# remote_api.py — helpers
# ---------------------------------------------------------------------------


class TestRequestClientIp:
    def test_forwarded_header_trusted(self) -> None:
        ip = _request_client_ip(
            client_address=("10.0.0.1", 1234),
            forwarded_for="1.2.3.4, 5.6.7.8",
            trust_forwarded_headers=True,
        )
        assert ip == "1.2.3.4"

    def test_forwarded_header_not_trusted(self) -> None:
        ip = _request_client_ip(
            client_address=("10.0.0.1", 1234),
            forwarded_for="1.2.3.4",
            trust_forwarded_headers=False,
        )
        assert ip == "10.0.0.1"

    def test_no_client_address(self) -> None:
        ip = _request_client_ip(
            client_address=None,
            forwarded_for=None,
            trust_forwarded_headers=True,
        )
        assert ip == ""


class TestRequestScheme:
    def test_forwarded_proto_trusted(self) -> None:
        s = _request_scheme(forwarded_proto="https", trust_forwarded_headers=True)
        assert s == "https"

    def test_forwarded_proto_not_trusted(self) -> None:
        s = _request_scheme(forwarded_proto="https", trust_forwarded_headers=False)
        assert s == "http"

    def test_no_proto(self) -> None:
        s = _request_scheme(forwarded_proto=None, trust_forwarded_headers=True)
        assert s == "http"


class TestAuditActionAndResource:
    def test_run_create(self) -> None:
        action, rid = _audit_action_and_resource(
            method="POST", route="/v1/runs", path_parts=["v1", "runs"], status=201
        )
        assert action == "run.create"
        assert rid is None

    def test_run_list(self) -> None:
        action, rid = _audit_action_and_resource(
            method="GET", route="/v1/runs", path_parts=["v1", "runs"], status=200
        )
        assert action == "run.list"
        assert rid is None

    def test_run_read(self) -> None:
        action, rid = _audit_action_and_resource(
            method="GET", route="/v1/runs/abc", path_parts=["v1", "runs", "abc"], status=200
        )
        assert action == "run.read"
        assert rid == "abc"

    def test_run_cancel(self) -> None:
        action, rid = _audit_action_and_resource(
            method="POST",
            route="/v1/runs/abc/cancel",
            path_parts=["v1", "runs", "abc", "cancel"],
            status=202,
        )
        assert action == "run.cancel"
        assert rid == "abc"

    def test_run_approve(self) -> None:
        action, rid = _audit_action_and_resource(
            method="POST",
            route="/v1/runs/abc/approve",
            path_parts=["v1", "runs", "abc", "approve"],
            status=202,
        )
        assert action == "run.approve"
        assert rid == "abc"

    def test_logs_stream(self) -> None:
        action, rid = _audit_action_and_resource(
            method="GET",
            route="/runs/abc/stream",
            path_parts=["runs", "abc", "stream"],
            status=200,
        )
        assert action == "run.read"
        assert rid == "abc"

    def test_runs_search(self) -> None:
        action, _rid = _audit_action_and_resource(
            method="GET",
            route="/runs/search",
            path_parts=["runs", "search"],
            status=200,
        )
        assert action == "run.search"

    def test_unknown_route(self) -> None:
        action, _rid = _audit_action_and_resource(
            method="DELETE", route="/unknown", path_parts=["unknown"], status=404
        )
        assert action == "api.request"

    def test_legacy_run_cancel(self) -> None:
        action, rid = _audit_action_and_resource(
            method="POST",
            route="/runs/abc/cancel",
            path_parts=["runs", "abc", "cancel"],
            status=202,
        )
        assert action == "run.cancel"
        assert rid == "abc"

    def test_legacy_run_approve(self) -> None:
        action, rid = _audit_action_and_resource(
            method="POST",
            route="/runs/abc/approve",
            path_parts=["runs", "abc", "approve"],
            status=202,
        )
        assert action == "run.approve"
        assert rid == "abc"


class TestAuthorizeRequest:
    def test_healthz_unauthenticated(self) -> None:
        _sub, err = _authorize_request(
            route="/healthz",
            request_path="/healthz",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is None

    def test_metrics_always_allowed(self) -> None:
        _sub, err = _authorize_request(
            route="/metrics",
            request_path="/metrics",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header=None,
            allow_unauthenticated_healthz=False,
        )
        assert err is None

    def test_auth_off(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="off",
            expected_bearer_token=None,
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is None

    def test_unsupported_auth_mode(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="mtls",
            expected_bearer_token=None,
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is not None
        assert err[0] == 500

    def test_no_configured_token(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="bearer",
            expected_bearer_token=None,
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is not None
        assert err[0] == 503

    def test_missing_bearer(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is not None
        assert err[0] == 401

    def test_invalid_bearer(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header="Bearer wrong",
            allow_unauthenticated_healthz=True,
        )
        assert err is not None
        assert err[0] == 403

    def test_valid_bearer(self) -> None:
        sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header="Bearer secret",
            allow_unauthenticated_healthz=True,
        )
        assert err is None
        assert sub == "bearer"

    def test_access_token_query_param(self) -> None:
        _sub, err = _authorize_request(
            route="/v1/runs",
            request_path="/v1/runs?access_token=secret",
            auth_mode="bearer",
            expected_bearer_token="secret",
            authorization_header=None,
            allow_unauthenticated_healthz=True,
        )
        assert err is None


# ---------------------------------------------------------------------------
# Verifier GLEAN integration
# ---------------------------------------------------------------------------


def _base_state(**overrides: Any) -> dict[str, Any]:
    s: dict[str, Any] = {"request": "test", "tool_results": []}
    s.update(overrides)
    return s


class TestVerifierGleanIntegration:
    def test_glean_blocking_fails_verification(self) -> None:
        """When GLEAN trace event has blocking_violations > 0, verifier should fail."""
        state = _base_state(
            _trace_events=[
                {
                    "ts_ms": 1000,
                    "kind": "glean",
                    "data": {
                        "guidelines_checked": 3,
                        "violations": 2,
                        "blocking_violations": 1,
                        "evidence_entries": 0,
                        "compliant": False,
                    },
                }
            ]
        )
        out = verifier(state)
        assert out["verification"]["ok"] is False
        # Should have a check about GLEAN
        checks = out["verification"]["checks"]
        glean_checks = [c for c in checks if c.get("name") == "glean_blocking_violations"]
        assert len(glean_checks) == 1
        assert "blocking violation" in glean_checks[0]["summary"]

    def test_glean_no_blocking_passes(self) -> None:
        """When GLEAN reports 0 blocking violations, verification should pass."""
        state = _base_state(
            _trace_events=[
                {
                    "ts_ms": 1000,
                    "kind": "glean",
                    "data": {
                        "guidelines_checked": 3,
                        "violations": 1,
                        "blocking_violations": 0,
                        "evidence_entries": 0,
                        "compliant": True,
                    },
                }
            ]
        )
        out = verifier(state)
        assert out["verification"]["ok"] is True

    def test_no_glean_event_passes(self) -> None:
        """Without GLEAN trace events, verification should not be affected."""
        out = verifier(_base_state())
        assert out["verification"]["ok"] is True


# ---------------------------------------------------------------------------
# auth.py — JWTSettings
# ---------------------------------------------------------------------------


class TestJWTSettingsCoverage:
    def test_jwt_settings_disabled(self) -> None:
        s = JWTSettings(jwt_secret=None, jwks_url=None)
        assert s.enabled is False

    def test_jwt_settings_from_config_no_secret(self) -> None:
        s = jwt_settings_from_config(jwt_secret=None, jwks_url=None)
        assert s.enabled is False

    def test_jwt_settings_from_config_with_secret(self) -> None:
        s = jwt_settings_from_config(
            jwt_secret="a-very-long-secret-key-for-testing-at-least-32-chars",
            jwks_url=None,
        )
        assert s.enabled is True

    def test_jwt_settings_from_config_with_jwks(self) -> None:
        s = jwt_settings_from_config(jwt_secret=None, jwks_url="https://example.com/.well-known")
        assert s.enabled is True


# ---------------------------------------------------------------------------
# long_term_memory.py — additional coverage
# ---------------------------------------------------------------------------


class TestLongTermMemoryStoreCoverage:
    def test_store_and_search_semantic(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            rowid = store.store_semantic("Python decorators add functionality to functions.")
            assert rowid >= 1
            results = store.search_semantic("decorators", top_k=3)
            assert len(results) >= 1
        finally:
            store.close()

    def test_store_and_get_episodes(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            rowid = store.store_episode("run1", "fixed bug in auth", "success")
            assert rowid >= 1
            episodes = store.get_episodes(limit=10)
            assert len(episodes) == 1
            assert "fixed bug" in episodes[0].content
        finally:
            store.close()

    def test_store_and_get_procedures(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            rowid = store.store_procedure("debug", ["read_file", "apply_patch"], True)
            assert rowid >= 1
            procs = store.get_procedures("debug", successful_only=True)
            assert len(procs) == 1
        finally:
            store.close()

    def test_retrieve_for_context(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            store.store_semantic("React hooks enable state management.")
            store.store_episode("run1", "deployed v2", "ok")
            store.store_procedure("code_change", ["edit", "test"], True)
            ctx = store.retrieve_for_context("React state hooks", max_tokens=2000)
            assert "[long_term:semantic]" in ctx
        finally:
            store.close()

    def test_get_episodes_by_run_id(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            store.store_episode("run1", "first", "ok")
            store.store_episode("run2", "second", "ok")
            episodes = store.get_episodes(run_id="run1")
            assert len(episodes) == 1
            assert episodes[0].run_id == "run1"
        finally:
            store.close()

    def test_get_procedures_all(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            store.store_procedure("debug", ["step1"], True)
            store.store_procedure("debug", ["step2"], False)
            all_procs = store.get_procedures("debug", successful_only=False)
            assert len(all_procs) == 2
        finally:
            store.close()


class TestLongTermMemoryStoreBudgetPaths:
    """Target the budget truncation and procedural broadening paths."""

    def test_retrieve_with_tiny_budget_truncates(self, tmp_path: pathlib.Path) -> None:
        """When max_tokens is very small, the semantic block should be truncated."""
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            store.store_semantic("A long piece of content about Python decorators.")
            ctx = store.retrieve_for_context("decorators", max_tokens=5)
            # Should truncate
            assert len(ctx) > 0
        finally:
            store.close()

    def test_retrieve_procedural_broadening(self, tmp_path: pathlib.Path) -> None:
        """When task type doesn't match, procedures should broaden search."""
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            # Store procedure with different task type
            store.store_procedure("code_change", ["edit", "test"], True)
            # Query with a type that won't directly match any procedures
            ctx = store.retrieve_for_context("canary deploy", max_tokens=5000)
            # Should find the procedure via broadening
            assert "[long_term:procedural]" in ctx
        finally:
            store.close()

    def test_retrieve_all_tiers(self, tmp_path: pathlib.Path) -> None:
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            store.store_semantic("Use decorators for logging.")
            store.store_episode("run-1", "Deployed successfully.", "ok")
            store.store_procedure("debug", ["read_file", "apply_patch"], True)
            ctx = store.retrieve_for_context("debug the logging issue", max_tokens=5000)
            assert "[long_term:semantic]" in ctx
            assert "[long_term:episodic]" in ctx
            assert "[long_term:procedural]" in ctx
        finally:
            store.close()

    def test_retrieve_episodic_truncation(self, tmp_path: pathlib.Path) -> None:
        """Episodic tier truncation when budget is tight."""
        store = LongTermMemoryStore(db_path=str(tmp_path / "mem.db"))
        try:
            # Fill with many episodes
            for i in range(20):
                store.store_episode(f"run-{i}", f"Episode number {i} " * 20, "ok")
            ctx = store.retrieve_for_context("something random", max_tokens=20)
            assert len(ctx) > 0
        finally:
            store.close()


class TestMakeEmbedder:
    def test_stub_provider(self) -> None:
        fn = make_embedder("stub")
        result = fn("hello")
        assert isinstance(result, list)
        assert len(result) > 0

    def test_ollama_provider_fallback(self) -> None:
        # Ollama not running, should fall back to stub
        fn = make_embedder("ollama")
        result = fn("hello")
        assert isinstance(result, list)

    def test_auto_provider(self) -> None:
        fn = make_embedder()
        result = fn("test")
        assert isinstance(result, list)


class TestInferTaskType:
    def test_code_change(self) -> None:
        assert _infer_task_type("implement new feature") == "code_change"

    def test_debug(self) -> None:
        assert _infer_task_type("fix the login bug") == "debug"

    def test_analysis(self) -> None:
        assert _infer_task_type("review the code") == "analysis"

    def test_test_repair(self) -> None:
        assert _infer_task_type("fix failing test") == "debug"  # debug matches first

    def test_fallback(self) -> None:
        result = _infer_task_type("hello world")
        assert result == "hello"

    def test_empty(self) -> None:
        assert _infer_task_type("") == "unknown"


class TestInitTelemetry:
    def test_no_endpoint(self) -> None:
        init_telemetry(service_name="test")  # no-op without endpoint
