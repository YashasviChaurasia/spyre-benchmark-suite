# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Config-driven vLLM benchmarking pipeline for IBM Spyre accelerators, deployed as OpenShift pods. Users edit `config/benchmark_config.yaml`, deploy via `oc apply -f build.yaml`, and results flow to a ClickHouse-backed dashboard.

## Architecture

The pipeline has 3 stages executed sequentially inside a pod:

1. **Config generation** (`scripts/generate_test_configs.py`) — reads `config/benchmark_config.yaml`, produces `results/latency-tests.json` and `results/throughput-tests.json` in the format expected by `vllm bench`
2. **Benchmark execution** (`scripts/run_benchmarks.sh`) — iterates over JSON test configs, converts each test's parameters to CLI args via `json2args` (jq), runs `vllm bench latency` / `vllm bench throughput`, writes per-test result JSONs
3. **Result collection** (`scripts/collect_results.py`) — aggregates per-test JSONs into `benchmark_summary.json` and prints a human-readable summary table

A separate local-only script (`scripts/push_to_clickhouse.py`) extracts results from pod logs and pushes to ClickHouse. This is run from the developer's machine, not inside the pod.

### Data flow

```
benchmark_config.yaml
  → generate_test_configs.py → latency-tests.json, throughput-tests.json
    → run_benchmarks.sh → per-test *.json result files
      → collect_results.py → benchmark_summary.json (printed between markers in stdout)
        → push_to_clickhouse.py (local) → ClickHouse oss_ci_benchmark_v3 table
```

### Key design decisions

- Each metric (latency, p50, p99, tokens/s) becomes a **separate row** in ClickHouse — not columns
- The summary JSON is printed between `========== BENCHMARK RESULTS (JSON) ==========` markers for extraction from pod logs
- Individual test failures do NOT abort the suite — `run_benchmarks.sh` continues and reports pass/fail counts
- `build.yaml` sets `restartPolicy: Never` so the pod stays in Completed state for log retrieval

## Development Commands

```bash
# Test config generation locally (only dependency: pyyaml)
pip install pyyaml
python3 scripts/generate_test_configs.py config/benchmark_config.yaml results/

# Deploy benchmark pod (requires oc login to benchmark cluster)
oc login --server=https://api.torch-cicd.spyre.res.ibm.com:6443
oc delete pod yc-vllm-spyre-benchmark -n torch-spyre-cicd 2>/dev/null
oc apply -f build.yaml -n torch-spyre-cicd

# Stream pod logs
oc logs -f yc-vllm-spyre-benchmark -n torch-spyre-cicd

# Dry-run ClickHouse push (requires port-forward to dashboard cluster on 8123)
oc logs yc-vllm-spyre-benchmark -n torch-spyre-cicd | \
  python3 scripts/push_to_clickhouse.py --from-logs --dry-run

# Push to ClickHouse
oc logs yc-vllm-spyre-benchmark -n torch-spyre-cicd | \
  python3 scripts/push_to_clickhouse.py --from-logs --benchmark-name vllm_benchmark

# Delete test data from ClickHouse
python3 scripts/push_to_clickhouse.py --delete --workflow-id <ID> --benchmark-name vllm_benchmark
```

## Clusters

| Cluster | Purpose | Namespace |
|---------|---------|-----------|
| `api.torch-cicd.spyre.res.ibm.com:6443` | Run benchmarks on Spyre PF cards | `torch-spyre-cicd` |
| `api.fmaas-devstage-backend.fmaas.res.ibm.com:6443` | ClickHouse + dashboard | `spyre-cdev` |

## ClickHouse Schema

- Table: `benchmark.oss_ci_benchmark_v3`
- Timestamp field: milliseconds (Int64)
- Metric value stored as string in `extra['value']`
- Dashboard visibility requires: `benchmark.name = "vllm_benchmark"`, `repo = "vllm-project/vllm"`, `extra_info.use_compile = "true"`

## Config Format

`config/benchmark_config.yaml` has 4 top-level keys:
- `engine` — vLLM engine params (dtype, max_model_len, max_num_seqs, load_format)
- `models` — list of model name + tensor_parallel_size
- `workloads.latency` — list of {input_len, output_len, batch_size, num_iters_warmup, num_iters}
- `workloads.throughput` — list of {input_len, output_len, num_prompts}

## Spyre-Specific Notes

- Base image has vLLM 0.18.1 which crashes during `compile_graph` warmup — the pod builds vLLM 0.19.0 from `spyre-inference` source
- Building torch-spyre requires Spyre SDK headers/libs at `/opt/ibm/spyre/` (see build.yaml for full include/library paths)
- Supported models at TP=1: `ibm-granite/granite-3.3-8b-instruct`, `ibm-granite/granite-4-8b-dense`, `meta-llama/Llama-3.1-8B-Instruct` (gated)
- `load_format: dummy` avoids HuggingFace download — uses random weights for benchmarking infra
- `SAVE_TO_PYTORCH_BENCHMARK_FORMAT=1` in build.yaml makes vllm bench auto-generate `.pytorch.json` files

## Git Workflow Defaults

- **Always create new branches** for new features/changes — never commit directly to main without testing
- Feature branches: `feat/<name>`, fixes: `fix/<name>`, tests: `test/<name>`
- Push to branch first, test via `bash run.sh` (pod clones from the remote branch), then merge to main
- The config's `deployment.benchmark_branch` must match the branch you're testing

## vLLM Bench CLI Notes

- `vllm bench latency` and `vllm bench throughput` use `--output-json <path>`
- `vllm bench serve` uses `--result-dir <dir> --result-filename <name>.json` (NOT --output-json)
- Serve benchmarks require starting a `vllm serve` server first, then running the bench client against it
- Server health check: `curl http://localhost:8000/health`
- vLLM 0.19 outputs JSON as a list `[{...}]` not a dict — handle both formats in parsers

## Session Defaults

- **Always update `docs/LEARNINGS.md`** when discovering new information about Spyre hardware behavior, vLLM quirks, cluster issues, OOM solutions, or dashboard requirements. This is the persistent knowledge base across sessions.
- Before making changes, check `docs/LEARNINGS.md` for known issues and validated solutions.
- When a test fails, document the root cause and fix in LEARNINGS.md before moving on.
- **Keep this CLAUDE.md updated** — when new patterns, conventions, or architectural decisions emerge during a session, add them here so future sessions start with accurate context.

## Git Commit Rules

- **Never add co-author lines** (no `Co-Authored-By` trailers) to commits.
- Keep commit messages concise and descriptive of the change.
