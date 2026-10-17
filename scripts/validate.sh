#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."

echo 'Checking upstream source pin and GPU build settings...'
grep -q 'UPSTREAM_SHA: 060117c4bf49e50e86ae8d79361540f9c83d77a1' .github/workflows/build-push.yml
grep -Fq 'ref: ${{ env.UPSTREAM_SHA }}' .github/workflows/build-push.yml
grep -q 'HIP_ARCHITECTURES=gfx1201' .github/workflows/build-push.yml
grep -q 'ARG HIP_ARCHITECTURES=gfx1201' Dockerfile
echo 'Static configuration checks passed.'
