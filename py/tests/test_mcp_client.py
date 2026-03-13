from __future__ import annotations

import hashlib
import json
from unittest.mock import MagicMock, patch

import pytest

from lg_orch.tools.mcp_client import MCPClient, _compute_tools_hash
from lg_orch.tools.runner_client import RunnerClient


def _runner_mock() -> RunnerClient:
    return RunnerClient(base_url="http://127.0.0.1:8088", _client=MagicMock())


# ---------------------------------------------------------------------------
# _compute_tools_hash
# ---------------------------------------------------------------------------

def test_compute_tools_hash_is_deterministic() -> None:
    tools = [{"name": "echo", "description": "Echo tool"}]
    assert _compute_tools_hash(tools) == _compute_tools_hash(tools)


def test_compute_tools_hash_order_independent_keys() -> None:
    # sort_keys=True applies to *object keys*, not array order.
    # Two dicts with same key/value pairs in different insertion order give same hash.
    a = _compute_tools_hash([{"description": "x", "name": "t"}])
    b = _compute_tools_hash([{"name": "t", "description": "x"}])
    assert a == b


def test_compute_tools_hash_differs_for_different_tools() -> None:
    a = _compute_tools_hash([{"name": "tool_a"}])
    b = _compute_tools_hash([{"name": "tool_b"}])
    assert a != b


def test_compute_tools_hash_matches_manual() -> None:
    tools = [{"name": "echo"}]
    canonical = json.dumps(tools, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    expected = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    assert _compute_tools_hash(tools) == expected


def test_discover_tools_uses_runner_mcp_discover() -> None:
    runner = _runner_mock()
    with patch.object(
        RunnerClient,
        "execute_tool",
        return_value={
            "tool": "mcp_discover",
            "ok": True,
            "stdout": '[{"name":"echo","description":"Echo"}]',
        },
    ) as mocked_execute:
        client = MCPClient(
            runner_client=runner,
            server_configs={"mock": {"command": "python", "args": ["server.py"]}},
        )

        tools = client.discover_tools()
        assert len(tools) == 1
        assert tools[0]["name"] == "echo"
        assert tools[0]["server_name"] == "mock"
        call = mocked_execute.call_args
        assert call.kwargs["tool"] == "mcp_discover"


def test_execute_tool_uses_runner_mcp_execute() -> None:
    runner = _runner_mock()
    with patch.object(
        RunnerClient,
        "execute_tool",
        return_value={
            "tool": "mcp_execute",
            "ok": True,
            "stdout": "{}",
            "stderr": "",
            "timing_ms": 1,
            "diagnostics": [],
            "artifacts": {},
            "exit_code": 0,
        },
    ) as mocked_execute:
        client = MCPClient(
            runner_client=runner,
            server_configs={"mock": {"command": "python", "args": ["server.py"]}},
        )

        result = client.execute_tool("mock", "echo", {"x": 1})
        assert result["ok"] is True
        call = mocked_execute.call_args
        assert call.kwargs["tool"] == "mcp_execute"
        assert call.kwargs["input"]["server_name"] == "mock"
        assert call.kwargs["input"]["tool_name"] == "echo"


def test_execute_tool_invalid_server_raises() -> None:
    client = MCPClient(runner_client=_runner_mock(), server_configs={})
    with pytest.raises(ValueError):
        client.execute_tool("unknown", "echo", {})


# ---------------------------------------------------------------------------
# Hash pinning in discover_tools
# ---------------------------------------------------------------------------

_TOOLS_LIST = [{"name": "echo", "description": "Echo tool"}]
_CORRECT_HASH = _compute_tools_hash(_TOOLS_LIST)


def _mock_discover_response() -> dict:  # type: ignore[type-arg]
    return {
        "tool": "mcp_discover",
        "ok": True,
        "stdout": json.dumps(_TOOLS_LIST),
    }


def test_discover_tools_accepts_correct_hash() -> None:
    runner = _runner_mock()
    with patch.object(RunnerClient, "execute_tool", return_value=_mock_discover_response()):
        client = MCPClient(
            runner_client=runner,
            server_configs={"mock": {"command": "python", "args": [], "schema_hash": _CORRECT_HASH}},
        )
        tools = client.discover_tools()
    valid = [t for t in tools if not t.get("_schema_hash_mismatch")]
    assert len(valid) == 1
    assert valid[0]["name"] == "echo"
    assert valid[0]["_schema_hash"] == _CORRECT_HASH


def test_discover_tools_rejects_wrong_hash() -> None:
    wrong_hash = "a" * 64
    runner = _runner_mock()
    with patch.object(RunnerClient, "execute_tool", return_value=_mock_discover_response()):
        client = MCPClient(
            runner_client=runner,
            server_configs={"mock": {"command": "python", "args": [], "schema_hash": wrong_hash}},
        )
        tools = client.discover_tools()
    mismatches = [t for t in tools if t.get("_schema_hash_mismatch")]
    valid = [t for t in tools if not t.get("_schema_hash_mismatch")]
    assert len(mismatches) == 1
    assert len(valid) == 0
    assert mismatches[0]["_expected_hash"] == wrong_hash
    assert mismatches[0]["_actual_hash"] == _CORRECT_HASH


def test_discover_tools_skips_hash_check_when_unpinned() -> None:
    runner = _runner_mock()
    with patch.object(RunnerClient, "execute_tool", return_value=_mock_discover_response()):
        client = MCPClient(
            runner_client=runner,
            server_configs={"mock": {"command": "python", "args": []}},
        )
        tools = client.discover_tools()
    assert len(tools) == 1
    assert tools[0]["name"] == "echo"
    assert not tools[0].get("_schema_hash_mismatch")


# ---------------------------------------------------------------------------
# summarize_tools mismatch filtering
# ---------------------------------------------------------------------------

def test_summarize_tools_excludes_mismatch_sentinels() -> None:
    mismatch_entry = {
        "server_name": "bad_server",
        "_schema_hash_mismatch": True,
        "_expected_hash": "a" * 64,
        "_actual_hash": "b" * 64,
    }
    good_tool = {"name": "echo", "description": "Echo", "server_name": "good_server"}
    runner = _runner_mock()
    client = MCPClient(runner_client=runner, server_configs={})
    result = client.summarize_tools(tools=[mismatch_entry, good_tool])
    server_names = [s["server_name"] for s in result["servers"]]
    assert "bad_server" not in server_names
    assert "good_server" in server_names
    assert "bad_server" in result["mismatch_servers"]
    assert result["tool_count"] == 1

