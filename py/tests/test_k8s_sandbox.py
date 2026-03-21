"""Tests for lg_orch.k8s_sandbox — validate_deployment_manifest and generate_sandbox_config_toml."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest
import yaml

from lg_orch.k8s_sandbox import (
    SandboxConfig,
    generate_sandbox_config_toml,
    validate_deployment_manifest,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_MANIFEST = textwrap.dedent("""\
    apiVersion: apps/v1
    kind: Deployment
    metadata:
      name: lg-orch-runner
      namespace: lg-orch
    spec:
      replicas: 1
      selector:
        matchLabels:
          app: lg-orch-runner
      template:
        metadata:
          labels:
            app: lg-orch-runner
        spec:
          runtimeClassName: gvisor
          containers:
            - name: runner
              image: example/runner:latest
              securityContext:
                runAsNonRoot: true
                runAsUser: 10001
                runAsGroup: 10001
                readOnlyRootFilesystem: true
                allowPrivilegeEscalation: false
                capabilities:
                  drop:
                    - ALL
""")


def _write_manifest(tmp_path: Path, content: str) -> str:
    p = tmp_path / "manifest.yaml"
    p.write_text(content, encoding="utf-8")
    return str(p)


def _mutate_manifest(content: str, **overrides: object) -> str:
    """Return a mutated YAML string with container securityContext fields overridden."""
    data = yaml.safe_load(content)
    sc = data["spec"]["template"]["spec"]["containers"][0].setdefault("securityContext", {})
    for key, value in overrides.items():
        if "." in key:
            # Support one level of nesting e.g. "capabilities.drop"
            parent, child = key.split(".", 1)
            sc.setdefault(parent, {})[child] = value
        else:
            sc[key] = value
    return yaml.dump(data)


# ---------------------------------------------------------------------------
# validate_deployment_manifest tests
# ---------------------------------------------------------------------------


def test_validate_valid_manifest_returns_no_violations(tmp_path: Path) -> None:
    """Point at the updated runner-deployment.yaml — expect zero violations."""
    manifest_path = Path(__file__).parent.parent.parent / "infra" / "k8s" / "runner-deployment.yaml"
    if not manifest_path.exists():
        pytest.skip("runner-deployment.yaml not found; skipping integration path")
    violations = validate_deployment_manifest(str(manifest_path), SandboxConfig())
    assert violations == [], f"Unexpected violations: {violations}"


def test_validate_synthetic_valid_manifest_returns_no_violations(tmp_path: Path) -> None:
    path = _write_manifest(tmp_path, _VALID_MANIFEST)
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert violations == []


def test_validate_missing_runtime_class_returns_violation(tmp_path: Path) -> None:
    data = yaml.safe_load(_VALID_MANIFEST)
    del data["spec"]["template"]["spec"]["runtimeClassName"]
    path = _write_manifest(tmp_path, yaml.dump(data))
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert any("runtimeClassName" in v for v in violations), violations


def test_validate_wrong_runtime_class_returns_violation(tmp_path: Path) -> None:
    data = yaml.safe_load(_VALID_MANIFEST)
    data["spec"]["template"]["spec"]["runtimeClassName"] = "kata-containers"
    path = _write_manifest(tmp_path, yaml.dump(data))
    violations = validate_deployment_manifest(path, SandboxConfig(runtime_class="gvisor"))
    assert any("runtimeClassName" in v for v in violations), violations


def test_validate_missing_read_only_root_returns_violation(tmp_path: Path) -> None:
    mutated = _mutate_manifest(_VALID_MANIFEST, readOnlyRootFilesystem=False)
    path = _write_manifest(tmp_path, mutated)
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert any("readOnlyRootFilesystem" in v for v in violations), violations


def test_validate_privilege_escalation_returns_violation(tmp_path: Path) -> None:
    mutated = _mutate_manifest(_VALID_MANIFEST, allowPrivilegeEscalation=True)
    path = _write_manifest(tmp_path, mutated)
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert any("allowPrivilegeEscalation" in v for v in violations), violations


def test_validate_run_as_root_returns_violation(tmp_path: Path) -> None:
    mutated = _mutate_manifest(_VALID_MANIFEST, runAsNonRoot=False)
    path = _write_manifest(tmp_path, mutated)
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert any("runAsNonRoot" in v for v in violations), violations


def test_validate_missing_capabilities_drop_returns_violation(tmp_path: Path) -> None:
    data = yaml.safe_load(_VALID_MANIFEST)
    sc = data["spec"]["template"]["spec"]["containers"][0]["securityContext"]
    sc["capabilities"]["drop"] = []
    path = _write_manifest(tmp_path, yaml.dump(data))
    violations = validate_deployment_manifest(path, SandboxConfig())
    assert any("capabilities.drop" in v for v in violations), violations


# ---------------------------------------------------------------------------
# generate_sandbox_config_toml tests
# ---------------------------------------------------------------------------


def test_generate_config_toml_contains_runtime_class() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig())
    assert 'runtime_class = "gvisor"' in toml_str


def test_generate_config_toml_gvisor_default() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig())
    assert 'runtime_class = "gvisor"' in toml_str
    assert "[sandbox]" in toml_str


def test_generate_config_toml_kata_runtime_class() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig(runtime_class="kata-containers"))
    assert 'runtime_class = "kata-containers"' in toml_str


def test_generate_config_toml_workspace_path() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig(workspace_path="/workspace"))
    assert 'workspace_path = "/workspace"' in toml_str


def test_generate_config_toml_enforce_read_only_root_true() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig(enforce_read_only_root=True))
    assert "enforce_read_only_root = true" in toml_str


def test_generate_config_toml_enforce_read_only_root_false() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig(enforce_read_only_root=False))
    assert "enforce_read_only_root = false" in toml_str


def test_generate_config_toml_network_policy_enabled() -> None:
    toml_str = generate_sandbox_config_toml(SandboxConfig(network_policy_enabled=True))
    assert "network_policy_enabled = true" in toml_str
