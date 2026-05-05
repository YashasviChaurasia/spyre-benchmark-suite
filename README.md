# Spyre Benchmark Suite

Config-driven vLLM benchmarking on IBM Spyre accelerators via OpenShift, with automated result push to a ClickHouse-backed dashboard.

---

## End-to-End Workflow

```
1. Edit config/benchmark_config.yaml (what to benchmark)
2. oc apply -f build.yaml                 (deploy benchmark pod)
3. Pod runs benchmarks → exits Completed
4. Watcher detects completion → pushes results to ClickHouse
5. Dashboard auto-displays new data
```

---

## Quick Start

### Prerequisites

- `oc` CLI installed
- Access to the benchmark cluster: `https://api.torch-cicd.spyre.res.ibm.com:6443`
- Python 3.7+ with `pyyaml` (for local config testing)

### Step 1: Configure Benchmarks

Edit `config/benchmark_config.yaml`:

```yaml
engine:
  dtype: float16
  max_model_len: 3072
  max_num_seqs: 16
  load_format: dummy        # "dummy" = random weights, "auto" = real weights

models:
  - name: ibm-granite/granite-3.3-8b-instruct
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
```

### Step 2: Deploy Benchmark Pod

```bash
oc login --token=<token> --server=https://api.torch-cicd.spyre.res.ibm.com:6443
oc delete pod yc-vllm-spyre-benchmark-v2 -n torch-spyre-cicd 2>/dev/null
oc apply -f build.yaml -n torch-spyre-cicd
```

### Step 3: Monitor Progress

```bash
oc logs -f yc-vllm-spyre-benchmark-v2 -n torch-spyre-cicd
```

### Step 4: Push Results to Dashboard (Automated)

Run the watcher — it polls for completed pods and pushes to ClickHouse:

```bash
export CLICKHOUSE_URL="https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com"
export CLICKHOUSE_USER="default"
export CLICKHOUSE_PASSWORD="<password>"

bash scripts/watcher.sh
```

Or run in background:
```bash
nohup bash scripts/watcher.sh >> watcher.log 2>&1 &
```

### Step 5: View on Dashboard

Open: https://vllm-dashboard-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com/benchmark/v3/dashboard/vllm_benchmark

---

## Repository Structure

```
spyre-benchmark-suite/
├── build.yaml                      # OpenShift Pod spec (4 user-configurable env vars)
├── config/
│   └── benchmark_config.yaml       # WHAT to benchmark — models, workloads, engine
├── scripts/
│   ├── generate_test_configs.py    # YAML config → vLLM JSON test format
│   ├── run_benchmarks.sh           # Orchestrator: json2args + vllm bench CLI
│   ├── collect_results.py          # Aggregates results into summary JSON + table
│   ├── push_to_clickhouse.py       # Pushes results to ClickHouse (used by watcher)
│   └── watcher.sh                  # Polls for completed pods, auto-pushes results
├── deploy/
│   └── clickhouse-route/
│       ├── route.yaml              # OpenShift Route for ClickHouse (dashboard cluster)
│       └── check-route.sh          # Checks if route exists
└── results/
    └── .gitkeep
```

---

## Dashboard Setup Guide

The dashboard runs on the **dashboard cluster** (`api.fmaas-devstage-backend.fmaas.res.ibm.com:6443`) in namespace `spyre-cdev`. It consists of:

1. **ClickHouse** — stores benchmark metrics
2. **torchci (Next.js)** — web UI that reads from ClickHouse
3. **ClickHouse Route** — public HTTPS endpoint for external data ingestion

### First-Time Dashboard Deployment

The dashboard manifests live in the `replicate-pytorch-hud` repo under `deploy/openshift/`. Apply them in order:

```bash
oc login --server=https://api.fmaas-devstage-backend.fmaas.res.ibm.com:6443

# 1. Image streams
oc apply -f deploy/openshift/00-imagestreams.yaml -n spyre-cdev

# 2. ClickHouse (PVC + config + deployment + service)
oc apply -f deploy/openshift/01-clickhouse-pvc.yaml -n spyre-cdev
oc apply -f deploy/openshift/02-clickhouse-deployment.yaml -n spyre-cdev
oc apply -f deploy/openshift/02a-clickhouse-config.yaml -n spyre-cdev
oc apply -f deploy/openshift/03-clickhouse-service.yaml -n spyre-cdev

# 3. ClickHouse public route (for external push)
oc apply -f deploy/openshift/03a-clickhouse-route.yaml -n spyre-cdev

# 4. Dashboard UI (secret + configmap + deployment + service + route)
oc apply -f deploy/openshift/04-torchci-secret.yaml -n spyre-cdev
oc apply -f deploy/openshift/05-torchci-configmap.yaml -n spyre-cdev
oc apply -f deploy/openshift/06-torchci-deployment.yaml -n spyre-cdev
oc apply -f deploy/openshift/07-torchci-service.yaml -n spyre-cdev
oc apply -f deploy/openshift/08-torchci-route.yaml -n spyre-cdev
```

### ClickHouse Auth

ClickHouse is protected with a password. The password is configured in `02a-clickhouse-config.yaml` (as plaintext in the XML) and must also be set in:
- `torchci-secrets` → `CLICKHOUSE_HUD_USER_PASSWORD` (for the dashboard to read)
- `CLICKHOUSE_PASSWORD` env var (for the watcher to push)

### Verify ClickHouse is Accessible

```bash
# Ping (no auth needed)
curl -sk https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com/ping

# Query (needs auth)
curl -sk -u "default:<password>" \
  "https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com/?query=SELECT+count()+FROM+benchmark.oss_ci_benchmark_v3"
```

---

## Watcher Guide

The watcher (`scripts/watcher.sh`) is a local background process that:
1. Polls the benchmark cluster every 60s for completed pods (label: `purpose=benchmark`)
2. Extracts benchmark results from pod logs
3. Pushes metrics to ClickHouse via the public HTTPS route
4. Annotates processed pods to prevent duplicate pushes

### Setup

```bash
# Required environment variables
export CLICKHOUSE_URL="https://clickhouse-ingest-spyre-cdev.apps.fmaas-devstage-backend.fmaas.res.ibm.com"
export CLICKHOUSE_USER="default"
export CLICKHOUSE_PASSWORD="<password>"

# Login to the benchmark cluster
oc login --server=https://api.torch-cicd.spyre.res.ibm.com:6443
```

### Run

```bash
# Foreground (see output live)
bash scripts/watcher.sh

# Background (logs to file)
nohup bash scripts/watcher.sh >> watcher.log 2>&1 &

# Check watcher log
tail -f watcher.log
```

### Configuration

| Env Var | Default | Description |
|---------|---------|-------------|
| `CLICKHOUSE_URL` | (required) | ClickHouse HTTPS endpoint |
| `CLICKHOUSE_USER` | (required) | ClickHouse username |
| `CLICKHOUSE_PASSWORD` | (required) | ClickHouse password |
| `NAMESPACE` | `torch-spyre-cicd` | Namespace to watch for pods |
| `BENCHMARK_NAME` | `vllm_benchmark` | Benchmark name in ClickHouse |
| `POLL_INTERVAL` | `60` | Seconds between polls |
| `CONTAINER_NAME` | `app` | Container name to read logs from |

### How It Works

- Finds pods with label `purpose=benchmark` and `status.phase=Succeeded`
- Skips pods already annotated with `benchmark-watcher/pushed=true`
- Extracts JSON from log markers (`========== BENCHMARK RESULTS (JSON) ==========`)
- Converts each metric to a ClickHouse row (one row per metric: latency, p50, p99, tokens/s, req/s)
- Pushes via authenticated HTTPS POST
- Annotates pod on success

### Manual Push (without watcher)

```bash
oc logs yc-vllm-spyre-benchmark-v2 -n torch-spyre-cicd | \
  python3 scripts/push_to_clickhouse.py --from-logs \
    --clickhouse-url "$CLICKHOUSE_URL" \
    --benchmark-name vllm_benchmark
```

### Clean Up Test Data

```bash
python3 scripts/push_to_clickhouse.py --delete \
  --workflow-id <ID> \
  --clickhouse-url "$CLICKHOUSE_URL" \
  --benchmark-name vllm_benchmark
```

---

## build.yaml Reference

### User-Configurable Environment Variables

| Variable | What to set | Default |
|----------|------------|---------|
| `BENCHMARK_REPO` | Your fork URL | `https://github.com/YashasviChaurasia/spyre-benchmark-suite.git` |
| `BENCHMARK_BRANCH` | Your branch | `main` |
| `SPYRE_INFERENCE_REPO` | vLLM+Spyre repo | `https://github.com/torch-spyre/spyre-inference.git` |
| `SPYRE_INFERENCE_BRANCH` | vLLM branch | `main` |

### What the Pod Does

```
Pod starts on Spyre node
 ├─ 1. Installs uv, creates Python venv
 ├─ 2. Clones spyre-inference → installs vLLM v0.19.0 + Spyre plugin
 ├─ 3. Clones YOUR benchmark suite fork
 ├─ 4. Generates JSON test configs from benchmark_config.yaml
 ├─ 5. Runs vllm bench latency for each test case
 ├─ 6. Runs vllm bench throughput for each test case
 ├─ 7. Aggregates results → prints summary JSON to stdout
 └─ 8. Pod exits with status Completed
```

### Re-running Benchmarks

```bash
oc delete pod yc-vllm-spyre-benchmark-v2 -n torch-spyre-cicd
oc apply -f build.yaml -n torch-spyre-cicd
```

---

## Config Reference

### `config/benchmark_config.yaml`

| Section | Fields | Description |
|---------|--------|-------------|
| `engine` | `dtype`, `max_model_len`, `max_num_seqs`, `load_format` | vLLM engine parameters |
| `models[]` | `name`, `tensor_parallel_size` | Models to benchmark |
| `workloads.latency[]` | `input_len`, `output_len`, `batch_size`, `num_iters_warmup`, `num_iters` | Latency tests |
| `workloads.throughput[]` | `input_len`, `output_len`, `num_prompts` | Throughput tests |

### Supported Models (TP=1)

- `ibm-granite/granite-3.3-8b-instruct` (max_model_len=3072, max_num_seqs=16)
- `ibm-granite/granite-4-8b-dense`
- `meta-llama/Llama-3.1-8B-Instruct` (gated — needs HF_TOKEN)

---

## Clusters

| Cluster | Server | Namespace | Purpose |
|---------|--------|-----------|---------|
| Benchmark | `api.torch-cicd.spyre.res.ibm.com:6443` | `torch-spyre-cicd` | Run benchmarks on Spyre PF cards |
| Dashboard | `api.fmaas-devstage-backend.fmaas.res.ibm.com:6443` | `spyre-cdev` | ClickHouse + dashboard UI |

**Note:** These clusters are network-isolated. The watcher bridges them by running on a machine that can reach both.

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Pod stuck in Pending | `oc describe pod ...` — likely no Spyre PF card available |
| Pod exits immediately | `oc logs ...` — check for git clone / uv sync errors |
| Tests fail but pod completes | Look for `FAIL:` in logs — reduce `max_model_len` or `batch_size` |
| Watcher not pushing | Check `oc whoami` (logged in?), check `CLICKHOUSE_URL` is set |
| ClickHouse auth error | Verify password matches `02a-clickhouse-config.yaml` |
| Dashboard not showing data | Check `benchmark.name` is `vllm_benchmark` and data exists in ClickHouse |
| Duplicate pushes | Pod should have annotation `benchmark-watcher/pushed=true` |

---

## Local Testing (no Spyre needed)

```bash
pip install pyyaml
python3 scripts/generate_test_configs.py config/benchmark_config.yaml results/
cat results/latency-tests.json
```
