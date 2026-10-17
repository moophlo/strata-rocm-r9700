# Strata ROCm / Radeon AI PRO R9700 CI

Standalone **packaging** repository for Strata HIP on AMD Radeon AI PRO R9700
(`gfx1201`). A GitHub Actions workflow builds `linux/amd64` OCI images and
publishes them to Docker Hub, with reproducible source and build-revision tags.

**Repository intended for**: `moophlo/strata-rocm-r9700`.

## Design

- This repository contains **our** ROCm `Dockerfile` and GitHub Actions workflow,
  **not** a full source-code fork of Strata.
- Each workflow run checks out the pinned public source from
  `dambaev/Strata@060117c4bf49e50e86ae8d79361540f9c83d77a1`, which matches
  the ROCm Dockerfile work from upstream PR
  [#1486](https://github.com/Niko1221/Strata/pull/1486).
- BuildKit sends `./upstream` (complete Strata source) as context while using
  our root `Dockerfile`. `HIP_ARCHITECTURES=gfx1201`, `BUILD_VISION=0`.
- Strata's `setup.py` builds the HIP engine and fetches TheRock ROCm wheels.
  **No physical GPU is needed during the Docker build**, but the builder needs
  enough storage and a CPU instruction set compatible with the runtime host.
- The image does **not** contain model weights. Model GGUFs are downloaded once
  at runtime to persistent `/data`, or can be mounted from a local `GGUF_DIR`.
- The workflow only runs on **push to `main`** when its Dockerfile/workflow
  changes and on explicit **workflow_dispatch**. It never publishes from PRs.

## Prerequisites

1. GitHub account with access to `moophlo` and `gh` CLI authenticated.
2. Docker Hub namespace and an existing target repository `strata-rocm-r9700`.
3. Docker Hub access token with permission to push to that repository.
4. A dedicated GitHub Actions **self-hosted runner**, Linux x86-64, registered
   for this repository, with Docker/Buildx access, **at least ~60 GiB free disk**
   and preferably 16+ GiB RAM / 8+ CPU threads (capacity guidance, not measured
   minimums). Do **not** register the runner against untrusted repositories.
   The exact disk and RAM requirements will depend on the upstream compilation.

GitHub standard hosted `ubuntu-24.04` runners list just 14 GB SSD, therefore
should **not** be assumed to be sufficient. Larger managed runners require an
eligible Team/Enterprise organization; alternatively, use a dedicated builder
VM/host you own. Building on DaiapoLinux itself works but competes with
inference and gives the runner essentially host-level access via Docker.

## One-time repository bootstrap

Copy these files into an empty directory and run:

```bash
gh auth login
gh repo create moophlo/strata-rocm-r9700 --private

# Configure these BEFORE the first push so the workflow has credentials.
# Docker Hub account used to authenticate (may differ from the namespace):
gh variable set DOCKERHUB_USERNAME --repo moophlo/strata-rocm-r9700 --body YOUR_DOCKERHUB_LOGIN
# Docker Hub user or organization owning the target image:
gh variable set DOCKERHUB_NAMESPACE --repo moophlo/strata-rocm-r9700 --body YOUR_DOCKERHUB_NAMESPACE
# This prompts securely for a token; do not store it in git:
gh secret set DOCKERHUB_TOKEN --repo moophlo/strata-rocm-r9700

# Register the runner through the existing Kubernetes Actions Runner Controller.
# Its GitHub App installation must have access to this repository.
kubectl apply -f runnerdeployment.yaml
kubectl -n github-actions get runnerdeployments,runners,pods

git init -b main
git add Dockerfile README.md .gitignore .github scripts runnerdeployment.yaml
git commit -m 'Initial ROCm gfx1201 image pipeline'
git remote add origin git@github.com:moophlo/strata-rocm-r9700.git
git push -u origin main
```

The first push triggers a build. Future changes to `Dockerfile` or the workflow
trigger a new build, and **Actions > Build and publish Strata ROCm (R9700) >
Run workflow** permits manual rebuilds even without a code change.

`runnerdeployment.yaml` uses the existing `actions.summerwind.dev/v1alpha1`
controller in `github-actions` and places the runner on `docker03.mufloland.it`.
It sets Docker's MTU to 1450 to match the Kubernetes pod network. Review these
cluster-specific settings before using the manifest elsewhere. The controller
registers the standard `self-hosted`, `Linux`, and `X64` labels automatically.
Apply runner changes while no job is running, because they can replace the pod.

If using HTTPS git auth instead of SSH, replace the origin with
`https://github.com/moophlo/strata-rocm-r9700.git`.

## Published tags

Assuming `DOCKERHUB_NAMESPACE=exampleuser`:

```text
exampleuser/strata-rocm-r9700:latest
exampleuser/strata-rocm-r9700:src-060117c4bf49
exampleuser/strata-rocm-r9700:build-<first12-of-packaging-repository-commit>
```

`latest` is convenient for testing; for Kubernetes deploy by the **OCI digest**
printed in the Actions job summary (`...@sha256:<digest>`) to make rollbacks
reproducible. The `src-...` tag gets overwritten if you rebuild identical
source with a modified Dockerfile; the OCI digest identifies the exact image.

## First smoke test on DaiapoLinux (outside CI)

The CI build does **not** exercise GPU inference: there is no Radeon GPU on the
builder. Run the runtime smoke test on the target host before K8s rollout.

```bash
mkdir -p "$HOME/strata-data"
# Save API_KEY=your-generated-long-random-secret in a local file; never commit it.
printf 'API_KEY=%s\n' "$(openssl rand -hex 32)" > .env
chmod 600 .env

# Replace NAMESPACE with the configured Docker Hub namespace.
docker run --rm \
  --device=/dev/kfd --device=/dev/dri \
  --group-add video --group-add render \
  --ulimit memlock=-1 \
  -p 127.0.0.1:8080:8080 \
  -v "$HOME/strata-data:/data" \
  --env-file .env \
  -e MODEL=IQ3_S \
  -e CONTEXT=131072 \
  -e KV=int8 \
  -e VISION=no \
  NAMESPACE/strata-rocm-r9700:latest
```

The first launch downloads/organizes the quantization if not already present;
provide persistent disk accordingly (>=100 GB recommended for IQ3_S plus packs
and temporary files, verify actual usage). Check `http://127.0.0.1:8080/health`
and make authenticated calls to `/v1/chat/completions`.

On the dual-AMD-GPU machine, check Strata's reported HIP GPU indexes and pin
`GPU` to the R9700 index when necessary. Do not assume the R9700 is GPU 0.
The image has code for **gfx1201 only**: RX 7900 XT (`gfx1100`) requires its
own image or a different ROCm strategy.

Only after proving 128K stable, test `CONTEXT=196608`. Changing saved setup
options on a persistent `/data` volume requires `REINSTALL=1` for one launch;
then remove it. Do not assume changing the environment alone modifies a
previously generated runtime configuration.

## Security / operations

- Protect the default branch. A push to `main` executes code on your runner
  and obtains a Docker Hub publishing token.
- Use a dedicated, isolated self-hosted runner. Docker socket access is
  effectively privileged access on its host. Avoid the K8s control-plane node.
- Restrict `DOCKERHUB_TOKEN` to the target repository if supported by your
  Docker Hub token type; use a revocable access token, not a password.
- No untrusted PR builds run on the publishing runner.
- Do not bind the inference API to an external interface without an API key,
  network policy and ingress-level authentication/TLS.
- `BUILD_VISION=0` disables the CPU vision encoder. This variant is for coding
  first; enabling vision changes both the image build and runtime requirements.
- Strata's ROCm Dockerfile comes from a still-open community PR. We pin the
  source deliberately rather than tracking the mutable branch or `main`.
- ROCm/TheRock wheels are external build dependencies; repeatability is
  *source-pinned*, not a full hermetic supply-chain lock.
- The base image `ubuntu:24.04` uses a mutable tag. Pin to a digest and pin
  third-party actions to SHAs for stronger supply-chain guarantees.

## Source / attribution

- Engine: https://github.com/Niko1221/Strata
- Community ROCm Dockerfile: https://github.com/Niko1221/Strata/pull/1486
- AMD instructions: https://github.com/Niko1221/Strata/blob/main/docs/AMD_HIP.md
- Docker Actions: https://github.com/docker/build-push-action
