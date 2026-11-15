# DeepSeek V4.1 Flash image for the R9700

Published and registry-verified on 15 November 2026:

- Image: `moophlo/strata-rocm-r9700:dsv41`
- Build tag: `dsv41-build-7c64a76d4005`
- OCI digest: `sha256:50eec886099238f349625b718192fbeeb38abc3e77a36311e24b54d38f0201cf`
- [Successful GitHub Actions run](https://github.com/moophlo/strata-rocm-r9700/actions/runs/43695715705)

The alias, source tag and build tag resolve to the same OCI index, containing
the `linux/amd64` image and its attestations. The offline engine and Terminal
smoke checks passed. The subsequent [Kubernetes trial](k8s/dsv41-trial/README.md)
also passed GPU loading, authenticated API, text, tool calling and native Vision
checks on the R9700.

The existing **Build and publish Strata ROCm (R9700)** workflow accepts a
`variant` input. Choose `dsv41` to publish `moophlo/strata-rocm-r9700:dsv41`.
The `qwen` choice and push-triggered builds retain the original source and tags.
The DeepSeek build also publishes `dsv41-src-<source SHA>` and
`dsv41-build-<packaging SHA>`; it does not update `latest`.

```bash
gh workflow run build-push.yml --repo moophlo/strata-rocm-r9700 -f variant=dsv41
```

This variant uses Strata 0.2.13, source commit
`d2f096475817d4666c364c820b0ca830f751cfc5`, and its official prebuilt
`strata-dsv41` 0.1.59 Linux HIP rx9070 package (gfx1200/gfx1201). The workflow
uses upstream's Docker build context and Terminal packager. It verifies the
signed release feed, the pinned engine archive SHA-256, the package signature,
every packaged file and runtime library dependencies. Before publishing, it
starts the Terminal and DeepSeek executable without GPU access or network.

The image defaults to `FAMILY=deepseek41`, `MODEL=ist-das-2.00bpw`,
`CONTEXT=8192`, `PRESET=dsv41`. It contains the engine, API server and preparation
tools; model weights are downloaded separately onto the mounted data volume.
The default model needs approximately 257 GB before additional preparation
space. These build checks do not establish inference quality or GPU performance.

The new entrypoint is `/opt/strata/entrypoint.sh`; the bundled Python is
`/opt/strata/terminal/python/bin/python3`. The old `.venv`, `CONFIG` JSON and
`docker-entrypoint.sh` conventions are not compatible with this image.
For the Kubernetes trial, adapt the GPU guard to the new entrypoint, request
`amd.com/gpu-ai-pro-r9700: 1`, and use a separate data directory on NVMe.
The upstream entrypoint can migrate older model directories; mounting the
existing Qwen data as `/data` would therefore modify their layout.

The [Kubernetes trial and rollback procedure](k8s/dsv41-trial/README.md) describe
the deployed configuration. `llm.mufloland.it` now serves DeepSeek through the
existing authenticated endpoint.
