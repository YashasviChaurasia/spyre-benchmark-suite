#!/bin/bash
# Local Benchmark Watcher
#
# Polls the benchmark cluster for completed pods and pushes results
# to ClickHouse via its public HTTPS route.
#
# Prerequisites:
#   - oc CLI installed and logged into the benchmark cluster
#   - Environment variables set: CLICKHOUSE_URL, CLICKHOUSE_USER, CLICKHOUSE_PASSWORD
#
# Usage:
#   export CLICKHOUSE_URL="https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com"
#   export CLICKHOUSE_USER="default"
#   export CLICKHOUSE_PASSWORD="<password>"
#   oc login --server=https://api.torch-cicd.spyre.res.ibm.com:6443
#   bash scripts/watcher.sh
#
# Run in background:
#   nohup bash scripts/watcher.sh >> watcher.log 2>&1 &

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
NAMESPACE="${NAMESPACE:-torch-spyre-cicd}"
LABEL_SELECTOR="purpose=benchmark"
ANNOTATION_KEY="benchmark-watcher/pushed"
CONTAINER_NAME="${CONTAINER_NAME:-app}"
BENCHMARK_NAME="${BENCHMARK_NAME:-vllm_benchmark}"
POLL_INTERVAL="${POLL_INTERVAL:-60}"

# Verify requirements
if [[ -z "${CLICKHOUSE_URL:-}" ]]; then
    echo "[watcher] ERROR: CLICKHOUSE_URL not set"
    echo "[watcher] Set it to: https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com"
    exit 1
fi

if ! oc whoami &>/dev/null; then
    echo "[watcher] ERROR: Not logged into OpenShift. Run: oc login --server=https://api.torch-cicd.spyre.res.ibm.com:6443"
    exit 1
fi

echo "[watcher] =========================================="
echo "[watcher] Spyre Benchmark Watcher (local)"
echo "[watcher] =========================================="
echo "[watcher] Namespace: ${NAMESPACE}"
echo "[watcher] ClickHouse: ${CLICKHOUSE_URL}"
echo "[watcher] Poll interval: ${POLL_INTERVAL}s"
echo "[watcher] Benchmark name: ${BENCHMARK_NAME}"
echo "[watcher] =========================================="

process_pod() {
    local pod_name="$1"

    # Check if already processed
    local annotation
    annotation=$(oc get pod "$pod_name" -n "$NAMESPACE" \
        -o jsonpath="{.metadata.annotations.benchmark-watcher/pushed}" 2>/dev/null || echo "")
    if [[ "$annotation" == "true" ]]; then
        return 0
    fi

    echo "[watcher] $(date '+%H:%M:%S') Processing pod: ${pod_name}"

    # Extract logs
    local logs
    logs=$(oc logs "$pod_name" -n "$NAMESPACE" -c "$CONTAINER_NAME" 2>&1) || {
        echo "[watcher] ERROR: Failed to read logs from ${pod_name}"
        return 1
    }

    # Check for benchmark results marker
    if ! echo "$logs" | grep -q "========== BENCHMARK RESULTS (JSON) =========="; then
        echo "[watcher] WARNING: No results marker in ${pod_name}, skipping."
        oc annotate pod "$pod_name" -n "$NAMESPACE" \
            "${ANNOTATION_KEY}=skipped-no-results" --overwrite 2>/dev/null
        return 0
    fi

    # Push to ClickHouse
    echo "[watcher] Pushing results to ClickHouse..."
    if echo "$logs" | python3 "$SCRIPT_DIR/push_to_clickhouse.py" \
        --from-logs \
        --clickhouse-url "${CLICKHOUSE_URL}" \
        --benchmark-name "${BENCHMARK_NAME}"; then
        echo "[watcher] SUCCESS: Results pushed for ${pod_name}"
        oc annotate pod "$pod_name" -n "$NAMESPACE" \
            "${ANNOTATION_KEY}=true" --overwrite 2>/dev/null
    else
        echo "[watcher] ERROR: Push failed for ${pod_name}"
        oc annotate pod "$pod_name" -n "$NAMESPACE" \
            "${ANNOTATION_KEY}=failed" --overwrite 2>/dev/null
        return 1
    fi
}

# Main polling loop
while true; do
    # Find completed benchmark pods
    pods=$(oc get pods -n "$NAMESPACE" -l "$LABEL_SELECTOR" \
        --field-selector=status.phase=Succeeded \
        -o jsonpath='{.items[*].metadata.name}' 2>/dev/null)

    if [[ -n "$pods" ]]; then
        for pod in $pods; do
            process_pod "$pod" || true
        done
    fi

    sleep "$POLL_INTERVAL"
done
