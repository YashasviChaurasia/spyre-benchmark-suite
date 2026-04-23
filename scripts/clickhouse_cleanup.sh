#!/usr/bin/env bash
# clickhouse_cleanup.sh — Delete test benchmark data from ClickHouse.
#
# Usage:
#   # Delete by workflow_id (safest — targets a single run)
#   ./clickhouse_cleanup.sh --workflow-id 1713200000
#
#   # Delete all data for a benchmark name
#   ./clickhouse_cleanup.sh --benchmark-name spyre_e2e_benchmark
#
#   # Dry run — show what would be deleted without deleting
#   ./clickhouse_cleanup.sh --workflow-id 1713200000 --dry-run

set -euo pipefail

CLICKHOUSE_URL="${CLICKHOUSE_URL:-http://localhost:8123}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-default}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-}"
DRY_RUN=false
WORKFLOW_ID=""
BENCHMARK_NAME=""

usage() {
    echo "Usage: $0 [--workflow-id ID] [--benchmark-name NAME] [--dry-run]"
    echo ""
    echo "Options:"
    echo "  --workflow-id ID       Delete rows matching this workflow_id"
    echo "  --benchmark-name NAME  Delete all rows for this benchmark name"
    echo "  --dry-run              Show what would be deleted without deleting"
    echo "  --clickhouse-url URL   ClickHouse URL (default: http://localhost:8123)"
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --workflow-id)     WORKFLOW_ID="$2"; shift 2 ;;
        --benchmark-name)  BENCHMARK_NAME="$2"; shift 2 ;;
        --dry-run)         DRY_RUN=true; shift ;;
        --clickhouse-url)  CLICKHOUSE_URL="$2"; shift 2 ;;
        *)                 usage ;;
    esac
done

if [[ -z "$WORKFLOW_ID" && -z "$BENCHMARK_NAME" ]]; then
    echo "ERROR: Specify --workflow-id or --benchmark-name"
    usage
fi

AUTH_PARAMS=""
if [[ -n "$CLICKHOUSE_PASSWORD" ]]; then
    AUTH_PARAMS="&user=${CLICKHOUSE_USER}&password=${CLICKHOUSE_PASSWORD}"
fi

ch_query() {
    curl -sf "${CLICKHOUSE_URL}/" -d "$1" || echo "QUERY FAILED: $1"
}

# Build WHERE clause
WHERE=""
if [[ -n "$WORKFLOW_ID" ]]; then
    WHERE="workflow_id = ${WORKFLOW_ID}"
elif [[ -n "$BENCHMARK_NAME" ]]; then
    WHERE="benchmark.name = '${BENCHMARK_NAME}'"
fi

echo "============================================="
echo " ClickHouse Cleanup"
echo "============================================="
echo " Target:  ${WHERE}"
echo " Dry run: ${DRY_RUN}"
echo ""

# Show what would be deleted
echo "Rows in oss_ci_benchmark_v3:"
ch_query "SELECT count(*) FROM benchmark.oss_ci_benchmark_v3 WHERE ${WHERE}"

echo "Rows in oss_ci_benchmark_metadata:"
if [[ -n "$WORKFLOW_ID" ]]; then
    ch_query "SELECT count(*) FROM benchmark.oss_ci_benchmark_metadata WHERE workflow_id = ${WORKFLOW_ID}"
elif [[ -n "$BENCHMARK_NAME" ]]; then
    ch_query "SELECT count(*) FROM benchmark.oss_ci_benchmark_metadata WHERE benchmark_name = '${BENCHMARK_NAME}'"
fi

echo ""
echo "Sample rows that would be deleted:"
ch_query "SELECT head_sha, model.name, metric.name, extra['value'] FROM benchmark.oss_ci_benchmark_v3 WHERE ${WHERE} LIMIT 10 FORMAT PrettyCompact"

if [[ "$DRY_RUN" == "true" ]]; then
    echo ""
    echo "DRY RUN — nothing deleted. Remove --dry-run to delete."
    exit 0
fi

echo ""
read -p "Delete these rows? (yes/no): " CONFIRM
if [[ "$CONFIRM" != "yes" ]]; then
    echo "Aborted."
    exit 0
fi

echo ""
echo "Deleting from oss_ci_benchmark_v3..."
ch_query "ALTER TABLE benchmark.oss_ci_benchmark_v3 DELETE WHERE ${WHERE}"

echo "Deleting from oss_ci_benchmark_metadata..."
if [[ -n "$WORKFLOW_ID" ]]; then
    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_metadata DELETE WHERE workflow_id = ${WORKFLOW_ID}"
elif [[ -n "$BENCHMARK_NAME" ]]; then
    ch_query "ALTER TABLE benchmark.oss_ci_benchmark_metadata DELETE WHERE benchmark_name = '${BENCHMARK_NAME}'"
fi

echo ""
echo "Done. Note: ClickHouse DELETE is async — rows may take a few seconds to disappear."
echo "Verify: curl '${CLICKHOUSE_URL}/?query=SELECT+count(*)+FROM+benchmark.oss_ci_benchmark_v3+WHERE+${WHERE// /+}'"
