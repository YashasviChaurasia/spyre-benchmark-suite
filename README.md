# Spyre Benchmark Suite

Config-driven vLLM benchmarking on IBM Spyre accelerators via OpenShift.

Fork this repo, edit `config/benchmark_config.yaml`, deploy with `oc apply -f build.yaml`.

---

## Quick Start

```bash
# 1. Fork this repo, then clone your fork
git clone https://github.com/<your-username>/spyre-benchmark-suite.git
cd spyre-benchmark-suite

# 2. Edit benchmark config (models, workloads, engine settings)
vim config/benchmark_config.yaml

# 3. Push changes
git add -A && git commit -m "customize benchmarks" && git push

# 4. Update build.yaml — set BENCHMARK_REPO to your fork URL
vim build.yaml

# 5. Deploy
oc login --token=<token> --server=https://api.torch-cicd.spyre.res.ibm.com:6443
oc apply -f build.yaml

# 6. Watch & retrieve results
oc logs -f yc-vllm-spyre-benchmark
oc logs yc-vllm-spyre-benchmark > results.log
```

---

## Repository Structure

```
spyre-benchmark-suite/
├── build.yaml                    # OpenShift Pod spec (edit 4 env vars)
├── config/
│   └── benchmark_config.yaml     # WHAT to benchmark — edit this
├── scripts/
│   ├── generate_test_configs.py  # YAML config → vLLM JSON test format
│   ├── run_benchmarks.sh         # Orchestrator: runs vllm bench per test
│   └── collect_results.py        # Aggregates results into summary
└── results/
    └── .gitkeep                  # Populated at runtime inside the pod
```

---

## What to Edit

### 1. `config/benchmark_config.yaml` (in your fork)

This is the **only file you need to edit** for most use cases.

```yaml
engine:
  enforce_eager: true        # true = skip torch.compile (recommended initially)
  dtype: float16             # model precision
  max_model_len: 2048        # max sequence length for 1 Spyre PF card
  max_num_seqs: 4            # max concurrent sequences
  load_format: dummy         # "dummy" = random weights (no download)
                             # "auto"  = real weights (needs HF access)

models:
  - name: meta-llama/Meta-Llama-3-8B
    tensor_parallel_size: 1  # must match ibm.com/spyre_pf in build.yaml

workloads:
  latency:
    - input_len: 128
      output_len: 128
      batch_size: 1
      num_iters_warmup: 3
      num_iters: 10

  throughput:
    - input_len: 128
      output_len: 128
      num_prompts: 50
```

### 2. `build.yaml` (4 env vars)

| Variable | What to set | Default |
|----------|------------|---------|
| `BENCHMARK_REPO` | Your fork URL | `https://github.com/YashasviChaurasia/spyre-benchmark-suite.git` |
| `BENCHMARK_BRANCH` | Your branch | `main` |
| `SPYRE_INFERENCE_REPO` | vLLM+Spyre repo (change only for custom builds) | `https://github.com/torch-spyre/spyre-inference.git` |
| `SPYRE_INFERENCE_BRANCH` | vLLM branch | `main` |

Everything else in `build.yaml` (Spyre hardware config, volumes, scheduler) should not be changed for standard benchmarks.

---

## What Happens When You Deploy

```
Pod starts on Spyre node
 ├─ 1. Installs uv, creates Python venv
 ├─ 2. Clones spyre-inference → installs vLLM v0.19.0 + Spyre plugin
 ├─ 3. Clones YOUR benchmark suite fork
 ├─ 4. Generates JSON test configs from your benchmark_config.yaml
 ├─ 5. Runs vllm bench latency for each latency test case
 ├─ 6. Runs vllm bench throughput for each throughput test case
 ├─ 7. Aggregates results → prints summary table + JSON to stdout
 └─ 8. Pod exits
```

Individual benchmark failures do **not** abort the run — the suite continues and reports pass/fail counts.

---

## What to Expect: Output

### In pod logs (`oc logs yc-vllm-spyre-benchmark`)

```
==========================================
  Spyre vLLM Benchmark Suite
  Timestamp: 20260423T120000Z
==========================================

=== Generating test configurations ===
Generated 3 latency tests -> results/latency-tests.json
Generated 3 throughput tests -> results/throughput-tests.json

============================================
  Running latency benchmarks
============================================
--- Test: latency_meta_llama_meta_llama_3_8b_in128_out128_bs1 ---
Command: vllm bench latency --output-json ...
PASS: latency_meta_llama_meta_llama_3_8b_in128_out128_bs1

latency summary: 3 passed, 0 failed

============================================
  Running throughput benchmarks
============================================
...
throughput summary: 3 passed, 0 failed

========================================================================
  Benchmark Summary — 20260423T120000Z — IBM Spyre PF
========================================================================

  LATENCY BENCHMARKS
  --------------------------------------------------------------------
  latency_meta_llama_meta_llama_3_8b_in128_out128_bs1
    Avg: 0.1234s | P50: 0.1200s | P99: 0.1500s

  THROUGHPUT BENCHMARKS
  --------------------------------------------------------------------
  throughput_meta_llama_meta_llama_3_8b_in128_out128_n50
    140.0 tok/s | 1.09 req/s | 45.7s elapsed
========================================================================

========== BENCHMARK RESULTS (JSON) ==========
{ "timestamp": "...", "device": "IBM_Spyre_PF", "total_tests": 6, "results": [...] }
========== END BENCHMARK RESULTS ==========

===== BENCHMARK COMPLETE =====
```

### Extract summary JSON from logs

```bash
oc logs yc-vllm-spyre-benchmark | \
  sed -n '/========== BENCHMARK RESULTS (JSON) ==========/,/========== END BENCHMARK RESULTS ==========/p' | \
  sed '1d;$d' > summary.json
```

---

## Common Customizations

### Test a custom vLLM build

```yaml
# build.yaml
- name: SPYRE_INFERENCE_REPO
  value: https://github.com/<you>/spyre-inference.git
- name: SPYRE_INFERENCE_BRANCH
  value: my-optimization
```

### Use real model weights (instead of dummy)

```yaml
# config/benchmark_config.yaml
engine:
  load_format: auto   # downloads from HuggingFace
```

For gated models (Llama-3), add `HF_TOKEN` to `build.yaml`:
```yaml
- name: HF_TOKEN
  valueFrom:
    secretKeyRef:
      name: hf-token
      key: token
```

### Scale to multiple Spyre PF cards

```yaml
# build.yaml — increase hardware
resources:
  limits:
    ibm.com/spyre_pf: '4'
  requests:
    ibm.com/spyre_pf: '4'

# config/benchmark_config.yaml — match tensor_parallel_size
models:
  - name: meta-llama/Meta-Llama-3-8B
    tensor_parallel_size: 4
```

### Add more workloads

```yaml
# config/benchmark_config.yaml
workloads:
  latency:
    - { input_len: 128, output_len: 128, batch_size: 1, num_iters_warmup: 3, num_iters: 10 }
    - { input_len: 512, output_len: 256, batch_size: 1, num_iters_warmup: 5, num_iters: 20 }
    - { input_len: 1024, output_len: 512, batch_size: 4, num_iters_warmup: 5, num_iters: 20 }
  throughput:
    - { input_len: 128, output_len: 128, num_prompts: 100 }
    - { input_len: 512, output_len: 256, num_prompts: 200 }
```

### Debug interactively

Add `/usr/bin/pause` at the end of `build.yaml` command to keep the pod alive:
```bash
# ... after run_benchmarks.sh ...
echo "===== PAUSING FOR DEBUG ====="
/usr/bin/pause
```
Then: `oc rsh yc-vllm-spyre-benchmark`

---

## Re-running Benchmarks

```bash
oc delete pod yc-vllm-spyre-benchmark
oc apply -f build.yaml
```

---

## Troubleshooting

| Problem | Check |
|---------|-------|
| Pod stuck in Pending | `oc describe pod yc-vllm-spyre-benchmark` — likely no Spyre PF card available |
| Pod exits immediately | `oc logs yc-vllm-spyre-benchmark` — check for git clone / uv sync / import errors |
| Tests fail but pod completes | Look for `FAIL: <test_name>` in logs — reduce `max_model_len` or `batch_size` |
| No results in summary | Verify result files match pattern `latency_*.json` / `throughput_*.json` |
| Need to inspect pod filesystem | Add `/usr/bin/pause` to command, then `oc rsh` into pod |

---

## Local Testing (no Spyre needed)

You can test the config generator locally to verify your YAML produces valid test configs:

```bash
pip install pyyaml
python3 scripts/generate_test_configs.py config/benchmark_config.yaml results/
cat results/latency-tests.json
cat results/throughput-tests.json
```
