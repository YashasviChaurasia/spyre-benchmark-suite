#!/usr/bin/env python3
"""Convert vLLM benchmark result JSON files to .pytorch.json format.

This bridges our benchmark output to the format expected by the
existing Spyre ClickHouse pipeline (pivot_spyre_results.py).

Usage:
    # From pod logs:
    oc logs <pod> | python3 convert_to_pytorch_json.py --from-logs --output-dir ./results

    # From result JSON files:
    python3 convert_to_pytorch_json.py --input-dir ./results --output-dir ./pytorch-results

    # Then feed into the existing pipeline:
    python3 pivot_spyre_results.py --input ./pytorch-results --output /tmp/spyre.jsonl
    curl -X POST 'http://localhost:8123/?query=INSERT+INTO+...' --data-binary @/tmp/spyre.jsonl
"""

import json
import os
import sys
import argparse
from pathlib import Path


def latency_json_to_pytorch(result_json, test_name, config):
    """Convert a vllm bench latency JSON result to .pytorch.json entries."""
    entries = []
    model = config.get("model", "unknown")
    args = {
        "bench_type": "latency",
        "model": model,
        "tensor_parallel_size": config.get("tensor_parallel_size", 1),
        "input_len": config.get("input_len"),
        "output_len": config.get("output_len"),
        "batch_size": config.get("batch_size"),
    }
    base = {
        "benchmark": {
            "name": "Spyre vLLM benchmark",
            "extra_info": {
                "args": {k: v for k, v in args.items() if v is not None},
                "use_compile": not config.get("enforce_eager", True),
            },
        },
        "model": {"name": model},
    }

    # avg latency
    avg = result_json.get("avg_latency") or result_json.get("mean_latency")
    if avg is not None:
        entry = json.loads(json.dumps(base))
        entry["metric"] = {"name": "latency", "benchmark_values": [avg]}
        entries.append(entry)

    # percentiles
    percentiles = result_json.get("percentiles", {})
    for pct_key, metric_name in [("50", "median_latency_ms"), ("99", "p99_latency_ms")]:
        val = percentiles.get(pct_key) or percentiles.get(f"p{pct_key}")
        if val is not None:
            entry = json.loads(json.dumps(base))
            # Convert seconds to ms if value seems to be in seconds
            val_ms = val * 1000 if val < 100 else val
            entry["metric"] = {"name": metric_name, "benchmark_values": [val_ms]}
            entries.append(entry)

    return entries


def throughput_json_to_pytorch(result_json, test_name, config):
    """Convert a vllm bench throughput JSON result to .pytorch.json entries."""
    entries = []
    model = config.get("model", "unknown")
    args = {
        "bench_type": "throughput",
        "model": model,
        "tensor_parallel_size": config.get("tensor_parallel_size", 1),
        "input_len": config.get("input_len"),
        "output_len": config.get("output_len"),
        "num_prompts": config.get("num_prompts"),
    }
    base = {
        "benchmark": {
            "name": "Spyre vLLM benchmark",
            "extra_info": {
                "args": {k: v for k, v in args.items() if v is not None},
                "use_compile": not config.get("enforce_eager", True),
            },
        },
        "model": {"name": model},
    }

    metric_map = {
        "requests_per_second": "requests_per_second",
        "tokens_per_second": "tokens_per_second",
        "output_throughput_tok_s": "output_throughput_tok_s",
        "total_token_throughput_tok_s": "total_token_throughput_tok_s",
    }

    for json_key, metric_name in metric_map.items():
        val = result_json.get(json_key)
        if val is not None:
            entry = json.loads(json.dumps(base))
            entry["metric"] = {"name": metric_name, "benchmark_values": [val]}
            entries.append(entry)

    return entries


def extract_config_from_commands(commands_file):
    """Read the .commands file to reconstruct test config."""
    try:
        with open(commands_file) as f:
            data = json.load(f)
        cmd = data.get("command", "")
        config = {}
        parts = cmd.split()
        i = 0
        while i < len(parts):
            if parts[i].startswith("--") and i + 1 < len(parts):
                key = parts[i][2:].replace("-", "_")
                val = parts[i + 1]
                if val.startswith("--"):
                    # Boolean flag
                    config[key] = True
                    i += 1
                    continue
                try:
                    config[key] = int(val)
                except ValueError:
                    try:
                        config[key] = float(val)
                    except ValueError:
                        config[key] = val
                i += 2
            elif parts[i].startswith("--"):
                # Boolean flag at end
                config[parts[i][2:].replace("-", "_")] = True
                i += 1
            else:
                i += 1
        return config
    except Exception:
        return {}


def process_results_dir(input_dir, output_dir):
    """Process all result JSON files and produce .pytorch.json files."""
    input_path = Path(input_dir)
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)

    # Create artifact dir matching expected naming: benchmark-results--Spyre-<model>
    artifact_dir = output_path / "benchmark-results--Spyre-ibm-granite_granite-3.3-8b-instruct"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    count = 0
    for result_file in sorted(input_path.glob("*.json")):
        name = result_file.stem
        # Skip test configs and summary
        if name.endswith("-tests") or name == "benchmark_summary":
            continue

        try:
            with open(result_file) as f:
                result_json = json.load(f)
        except Exception as e:
            print(f"  WARNING: Could not read {result_file}: {e}")
            continue

        # Try to load config from .commands file
        commands_file = result_file.with_suffix(".commands")
        config = extract_config_from_commands(commands_file)

        # Detect type from test name
        if name.startswith("latency_"):
            entries = latency_json_to_pytorch(result_json, name, config)
        elif name.startswith("throughput_"):
            entries = throughput_json_to_pytorch(result_json, name, config)
        else:
            continue

        if entries:
            pytorch_file = artifact_dir / f"{name}.pytorch.json"
            with open(pytorch_file, "w") as f:
                json.dump(entries, f, indent=2)
            count += len(entries)
            print(f"  {name}: {len(entries)} metrics -> {pytorch_file.name}")

    print(f"\nTotal: {count} metric entries in {artifact_dir}")
    return artifact_dir


def extract_json_from_logs(log_text):
    """Extract benchmark_summary.json and individual results from pod logs."""
    results = {}

    # Extract individual result JSONs (between --- filename.json --- markers)
    lines = log_text.split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        if line.startswith("--- ") and line.endswith(".json ---"):
            filename = line[4:-4].strip()
            if filename.endswith("-tests.json") or filename == "benchmark_summary.json":
                i += 1
                continue
            # Collect JSON lines until next marker or empty line
            json_lines = []
            i += 1
            while i < len(lines):
                l = lines[i].strip()
                if l.startswith("---") or l.startswith("=====") or l.startswith("=========="):
                    break
                if l:
                    json_lines.append(l)
                i += 1
            if json_lines:
                try:
                    results[filename.replace(".json", "")] = json.loads("".join(json_lines))
                except json.JSONDecodeError:
                    pass
        else:
            i += 1

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Convert vLLM benchmark results to .pytorch.json format"
    )
    parser.add_argument("--input-dir", type=str,
                        help="Directory with vLLM benchmark result JSON files")
    parser.add_argument("--from-logs", action="store_true",
                        help="Read from stdin (pipe oc logs into this)")
    parser.add_argument("--output-dir", type=str, required=True,
                        help="Output directory for .pytorch.json files")
    args = parser.parse_args()

    if args.from_logs:
        print("Reading from stdin (pipe pod logs)...")
        log_text = sys.stdin.read()
        results = extract_json_from_logs(log_text)
        if not results:
            print("ERROR: No result JSONs found in logs")
            sys.exit(1)

        # Write individual result files to a temp dir, then process
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            for name, data in results.items():
                with open(os.path.join(tmpdir, f"{name}.json"), "w") as f:
                    json.dump(data, f)
            process_results_dir(tmpdir, args.output_dir)
    elif args.input_dir:
        process_results_dir(args.input_dir, args.output_dir)
    else:
        parser.error("Either --input-dir or --from-logs is required")


if __name__ == "__main__":
    main()
