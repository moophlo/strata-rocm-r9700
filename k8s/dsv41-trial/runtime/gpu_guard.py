"""Check the actual allocated GPU before running Strata; no GPU computation."""
import os
from pathlib import Path
import stat
import sys

EXPECTED_PCI = "0000:06:00.0"
EXPECTED_VENDOR = 0x1002
EXPECTED_DEVICE = 0x7551
EXPECTED_GFX = 120001  # gfx1201


def verify_gpu(root=Path("/")):
    renders = sorted((root / "dev/dri").glob("renderD*"))
    if len(renders) != 1:
        raise RuntimeError(f"Expected exactly one allocated render device, found {renders}")
    render = renders[0]
    for device in (root / "dev/kfd", render):
        if not stat.S_ISCHR(device.stat().st_mode):
            raise RuntimeError(f"Not a character device: {device}")
        # Opening and immediately closing checks cgroup/device permissions too.
        fd = os.open(device, os.O_RDWR | os.O_CLOEXEC)
        os.close(fd)
    gpu = root / "sys/class/drm" / render.name / "device"
    pci = gpu.resolve().name
    vendor = int((gpu / "vendor").read_text(), 16)
    device_id = int((gpu / "device").read_text(), 16)
    vram = int((gpu / "mem_info_vram_total").read_text())
    if (pci, vendor, device_id) != (EXPECTED_PCI, EXPECTED_VENDOR, EXPECTED_DEVICE):
        raise RuntimeError(f"Wrong GPU: PCI={pci}, vendor={vendor:#x}, device={device_id:#x}; R9700 required")
    if vram < 29 * 2**30:
        raise RuntimeError(f"Unexpected R9700 VRAM: {vram / 2**30:.2f} GiB")

    # Match upstream setup.py's enumeration of readable KFD nodes. Sysfs DRM
    # alone can list both cards even when only one is allocated to the container.
    visible = []
    for props_file in (root / "sys/class/kfd/kfd/topology/nodes").glob("*/properties"):
        try:
            props = dict(line.split(maxsplit=1) for line in props_file.read_text().splitlines() if line.strip())
            gfx = int(props.get("gfx_target_version", "0"))
            simds = int(props.get("simd_count", "0"))
            if gfx and simds:
                visible.append((gfx, int(props["drm_render_minor"])))
        except OSError:
            # The AMD device plugin masks unallocated GPU nodes with EACCES/EPERM.
            continue
    expected = [(EXPECTED_GFX, int(render.name.removeprefix("renderD")))]
    if visible != expected:
        raise RuntimeError(f"Unexpected visible KFD GPUs: {visible}; expected {expected}")
    print(f"GPU verified: R9700, PCI {pci}, gfx1201, {vram / 2**30:.2f} GiB, {render.name}; GPU=0", flush=True)


def main():
    if sys.argv[1:] not in ([], ["--check-only"]):
        raise RuntimeError("Usage: gpu_guard.py [--check-only]")
    verify_gpu()
    if sys.argv[1:] == ["--check-only"]:
        return
    if not os.environ.get("API_KEY", "").strip():
        raise RuntimeError("A nonempty API_KEY from Secret strata-api is required")
    if os.environ.get("GPU") != "0" or os.environ.get("GPUS"):
        raise RuntimeError("This deployment requires GPU=0 and no GPUS override")
    os.chdir("/opt/strata")
    os.execv("./docker-entrypoint.sh", ["./docker-entrypoint.sh"])


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        print(f"Strata startup refused: {exc}", file=sys.stderr, flush=True)
        sys.exit(1)
