# DeepSeek V4.1 Flash IQ2_XXS trial on the R9700

Image: `moophlo/strata-rocm-r9700@sha256:50eec886099238f349625b718192fbeeb38abc3e77a36311e24b54d38f0201cf`.
Namespace: `vllm`. Deployment and direct Service: `strata-dsv41-trial`.

## Initial verification, 15 November 2026

`strata-dsv41-trial` is 1/1 Ready, zero restarts. `strata-orca-trial` and the
original `strata` Deployment are both at zero replicas. The historical Service
`strata-orca-trial` selects the DeepSeek pod, so the existing Ingress and LiteLLM
endpoint serve DeepSeek.

Verified through the direct Service and `https://llm.mufloland.it`:

- `/health`: model `deepseek-v4.1-flash`, loaded, maximum context 262144,
  images enabled and API key required.
- Anonymous `/v1/models`: HTTP 401; authenticated model discovery succeeds.
- Text inference: answered `Paris` to the short capital question.
- Tool calling through the public endpoint with the previous Orca model alias:
  emitted `add` with integer arguments `a=2`, `b=3`.
- Native Vision through the public endpoint: correctly read `DEEPSEEK 7412`.
- Container allocation and GPU guard: R9700 `0000:06:00.0`, Healthy,
  `gfx1201`, 29.86 GiB visible VRAM.

These are functional smoke checks, not a benchmark or a full-context quality
assessment. The engine's default **Faster** profile uses **5 of 6 experts per
token** on this single-R9700 setup. DSpark auto leaves the drafter unloaded
because most experts reside outside GPU memory. The initial RAM store is
38.3 GB; 2577 experts (22.55 GB) are resident in VRAM, with 5.86 GB of other
weights and 0.24 GB of caches. The current explicit RAM budget and tuning
result are documented below.

Private receipt, previous live manifests, startup evidence and API results:
`/home/muflo/.local/state/strata-tests/dsv41-20261115T090014Z/`.

All model files, pack, logs and settings live on the fast NVMe:
`/media/muflo/2TBfast1/strata-cache/dsv41`, mounted as `/data`. This is
`/dev/nvme3n1p1`, also used by the Qwen NVMe cache. The Qwen directories and
the HDD PVC are not mounted into this deployment.

The selected set is ISTA-DAS 2.00 bpw, IQ2_XXS. Its four shards, shared Engram
table, DSpark drafter, metadata and native vision weights total
257,789,086,989 bytes. The preparation Job uses the image's upstream downloader,
pinned revisions and SHA-256 checks, resumes partial downloads and builds the
official pack recipe. It needs no GPU. The pack maps the existing GGUF files;
it does not duplicate hundreds of GB of weights.

The deployment requests the product-specific R9700 resource and checks the
allocated PCI device `0000:06:00.0`, `gfx1201` and VRAM before starting. It uses
the existing `strata-api` Secret, a 256K context, native vision, automatic GPU
expert placement, a 44 GB expert RAM store, and 32 CPU / 54 GiB limits (updated 17 November). CPU
experts exceeding the RAM store are read from NVMe.

## Controlled optimization, 17 November 2026

The current CPU limit is **32**, with **24 automatically selected physical-core
workers** and Turbo Boost still disabled. A longer, fixed three-case benchmark
and separate A/B/B/A engine starts confirmed **+10.1% decode throughput**
(15.13 -> 16.65 tokens/s geometric mean). Per-case gains were 8.6% coding,
11.5% technical Italian and 10.2% long-document analysis. CFS throttled periods
fell from about 87% to 0% in the candidate samples. CPU request 4, memory 32Gi/54Gi,
cache 44 GB, model, 256K context, vision and five-expert mode are unchanged.

The 18-configuration screen tested DSpark blocks/confidence, CPU placement,
RAM budgets, cache policies, NVMe reader counts and lookahead. DSpark's faster
screening candidate failed the arithmetic acceptance check on both independent
starts and was rejected. **DSpark remains auto/unloaded**. Cache 48 GB brought
less than 1% gain, so the explicit 44 GB store was retained.

The new pod passed local/public health, authentication, text, tools and native
vision checks. It has zero restarts, the R9700 allocation is Healthy, and ComfyUI
remains 2/2 Ready with zero restarts. The local monitor forward is restored.
The complete measurements, failed/excluded attempts, limits and CPU-only
rollback are in [the optimization report](optimize/RESULTS-2026-11-17.md).

## CPU quota comparison, 15 November 2026

The engine selected 24 physical-core workers, but the original container CPU
limit was 16 vCPUs. During real inference all 120 observed CFS periods were
throttled. The limit is now 24 vCPUs; the CPU request remains 4. During this
CPU comparison memory was 32 GiB requested / 48 GiB limited, and model, image,
context and engine flags were unchanged. The later RAM change is below.

A short sequential API comparison used one fixed coding request, temperature
0, seed 1729 and 256 output tokens. One initial warm-up was excluded. The
pod's CPU quota was resized in place for the comparison, keeping the same
process, loaded weights and caches:

| CPU limit | Measured decode | Median | Runs |
| --- | --- | --- | --- |
| 16 vCPUs | 5.3, 5.7 tokens/s | 5.5 tokens/s | 2 |
| 24 vCPUs | 16.2, 16.8 tokens/s | 16.5 tokens/s | 2 |
| 16 vCPUs, return control with warm cache | 7.0 tokens/s | 7.0 tokens/s | 1 |

The gain was 3.0x against the first median and 2.36x against the warm return
control. One response hash matched across all three stages. This is a short
quota experiment on this prompt, not a general workload benchmark: routing and
cache adaptation can change throughput. CPU throttling was reduced but not
eliminated (about 81% of CFS periods at 24 vCPUs, versus 93% in the initial
16-vCPU samples). The final limit was restored to 24 and saved in the
Deployment template, which recreates the pod once to persist the setting.

Private request, per-run timings, cgroup counters and original manifests:
`/home/muflo/.local/state/strata-tests/dsv41-cpu24-20261115T144500Z/`.

## RAM budget and official DeepSeek tuning, 15 November 2026

The container limit is now **54 GiB = 57.98 GB**; its request remains 32 GiB.
`STRATA_SET=ssd-cache=44` explicitly budgets **44 decimal GB** for the expert
RAM store. The container limit includes every other allocation, so it must
not be used as the store size. Raising only the Kubernetes limit would leave
the engine's automatic store sizing based on the host's free RAM.

The store now has 4,283 slots instead of 3,666 at the previous 37.7 GB budget.
Startup preloaded 33.3 GB of hot experts, versus 28.5 GB previously. GPU
placement remains 2,577 experts / 22.55 GB on the R9700; the CPU quota and
256K context are unchanged.

Ran the image's official `tools/dsv41/tune.py` through `TUNE=thorough`, following
[upstream's DeepSeek guide](https://github.com/Niko1221/Strata/blob/main/docs/DEEPSEEK41.md).
It loads the model separately for each trial and retains improvements above
5%:

| Trial | Short tuner decode, tokens/s |
| --- | --- |
| Standard settings, DSpark auto | 13.0 |
| DSpark forced on, confidence 0.65 | 10.4 |
| Explicit 24 CPU workers | 4.5 |
| All 32 logical CPU workers | 13.2 |

No candidate passed its improvement threshold. The short draft-block trial
was skipped because DSpark was not retained. The engine remains at its
standard settings, DSpark auto and automatically selected 24 CPU workers.
The short fixed question and large variation between equivalent 24-worker
settings limit the precision of this ranking; this does not establish that
24 explicit workers are intrinsically slower than 24 automatic workers.

A separate API check reused the exact coding request from the CPU experiment
(temperature 0, seed 1729, 256 output tokens). Excluding one cold warm-up
(11.0 tokens/s), two samples gave **20.9 and 27.2 tokens/s**, median **24.05**,
versus the earlier 24-vCPU median of 16.5. This is a short sequential
comparison with progressively warmer caches, not a controlled attribution
of the entire gain to RAM. During these checks the container peaked at
49.42 GB, with no memory-limit hits or OOM kills; host available RAM was
about 10.6 GB after the coding requests.

The final restart was verified without repeating tuning: local and public
health/model discovery succeeded, anonymous discovery returned 401, text
inference answered `Paris`, and tool calling emitted `add(a=2, b=3)`. The
container is Ready with zero restarts and no OOM events; this final startup
and smoke check peaked at 50.18 GB. The local monitor forward on port 8080
was restored. ComfyUI remains 2/2 Ready with zero restarts.

`TUNE=skip` and the saved launch preset disable repeated measurements on
restart. To repeat during a maintenance window, temporarily use
`TUNE=thorough` and `RETUNE=1`; serving is unavailable during the trials.
Restore `TUNE=skip`, remove `RETUNE`, and set the saved launch preset's
`tune` field back to `skip` afterwards (this engine version retains it).
Do not run a second engine alongside the serving process on the same GPU.

Private baseline snapshots, tuner output, API samples and resource counters:
`/home/muflo/.local/state/strata-tests/dsv41-tune-20261115T150452Z/`.

Use `/usr/bin/kubectl` (the older `/usr/local/bin/kubectl` renders different
Kustomize hashes). Prepare the files while Orca is still running:

```bash
/usr/bin/kubectl apply -k k8s/dsv41-trial/prepare
/usr/bin/kubectl -n vllm logs -f job/strata-dsv41-prepare
```

Verify that the Job completes and `/data/preparation-receipt.json` is present
before handing over the GPU. Save the live Deployments and Service selector.
The manifest now specifies one replica to match the active deployment. For
pre-staging while Orca is running, temporarily set it to zero. Stop Orca and
wait for its process to terminate before starting DeepSeek. Only one
deployment can own the R9700.
The runtime guard refuses to start if the prepared receipt is absent.

The new Service is available for direct API checks before routing traffic.
After readiness and inference checks, apply `route.yaml`: the historical
`strata-orca-trial` Service selects DeepSeek, retaining the existing Ingress,
TLS and LiteLLM endpoint at `https://llm.mufloland.it/v1`. LiteLLM's existing
`coding` / Orca aliases then reach DeepSeek through that endpoint; the alias
names have not been renamed.

Rollback, if needed: `bash k8s/dsv41-trial/rollback.sh`. This releases the R9700,
restarts the saved Orca deployment, waits for readiness, then restores the
historical Service selector. The weights and profiles for both models remain.
