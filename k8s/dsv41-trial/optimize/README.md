# Bounded DeepSeek performance search

This runner measures the installed DeepSeek V4.1 engine, not Qwen's calibrator.
It temporarily releases the R9700 from `strata-dsv41-trial`, creates an isolated
Job with the same image, NVMe, GPU guard and resource limits, and restores the
original Deployment in `finally`. Serving is unavailable during the search.

The model, IQ2_XXS weights, 262144 context, native vision and current five-expert
Faster mode stay fixed. No expert-skipping, smaller quantization, pruned model,
weight conversion or unrelated workload changes are part of this search.

The starting expert placement is frozen after the serving engine saves its
counts on shutdown. Every candidate gets the same frozen profile and
`--profile-readonly`; the production profile and launch config are not edited
by the benchmark. Cache adaptation inside each engine remains enabled.
The private benchmark copy clears standing `user_args` overrides before
applying a trial: otherwise the saved `ssd-cache=44` wins over an edited
`args` value. Every new run verifies the effective native process argv before
timing anything. Invalid requested/effective settings are excluded and retained
as failed measurement attempts; a resumed run reuses the frozen placement and
only previously verified observations.

Each launch warms all three cases with 128 output tokens, then measures 256:
Python coding, technical Italian, and an incident document of several thousand
tokens. Fixed request tags avoid full prompt-prefix reuse between warm-up and
measurement. Raw prompt reuse, prefill/decode times, output lengths, hashes,
CPU quota/throttling, memory, I/O and hardware telemetry are retained.

Screening explores at most 18 configurations, with a 68-minute cutoff to leave
room for confirmation. The primary metric is the geometric mean of decode
tokens/second across cases. A candidate needs more than 5% screening gain,
with no case losing more than 10%. Screening does not change production.
The selected candidate and original baseline then get separate A/B/B/A engine
starts, two rounds of 512 output tokens per case. Final acceptance requires
more than 5% aggregate gain and no case regressing more than 5%.

If a model-side candidate fails a quality gate, a separately recorded follow-up
can isolate the CPU quota alone. Its `resume-state.json` carries
`confirmation_candidate`, `confirmation_reference` and
`confirmation_reference_source`: the immediately preceding 512-token,
two-round baseline serves as A, followed by fresh B/B/A starts with the same
frozen profile. The candidate passes the same gates; the rejected search result
is retained, not relabeled as a success. This follow-up has one candidate and
three new engine starts, with no further parameter search.

Before these control runs, the engine must pass known-answer text/arithmetic
checks and generate a pure Python interval-merging function that passes six
independent test cases. A previously observed arithmetic error in the deployed
reference is retained rather than aborting speed measurement; proposed
replacements must pass that arithmetic check as well. Generated code runs with restricted builtins, an AST
gate, no imports or file/network builtins, and CPU/address-space limits. These
checks cover functional regressions; they are not a general capability or
full-context quality evaluation.

Candidate families, changing one hypothesis at a time:

- DSpark on with draft blocks 1, 2, 3 and 5; confidence 0.50/0.80 when promising.
- CPU quota 32, 16 explicit workers, P-core placement, and SMT placement.
- Expert RAM store 48 or 40 GB against 44 GB, and LFU/LFU-LRU policies.
- Four or sixteen NVMe reader threads, deeper lookahead, and the native
  GPU-accessible RAM-store path.

A memory watchdog stops a candidate near its cgroup ceiling or when host
available RAM stays below 1.3 GiB. The Job has a deadline and does not retry
failed containers. Individual failure reports remain in the ledger.

Prepare a private experiment directory containing `deployment-before.json`,
`service-before.json` and `protocol.json`, obtained from the current cluster.
Run from the packaging repository, using `/usr/bin/kubectl`:

```bash
DSV41_SSH_HOST=192.168.10.15 python3 -u \
  k8s/dsv41-trial/optimize/run_cluster.py --root /absolute/private/experiment
```

SSH mode uses the node's existing Rancher kubeconfig for short administrative
calls and its CRI endpoint to launch each experiment inside the allocated Job.
The measurement runs detached, writes results on NVMe, and is polled over LAN
SSH, avoiding a long-lived Rancher exec stream. No credentials are copied to a
new host. Native run configs retain their key on the existing private NVMe;
the returned archive excludes them and the disposable `ds4-slots` KV checkpoints.
A file named `STOP` in the local experiment
directory stops optional search between trials and proceeds to cleanup.

The script restores the original configuration. Promoting a confirmed result
is a separate step: encode its flags/resource limits in the Deployment, verify
public text/tool/vision behavior, and retain rollback to the saved baseline.
This bounded coordinate search does not establish a global optimum.
