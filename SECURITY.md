# Security

## Reporting Vulnerabilities

Please report security vulnerabilities through [GitHub Security Advisories](https://github.com/christianmeurer/Lula/security/advisories/new) or by emailing `security@lula.dev`.

Do **not** open public issues for security vulnerabilities. We follow responsible disclosure: once a fix is available, we will coordinate a public disclosure timeline with the reporter.

We aim to acknowledge reports within 48 hours and provide an initial assessment within 5 business days.

## Scope

The Rust runner is a security-critical execution boundary. It enforces:

- **HMAC-SHA256 approval gates** — Time-bounded, nonce-bearing tokens with constant-time validation (`subtle::ConstantTimeEq`) for all mutation operations. Every `apply_patch` must carry a signed token; there is no client-side flag that skips the check.
- **Authentication by default** — The runner reads its bearer key from `--api-key` or `LG_RUNNER_API_KEY` and refuses to bind a non-loopback address without one.
- **Path confinement** — cap-std capability-based filesystem access (TOCTOU-safe); `**/.git/**` is always denied, re-checked on the symlink-resolved path.
- **Command allowlist** — Single canonical allowlist in `config.rs`; only `uv`, `python`, `pytest`, `ruff`, `mypy`, `cargo`, `git` permitted, with no shell and no shell metacharacters in arguments.
- **Sandbox tiers** — Firecracker MicroVM, Linux namespaces, or process-level `SafeFallback`, selected at startup and reported on every `exec` envelope (`isolation.backend`).
- **Prompt injection detection** — Bidirectional Unicode overrides, RCE shell vectors, and cryptomining patterns blocked.

## Secrets Handling

- Real credentials never live in the repository. Deployment values are tracked only as `charts/lula/values-doks.example.yaml` with `CHANGE_ME` placeholders; real values go in a gitignored `values-doks.local.yaml`, in `--set` flags, or in a Secret managed by the External Secrets Operator (`infra/k8s/external-secrets/`).
- `.gitignore` excludes credential-bearing files (`charts/lula/values-doks.yaml`, `.docker-cfg*`, `.claude/settings.local.json`, `infra/k8s/secrets.yaml`, `.env*`).
- **gitleaks** runs on every push and pull request over the full history (`.github/workflows/secret-scan.yml`, rules in `.gitleaks.toml`) and fails the build on a finding. Install the same check locally with `pre-commit install` (`.pre-commit-config.yaml`).
- Secrets injected into pods arrive as environment variables from Kubernetes Secrets, never from TOML files in the image.

### 2026-09-09 credential exposure

Deployment credentials (an API bearer token, a runner API key, an HMAC secret, a DigitalOcean model access key) were committed in `charts/lula/values-doks.yaml`, and a DigitalOcean personal access token was committed inside three Docker registry config files. All of them were revoked and rotated. The repository history was rewritten with `git filter-repo` to replace the literals and drop the Docker config files, and every branch and tag was force-pushed. Anyone holding a clone or fork from before that date should re-clone; commits from the old history may still be reachable on GitHub by hash until GitHub Support purges them.

## Supply-Chain Security

- `cargo deny` scans the Rust dependency tree on every pull request (license allowlist + advisory database)
- `pip-audit` scans Python dependencies in the `security-audit` CI job
- `trivy-action` (pinned to SHA) scans container images
- Cosign image signing in the release workflow

## Runtime Hardening

- Container runs as UID 10001 (non-root)
- `readOnlyRootFilesystem: true`
- `capabilities.drop: [ALL]`
- `seccompProfile: RuntimeDefault`
- `automountServiceAccountToken: false` on runner pods
- gVisor `runtimeClassName` in Kubernetes
