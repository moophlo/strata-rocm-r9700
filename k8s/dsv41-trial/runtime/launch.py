"""Validate the allocated R9700 and prepared model before the upstream entrypoint."""
import json
import os
from pathlib import Path

from gpu_guard import verify_gpu

verify_gpu()
if not os.environ.get("API_KEY", "").strip():
    raise RuntimeError("API_KEY must come from the existing strata-api Secret")
if os.environ.get("GPU") != "0" or os.environ.get("GPUS"):
    raise RuntimeError("This deployment requires GPU=0 and no GPUS override")
receipt = json.loads(Path("/data/preparation-receipt.json").read_text())
if receipt.get("quant") != "IQ2_XXS" or not (Path(receipt["pack"]) / "tensors.txt").is_file():
    raise RuntimeError("The verified IQ2_XXS pack has not been prepared")
os.execv("/usr/bin/tini", ["tini", "--", "/opt/strata/entrypoint.sh"])
