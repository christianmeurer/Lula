# Lula — Roadmap

_Derived from `docs/quality_report.md` (2026-03-20). Items are ordered by severity and then by layer._

## Wave 1 — Critical Python Fixes ✅
- [x] Fix `build_meta_graph()` import crash in `main.py`
- [x] Restrict auth open-fallback: vote/approval-policy endpoints require authentication
- [x] Replace silent `except Exception: pass` in `audit.py` S3/GCS export with structured error logging

## Wave 2 — Critical Rust Fixes ✅
- [x] Add graceful HTTP shutdown (`ctrl_c` signal) to `rs/runner/src/main.rs`
- [x] Cap MCP `Content-Length` allocation at 64 MiB in `rs/runner/src/tools/mcp.rs`
- [x] Change default sandbox preference to `LinuxNamespace` when `unshare` is available

## Wave 3 — Infrastructure / DevOps Fixes ✅
- [x] Fix `Dockerfile.python`: non-root user, replace `curl | sh` with pinned installer
- [x] Restrict `sourceRepos` in `infra/k8s/argocd-project.yaml` to exact repo URL
- [x] Remove RBAC self-management from ArgoCD `ClusterRole` in `infra/k8s/argocd-rbac.yaml`
- [x] Add `NetworkPolicy` for `lula-orch` with explicit egress allowlist
- [x] Pin `trivy-action` to commit SHA in CI workflows
- [x] Add cosign image signing step to release workflow

## Wave 4 — Rust Soundness Fixes ✅
- [x] Replace `AF_VSOCK`-as-`TcpStream` with `AsyncFd<OwnedFd>` in `rs/runner/src/vsock.rs`
- [x] Replace same pattern in `rs/guest-agent/src/main.rs`
- [x] Add per-repo mutex for concurrent `git reset --hard` in `rs/runner/src/snapshots.rs`
- [x] Add guest agent `cmd` allowlist check

## Wave 5 — Python Long-Term Memory ✅
- [x] Wire real embedding provider (configurable, with Ollama/OpenAI adapters)
- [x] Add startup warning when `stub_embedder` is active
- [x] Document O(n) scan limitation; add row-count guard (warn at > 5 000 rows)

## Wave 6 — Structural Debt ✅
- [x] Split `py/src/lg_orch/checkpointing.py` into `backends/` submodule
- [x] Refactor `_api_http_dispatch()` in `remote_api.py` to dispatch table
- [x] Migrate `worktree.py` from stdlib `logging` to `structlog`
- [x] Migrate `python-jose` to `PyJWT`
- [x] Fix `model_routing.py` dict surgery with Pydantic model

## Wave 7 — Test & Eval Completion ✅
- [x] Add `--cov-fail-under=80` gate to CI
- [x] Remove `LG_E2E=1` guard from structural smoke tests in `test_e2e.py`
- [x] Complete golden assertion files for all 8 eval task categories
- [x] Parallelize `pass@k` multi-run loop in `eval/run.py`

## Backlog (Medium-term)
- [ ] Replace `stub_embedder` with configurable embedding provider (Ollama / OpenAI embeddings)
- [ ] Add vector index (sqlite-vec or pgvector) to replace full-table cosine scan
- [ ] Implement External Secrets Operator integration for K8s secret management
- [ ] Add `startupProbe` to both K8s deployments
- [ ] Add `deployment.yaml` static replicas = 2 to match HPA `minReplicas`
- [ ] Add SBOM generation (CycloneDX) to release workflow
- [ ] Fix `approval.rs` rotation secret to use `OnceLock`
- [ ] Fix `config.rs` allowlist wildcard arm to be locked-down default
- [ ] Add maximum timeout cap in `exec.rs` (e.g., 3 600 s)
- [ ] Add batch size limit to `batch_execute_tool` in `main.rs`
