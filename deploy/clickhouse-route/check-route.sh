#!/bin/bash
# Check if a ClickHouse route already exists on the dashboard cluster.
# Run this from a machine with oc access to the fmaas dashboard cluster.
set -euo pipefail

echo "Checking for existing ClickHouse routes in spyre-cdev namespace..."
oc login --server=https://api.fmaas-devstage-backend.fmaas.res.ibm.com:6443

ROUTES=$(oc get routes -n spyre-cdev -l app=clickhouse -o name 2>/dev/null || true)

if [[ -n "$ROUTES" ]]; then
    echo "Found existing route(s):"
    oc get routes -n spyre-cdev -l app=clickhouse
    echo ""
    echo "ClickHouse is already exposed. Use the HOST/PORT from above as CLICKHOUSE_URL."
else
    echo "No ClickHouse route found."
    echo ""
    echo "To create one, run:"
    echo "  oc apply -f deploy/clickhouse-route/route.yaml"
    echo ""
    echo "Then verify with:"
    echo "  curl -sk https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com/ping"
fi
