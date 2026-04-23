#!/usr/bin/env python3
"""Push benchmark results from pod logs directly to ClickHouse.

Extracts the benchmark_summary.json from pod logs and converts each
metric into ClickHouse oss_ci_benchmark_v3 JSONL rows.

Usage:
    # From pod logs (pipe):
    oc logs <pod> -n <ns> | python3 push_to_clickhouse.py --from-logs

    # From a saved summary JSON:
    python3 push_to_clickhouse.py --summary-file benchmark_summary.json

    # Dry run (print JSONL without pushing):
    oc logs <pod> -n <ns> | python3 push_to_clickhouse.py --from-logs --dry-run

    # Clean up test data:
    python3 push_to_clickhouse.py --delete --workflow-id <ID>
"""

import json
import sys
import argparse
import os
import subprocess
from datetime import datetime, timezone


CLICKHOUSE_URL = os.environ.get("CLICKHOUSE_URL", "http://localhost:8123")
BENCHMARK_NAME = "spyre_e2e_benchmark"
REPO = "ibm/vllm-spyre"
DEVICE = "spyre"
ARCH = "IBM Spyre"


def _parse_json_block(lines):
    """Parse a JSON object from log lines, handling shell trace artifacts."""
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("+ "):
            continue
        if "+ " in stripped and not stripped.startswith("{") and not stripped.startswith("["):
            stripped = stripped[:stripped.index("+ ")].rstrip()
        if stripped:
            cleaned.append(stripped)
    if not cleaned:
        return None
    raw = "\n".join(cleaned)
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        for i in range(len(raw) - 1, 0, -1):
            if raw[i] in ("}","]"):
                try:
                    return json.loads(raw[:i + 1])
                except json.JSONDecodeError:
                    continue
        return None


def extract_summary_from_logs(log_text):
    """Extract benchmark_summary.json from pod log output."""
    start_marker = "========== BENCHMARK RESULTS (JSON) =========="
    end_marker = "========== END BENCHMARK RESULTS =========="

    in_block = False
    json_lines = []
    for line in log_text.split("\n"):
        if start_marker in line:
            in_block = True
            continue
        if end_marker in line:
            break
        if in_block:
            json_lines.append(line)

    return _parse_json_block(json_lines)


def extract_individual_results_from_logs(log_text):
    """Extract individual result JSONs from the per-file printout in logs.

    Looks for blocks like:
        --- latency_test_name.json ---
        { ... json ... }
        --- next_file.json ---
    """
    import re
    results = {}
    lines = log_text.split("\n")
    i = 0
    while i < len(lines):
        # Match: --- some_test_name.json ---
        m = re.match(r'^---\s+(\S+\.json)\s+---', lines[i].strip())
        if m:
            filename = m.group(1)
            # Skip .pytorch.json, -tests.json, benchmark_summary.json
            if filename.endswith(".pytorch.json") or filename.endswith("-tests.json") or filename == "benchmark_summary.json":
                i += 1
                continue
            test_name = filename.replace(".json", "")
            json_lines = []
            i += 1
            while i < len(lines):
                l = lines[i].strip()
                if l.startswith("---") or l.startswith("===="):
                    break
                json_lines.append(lines[i])
                i += 1
            parsed = _parse_json_block(json_lines)
            if parsed:
                results[test_name] = parsed
        else:
            i += 1
    return results


def summary_to_clickhouse_rows(summary, head_sha=None, head_branch="main",
                                workflow_id=None, repo=REPO,
                                benchmark_name=BENCHMARK_NAME):
    """Convert benchmark_summary.json to ClickHouse JSONL rows."""
    timestamp_str = summary.get("timestamp", "")
    try:
        dt = datetime.strptime(timestamp_str, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
        timestamp_ms = int(dt.timestamp() * 1000)
    except (ValueError, TypeError):
        timestamp_ms = int(datetime.now(timezone.utc).timestamp() * 1000)

    if workflow_id is None:
        workflow_id = int(timestamp_ms / 1000)

    if head_sha is None:
        head_sha = str(workflow_id)[:9]

    rows = []
    for result in summary.get("results", []):
        test_name = result.get("test_name", "")
        test_type = result.get("type", "")
        model_name = "unknown"

        # Extract model from test name
        # e.g. latency_ibm_granite_granite_3.3_8b_instruct_in128_out128_bs1
        if "ibm_granite" in test_name:
            model_name = "ibm-granite/granite-3.3-8b-instruct"
        elif "meta_llama" in test_name:
            model_name = "meta-llama/Meta-Llama-3-8B"

        base_extra_info = {
            "device": DEVICE,
            "arch": ARCH,
            "hardware_type": DEVICE,
            "use_compile": "false",
        }

        base_extra = {
            "device": DEVICE,
            "arch": ARCH,
            "test_name": test_name,
        }

        # Generate one row per metric
        metrics = {}
        if test_type == "latency":
            if result.get("avg_latency_s") is not None:
                metrics["latency"] = result["avg_latency_s"]
            if result.get("p50_latency_s") is not None:
                metrics["median_latency_ms"] = result["p50_latency_s"] * 1000
            if result.get("p99_latency_s") is not None:
                metrics["p99_latency_ms"] = result["p99_latency_s"] * 1000
        elif test_type == "throughput":
            if result.get("tokens_per_second") is not None:
                metrics["tokens_per_second"] = result["tokens_per_second"]
            if result.get("requests_per_second") is not None:
                metrics["requests_per_second"] = result["requests_per_second"]

        for metric_name, value in metrics.items():
            extra = dict(base_extra)
            extra["value"] = str(float(value))

            row = {
                "repo": repo,
                "timestamp": timestamp_ms,
                "head_branch": head_branch,
                "head_sha": head_sha,
                "workflow_id": workflow_id,
                "model": {"name": model_name},
                "metric": {"name": metric_name},
                "benchmark": {
                    "name": benchmark_name,
                    "extra_info": base_extra_info,
                },
                "runners": {"name": ""},
                "extra": extra,
                "metadata_info": "",
            }
            rows.append(row)

    return rows


def push_to_clickhouse(jsonl_data, clickhouse_url=CLICKHOUSE_URL):
    """Push JSONL data to ClickHouse via HTTP API."""
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as tmp:
        tmp.write(jsonl_data)
        tmp_path = tmp.name

    result = subprocess.run(
        ["curl", "-s", "-w", "%{http_code}",
         "-X", "POST",
         f"{clickhouse_url}/?query=INSERT+INTO+benchmark.oss_ci_benchmark_v3+FORMAT+JSONEachRow",
         "--data-binary", f"@{tmp_path}"],
        capture_output=True, text=True,
    )
    os.unlink(tmp_path)
    http_code = result.stdout.strip()[-3:]
    body = result.stdout.strip()[:-3]
    if http_code != "200":
        print(f"ERROR: ClickHouse INSERT failed (HTTP {http_code}): {body}")
        return False
    return True


def delete_from_clickhouse(workflow_id=None, benchmark_name=None,
                            clickhouse_url=CLICKHOUSE_URL):
    """Delete rows from ClickHouse."""
    if workflow_id:
        where = f"workflow_id = {workflow_id}"
        meta_where = f"workflow_id = {workflow_id}"
    elif benchmark_name:
        where = f"benchmark.name = '{benchmark_name}'"
        meta_where = f"benchmark_name = '{benchmark_name}'"
    else:
        print("ERROR: Specify --workflow-id or --benchmark-name")
        return False

    # Show what would be deleted
    count_cmd = f"SELECT count(*) FROM benchmark.oss_ci_benchmark_v3 WHERE {where}"
    result = subprocess.run(
        ["curl", "-sf", f"{clickhouse_url}/", "-d", count_cmd],
        capture_output=True, text=True,
    )
    count = result.stdout.strip()
    print(f"Rows to delete: {count}")

    if count == "0":
        print("Nothing to delete.")
        return True

    # Delete
    subprocess.run(
        ["curl", "-sf", f"{clickhouse_url}/", "-d",
         f"ALTER TABLE benchmark.oss_ci_benchmark_v3 DELETE WHERE {where}"],
        capture_output=True,
    )
    subprocess.run(
        ["curl", "-sf", f"{clickhouse_url}/", "-d",
         f"ALTER TABLE benchmark.oss_ci_benchmark_metadata DELETE WHERE {meta_where}"],
        capture_output=True,
    )
    print(f"Deleted {count} rows. (async — may take a few seconds)")
    return True


def main():
    parser = argparse.ArgumentParser(description="Push benchmark results to ClickHouse")
    parser.add_argument("--from-logs", action="store_true",
                        help="Read benchmark_summary.json from stdin (pipe oc logs)")
    parser.add_argument("--summary-file", type=str,
                        help="Path to benchmark_summary.json file")
    parser.add_argument("--dry-run", action="store_true",
                        help="Print JSONL rows without pushing to ClickHouse")
    parser.add_argument("--head-sha", type=str, default=None)
    parser.add_argument("--head-branch", type=str, default="main")
    parser.add_argument("--workflow-id", type=int, default=None)
    parser.add_argument("--clickhouse-url", type=str, default=CLICKHOUSE_URL)
    parser.add_argument("--delete", action="store_true",
                        help="Delete data instead of inserting")
    parser.add_argument("--benchmark-name", type=str, default=BENCHMARK_NAME)
    args = parser.parse_args()

    if args.delete:
        delete_from_clickhouse(
            workflow_id=args.workflow_id,
            benchmark_name=args.benchmark_name,
            clickhouse_url=args.clickhouse_url,
        )
        return

    # Load summary
    if args.from_logs:
        log_text = sys.stdin.read()
        summary = extract_summary_from_logs(log_text)
        if not summary or summary.get("total_tests", 0) == 0:
            # Fallback: build summary from individual result JSONs in logs
            print("Summary empty, extracting individual results from logs...")
            individual = extract_individual_results_from_logs(log_text)
            if individual:
                ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                results = []
                for test_name, data in individual.items():
                    if test_name.startswith("latency_"):
                        r = {"test_name": test_name, "type": "latency"}
                        r["avg_latency_s"] = data.get("avg_latency")
                        pcts = data.get("percentiles", {})
                        r["p50_latency_s"] = pcts.get("50")
                        r["p99_latency_s"] = pcts.get("99")
                        results.append(r)
                    elif test_name.startswith("throughput_"):
                        r = {"test_name": test_name, "type": "throughput"}
                        r["tokens_per_second"] = data.get("tokens_per_second")
                        r["requests_per_second"] = data.get("requests_per_second")
                        results.append(r)
                summary = {"timestamp": ts, "device": "IBM_Spyre_PF", "total_tests": len(results), "results": results}
            else:
                print("ERROR: No results found in logs")
                sys.exit(1)
    elif args.summary_file:
        with open(args.summary_file) as f:
            summary = json.load(f)
    else:
        parser.error("Either --from-logs or --summary-file is required")

    print(f"Summary: {summary.get('total_tests', 0)} tests, timestamp={summary.get('timestamp')}")

    # Convert to JSONL
    rows = summary_to_clickhouse_rows(
        summary,
        head_sha=args.head_sha,
        head_branch=args.head_branch,
        workflow_id=args.workflow_id,
        benchmark_name=args.benchmark_name,
    )

    if not rows:
        print("ERROR: No metrics to push")
        sys.exit(1)

    jsonl = "\n".join(json.dumps(r) for r in rows)
    wf_id = rows[0]["workflow_id"]

    print(f"Generated {len(rows)} ClickHouse rows (workflow_id={wf_id})")

    if args.dry_run:
        print("\n--- JSONL (dry run) ---")
        for r in rows:
            print(json.dumps(r, indent=2))
        print(f"\nTo delete later: python3 {sys.argv[0]} --delete --workflow-id {wf_id}")
        return

    # Push
    print(f"Pushing to {args.clickhouse_url}...")
    if push_to_clickhouse(jsonl, args.clickhouse_url):
        print(f"SUCCESS: {len(rows)} rows inserted")
        print(f"Dashboard: /benchmark/v3/dashboard/{args.benchmark_name}")
        print(f"To delete: python3 {sys.argv[0]} --delete --workflow-id {wf_id}")
    else:
        sys.exit(1)


if __name__ == "__main__":
    main()
