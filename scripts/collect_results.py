#!/usr/bin/env python3
"""Aggregate benchmark JSON results into a single summary file.

Reads individual result files from the results directory, adds metadata,
and produces benchmark_summary.json + a human-readable table on stdout.

Usage:
    python collect_results.py [results_dir] [timestamp]
"""

import glob
import json
import os
import sys
from datetime import datetime, timezone


def collect_latency_results(results_dir: str) -> list[dict]:
    results = []
    for f in sorted(glob.glob(os.path.join(results_dir, "latency_*.json"))):
        if f.endswith("-tests.json"):
            continue
        test_name = os.path.splitext(os.path.basename(f))[0]
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not read {f}: {e}")
            continue

        result = {
            "test_name": test_name,
            "type": "latency",
        }

        # Handle different vLLM output formats
        if "avg_latency" in data:
            result["avg_latency_s"] = data["avg_latency"]
        elif "mean_latency" in data:
            result["avg_latency_s"] = data["mean_latency"]

        # Percentiles may be nested differently across vLLM versions
        percentiles = data.get("percentiles", {})
        if percentiles:
            result["p50_latency_s"] = percentiles.get("50") or percentiles.get("p50")
            result["p99_latency_s"] = percentiles.get("99") or percentiles.get("p99")

        if "latencies" in data:
            result["num_iterations"] = len(data["latencies"])

        results.append(result)
    return results


def collect_throughput_results(results_dir: str) -> list[dict]:
    results = []
    for f in sorted(glob.glob(os.path.join(results_dir, "throughput_*.json"))):
        if f.endswith("-tests.json"):
            continue
        test_name = os.path.splitext(os.path.basename(f))[0]
        try:
            with open(f) as fh:
                data = json.load(fh)
        except (json.JSONDecodeError, OSError) as e:
            print(f"  WARNING: Could not read {f}: {e}")
            continue

        result = {
            "test_name": test_name,
            "type": "throughput",
            "elapsed_time_s": data.get("elapsed_time"),
            "num_requests": data.get("num_requests"),
            "total_tokens": data.get("total_num_tokens"),
            "requests_per_second": data.get("requests_per_second"),
            "tokens_per_second": data.get("tokens_per_second"),
        }
        results.append(result)
    return results


def print_summary_table(results: list[dict], timestamp: str) -> None:
    print()
    print("=" * 72)
    print(f"  Benchmark Summary — {timestamp} — IBM Spyre PF")
    print("=" * 72)

    latency_results = [r for r in results if r["type"] == "latency"]
    throughput_results = [r for r in results if r["type"] == "throughput"]

    if latency_results:
        print()
        print("  LATENCY BENCHMARKS")
        print("  " + "-" * 68)
        for r in latency_results:
            print(f"  {r['test_name']}")
            avg = r.get("avg_latency_s")
            p50 = r.get("p50_latency_s")
            p99 = r.get("p99_latency_s")
            parts = []
            if avg is not None:
                parts.append(f"Avg: {avg:.4f}s")
            if p50 is not None:
                parts.append(f"P50: {p50:.4f}s")
            if p99 is not None:
                parts.append(f"P99: {p99:.4f}s")
            if parts:
                print(f"    {' | '.join(parts)}")
            else:
                print("    (no metrics parsed)")

    if throughput_results:
        print()
        print("  THROUGHPUT BENCHMARKS")
        print("  " + "-" * 68)
        for r in throughput_results:
            print(f"  {r['test_name']}")
            tok_s = r.get("tokens_per_second")
            req_s = r.get("requests_per_second")
            elapsed = r.get("elapsed_time_s")
            parts = []
            if tok_s is not None:
                parts.append(f"{tok_s:.1f} tok/s")
            if req_s is not None:
                parts.append(f"{req_s:.2f} req/s")
            if elapsed is not None:
                parts.append(f"{elapsed:.1f}s elapsed")
            if parts:
                print(f"    {' | '.join(parts)}")
            else:
                print("    (no metrics parsed)")

    if not results:
        print("  No benchmark results found.")

    print()
    print("=" * 72)


def main():
    results_dir = sys.argv[1] if len(sys.argv) > 1 else "results"
    timestamp = (
        sys.argv[2]
        if len(sys.argv) > 2
        else datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    )

    latency_results = collect_latency_results(results_dir)
    throughput_results = collect_throughput_results(results_dir)
    all_results = latency_results + throughput_results

    summary = {
        "timestamp": timestamp,
        "device": "IBM_Spyre_PF",
        "total_tests": len(all_results),
        "results": all_results,
    }

    output_path = os.path.join(results_dir, "benchmark_summary.json")
    with open(output_path, "w") as f:
        json.dump(summary, f, indent=2)

    print(f"  Summary written to {output_path}")
    print(f"  Total results: {len(all_results)} ({len(latency_results)} latency, {len(throughput_results)} throughput)")

    print_summary_table(all_results, timestamp)


if __name__ == "__main__":
    main()
