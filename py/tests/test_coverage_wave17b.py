# SPDX-License-Identifier: MIT
"""Wave 17 coverage tests (batch 2) — inference_client, context_builder, remote_api."""

from __future__ import annotations

import pathlib
import time
from unittest.mock import patch

import pytest

from lg_orch.auth import (
    AuthError,
    JWTSettings,
    TokenClaims,
    _check_roles,
    _clear_jwks_cache,
    _extract_bearer_token,
)
from lg_orch.backends._base import (
    _dump_typed_mixin,
    _load_typed_mixin,
    parse_config,
    resolve_checkpoint_db_path,
    stable_checkpoint_thread_id,
)
from lg_orch.nodes.coder import _coerce_handoff, _dedupe, _first_step
from lg_orch.nodes.context_builder import (
    _generate_repo_map,
    _load_cached_procedures,
    _load_episodic_context,
    _load_semantic_context,
    _runner_client_from_state,
    _semantic_query_from_request,
)
from lg_orch.tools.inference_client import (
    InferenceResponse,
    ToolCall,
    ToolDefinition,
    _CircuitBreaker,
    _get_default_sla_policy,
    _get_or_create_client,
    clear_client_cache,
    reset_default_sla_policy,
)

# ---------------------------------------------------------------------------
# tools/inference_client.py — _CircuitBreaker
# ---------------------------------------------------------------------------


class TestCircuitBreaker:
    def test_starts_closed(self) -> None:
        cb = _CircuitBreaker()
        assert cb.allow_request() is True

    def test_opens_after_threshold_failures(self) -> None:
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        assert cb.allow_request() is False

    def test_stays_closed_under_threshold(self) -> None:
        cb = _CircuitBreaker()
        for _ in range(4):
            cb.record_failure()
        assert cb.allow_request() is True

    def test_half_open_after_timeout(self) -> None:
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        assert cb._state == "open"
        # Simulate timeout elapsed
        cb._opened_at = time.monotonic() - 31.0
        assert cb.allow_request() is True
        assert cb._state == "half_open"

    def test_half_open_success_closes(self) -> None:
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        cb._opened_at = time.monotonic() - 31.0
        cb.allow_request()  # transitions to half_open
        cb.record_success()
        assert cb._state == "closed"
        assert cb._failures == 0

    def test_half_open_failure_reopens(self) -> None:
        cb = _CircuitBreaker()
        for _ in range(5):
            cb.record_failure()
        cb._opened_at = time.monotonic() - 31.0
        cb.allow_request()  # transitions to half_open
        cb.record_failure()
        assert cb._state == "open"


class TestInferenceClientHelpers:
    def test_get_default_sla_policy(self) -> None:
        reset_default_sla_policy()
        policy = _get_default_sla_policy()
        assert policy is not None
        # Second call returns same instance
        assert _get_default_sla_policy() is policy
        reset_default_sla_policy()

    def test_get_or_create_client_caching(self) -> None:
        try:
            c1 = _get_or_create_client("http://localhost:9999", "key1", 30)
            c2 = _get_or_create_client("http://localhost:9999", "key1", 30)
            assert c1 is c2
            # Different key = different client
            c3 = _get_or_create_client("http://localhost:9999", "key2", 30)
            assert c1 is not c3
        finally:
            clear_client_cache()

    def test_clear_client_cache(self) -> None:
        _get_or_create_client("http://localhost:9998", "k", 10)
        clear_client_cache()
        # After clearing, a new client is created
        c = _get_or_create_client("http://localhost:9998", "k", 10)
        assert c is not None
        clear_client_cache()


class TestInferenceResponse:
    def test_fields(self) -> None:
        resp = InferenceResponse(text="hello", latency_ms=100, provider="openai", model="gpt-4")
        assert resp.text == "hello"
        assert resp.latency_ms == 100
        assert resp.tool_calls == []

    def test_with_tool_calls(self) -> None:
        tc = ToolCall(id="1", name="bash", arguments={"cmd": "ls"})
        resp = InferenceResponse(text="", latency_ms=50, tool_calls=[tc])
        assert len(resp.tool_calls) == 1


class TestToolDefinition:
    def test_fields(self) -> None:
        td = ToolDefinition(name="bash", description="Run a command", parameters={"type": "object"})
        assert td.name == "bash"


# ---------------------------------------------------------------------------
# nodes/context_builder.py — pure helpers
# ---------------------------------------------------------------------------


class TestSemanticQueryFromRequest:
    def test_normal(self) -> None:
        result = _semantic_query_from_request("Please fix the broken test in module foo")
        assert "fix" in result
        assert "broken" in result

    def test_empty(self) -> None:
        result = _semantic_query_from_request("")
        assert result == "repository structure"

    def test_truncates_to_8_tokens(self) -> None:
        result = _semantic_query_from_request("a b c d e f g h i j k l m")
        words = result.split()
        assert len(words) <= 8


class TestGenerateRepoMap:
    def test_basic_tree(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / "src").mkdir()
        (tmp_path / "src" / "main.py").write_text("x", encoding="utf-8")
        (tmp_path / "tests").mkdir()
        result = _generate_repo_map(tmp_path, max_depth=2)
        assert "src" in result
        assert "main.py" in result
        assert "tests" in result

    def test_respects_max_depth(self, tmp_path: pathlib.Path) -> None:
        nested = tmp_path / "a" / "b" / "c" / "d"
        nested.mkdir(parents=True)
        result = _generate_repo_map(tmp_path, max_depth=1)
        # Should show 'a' and 'b' but not 'c' or 'd'
        assert "a" in result
        assert "d" not in result

    def test_ignores_hidden(self, tmp_path: pathlib.Path) -> None:
        (tmp_path / ".git").mkdir()
        (tmp_path / "visible").mkdir()
        result = _generate_repo_map(tmp_path)
        assert ".git" not in result
        assert "visible" in result


# ---------------------------------------------------------------------------
# nodes/context_builder.py — _load_cached_procedures, _load_episodic_context
# ---------------------------------------------------------------------------


class TestRunnerClientFromState:
    def test_disabled(self) -> None:
        assert _runner_client_from_state({"_runner_enabled": False}) is None

    def test_no_url(self) -> None:
        assert _runner_client_from_state({}) is None

    def test_invalid_url(self) -> None:
        assert _runner_client_from_state({"_runner_base_url": "bad"}) is None


class TestLoadCachedProcedures:
    def test_no_path(self) -> None:
        assert _load_cached_procedures({}) == []

    def test_no_request(self) -> None:
        assert _load_cached_procedures({"_procedure_cache_path": "/tmp/cache.db"}) == []


class TestLoadEpisodicContext:
    def test_no_run_store(self) -> None:
        assert _load_episodic_context({}) == []


class TestLoadSemanticContext:
    def test_no_memory_store(self) -> None:
        assert _load_semantic_context({}) == []


# ---------------------------------------------------------------------------
# nodes/coder.py — helper functions
# ---------------------------------------------------------------------------


class TestCoerceHandoff:
    def test_valid_handoff(self) -> None:
        result = _coerce_handoff(
            {
                "producer": "planner",
                "consumer": "coder",
                "objective": "fix bug",
            }
        )
        assert result is not None
        assert result["producer"] == "planner"

    def test_not_dict(self) -> None:
        assert _coerce_handoff("string") is None

    def test_missing_fields(self) -> None:
        assert _coerce_handoff({"producer": "a", "consumer": "b"}) is None
        assert _coerce_handoff({"producer": "", "consumer": "b", "objective": "c"}) is None


class TestFirstStep:
    def test_returns_first_dict(self) -> None:
        result = _first_step({"steps": [{"action": "read"}, {"action": "write"}]})
        assert result == {"action": "read"}

    def test_empty_steps(self) -> None:
        assert _first_step({"steps": []}) is None

    def test_not_list(self) -> None:
        assert _first_step({"steps": "bad"}) is None


class TestDedupe:
    def test_basic(self) -> None:
        assert _dedupe(["a", "b", "a", "c"]) == ["a", "b", "c"]

    def test_empty_and_whitespace(self) -> None:
        assert _dedupe(["  ", "", "a", " a "]) == ["a"]

    def test_preserves_order(self) -> None:
        assert _dedupe(["c", "b", "a"]) == ["c", "b", "a"]


# ---------------------------------------------------------------------------
# auth.py — JWTSettings, _extract_bearer_token, _check_roles
# ---------------------------------------------------------------------------


class TestJWTSettingsFromEnv:
    def test_from_env_with_secret(self) -> None:
        import os

        with patch.dict(os.environ, {"JWT_SECRET": "mysecret", "JWKS_URL": ""}):
            settings = JWTSettings.from_env()
        assert settings.jwt_secret == "mysecret"
        assert settings.jwks_url is None
        assert settings.enabled is True

    def test_from_env_empty(self) -> None:
        import os

        with patch.dict(os.environ, {"JWT_SECRET": "", "JWKS_URL": ""}, clear=False):
            # Remove keys if present
            env = {k: v for k, v in os.environ.items() if k not in ("JWT_SECRET", "JWKS_URL")}
            with patch.dict(os.environ, env, clear=True):
                settings = JWTSettings.from_env()
        assert settings.enabled is False


class TestExtractBearerToken:
    def test_valid(self) -> None:
        assert _extract_bearer_token("Bearer abc123") == "abc123"

    def test_missing_header(self) -> None:
        with pytest.raises(AuthError, match="missing_authorization_header"):
            _extract_bearer_token(None)
        with pytest.raises(AuthError, match="missing_authorization_header"):
            _extract_bearer_token("")

    def test_invalid_format(self) -> None:
        with pytest.raises(AuthError, match="invalid_authorization_header"):
            _extract_bearer_token("Basic abc123")
        with pytest.raises(AuthError, match="invalid_authorization_header"):
            _extract_bearer_token("Bearer ")


class TestCheckRoles:
    def test_passes_with_matching_role(self) -> None:
        claims = TokenClaims(sub="u", roles=["admin"], exp=9999999999, iat=1000000000)
        _check_roles(claims, ("admin",))  # should not raise

    def test_fails_without_matching_role(self) -> None:
        claims = TokenClaims(sub="u", roles=["viewer"], exp=9999999999, iat=1000000000)
        with pytest.raises(AuthError, match="insufficient_roles"):
            _check_roles(claims, ("admin",))

    def test_empty_required_passes(self) -> None:
        claims = TokenClaims(sub="u", roles=[], exp=9999999999, iat=1000000000)
        _check_roles(claims, ())  # no roles required = always passes


class TestClearJwksCache:
    def test_clears(self) -> None:
        _clear_jwks_cache()  # should not raise


# ---------------------------------------------------------------------------
# backends/_base.py — parse_config, resolve_checkpoint_db_path
# ---------------------------------------------------------------------------


class TestParseConfig:
    def test_valid(self) -> None:
        config = {"configurable": {"thread_id": "t1", "checkpoint_ns": "ns", "checkpoint_id": "c1"}}
        tid, ns, cid = parse_config(config)
        assert tid == "t1"
        assert ns == "ns"
        assert cid == "c1"

    def test_missing_thread_id(self) -> None:
        with pytest.raises(ValueError, match=r"missing configurable\.thread_id"):
            parse_config({"configurable": {}})

    def test_bad_configurable(self) -> None:
        with pytest.raises(ValueError, match="configurable must be a dict"):
            parse_config({"configurable": "bad"})


class TestResolveCheckpointDbPath:
    def test_absolute(self, tmp_path: pathlib.Path) -> None:
        result = resolve_checkpoint_db_path(repo_root=tmp_path, db_path=str(tmp_path / "db.sqlite"))
        assert result.name == "db.sqlite"

    def test_relative(self, tmp_path: pathlib.Path) -> None:
        result = resolve_checkpoint_db_path(repo_root=tmp_path, db_path="data/db.sqlite")
        assert "data" in str(result)


class TestDumpLoadTypedMixin:
    def test_dump_typed(self) -> None:
        from unittest.mock import MagicMock

        serde = MagicMock()
        serde.dumps_typed.return_value = ("json", b'{"key": "val"}')
        tag, payload = _dump_typed_mixin(serde, {"key": "val"})
        assert tag == "json"
        assert payload == b'{"key": "val"}'

    def test_load_typed(self) -> None:
        from unittest.mock import MagicMock

        serde = MagicMock()
        serde.loads_typed.return_value = {"key": "val"}
        result = _load_typed_mixin(serde, type_tag="json", payload=b'{"key": "val"}')
        assert result == {"key": "val"}


class TestStableCheckpointThreadId:
    def test_with_provided(self) -> None:
        result = stable_checkpoint_thread_id(
            request="x", thread_prefix="p", provided="custom"
        )
        assert result == "custom"

    def test_generated(self) -> None:
        result = stable_checkpoint_thread_id(request="hello", thread_prefix="test", provided=None)
        assert result.startswith("test-")
        assert len(result) > 5
