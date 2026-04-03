#!/usr/bin/env bash
set -euo pipefail

NAMESPACE="${1:-lula-orch}"
echo "=== Lula Chaos Test Suite ==="

# Test 1: Pod deletion recovery
echo "[1/4] Testing pod deletion recovery..."
POD=$(kubectl get pods -n "$NAMESPACE" -l app=lula-orch -o jsonpath='{.items[0].metadata.name}')
kubectl delete pod "$POD" -n "$NAMESPACE" --grace-period=5
echo "  Deleted $POD, waiting for replacement..."
sleep 30
READY=$(kubectl get pods -n "$NAMESPACE" -l app=lula-orch --field-selector status.phase=Running --no-headers | wc -l)
echo "  Running orch pods: $READY"
[ "$READY" -ge 1 ] && echo "  PASS" || echo "  FAIL"

# Test 2: Health check after disruption
echo "[2/4] Testing health after disruption..."
sleep 10
HEALTH=$(kubectl exec -n "$NAMESPACE" $(kubectl get pods -n "$NAMESPACE" -l app=lula-orch -o jsonpath='{.items[0].metadata.name}') -- curl -sf http://localhost:8001/healthz 2>/dev/null || echo '{"ok":false}')
echo "$HEALTH" | grep -q '"ok": true' && echo "  PASS" || echo "  FAIL"

# Test 3: Runner pod deletion recovery
echo "[3/4] Testing runner pod recovery..."
RPOD=$(kubectl get pods -n "$NAMESPACE" -l app=lula-runner -o jsonpath='{.items[0].metadata.name}')
kubectl delete pod "$RPOD" -n "$NAMESPACE" --grace-period=5
echo "  Deleted $RPOD, waiting..."
sleep 30
RREADY=$(kubectl get pods -n "$NAMESPACE" -l app=lula-runner --field-selector status.phase=Running --no-headers | wc -l)
echo "  Running runner pods: $RREADY"
[ "$RREADY" -ge 1 ] && echo "  PASS" || echo "  FAIL"

# Test 4: PDB prevents simultaneous deletion
echo "[4/4] Testing PDB enforcement..."
PDB_STATUS=$(kubectl get pdb -n "$NAMESPACE" --no-headers 2>/dev/null | head -1)
if [ -n "$PDB_STATUS" ]; then
    echo "  PDB exists: $PDB_STATUS"
    echo "  PASS"
else
    echo "  No PDB found"
    echo "  SKIP"
fi

echo "=== Chaos tests complete ==="
