"""Download the pinned IQ2_XXS set to NVMe and prepare its pack without a GPU."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

sys.path.insert(0, "/opt/strata/terminal")
import setup

DATA = Path("/data/Strata-data")
MODEL = "IST-DAS-2.00bpw"
FAMILY = setup.MULTI_FILE_FAMILIES["deepseek41"]
MD = FAMILY["models"][MODEL]
FOLDER = DATA / "models" / (FAMILY["tag"] + MODEL)
SHARED = DATA / "models" / (FAMILY["tag"] + "shared")
PACK = DATA / "packs" / (FAMILY["tag"] + MODEL).lower()


def main():
    DATA.mkdir(parents=True, exist_ok=True)
    files = [(FOLDER / name, MD["hf"] + name) for name in MD["shards"]]
    files += [(SHARED / name, setup.DSV41_SHARED[name] + name)
              for name in [*MD["shared"], setup.DSV41_MMPROJ]]
    remaining = sum(FAMILY["sha256"][f.name][0] for f, _ in files if not (f.is_file() and setup.done(f)))
    if shutil.disk_usage(DATA).free < remaining + 10 * 2**30:
        raise RuntimeError("Not enough free NVMe space for the pinned files and preparation margin")
    print(json.dumps({"stage": "download", "model": MODEL, "bytes": remaining,
                      "ista_revision": setup.HF_REVISIONS[setup.DSV41_ISTA_REPO],
                      "unsloth_revision": setup.HF_REVISIONS[setup.DSV41_REPO]}), flush=True)
    receipt = []
    for f, url in files:
        setup.download(url, f)
        size, sha = FAMILY["sha256"][f.name]
        setup.verify_sha256(f, size, sha)
        receipt.append({"path": str(f), "bytes": size, "sha256": sha})
        print(json.dumps({"stage": "verified", "file": f.name, "bytes": size}), flush=True)
    print(json.dumps({"stage": "pack"}), flush=True)
    shared = [SHARED / n for n in MD["shared"]]
    subprocess.run(setup.dsv41_pack_cmd(FOLDER, MD["shards"], shared[0], shared[1], shared[2],
                                      PACK, SHARED / setup.DSV41_MMPROJ), check=True)
    setup.dsv41_write_pack_source(PACK)
    shutil.copyfile(setup.DSV41_PROFILES / MD["profile"], PACK / "expert-profile.bin")
    exe = Path(os.environ["STRATA_DSV41_EXE"])
    env = dict(os.environ, LD_LIBRARY_PATH=str(exe.parent))
    subprocess.run([str(exe), "--pack-info", "--pack", str(PACK)], env=env, check=True)
    result = {"model": MODEL, "quant": "IQ2_XXS", "pack": str(PACK),
              "completed_unix": time.time(), "files": receipt}
    (Path("/data") / "preparation-receipt.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({"stage": "complete", "bytes": sum(f["bytes"] for f in receipt), "pack": str(PACK)}), flush=True)


if __name__ == "__main__":
    main()
