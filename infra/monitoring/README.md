# Lula Operations — Monitoring

This directory contains a Grafana dashboard template and Prometheus alert
rules for monitoring Lula's production services.

## Alert rules

`prometheus-alerts.yaml` defines a `PrometheusRule` CRD for the
`monitoring.coreos.com/v1` API (Prometheus Operator). Apply it with:

```bash
kubectl apply -f infra/monitoring/prometheus-alerts.yaml
```

| Alert | Severity | Trigger |
|---|---|---|
| LulaOrchestratorDown | critical | Orchestrator target down for 2m |
| LulaRunnerDown | critical | Runner target down for 2m |
| LulaHighToolLatency | warning | Tool p95 latency > 30s for 5m |
| LulaHighErrorRate | warning | Tool error rate > 10% for 5m |
| LulaCgroupUnavailable | warning | cgroup v2 unavailable for 1m |
| LulaHighMemoryUsage | warning | Orchestrator memory > 90% for 5m |
| LulaPendingApprovals | info | > 5 suspended runs for 15m |

## Grafana dashboard

## Import instructions

1. Open your Grafana instance and navigate to **Dashboards → Import**.
2. Click **Upload JSON file** and select `grafana-dashboard.json`.
3. On the import screen, select your Prometheus datasource for the
   `DS_PROMETHEUS` input.
4. Click **Import**.

The dashboard will appear under the `lula` and `operations` tags.

## Panels

| # | Title | Query | Visualization |
|---|-------|-------|---------------|
| 1 | Tool Call Rate | `rate(runner_tool_calls_total[5m])` by `tool` | Time series |
| 2 | Tool Latency p95 | `histogram_quantile(0.95, rate(runner_tool_duration_seconds_bucket[5m]))` | Time series |
| 3 | Sandbox Tier Distribution | `runner_sandbox_tier` by `tier` | Pie chart |
| 4 | Run Rate | `rate(lula_runs_total[5m])` by `status` | Time series |
| 5 | Active Runs | `lula_runs_total{status="running"}` | Stat |
| 6 | cgroup Availability | `runner_cgroup_available` | Stat |

## Required metrics

The following Prometheus metrics must be exposed by your Lula services:

- `runner_tool_calls_total` — counter with `tool` label
- `runner_tool_duration_seconds` — histogram with `tool` label
- `runner_sandbox_tier` — gauge with `tier` label
- `lula_runs_total` — counter with `status` label
- `runner_cgroup_available` — gauge (0 or 1)
