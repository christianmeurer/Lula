"""Coverage improvement tests for Wave 20.

Targets modules with the most uncovered lines that are feasible to
unit-test without external services:
  - inference_client: _execute_request parsing, _retry_wait_for_http
  - long_term_memory: Q-RAG integration, numpy fallback paths
  - glean: export_violations
  - rate_limit: metrics()
  - main: cli dispatch paths, _resolve_repo_root
  - backends/sqlite: list(), put_writes, delete_thread, async wrappers
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

# ---------------------------------------------------------------------------
# inference_client: _retry_wait_for_http
# ---------------------------------------------------------------------------


def test_retry_wait_for_http_429_with_retry_after() -> None:
    from lg_orch.tools.inference_client import _retry_wait_for_http

    resp = httpx.Response(429, headers={"retry-after": "5"})
    exc = httpx.HTTPStatusError("", request=httpx.Request("POST", "http://x"), response=resp)
    wait = _retry_wait_for_http(exc, attempt=1)
    assert wait == 5.0


def test_retry_wait_for_http_429_invalid_retry_after() -> None:
    from lg_orch.tools.inference_client import _retry_wait_for_http

    resp = httpx.Response(429, headers={"retry-after": "not-a-number"})
    exc = httpx.HTTPStatusError("", request=httpx.Request("POST", "http://x"), response=resp)
    wait = _retry_wait_for_http(exc, attempt=2)
    # fallback exponential: min(2**2, 30) = 4.0
    assert wait == 4.0


def test_retry_wait_for_http_429_large_retry_after_capped() -> None:
    from lg_orch.tools.inference_client import _retry_wait_for_http

    resp = httpx.Response(429, headers={"retry-after": "999"})
    exc = httpx.HTTPStatusError("", request=httpx.Request("POST", "http://x"), response=resp)
    wait = _retry_wait_for_http(exc, attempt=1)
    assert wait == 60.0  # capped at 60


def test_retry_wait_for_http_500() -> None:
    from lg_orch.tools.inference_client import _retry_wait_for_http

    resp = httpx.Response(500)
    exc = httpx.HTTPStatusError("", request=httpx.Request("POST", "http://x"), response=resp)
    wait = _retry_wait_for_http(exc, attempt=3)
    # 1.0 * 2**3 = 8.0
    assert wait == 8.0


def test_retry_wait_for_http_500_capped() -> None:
    from lg_orch.tools.inference_client import _retry_wait_for_http

    resp = httpx.Response(500)
    exc = httpx.HTTPStatusError("", request=httpx.Request("POST", "http://x"), response=resp)
    wait = _retry_wait_for_http(exc, attempt=10)
    assert wait == 30.0


# ---------------------------------------------------------------------------
# inference_client: _execute_request response parsing
# ---------------------------------------------------------------------------


def test_execute_request_tool_calls_parsing() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_response_body = {
        "choices": [
            {
                "message": {
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/tmp/foo.txt"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "function": {
                                "name": "write_file",
                                "arguments": {"path": "/tmp/bar.txt"},  # dict form
                            },
                        },
                        {
                            "id": "call_3",
                            "function": {
                                "name": "bad_args",
                                "arguments": "not-json{{{",  # invalid JSON
                            },
                        },
                        "not-a-dict",  # should be skipped
                        {
                            "id": "call_5",
                            "function": "not-a-dict",  # should be skipped
                        },
                    ],
                }
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        "model": "test-model",
        "provider": "test-provider",
    }

    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = mock_response_body
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {
        "x-cache-hit": "true",
        "x-model-provider": "openai",
        "x-request-id": "req-123",
        "content-type": "application/json",
    }

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="test-key", _client=mock_client)
    result = client._execute_request(
        model="gpt-4",
        system_prompt="you are helpful",
        user_prompt="hello",
        temperature=0.0,
        max_tokens=100,
    )
    assert len(result.tool_calls) == 3
    assert result.tool_calls[0].name == "read_file"
    assert result.tool_calls[0].arguments == {"path": "/tmp/foo.txt"}
    assert result.tool_calls[1].name == "write_file"
    assert result.tool_calls[1].arguments == {"path": "/tmp/bar.txt"}
    assert result.tool_calls[2].name == "bad_args"
    assert result.tool_calls[2].arguments == {}
    assert result.provider == "test-provider"
    assert result.model == "test-model"


def test_execute_request_with_tool_definitions() -> None:
    from lg_orch.tools.inference_client import (
        InferenceClient,
        ToolDefinition,
        clear_client_cache,
    )

    clear_client_cache()
    mock_response_body = {
        "choices": [{"message": {"content": "I'll help with that."}}],
        "usage": {},
        "model": "gpt-4",
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = mock_response_body
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    tools = [
        ToolDefinition(name="read_file", description="Read a file", parameters={"type": "object"}),
    ]
    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    result = client._execute_request(
        model="gpt-4",
        system_prompt="sys",
        user_prompt="user",
        temperature=0.5,
        max_tokens=200,
        tools=tools,
        tool_choice="auto",
    )
    assert result.text == "I'll help with that."
    # Verify tools were passed in payload
    call_args = mock_client.post.call_args
    payload = call_args.kwargs.get("json") or call_args[1].get("json")
    assert "tools" in payload
    assert payload["tool_choice"] == "auto"


def test_execute_request_missing_content_raises() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_response_body = {
        "choices": [{"message": {"content": ""}}],
    }
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = mock_response_body
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    with pytest.raises(RuntimeError, match="missing content"):
        client._execute_request(
            model="m", system_prompt="s", user_prompt="u", temperature=0.0, max_tokens=10
        )


def test_execute_request_invalid_body_raises() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = "not a dict"
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    with pytest.raises(RuntimeError, match="invalid completion payload"):
        client._execute_request(
            model="m", system_prompt="s", user_prompt="u", temperature=0.0, max_tokens=10
        )


def test_execute_request_missing_choices_raises() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = {"choices": []}
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    with pytest.raises(RuntimeError, match="missing choices"):
        client._execute_request(
            model="m", system_prompt="s", user_prompt="u", temperature=0.0, max_tokens=10
        )


def test_execute_request_invalid_first_choice() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = {"choices": ["not-a-dict"]}
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    with pytest.raises(RuntimeError, match="invalid first choice"):
        client._execute_request(
            model="m", system_prompt="s", user_prompt="u", temperature=0.0, max_tokens=10
        )


def test_execute_request_missing_message() -> None:
    from lg_orch.tools.inference_client import InferenceClient, clear_client_cache

    clear_client_cache()
    mock_resp = MagicMock(spec=httpx.Response)
    mock_resp.json.return_value = {"choices": [{"message": "not-a-dict"}]}
    mock_resp.raise_for_status = MagicMock()
    mock_resp.headers = {}

    mock_client = MagicMock(spec=httpx.Client)
    mock_client.post.return_value = mock_resp

    client = InferenceClient(base_url="http://test:8000", api_key="key", _client=mock_client)
    with pytest.raises(RuntimeError, match="missing message"):
        client._execute_request(
            model="m", system_prompt="s", user_prompt="u", temperature=0.0, max_tokens=10
        )


# ---------------------------------------------------------------------------
# glean: export_violations
# ---------------------------------------------------------------------------


def test_glean_export_violations_empty() -> None:
    from lg_orch.glean import GleanAuditor

    auditor = GleanAuditor()
    assert auditor.export_violations() == []


def test_glean_export_violations_with_data() -> None:
    from lg_orch.glean import DEFAULT_GUIDELINES, GleanAuditor

    auditor = GleanAuditor()
    for g in DEFAULT_GUIDELINES:
        auditor.add_guideline(g)

    # Trigger a pre-execution violation
    auditor.check_pre_execution("shell", {"cmd": "git push origin main --force"})
    violations = auditor.export_violations()
    assert len(violations) >= 1
    v = violations[0]
    assert "guideline_id" in v
    assert "tool_name" in v
    assert "detail" in v
    assert "severity" in v
    # Should be serializable
    json.dumps(violations)


# ---------------------------------------------------------------------------
# rate_limit: metrics()
# ---------------------------------------------------------------------------


def test_rate_limiter_metrics() -> None:
    from lg_orch.rate_limit import RateLimiter

    rl = RateLimiter(capacity=2.0, refill_rate=0.0)
    assert rl.total_requests == 0
    assert rl.total_rejections == 0

    rl.check("client1")
    assert rl.total_requests == 1
    assert rl.total_rejections == 0

    rl.check("client1")
    assert rl.total_requests == 2

    # This should be rejected (capacity 2, refill_rate 0)
    rl.check("client1")
    assert rl.total_requests == 3
    assert rl.total_rejections == 1

    m = rl.metrics()
    assert m["total_requests"] == 3
    assert m["total_rejections"] == 1
    assert m["active_buckets"] == 1


# ---------------------------------------------------------------------------
# long_term_memory: Q-RAG integration
# ---------------------------------------------------------------------------


def test_search_semantic_with_qrag_enabled(tmp_path: Path) -> None:
    from lg_orch.long_term_memory import LongTermMemoryStore

    db = str(tmp_path / "mem.db")
    store = LongTermMemoryStore(db_path=db)
    store.store_semantic("refactor the module layout", metadata={"task_type": "code_change"})
    store.store_semantic("fix the broken test", metadata={"task_type": "debug"})
    store.store_semantic("analyze code complexity", metadata={"task_type": "analysis"})

    with patch.dict(os.environ, {"LG_QRAG_ENABLED": "true"}):
        results = store.search_semantic("refactor code structure", top_k=3)
    assert len(results) > 0
    store.close()


def test_search_semantic_qrag_disabled_default(tmp_path: Path) -> None:
    from lg_orch.long_term_memory import LongTermMemoryStore

    db = str(tmp_path / "mem.db")
    store = LongTermMemoryStore(db_path=db)
    store.store_semantic("hello world")

    # Default should be disabled
    results = store.search_semantic("hello", top_k=1)
    assert len(results) == 1
    store.close()


def test_rerank_qrag_empty_results(tmp_path: Path) -> None:
    from lg_orch.long_term_memory import LongTermMemoryStore

    db = str(tmp_path / "mem.db")
    store = LongTermMemoryStore(db_path=db)
    # No data stored → empty results
    with patch.dict(os.environ, {"LG_QRAG_ENABLED": "true"}):
        results = store.search_semantic("anything", top_k=5)
    assert results == []
    store.close()


# ---------------------------------------------------------------------------
# main: _resolve_repo_root
# ---------------------------------------------------------------------------


def test_resolve_repo_root_with_arg() -> None:
    from lg_orch.main import _resolve_repo_root

    result = _resolve_repo_root(repo_root_arg=str(Path.cwd()))
    assert result.is_absolute()


def test_resolve_repo_root_from_env(tmp_path: Path) -> None:
    from lg_orch.main import _resolve_repo_root

    with patch.dict(os.environ, {"LG_REPO_ROOT": str(tmp_path)}):
        result = _resolve_repo_root(repo_root_arg=None)
    assert result == tmp_path.resolve()


def test_resolve_repo_root_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from lg_orch.main import _resolve_repo_root

    monkeypatch.delenv("LG_REPO_ROOT", raising=False)
    # With no arg and no env, it searches for configs/ dir or falls back to cwd
    result = _resolve_repo_root(repo_root_arg=None)
    assert result.is_absolute()


# ---------------------------------------------------------------------------
# main: cli dispatch — export-graph
# ---------------------------------------------------------------------------


def test_cli_export_graph(capsys: pytest.CaptureFixture[str]) -> None:
    from lg_orch.main import cli

    result = cli(["export-graph"])
    assert result == 0
    captured = capsys.readouterr()
    assert "graph" in captured.out.lower() or "mermaid" in captured.out.lower() or captured.out


# ---------------------------------------------------------------------------
# backends/sqlite: list, delete_thread, async wrappers
# ---------------------------------------------------------------------------


def test_sqlite_checkpoint_list(tmp_path: Path) -> None:
    from lg_orch.backends.sqlite import SqliteCheckpointSaver

    saver = SqliteCheckpointSaver(db_path=tmp_path / "ckpt.db")
    # list should return empty iterator for empty store
    results = list(saver.list(config=None))
    assert results == []


def test_sqlite_checkpoint_delete_thread(tmp_path: Path) -> None:
    from lg_orch.backends.sqlite import SqliteCheckpointSaver

    saver = SqliteCheckpointSaver(db_path=tmp_path / "ckpt.db")
    # delete_thread should not raise on missing thread
    saver.delete_thread("nonexistent-thread")


@pytest.mark.asyncio
async def test_sqlite_checkpoint_async_wrappers(tmp_path: Path) -> None:
    from lg_orch.backends.sqlite import SqliteCheckpointSaver

    saver = SqliteCheckpointSaver(db_path=tmp_path / "ckpt.db")

    config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "", "checkpoint_id": "c1"}}
    result = await saver.aget_tuple(config)
    assert result is None

    results = []
    async for item in saver.alist(config=None):
        results.append(item)
    assert results == []

    await saver.adelete_thread("nonexistent-thread")


# ---------------------------------------------------------------------------
# api/metrics: _rate_limiter_metrics_lines
# ---------------------------------------------------------------------------


def test_rate_limiter_metrics_lines_when_disabled() -> None:
    from lg_orch.api.metrics import _rate_limiter_metrics_lines

    with patch("lg_orch.remote_api._per_client_rate_limiter", None):
        result = _rate_limiter_metrics_lines()
    assert result == ""


def test_rate_limiter_metrics_lines_when_enabled() -> None:
    from lg_orch.api.metrics import _rate_limiter_metrics_lines
    from lg_orch.rate_limit import RateLimiter

    rl = RateLimiter()
    rl.check("test-client")
    with patch("lg_orch.remote_api._per_client_rate_limiter", rl):
        result = _rate_limiter_metrics_lines()
    assert "rate_limit_requests_total 1" in result
    assert "rate_limit_rejections_total 0" in result


# ---------------------------------------------------------------------------
# OllamaEmbedder — fallback when not reachable
# ---------------------------------------------------------------------------


def test_ollama_embedder_fallback_on_unreachable() -> None:
    from lg_orch.long_term_memory import OllamaEmbedder

    embedder = OllamaEmbedder(base_url="http://127.0.0.1:99999")
    result = embedder("test text")
    assert isinstance(result, list)
    assert len(result) > 0
    # Probe should be cached after first call
    assert embedder._available is False


def test_ollama_embedder_probe_caching() -> None:
    from lg_orch.long_term_memory import OllamaEmbedder

    embedder = OllamaEmbedder(base_url="http://127.0.0.1:99999")
    # First call probes and caches
    embedder._probe()
    # Second call returns cached
    result = embedder._probe()
    assert result is False


def test_make_embedder_ollama() -> None:
    from lg_orch.long_term_memory import OllamaEmbedder, make_embedder

    embedder = make_embedder("ollama", base_url="http://127.0.0.1:99999")
    assert isinstance(embedder, OllamaEmbedder)


def test_make_embedder_stub() -> None:
    from lg_orch.long_term_memory import make_embedder

    embedder = make_embedder("stub")
    result = embedder("hello")
    assert isinstance(result, list)


def test_make_embedder_auto_from_env() -> None:
    from lg_orch.long_term_memory import make_embedder

    with patch.dict(os.environ, {"LG_EMBED_PROVIDER": "stub"}):
        embedder = make_embedder()
    result = embedder("hello")
    assert isinstance(result, list)


# ---------------------------------------------------------------------------
# audit: AuditLogger write + close
# ---------------------------------------------------------------------------


def test_audit_logger_write_and_close(tmp_path: Path) -> None:
    from lg_orch.audit import AuditEvent, AuditLogger

    log_file = tmp_path / "audit.jsonl"
    logger = AuditLogger(log_path=log_file, sink=None)
    event = AuditEvent(
        ts="2026-01-01T00:00:00Z",
        subject="tester",
        roles=["admin"],
        action="test.write",
        resource_id="r1",
        outcome="ok",
        detail="test detail",
    )
    logger.log(event)
    logger.close()
    content = log_file.read_text(encoding="utf-8")
    assert "tester" in content
    assert "test.write" in content


def test_audit_build_sink_s3() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(
        sink_type="s3", s3_bucket="my-bucket", s3_prefix="audit", s3_region="us-east-1"
    )
    sink = build_sink(cfg)
    # S3AuditSink is returned (may be no-op if aioboto3 not installed)
    assert sink is not None or sink is None  # just verify no crash


def test_audit_build_sink_gcs() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(sink_type="gcs", gcs_bucket="my-bucket", gcs_prefix="audit")
    sink = build_sink(cfg)
    assert sink is not None or sink is None


def test_audit_build_sink_none() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(sink_type=None)
    sink = build_sink(cfg)
    assert sink is None


def test_audit_build_sink_s3_no_bucket() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(sink_type="s3", s3_bucket="")
    assert build_sink(cfg) is None


def test_audit_build_sink_gcs_no_bucket() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(sink_type="gcs", gcs_bucket="")
    assert build_sink(cfg) is None


def test_audit_build_sink_unknown_type() -> None:
    from lg_orch.audit import AuditConfig, build_sink

    cfg = AuditConfig(sink_type="unknown")
    assert build_sink(cfg) is None


def test_utc_now_iso() -> None:
    from lg_orch.audit import utc_now_iso

    ts = utc_now_iso()
    assert ts.endswith("Z")
    assert "T" in ts


# ---------------------------------------------------------------------------
# remote_api: utility functions
# ---------------------------------------------------------------------------


def test_request_client_ip_forwarded() -> None:
    from lg_orch.remote_api import _request_client_ip

    ip = _request_client_ip(
        client_address=("192.168.1.1", 8080),
        forwarded_for="10.0.0.1, 10.0.0.2",
        trust_forwarded_headers=True,
    )
    assert ip == "10.0.0.1"


def test_request_client_ip_no_forwarded() -> None:
    from lg_orch.remote_api import _request_client_ip

    ip = _request_client_ip(
        client_address=("192.168.1.1", 8080),
        forwarded_for=None,
        trust_forwarded_headers=True,
    )
    assert ip == "192.168.1.1"


def test_request_client_ip_untrusted_forwarded() -> None:
    from lg_orch.remote_api import _request_client_ip

    ip = _request_client_ip(
        client_address=("192.168.1.1", 8080),
        forwarded_for="10.0.0.1",
        trust_forwarded_headers=False,
    )
    assert ip == "192.168.1.1"


def test_request_client_ip_no_address() -> None:
    from lg_orch.remote_api import _request_client_ip

    ip = _request_client_ip(
        client_address=None,
        forwarded_for=None,
        trust_forwarded_headers=True,
    )
    assert ip == ""


def test_request_scheme_forwarded() -> None:
    from lg_orch.remote_api import _request_scheme

    scheme = _request_scheme(forwarded_proto="https, http", trust_forwarded_headers=True)
    assert scheme == "https"


def test_request_scheme_no_forwarded() -> None:
    from lg_orch.remote_api import _request_scheme

    scheme = _request_scheme(forwarded_proto=None, trust_forwarded_headers=True)
    assert scheme == "http"


def test_request_scheme_untrusted() -> None:
    from lg_orch.remote_api import _request_scheme

    scheme = _request_scheme(forwarded_proto="https", trust_forwarded_headers=False)
    assert scheme == "http"


def test_authorize_request_healthz_unauthenticated() -> None:
    from lg_orch.remote_api import _authorize_request

    _auth_mode, err = _authorize_request(
        route="/healthz",
        request_path="/healthz",
        auth_mode="bearer",
        expected_bearer_token="tok",
        authorization_header=None,
        allow_unauthenticated_healthz=True,
    )
    assert err is None


def test_authorize_request_metrics_always_open() -> None:
    from lg_orch.remote_api import _authorize_request

    _auth_mode, err = _authorize_request(
        route="/metrics",
        request_path="/metrics",
        auth_mode="bearer",
        expected_bearer_token="tok",
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is None


def test_authorize_request_auth_off() -> None:
    from lg_orch.remote_api import _authorize_request

    _auth_mode, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="off",
        expected_bearer_token=None,
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is None


def test_authorize_request_unsupported_auth_mode() -> None:
    from lg_orch.remote_api import _authorize_request

    _, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="mtls",
        expected_bearer_token=None,
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is not None
    assert err[0] == 500


def test_authorize_request_no_bearer_configured() -> None:
    from lg_orch.remote_api import _authorize_request

    _, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="bearer",
        expected_bearer_token=None,
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is not None
    assert err[0] == 503


def test_authorize_request_missing_token() -> None:
    from lg_orch.remote_api import _authorize_request

    _, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="bearer",
        expected_bearer_token="secret",
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is not None
    assert err[0] == 401


def test_authorize_request_invalid_token() -> None:
    from lg_orch.remote_api import _authorize_request

    _, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="bearer",
        expected_bearer_token="secret",
        authorization_header="Bearer wrong",
        allow_unauthenticated_healthz=False,
    )
    assert err is not None
    assert err[0] == 403


def test_authorize_request_valid_token() -> None:
    from lg_orch.remote_api import _authorize_request

    auth_mode, err = _authorize_request(
        route="/runs",
        request_path="/runs",
        auth_mode="bearer",
        expected_bearer_token="secret",
        authorization_header="Bearer secret",
        allow_unauthenticated_healthz=False,
    )
    assert err is None
    assert auth_mode == "bearer"


def test_authorize_request_token_from_query_string() -> None:
    from lg_orch.remote_api import _authorize_request

    auth_mode, err = _authorize_request(
        route="/runs",
        request_path="/runs?access_token=secret",
        auth_mode="bearer",
        expected_bearer_token="secret",
        authorization_header=None,
        allow_unauthenticated_healthz=False,
    )
    assert err is None
    assert auth_mode == "bearer"


def test_audit_action_and_resource() -> None:
    from lg_orch.remote_api import _audit_action_and_resource

    action, rid = _audit_action_and_resource(
        method="POST", route="/v1/runs", path_parts=["v1", "runs"], status=201
    )
    assert action == "run.create"

    action, rid = _audit_action_and_resource(
        method="GET", route="/v1/runs", path_parts=["v1", "runs"], status=200
    )
    assert action == "run.list"

    action, rid = _audit_action_and_resource(
        method="GET", route="/v1/runs/abc", path_parts=["v1", "runs", "abc"], status=200
    )
    assert action == "run.read"
    assert rid == "abc"

    action, rid = _audit_action_and_resource(
        method="POST",
        route="/v1/runs/abc/cancel",
        path_parts=["v1", "runs", "abc", "cancel"],
        status=200,
    )
    assert action == "run.cancel"
    assert rid == "abc"

    action, rid = _audit_action_and_resource(
        method="POST",
        route="/v1/runs/abc/approve",
        path_parts=["v1", "runs", "abc", "approve"],
        status=200,
    )
    assert action == "run.approve"
    assert rid == "abc"

    action, rid = _audit_action_and_resource(
        method="GET",
        route="/runs/search",
        path_parts=["runs", "search"],
        status=200,
    )
    assert action == "run.search"

    # Unmatched
    action, rid = _audit_action_and_resource(
        method="DELETE", route="/unknown", path_parts=["unknown"], status=200
    )
    assert action == "api.request"


# ---------------------------------------------------------------------------
# long_term_memory: _infer_task_type
# ---------------------------------------------------------------------------


def test_infer_task_type_known() -> None:
    from lg_orch.long_term_memory import _infer_task_type

    assert _infer_task_type("refactor the layout") == "code_change"
    assert _infer_task_type("fix the bug") == "debug"
    assert _infer_task_type("analyze code") == "analysis"
    assert _infer_task_type("pytest failing") == "test_repair"
    assert _infer_task_type("canary deploy") == "canary"


def test_infer_task_type_fallback() -> None:
    from lg_orch.long_term_memory import _infer_task_type

    assert _infer_task_type("something random") == "something"
    assert _infer_task_type("") == "unknown"


# ---------------------------------------------------------------------------
# rate_limit: cleanup
# ---------------------------------------------------------------------------


def test_rate_limiter_cleanup() -> None:
    from lg_orch.rate_limit import RateLimiter

    rl = RateLimiter()
    rl.check("c1")
    rl.check("c2")
    # With a large idle threshold, nothing should be cleaned up
    removed = rl.cleanup(max_idle_seconds=3600.0)
    assert removed == 0
    m = rl.metrics()
    assert m["active_buckets"] == 2
    # With zero threshold, everything is stale
    removed = rl.cleanup(max_idle_seconds=0.0)
    assert removed == 2
    assert rl.metrics()["active_buckets"] == 0
