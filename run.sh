#!/bin/bash
# Spyre Benchmark Suite — Single Command Runner
#
# Deploys benchmarks and pushes results to the dashboard.
#
# Usage:
#   bash run.sh                  # deploy + wait + push (default)
#   bash run.sh --no-push        # deploy + wait only
#   bash run.sh --follow         # deploy + stream logs + push
#   bash run.sh --config my.yaml # use custom config file
#
# Prerequisites:
#   1. cp .env.example .env && edit .env (one-time)
#   2. Edit config/benchmark_config.yaml (what to benchmark)
#   3. bash run.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/config/benchmark_config.yaml"
AUTO_PUSH="true"
FOLLOW_LOGS="false"

# Parse args
while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-push)    AUTO_PUSH="false"; shift ;;
        --follow)     FOLLOW_LOGS="true"; shift ;;
        --config)     CONFIG_FILE="$2"; shift 2 ;;
        -h|--help)
            echo "Usage: bash run.sh [--no-push] [--follow] [--config path]"
            echo ""
            echo "Options:"
            echo "  --no-push     Deploy and wait, but skip pushing results to dashboard"
            echo "  --follow      Stream pod logs in real-time while waiting"
            echo "  --config      Path to config YAML (default: config/benchmark_config.yaml)"
            exit 0 ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# -----------------------------------------------
# Load .env
# -----------------------------------------------
if [[ -f "$SCRIPT_DIR/.env" ]]; then
    set -a
    source "$SCRIPT_DIR/.env"
    set +a
else
    echo "ERROR: .env file not found."
    echo ""
    echo "Setup:"
    echo "  cp .env.example .env"
    echo "  # Edit .env with your credentials"
    exit 1
fi

# -----------------------------------------------
# Read config
# -----------------------------------------------
if [[ ! -f "$CONFIG_FILE" ]]; then
    echo "ERROR: Config file not found: $CONFIG_FILE"
    exit 1
fi

# Extract deployment settings from config YAML
POD_NAME=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('deployment',{}).get('pod_name','yc-vllm-spyre-benchmark'))")
NAMESPACE=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('deployment',{}).get('namespace','torch-spyre-cicd'))")
TIMEOUT=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('dashboard',{}).get('timeout',3600))")
BENCHMARK_NAME=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(c.get('dashboard',{}).get('benchmark_name','spyre_e2e_benchmark'))")
CFG_AUTO_PUSH=$(python3 -c "import yaml; c=yaml.safe_load(open('$CONFIG_FILE')); print(str(c.get('dashboard',{}).get('auto_push',True)).lower())")

# Config auto_push can be overridden by --no-push flag
if [[ "$AUTO_PUSH" == "true" ]]; then
    AUTO_PUSH="$CFG_AUTO_PUSH"
fi

echo "=========================================="
echo "  Spyre Benchmark Suite"
echo "=========================================="
echo "  Config:    $CONFIG_FILE"
echo "  Pod:       $POD_NAME"
echo "  Namespace: $NAMESPACE"
echo "  Timeout:   ${TIMEOUT}s"
echo "  Auto-push: $AUTO_PUSH"
echo "=========================================="

# -----------------------------------------------
# Verify oc login
# -----------------------------------------------
if ! oc whoami &>/dev/null; then
    if [[ -n "${BENCHMARK_SERVER:-}" ]]; then
        echo "[run] Logging into benchmark cluster..."
        oc login --server="$BENCHMARK_SERVER" || {
            echo "ERROR: oc login failed. Run manually: oc login --server=$BENCHMARK_SERVER"
            exit 1
        }
    else
        echo "ERROR: Not logged into OpenShift and BENCHMARK_SERVER not set in .env"
        exit 1
    fi
fi

# -----------------------------------------------
# Render build.yaml from template
# -----------------------------------------------
echo "[run] Rendering build.yaml from config..."
python3 "$SCRIPT_DIR/scripts/render_build_yaml.py" "$CONFIG_FILE" "$SCRIPT_DIR/build.yaml.tpl" "$SCRIPT_DIR/build.yaml"

# -----------------------------------------------
# Deploy
# -----------------------------------------------
echo "[run] Deploying benchmark pod..."
oc delete pod "$POD_NAME" -n "$NAMESPACE" 2>/dev/null || true
sleep 2
oc apply -f "$SCRIPT_DIR/build.yaml" -n "$NAMESPACE"

echo "[run] Pod $POD_NAME deployed. Waiting for completion (timeout: ${TIMEOUT}s)..."

# -----------------------------------------------
# Follow logs (optional) + wait
# -----------------------------------------------
if [[ "$FOLLOW_LOGS" == "true" ]]; then
    # Wait for pod to start, then tail logs
    echo "[run] Waiting for pod to start..."
    oc wait --for=condition=Ready pod/"$POD_NAME" -n "$NAMESPACE" --timeout=300s 2>/dev/null || true
    echo "[run] Streaming logs (Ctrl+C to stop following, pod continues running)..."
    oc logs -f "$POD_NAME" -n "$NAMESPACE" -c app 2>/dev/null || true
fi

# Wait for pod to reach Succeeded
oc wait --for=jsonpath='{.status.phase}'=Succeeded pod/"$POD_NAME" -n "$NAMESPACE" --timeout="${TIMEOUT}s" || {
    PHASE=$(oc get pod "$POD_NAME" -n "$NAMESPACE" -o jsonpath='{.status.phase}' 2>/dev/null || echo "Unknown")
    echo "ERROR: Pod did not complete successfully. Phase: $PHASE"
    echo "Check logs: oc logs $POD_NAME -n $NAMESPACE -c app"
    exit 1
}

echo "[run] Pod completed successfully."

# -----------------------------------------------
# Push results
# -----------------------------------------------
if [[ "$AUTO_PUSH" == "true" ]]; then
    if [[ -z "${CLICKHOUSE_URL:-}" ]]; then
        echo "WARNING: CLICKHOUSE_URL not set in .env — skipping push."
    else
        echo "[run] Pushing results to dashboard..."
        oc logs "$POD_NAME" -n "$NAMESPACE" -c app | \
            python3 "$SCRIPT_DIR/scripts/push_to_clickhouse.py" \
                --from-logs \
                --clickhouse-url "$CLICKHOUSE_URL" \
                --benchmark-name "$BENCHMARK_NAME"

        if [[ $? -eq 0 ]]; then
            oc annotate pod "$POD_NAME" -n "$NAMESPACE" \
                "benchmark-watcher/pushed=true" --overwrite 2>/dev/null
        fi
    fi
else
    echo "[run] Skipping push (--no-push or auto_push: false)."
    echo "  Manual push: oc logs $POD_NAME -n $NAMESPACE -c app | python3 scripts/push_to_clickhouse.py --from-logs --clickhouse-url \$CLICKHOUSE_URL --benchmark-name $BENCHMARK_NAME"
fi

echo ""
echo "=========================================="
echo "  Done!"
echo "=========================================="
