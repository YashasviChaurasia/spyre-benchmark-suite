# Spyre Benchmark Suite

Config-driven vLLM benchmarking on IBM Spyre accelerators via OpenShift, with automated result push to a ClickHouse-backed dashboard.

---

## Quick Start

```bash
# 1. Fork this repo on GitHub, then clone your fork
git clone https://github.com/<your-username>/spyre-benchmark-suite.git
cd spyre-benchmark-suite

# 2. One-time setup: create .env with credentials
cp .env.example .env
vim .env    # fill in CLICKHOUSE_PASSWORD

# 3. Configure what to benchmark
vim config/benchmark_config.yaml

# 4. Update deployment.benchmark_repo in config to point to YOUR fork
#    (the pod clones from this URL at runtime)

# 5. Push your config changes
git add -A && git commit -m "configure benchmarks" && git push

# 6. Login and run
oc login --server=<benchmark-cluster-api-url>
bash run.sh
```

That's it. `run.sh` deploys the benchmark pod, waits for completion, and pushes results to the dashboard automatically.

> **Important:** The benchmark pod clones config from your **remote repo** (the `benchmark_repo` + `benchmark_branch` in config). You must **push changes before running** — local-only edits won't be picked up by the pod.

---

## How `run.sh` Works

```
bash run.sh
 ├─ 1. Reads config/benchmark_config.yaml
 ├─ 2. Renders build.yaml from template (build.yaml.tpl)
 ├─ 3. Deploys the benchmark pod to OpenShift
 ├─ 4. Waits for pod to complete (configurable timeout)
 ├─ 5. Extracts results from pod logs
 ├─ 6. Pushes metrics to ClickHouse (dashboard DB)
 ├─ 7. Annotates pod as processed
 └─ 8. Prints summary + dashboard link
```

### Options

```bash
bash run.sh                  # default: deploy + wait + push
bash run.sh --follow         # stream pod logs in real-time while waiting
bash run.sh --no-push        # deploy + wait only, skip dashboard push
bash run.sh --config my.yaml # use a different config file
```

---

## Repository Structure

```
spyre-benchmark-suite/
├── run.sh                          # Single-command entry point
├── .env.example                    # Credentials template (copy to .env)
├── build.yaml.tpl                  # Pod spec template (auto-rendered)
├── build.yaml                      # Generated — do not edit directly
├── config/
│   └── benchmark_config.yaml       # THE file to configure everything
├── scripts/
│   ├── render_build_yaml.py        # Config → build.yaml renderer
│   ├── generate_test_configs.py    # Config → vLLM JSON test format
│   ├── run_benchmarks.sh           # Benchmark orchestrator (runs inside pod)
│   ├── collect_results.py          # Aggregates results into summary
│   ├── push_to_clickhouse.py       # Pushes results to ClickHouse
│   ├── watcher.sh                  # Background polling watcher (alternative)
│   └── clickhouse_admin.sh         # Admin: list, query, delete data
└── deploy/
    └── clickhouse-route/           # Route manifest for ClickHouse
```

---

## Configuration

### `config/benchmark_config.yaml`

This single file controls everything. Edit it in your fork, push, then run.

```yaml
# What to benchmark
engine:
  dtype: float16
  max_model_len: 3072
  max_num_seqs: 16
  load_format: auto               # "auto" = real HF weights, "dummy" = random weights

models:
  - name: ibm-granite/granite-3.3-8b-instruct
    tensor_parallel_size: 1
  - name: ibm-ai-platform/micro-g3.3-8b-instruct-1b
    tensor_parallel_size: 1

workloads:
  latency:
    - input_len: 128
      output_len: 128
      batch_size: 1
      num_iters_warmup: 2
      num_iters: 5
  throughput:
    - input_len: 128
      output_len: 128
      num_prompts: 20

# Deployment settings
deployment:
  pod_name: yc-vllm-spyre-benchmark       # change to avoid conflicts with others
  namespace: torch-spyre-cicd
  spyre_pf_cards: 1
  benchmark_repo: https://github.com/<your-username>/spyre-benchmark-suite.git  # YOUR fork
  benchmark_branch: main                   # branch with your config changes
  spyre_inference_repo: https://github.com/torch-spyre/spyre-inference.git
  spyre_inference_branch: main

# Dashboard push settings
dashboard:
  benchmark_name: spyre_e2e_benchmark
  auto_push: true
  timeout: 3600
```

### How the pod picks up your config

The pod **clones your fork** at runtime using `deployment.benchmark_repo` + `deployment.benchmark_branch`. This means:

1. You edit `config/benchmark_config.yaml` locally
2. You **push to your fork** (`git push`)
3. You run `bash run.sh`
4. The pod clones your pushed config and runs those benchmarks

If you don't push, the pod uses whatever is on the remote branch — not your local changes.

### `.env` (secrets — gitignored)

```bash
BENCHMARK_SERVER=<benchmark-cluster-api-url>
CLICKHOUSE_URL=<clickhouse-route-url>
CLICKHOUSE_USER=default
CLICKHOUSE_PASSWORD=<password>
```

---

## Supported Models (TP=1)

- `ibm-granite/granite-3.3-8b-instruct` (max_model_len=3072)
- `ibm-granite/granite-4-8b-dense`
- `meta-llama/Llama-3.1-8B-Instruct` (gated — needs HF_TOKEN)

---

## Admin Operations

```bash
# List all benchmark runs in the database
bash scripts/clickhouse_admin.sh list

# Count rows
bash scripts/clickhouse_admin.sh count

# Delete a specific run
bash scripts/clickhouse_admin.sh delete-workflow <workflow_id>

# Delete all spyre data
bash scripts/clickhouse_admin.sh delete-all

# Run arbitrary SQL
bash scripts/clickhouse_admin.sh query "SELECT count() FROM benchmark.oss_ci_benchmark_v3"
```

---

## Alternative: Background Watcher

If you want continuous monitoring instead of one-shot runs:

```bash
bash scripts/watcher.sh
```

This polls every 60s for completed benchmark pods and pushes their results automatically. Useful when multiple users deploy benchmark pods.

---

## Re-running Benchmarks

Change config, push, run:

```bash
vim config/benchmark_config.yaml   # change models/workloads
git add -A && git commit -m "update config" && git push
bash run.sh
```

The script deletes the old pod and deploys fresh each time.

---

## What Happens During a Run

When you run `bash run.sh`, the pod goes through these phases:

1. **Build phase** (~10-15 min): Installs `uv`, clones `spyre-inference`, builds vLLM 0.19.0 + torch-spyre from source
2. **Benchmark phase** (~2-5 min): Runs `vllm bench latency` and `vllm bench throughput` for each test case
3. **Collection phase**: Aggregates results, prints summary JSON between markers
4. **Pod exits** with status `Succeeded`

### Successful Output

On a successful run, `run.sh` prints:

```
==========================================
  Spyre Benchmark Suite
==========================================
  Config:    config/benchmark_config.yaml
  Pod:       yc-vllm-spyre-benchmark
  Namespace: torch-spyre-cicd
  Timeout:   3600s
  Auto-push: true
==========================================
[run] Rendering build.yaml from config...
[run] Deploying benchmark pod...
[run] Pod yc-vllm-spyre-benchmark deployed. Waiting for completion...
pod/yc-vllm-spyre-benchmark condition met
[run] Pod completed successfully.
[run] Pushing results to dashboard...
Summary: 2 tests, timestamp=20260506T051854Z
Generated 5 ClickHouse rows (workflow_id=1778044734)
SUCCESS: 5 rows inserted into oss_ci_benchmark_v3
SUCCESS: 5 metadata rows inserted
==========================================
  Done!
==========================================
```

<!-- TODO: Add screenshot of pod completion in OpenShift console -->
<!-- ![Pod Completion](docs/pod-completion.png) -->

---

## Querying the Database

You can run arbitrary SQL against ClickHouse:

```bash
# Count all entries
bash scripts/clickhouse_admin.sh query "SELECT count() FROM benchmark.oss_ci_benchmark_v3"

# List recent results with timestamps
bash scripts/clickhouse_admin.sh query "
  SELECT workflow_id, fromUnixTimestamp64Milli(timestamp) as time, metric.name, model.name
  FROM benchmark.oss_ci_benchmark_v3
  WHERE benchmark.name = 'spyre_e2e_benchmark'
  ORDER BY timestamp DESC
  LIMIT 20
  FORMAT Pretty
"

# Check what models have data
bash scripts/clickhouse_admin.sh query "
  SELECT DISTINCT model.name FROM benchmark.oss_ci_benchmark_v3
  WHERE benchmark.name = 'spyre_e2e_benchmark'
  FORMAT Pretty
"
```

---

## Cleanup

```bash
# Delete a specific benchmark run by workflow ID
bash scripts/clickhouse_admin.sh delete-workflow 1778044734

# Delete ALL spyre benchmark data (with confirmation prompt)
bash scripts/clickhouse_admin.sh delete-all

# List runs to find workflow IDs
bash scripts/clickhouse_admin.sh list
```

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Pod stuck in Pending | `oc describe pod ...` — likely no Spyre PF card available |
| Pod exits immediately | `oc logs ...` — check for git clone / uv sync errors |
| Tests fail but pod completes | Look for `FAIL:` in logs — reduce `max_model_len` or `batch_size` |
| Push fails | Verify `.env` has correct `CLICKHOUSE_PASSWORD` |
| Dashboard not showing data | Ensure `benchmark_name` matches dashboard config (`spyre_e2e_benchmark`) |

---

## Local Testing (no Spyre needed)

```bash
pip install pyyaml
python3 scripts/generate_test_configs.py config/benchmark_config.yaml results/
python3 scripts/render_build_yaml.py     # verify build.yaml renders correctly
```
