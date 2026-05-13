# Issue: TP>1 Fails with vLLM's multiproc_executor on Spyre

## Summary

`tensor_parallel_size > 1` fails when using `vllm bench` or `vllm serve` on Spyre because vLLM's `multiproc_executor` spawns child workers that can't open their assigned Spyre VFIO devices. The same setup works when processes are launched via `torchrun`.

## Environment

- Image: `us.icr.io/wxpe-cicd-internal/amd64/vllm-spyre-dev:dev-next`
- vLLM: 0.19.0 (built from `torch-spyre/spyre-inference` main)
- torch-spyre: `bdd54af5549532e4ff7a21c9787b1397aeb85c75`
- Pod: 2 Spyre PF cards (`ibm.com/spyre_pf: '2'`)
- Env vars set: `SPYRE_DEVICES=0,1`, `AIU_WORLD_SIZE=2`, `DEEPTOOLS_PATH`, `LD_LIBRARY_PATH`

## Reproduction

```bash
# This fails:
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 LOCAL_RANK=0 \
  vllm bench latency --model ibm-ai-platform/micro-g3.3-8b-instruct-1b \
    --tensor-parallel-size 2 --input-len 128 --output-len 128 --batch-size 1

# This also fails:
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 LOCAL_RANK=0 \
  vllm serve ibm-ai-platform/micro-g3.3-8b-instruct-1b --tensor-parallel-size 2
```

## Error

```
Worker_TP0 (or TP1):
RuntimeError: {
  "Device": "/dev/vfio/75",
  "code": "0x332b",
  "description": "The specified PCIe address was not found in the /dev/vfio subsystem.",
  "errno": "Device or resource busy",
  "message": "Failed to open the IBM Spyre VFIO device.",
  "name": "RAS::VFIO::DeviceOpenFail",
  "step": "Validate user environment configuration"
}
```

## Call Stack

```
multiproc_executor.py:944 → worker_busy_loop → func(*args)
  cpu_worker.py:153 → compile_or_warm_up_model → self.model_runner.warming_up_model()
    cpu_model_runner.py:85 → warming_up_model → self._dummy_run()
      gpu_model_runner.py:5474 → outputs = self.model(...)
        decorators.py:611 → self.aot_compile(*args, **kwargs)
          ... torch._dynamo → symbolic_convert.py:4581
            streams.py:217 → torch.accelerator.current_stream()
              accelerator/__init__.py:223 → _get_device_index()
                accelerator/__init__.py:136 → torch._C._accelerator_getDeviceIndex()
                  torch_spyre/__init__.py:67 → self._C.start_runtime()  ← FAILS HERE
```

## Root Cause Analysis

vLLM's `multiproc_executor` (in `vllm/v1/executor/multiproc_executor.py`) spawns TP workers using Python's `multiprocessing.Process`:

```python
worker = multiprocessing.Process(target=worker_fn, ...)
worker.start()
```

The child process inherits env vars (`SPYRE_DEVICES=0,1`, `AIU_WORLD_SIZE=2`) but when `torch_spyre._lazy_init()` calls `start_runtime()`, it fails to correctly map the worker's `LOCAL_RANK` to a physical device within the `SPYRE_DEVICES` set.

**Why torchrun works:** torchrun launches fully independent processes (not `multiprocessing.Process` children). Each gets its own `LOCAL_RANK` set before any Python code runs, and `torch_spyre` sees it at init time before `start_runtime()`.

**Why multiproc_executor fails:** The executor sets `LOCAL_RANK` via vLLM's internal distributed state, but by the time `torch_spyre._lazy_init()` fires (triggered by `torch.accelerator.current_device_index()`), the Spyre runtime doesn't see the correct device mapping.

## What Works (for comparison)

Running TP=1 processes in parallel with explicit device isolation works perfectly:

```bash
# Process 1 — device 0
SPYRE_DEVICES=0 AIU_WORLD_SIZE=1 LOCAL_RANK=0 vllm bench latency --model A --tp 1 &

# Process 2 — device 1
SPYRE_DEVICES=1 AIU_WORLD_SIZE=1 LOCAL_RANK=0 vllm bench latency --model B --tp 1 &

wait  # Both pass
```

This proves the devices are accessible and the VFIO binding works when each process has a single explicit device.

## Suggested Fix

In `torch_spyre/__init__.py` (`_lazy_init` / `start_runtime`):

1. When called inside a child process spawned by `multiprocessing.Process`:
   - Read `LOCAL_RANK` from the environment (set by vLLM's multiproc_executor)
   - Map it to the correct index within `SPYRE_DEVICES`
   - Open only that specific VFIO device

2. Or: ensure `torch_spyre.spyre.set_device(idx)` is called by vLLM's worker before any accelerator call triggers `_lazy_init()`

The fix should make this work:
```bash
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 \
  vllm bench latency --model X --tensor-parallel-size 2
```

## Workaround (Current)

For benchmarking, we use N models × TP=1 in parallel instead of 1 model × TP=N:

```bash
# Instead of TP=2:
SPYRE_DEVICES=0 AIU_WORLD_SIZE=1 LOCAL_RANK=0 vllm bench latency --model A --tp 1 &
SPYRE_DEVICES=1 AIU_WORLD_SIZE=1 LOCAL_RANK=0 vllm bench latency --model B --tp 1 &
```

For serving with TP>1, the workaround would be launching via torchrun:
```bash
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 \
  torchrun --nproc_per_node=2 -m vllm.entrypoints.openai.api_server \
    --model X --tensor-parallel-size 2
```

## Fix v2: `fix/tp-multiproc-device-init` (5e1fe9c)

### Root Cause of v1 Failure

v1 (57ed94e) had a race condition: `_lazy_init()` read `LOCAL_RANK=0` from the parent's inherited environment before `set_device(1)` was called by vLLM's executor for Worker_TP1.

### v2 Changes (5e1fe9c)

- **Removed** the `LOCAL_RANK` fallback from `_lazy_init()` — it read the parent's stale value
- **Added** `os.environ["LOCAL_RANK"] = str(idx)` inside `set_device()` — so even if `_lazy_init()` fires from an unexpected dynamo code path, the C++ runtime reads the correct device

### Critical: Do NOT set `LOCAL_RANK=0` in parent command

```bash
# WRONG (causes both workers to inherit LOCAL_RANK=0):
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 LOCAL_RANK=0 vllm bench ... --tp 2

# CORRECT (let set_device() manage LOCAL_RANK per worker):
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 vllm bench ... --tp 2
```

### Remaining Concern

If vLLM's `multiproc_executor` triggers `torch._dynamo` compilation (which calls `torch.accelerator.current_device_index()` → `_lazy_init()`) **BEFORE** it calls `Worker.set_device(rank)`, then Worker_TP1 still has no rank set. The fix works only if vLLM calls `set_device(rank)` early enough.

If that's still failing, the fix needs to move to `spyre-inference` — adding `os.environ['LOCAL_RANK'] = str(rank)` at the very top of the worker function before any torch import path triggers device access.

---

## Fix v1 Attempt: `fix/tp-multiproc-device-init` (57ed94e)

Branch: `YashasviChaurasia/torch-spyre@fix/tp-multiproc-device-init`

### Changes Made

**`torch_spyre/device/interface.py`**
- `Worker.set_device(device)` → calls `torch.spyre.set_device(device)` instead of raising `NotImplementedError`
- `Worker.current_device()` → returns actual device index instead of hardcoded 0

**`torch_spyre/__init__.py`**
- `_lazy_init()` → reads `LOCAL_RANK` env var as fallback when no explicit `set_device()` was called
- `_mark_after_fork()` → only blocks child processes if the parent had already initialized the runtime

### Test Result (2026-05-13)

**Partial success — Worker_TP0 works, Worker_TP1 still fails.**

Environment:
- Image: `us.icr.io/wxpe-cicd-internal/amd64/vllm-spyre-dev:dev-next`
- vLLM: 0.19.1 (built from `torch-spyre/spyre-inference` main)
- Base torch-spyre: `bdd54af` with fix files patched in-place
- Pod: 2 Spyre PF cards, node `p1-worker-72`
- Env: `SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 LOCAL_RANK=0`

Command:
```bash
SPYRE_DEVICES=0,1 AIU_WORLD_SIZE=2 LOCAL_RANK=0 \
  vllm bench latency --model ibm-ai-platform/micro-g3.3-8b-instruct-1b \
    --tensor-parallel-size 2 --input-len 128 --output-len 128 --batch-size 1
```

### Observed Behavior

| Worker | PID | Result | VFIO Device |
|--------|-----|--------|-------------|
| Worker_TP0 | 4664 | **SUCCESS** — compiled model, saved AOT function | Opened OK |
| Worker_TP1 | 4665 | **FAILED** — VFIO device busy | `/dev/vfio/87` |

Worker_TP0 completed compilation:
```
(Worker_TP0 pid=4664) INFO 05-13 09:22:50 [decorators.py:655] saved AOT compiled function to
  /dev/shm/.cache/vllm/torch_compile_cache/.../rank_0_0/model
```

Worker_TP1 failed at the same point as before:
```
(Worker_TP1 pid=4665) ERROR 05-13 09:22:01 [multiproc_executor.py:949]
  File ".../torch_spyre/__init__.py", line 75, in _lazy_init
    self._C.start_runtime()
RuntimeError: {
  "Device": "/dev/vfio/87",
  "code": "0x332b",
  "errno": "Device or resource busy",
  "message": "Failed to open the IBM Spyre VFIO device.",
  "name": "RAS::VFIO::DeviceOpenFail"
}
```

### Full Call Stack (Worker_TP1 failure)

```
multiproc_executor.py:949 → worker_busy_loop
  torch._dynamo/convert_frame.py:1250 → _fullgraph_capture_frame
    convert_frame.py:1341 → compile_frame
      bytecode_transformation.py:1600 → transform_code_object
        convert_frame.py:1313 → transform → trace_frame
          convert_frame.py:328 → _fn
            convert_frame.py:795 → trace_frame
              symbolic_convert.py:4581 → InstructionTranslator.__init__
                streams.py:217 → SymbolicStreamState() → torch.accelerator.current_stream()
                  accelerator/__init__.py:223 → current_stream → _get_device_index()
                    accelerator/_utils.py:25 → torch.accelerator.current_device_index()
                      accelerator/__init__.py:136 → torch._C._accelerator_getDeviceIndex()
                        torch_spyre/__init__.py:75 → _lazy_init() → self._C.start_runtime()
                          ← FAILS: VFIO device busy
```

### Analysis of Fix Gap

The fix ensures `set_device(rank)` is callable and `_lazy_init()` reads `LOCAL_RANK` as fallback. However:

1. **Worker_TP0 succeeds** — suggesting `set_device(0)` works correctly for rank 0
2. **Worker_TP1 fails on VFIO** — suggesting one of:
   - `set_device(1)` IS called but `_lazy_init()` doesn't map rank 1 → physical device 1 within `SPYRE_DEVICES=0,1`
   - OR `set_device(1)` is NOT called before `_lazy_init()` fires (timing issue — compilation triggers device access before the executor calls `set_device`)
   - OR `LOCAL_RANK=0` in the parent env overrides the `set_device(1)` call (child inherits `LOCAL_RANK=0`)

3. **Likely culprit: `LOCAL_RANK=0` env override.** The parent process sets `LOCAL_RANK=0` in the environment. Child workers spawned by `multiprocessing.Process` inherit this. If `_lazy_init()` checks `os.environ['LOCAL_RANK']` before checking the value set by `set_device()`, Worker_TP1 would also try to open device 0 (already held by Worker_TP0 → "busy").

### Suggested Next Steps

1. **Don't use `LOCAL_RANK` env var as device selector in multiproc mode.** In multiproc_executor, `set_device(rank)` is the authoritative source. The env var `LOCAL_RANK` is only meaningful for torchrun-launched processes.

2. **Ensure `set_device()` state takes priority over env var in `_lazy_init()`:**
   ```python
   def _lazy_init(self):
       # Priority: explicit set_device() > LOCAL_RANK env > default 0
       device_idx = self._explicit_device  # set by set_device()
       if device_idx is None:
           device_idx = int(os.environ.get('LOCAL_RANK', 0))
       # Map device_idx to physical device within SPYRE_DEVICES
       ...
   ```

3. **Verify `set_device()` is called BEFORE any code path triggers `_lazy_init()`.** The traceback shows `_lazy_init()` fires during `torch._dynamo` compilation. If vLLM's executor triggers compilation before calling `set_device()`, the fix won't help.

4. **Debug test:** Add logging to `_lazy_init()` and `set_device()` to confirm call ordering:
   ```python
   def set_device(self, device):
       print(f"[torch_spyre] set_device({device}) called, pid={os.getpid()}", flush=True)
       torch.spyre.set_device(device)

   def _lazy_init(self):
       rank = ...  # whatever logic
       print(f"[torch_spyre] _lazy_init() rank={rank}, pid={os.getpid()}, LOCAL_RANK={os.environ.get('LOCAL_RANK')}", flush=True)
       self._C.start_runtime()
   ```

## Test Verification

Once fully fixed, this should pass:
```bash
# On a pod with ibm.com/spyre_pf: '2'
export SPYRE_DEVICES=0,1
export AIU_WORLD_SIZE=2
export DEEPTOOLS_PATH=/opt/ibm/spyre/deeptools/share

vllm bench latency \
  --model ibm-ai-platform/micro-g3.3-8b-instruct-1b \
  --tensor-parallel-size 2 \
  --input-len 128 --output-len 128 --batch-size 1 \
  --num-iters 5
```

## Test Branch

Benchmark suite branch used for testing: `test/tp2-multiproc-fix`
- Repo: `YashasviChaurasia/spyre-benchmark-suite`
- What it does: builds vLLM 0.19.1, patches torch-spyre `__init__.py` + `device/interface.py` from fix branch, runs TP=2 latency test
