#!/bin/bash
# ClickHouse Admin — query, list, and delete benchmark entries.
#
# Usage:
#   export CLICKHOUSE_URL="<clickhouse-route-url>"
#   export CLICKHOUSE_USER="default"
#   export CLICKHOUSE_PASSWORD="<password>"
#
#   bash scripts/clickhouse_admin.sh list                  # list all workflows
#   bash scripts/clickhouse_admin.sh query "SELECT ..."    # run arbitrary query
#   bash scripts/clickhouse_admin.sh delete-workflow 123   # delete by workflow_id
#   bash scripts/clickhouse_admin.sh delete-all            # delete ALL spyre data
#   bash scripts/clickhouse_admin.sh count                 # count rows per table

set -uo pipefail

CLICKHOUSE_URL="${CLICKHOUSE_URL:?Set CLICKHOUSE_URL}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:?Set CLICKHOUSE_PASSWORD}"

ch_query() {
    local query="$1"
    curl -sk -u "${CLICKHOUSE_USER}:${CLICKHOUSE_PASSWORD}" \
        "${CLICKHOUSE_URL}/" -d "$query"
}

cmd_list() {
    echo "=== Workflows in spyre_e2e_benchmark ==="
    ch_query "SELECT
        workflow_id,
        fromUnixTimestamp64Milli(timestamp) AS time,
        model.name AS model,
        count() AS metrics
    FROM benchmark.oss_ci_benchmark_v3
    WHERE benchmark.name = 'spyre_e2e_benchmark'
    GROUP BY workflow_id, timestamp, model.name
    ORDER BY timestamp DESC
    FORMAT Pretty"

    echo ""
    echo "=== Workflows in vllm_benchmark ==="
    ch_query "SELECT
        workflow_id,
        fromUnixTimestamp64Milli(timestamp) AS time,
        model.name AS model,
        count() AS metrics
    FROM benchmark.oss_ci_benchmark_v3
    WHERE benchmark.name = 'vllm_benchmark'
    GROUP BY workflow_id, timestamp, model.name
    ORDER BY timestamp DESC
    FORMAT Pretty"
}

cmd_count() {
    echo "=== Row counts ==="
    echo -n "oss_ci_benchmark_v3:       "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_v3"
    echo -n "oss_ci_benchmark_metadata: "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_metadata"
    echo ""
    echo "=== By benchmark name ==="
    ch_query "SELECT benchmark.name, count() as rows FROM benchmark.oss_ci_benchmark_v3 GROUP BY benchmark.name FORMAT Pretty"
}

cmd_query() {
    local query="$1"
    ch_query "$query"
}

cmd_delete_workflow() {
    local wf_id="$1"
    echo "Deleting workflow_id=${wf_id}..."

    echo -n "  v3 rows to delete: "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_v3 WHERE workflow_id = ${wf_id}"

    echo -n "  metadata rows to delete: "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_metadata WHERE workflow_id = ${wf_id}"

    read -p "  Confirm delete? (yes/no): " confirm
    if [[ "$confirm" != "yes" ]]; then
        echo "  Aborted."
        return 1
    fi

    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_v3 DELETE WHERE workflow_id = ${wf_id}"
    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_metadata DELETE WHERE workflow_id = ${wf_id}"
    echo "  Deleted. (async — may take a few seconds)"
}

cmd_delete_all() {
    echo "WARNING: This will delete ALL spyre benchmark data from both tables."
    echo ""

    echo -n "  v3 rows (spyre_e2e_benchmark): "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_v3 WHERE benchmark.name = 'spyre_e2e_benchmark'"

    echo -n "  v3 rows (vllm_benchmark): "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_v3 WHERE benchmark.name = 'vllm_benchmark'"

    echo -n "  metadata rows: "
    ch_query "SELECT count() FROM benchmark.oss_ci_benchmark_metadata WHERE benchmark_name IN ('spyre_e2e_benchmark', 'vllm_benchmark')"

    echo ""
    read -p "  Type 'DELETE ALL' to confirm: " confirm
    if [[ "$confirm" != "DELETE ALL" ]]; then
        echo "  Aborted."
        return 1
    fi

    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_v3 DELETE WHERE benchmark.name IN ('spyre_e2e_benchmark', 'vllm_benchmark')"
    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_metadata DELETE WHERE benchmark_name IN ('spyre_e2e_benchmark', 'vllm_benchmark')"
    echo "  All spyre data deleted. (async — may take a few seconds)"
}

# --- Main ---
case "${1:-help}" in
    list)
        cmd_list
        ;;
    count)
        cmd_count
        ;;
    query)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 query \"SELECT ...\""; exit 1; }
        cmd_query "$2"
        ;;
    delete-workflow)
        [[ -z "${2:-}" ]] && { echo "Usage: $0 delete-workflow <workflow_id>"; exit 1; }
        cmd_delete_workflow "$2"
        ;;
    delete-all)
        cmd_delete_all
        ;;
    *)
        echo "ClickHouse Admin for Spyre Benchmarks"
        echo ""
        echo "Usage: $0 <command> [args]"
        echo ""
        echo "Commands:"
        echo "  list                     List all benchmark workflows"
        echo "  count                    Count rows in each table"
        echo "  query \"SQL\"              Run arbitrary ClickHouse query"
        echo "  delete-workflow <id>     Delete a specific workflow (with confirmation)"
        echo "  delete-all              Delete ALL spyre benchmark data (with confirmation)"
        echo ""
        echo "Required env vars: CLICKHOUSE_URL, CLICKHOUSE_USER, CLICKHOUSE_PASSWORD"
        ;;
esac
