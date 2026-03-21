"""Tests for PythonInvariantChecker in lg_orch.vericoding."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from lg_orch.vericoding import InvariantViolation, PythonInvariantChecker

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def checker(tmp_path: Path) -> PythonInvariantChecker:
    return PythonInvariantChecker(
        allowed_root=str(tmp_path),
        allowed_commands=["git", "cargo", "python", "uv"],
    )


# ---------------------------------------------------------------------------
# PathConfinementInvariant
# ---------------------------------------------------------------------------


def test_path_confinement_allows_valid_path(
    checker: PythonInvariantChecker, tmp_path: Path
) -> None:
    inner = tmp_path / "sub" / "file.txt"
    inner.parent.mkdir(parents=True)
    inner.write_text("x")
    # Must not raise
    checker.check_path_confinement(str(inner))


def test_path_confinement_allows_relative_inside_root(
    checker: PythonInvariantChecker, tmp_path: Path
) -> None:
    (tmp_path / "safe.txt").write_text("ok")
    checker.check_path_confinement("safe.txt")


def test_path_confinement_rejects_traversal(
    checker: PythonInvariantChecker, tmp_path: Path
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_path_confinement("../../etc/passwd")
    assert "PathConfinementInvariant" in exc_info.value.invariant
    assert "escapes" in exc_info.value.message


def test_path_confinement_rejects_absolute_outside_root(
    checker: PythonInvariantChecker, tmp_path: Path
) -> None:
    outside = "/tmp/evil.txt" if os.name != "nt" else "C:\\Windows\\evil.txt"
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_path_confinement(outside)
    assert "PathConfinementInvariant" in exc_info.value.invariant


# ---------------------------------------------------------------------------
# CommandAllowlistInvariant
# ---------------------------------------------------------------------------


def test_command_allowlist_allows_permitted_command(
    checker: PythonInvariantChecker,
) -> None:
    checker.check_command_allowlist("git")
    checker.check_command_allowlist("cargo test")
    checker.check_command_allowlist("python --version")


def test_command_allowlist_rejects_unknown_command(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_command_allowlist("curl https://evil.example")
    assert "CommandAllowlistInvariant" in exc_info.value.invariant
    assert "curl" in exc_info.value.message


def test_command_allowlist_rejects_empty_string(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation):
        checker.check_command_allowlist("")


# ---------------------------------------------------------------------------
# NoShellMetacharInvariant
# ---------------------------------------------------------------------------


def test_no_shell_metachars_allows_clean_args(
    checker: PythonInvariantChecker,
) -> None:
    checker.check_no_shell_metachars(["--version", "src/main.rs", "tests/"])


def test_no_shell_metachars_rejects_backtick(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_no_shell_metachars(["`id`"])
    assert "NoShellMetacharInvariant" in exc_info.value.invariant


def test_no_shell_metachars_rejects_pipe(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_no_shell_metachars(["foo|bar"])
    assert "NoShellMetacharInvariant" in exc_info.value.invariant


def test_no_shell_metachars_rejects_dollar(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation):
        checker.check_no_shell_metachars(["$(evil)"])


def test_no_shell_metachars_rejects_newline(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation):
        checker.check_no_shell_metachars(["line1\nline2"])


# ---------------------------------------------------------------------------
# ToolNameKnownInvariant
# ---------------------------------------------------------------------------


def test_tool_name_known_allows_registered_tool(
    checker: PythonInvariantChecker,
) -> None:
    for tool in ("exec", "read_file", "apply_patch", "health"):
        checker.check_tool_name_known(tool)


def test_tool_name_known_rejects_unknown_tool(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_tool_name_known("rm_rf_everything")
    assert "ToolNameKnownInvariant" in exc_info.value.invariant
    assert "rm_rf_everything" in exc_info.value.message


# ---------------------------------------------------------------------------
# check_all composite
# ---------------------------------------------------------------------------


def test_check_all_raises_on_first_violation(
    checker: PythonInvariantChecker,
) -> None:
    # tool_name is unknown → should raise on ToolNameKnownInvariant first
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_all(
            tool_name="unknown_tool_xyz",
            path=None,
            command="curl https://evil.example",  # also wrong, but checked later
            args=["`id`"],  # also wrong, but checked later
        )
    assert exc_info.value.invariant == "ToolNameKnownInvariant"


def test_check_all_passes_for_valid_exec(
    checker: PythonInvariantChecker,
) -> None:
    checker.check_all(
        tool_name="exec",
        path=None,
        command="git",
        args=["--version"],
    )


def test_check_all_passes_for_valid_read_file(
    checker: PythonInvariantChecker, tmp_path: Path
) -> None:
    (tmp_path / "file.txt").write_text("content")
    checker.check_all(
        tool_name="read_file",
        path=str(tmp_path / "file.txt"),
        command=None,
        args=[],
    )


def test_check_all_path_violation_after_tool_name_ok(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_all(
            tool_name="read_file",
            path="../../etc/passwd",
            command=None,
            args=[],
        )
    assert exc_info.value.invariant == "PathConfinementInvariant"


def test_check_all_command_violation(
    checker: PythonInvariantChecker,
) -> None:
    with pytest.raises(InvariantViolation) as exc_info:
        checker.check_all(
            tool_name="exec",
            path=None,
            command="wget http://evil.example",
            args=[],
        )
    assert exc_info.value.invariant == "CommandAllowlistInvariant"


def test_invariant_violation_str() -> None:
    v = InvariantViolation(invariant="SomeInvariant", message="bad thing happened")
    assert "SomeInvariant" in str(v)
    assert "bad thing happened" in str(v)
