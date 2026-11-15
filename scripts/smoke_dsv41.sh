#!/usr/bin/env bash
# No GPU, model weights, network, or host data mounts are used by these checks.
set -euo pipefail
image=${1:?Usage: smoke_dsv41.sh IMAGE}
docker run --rm --network none "$image" strata --version
docker run --rm --network none "$image" strata launch --help >/dev/null
docker run --rm --network none --entrypoint /bin/sh "$image" -ec '
  test "$FAMILY" = deepseek41
  test "$MODEL" = ist-das-2.00bpw
  test -x "$STRATA_DSV41_EXE"
  ! ldd "$STRATA_DSV41_EXE" | grep "not found"
  LD_LIBRARY_PATH="$(dirname "$STRATA_DSV41_EXE")" "$STRATA_DSV41_EXE" --version
'
docker run --rm --network none --entrypoint /opt/strata/terminal/python/bin/python3 \
  --workdir /opt/strata/terminal "$image" -c '
import os
from pathlib import Path
import setup
from strata_control import engine_component
assert setup.dsv41_exe(Path("/unused")) == Path(os.environ["STRATA_DSV41_EXE"])
component = engine_component.resolve(backend="hip", full=True)
assert component.entrypoint == "strata-dsv41"
assert component.root / component.entrypoint == Path(os.environ["STRATA_DSV41_EXE"])
assert (Path("tools/dsv41/pack_gguf.py")).is_file()
assert (Path("tools/dsv41/smoke.py")).is_file()
print("DeepSeek setup, packer and engine path verified without downloads")
'
