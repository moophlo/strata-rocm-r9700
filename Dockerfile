# syntax=docker/dockerfile:1
# Strata ROCm/HIP image for Radeon AI PRO R9700 (gfx1201).
# Derived from dambaev/Strata Dockerfile.rocm-stable, upstream PR #1486.
# The build context must be the complete Strata source tree at the pinned commit.
# A GPU is NOT required at image build time.
FROM ubuntu:24.04

ENV DEBIAN_FRONTEND=noninteractive \
    PYTHONUNBUFFERED=1 \
    LANG=C.UTF-8 \
    STRATA_EXECV=1

RUN apt-get update && apt-get install -y --no-install-recommends \
      build-essential ca-certificates curl git libatomic1 libgomp1 \
      python3 python3-pip python3-venv unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/strata
COPY . .

# A single ROCm wheel family. Do not combine gfx1100 and gfx1201 here.
ARG HIP_ARCHITECTURES=gfx1201
# Strata's AMD image encoder is CPU-only; disable for a leaner coding PoC.
ARG BUILD_VISION=0

RUN python3 -m venv .venv \
    && .venv/bin/pip install --no-cache-dir --upgrade pip \
    && .venv/bin/pip install --no-cache-dir -r requirements.txt \
    && chmod +x setup.sh docker-entrypoint.sh

# Reuse Strata's native HIP setup implementation, which fetches TheRock wheels
# if there is no system ROCm 7 toolchain and records engine/BUILD.json.
RUN .venv/bin/python - <<'PYEOF'
import os
import setup

archs = [
    arch for arch in os.environ.get("HIP_ARCHITECTURES", "gfx1201")
    .replace(",", ";").split(";") if arch
]
if not archs:
    raise ValueError("HIP_ARCHITECTURES must be set")
vision = "cpu" if os.environ.get("BUILD_VISION", "0") == "1" else "none"
gpu = {
    "index": 0,
    "name": "AMD (configured at build time)",
    "vram_gb": 32.0,
    "arch": archs[0],
    "archs": archs,
    "driver": "hip",
    "vendor": "amd",
}
setup.build_engine_hip(gpu, setup.get_llama_cpp(), vision)
PYEOF

RUN rm -rf build-hip build-vision

VOLUME ["/data"]
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s --start-period=600s --retries=3 \
  CMD curl -fs "http://127.0.0.1:${PORT:-8080}/health" || exit 1
ENTRYPOINT ["./docker-entrypoint.sh"]
