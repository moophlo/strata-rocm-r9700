#!/usr/bin/env python3
"""Assemble the upstream Docker image with the pinned, signed DeepSeek HIP engine."""
import argparse
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

SOURCE_SHA = "d2f096475817d4666c364c820b0ca830f751cfc5"
RELEASE = "v0.2.13"
ENGINE_VERSION = "0.1.59"
ENGINE_SHA256 = "fcc7b94f8274887b532a40e10817b58adf62ef7f2c9c32bb4b6317e224aa58d0"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    source, out = args.source.resolve(), args.out.resolve()
    actual = subprocess.check_output(["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    if actual != SOURCE_SHA:
        raise SystemExit(f"Unexpected upstream source revision: {actual}")
    if out.exists():
        raise SystemExit(f"Build directory already exists: {out}")
    sys.path.insert(0, str(source))
    from strata_control import feed as feedmod, signing

    spec = importlib.util.spec_from_file_location("fetch_engines", source / "docker/fetch-engines.py")
    fetcher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetcher)
    trusted = signing.load_trusted_keys()
    feed = feedmod.fetch(f"https://github.com/Niko1221/Strata/releases/download/{RELEASE}", trusted)
    matches = [p for p in feed.dsv41 if p.platform == "linux-x86_64" and p.backend == "hip"
               and p.variant == "rx9070" and p.version == ENGINE_VERSION and p.sha256 == ENGINE_SHA256]
    if len(matches) != 1:
        raise SystemExit("The signed release feed does not contain exactly the pinned DeepSeek HIP package")
    out.mkdir(parents=True)
    package = fetcher.fetch_one(feed, matches[0], out / "packages", trusted)
    manifest = json.loads((package / "engine-component.json").read_text())
    if manifest["entrypoint"] != "strata-dsv41" or "gfx1201" not in manifest["archs"]:
        raise SystemExit("Package does not contain the DeepSeek engine for the R9700")
    context = out / "context"
    subprocess.run(["sh", str(source / "docker/build.sh"), "--flavor", "hip", "--engine", str(package),
                    "--ctx", str(context), "--ctx-only"], check=True)
    executable = f"/opt/strata/components/engine/{package.name}/strata-dsv41"
    with (context / "Dockerfile").open("a") as dockerfile:
        dockerfile.write("\n# Dedicated DeepSeek image; model weights remain on the data volume.\n"
                         "ENV FAMILY=deepseek41 MODEL=ist-das-2.00bpw CONTEXT=8192 PRESET=dsv41 \\\n"
                         f"    STRATA_DSV41_EXE={executable}\n")
    receipt = {"source_sha": actual, "release": RELEASE, "engine_version": ENGINE_VERSION,
               "engine_sha256": ENGINE_SHA256, "engine_build": manifest["build_id"],
               "archs": manifest["archs"], "executable": executable}
    (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps(receipt, indent=2))


if __name__ == "__main__":
    main()
