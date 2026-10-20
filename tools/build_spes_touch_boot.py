#!/usr/bin/env python3
"""Wait for a Spes XNU CI run and package its kernel into the preserved v4 image."""

import hashlib
import pathlib
import shutil
import struct
import subprocess
import sys
import tempfile
import time

REPO = "dinosaurrus/ios-spes-touch-fix"
ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = pathlib.Path(
    "/home/dino/İndirilenler/MIO-KITCHEN-4.2.1-linux/spesios16touch/"
    "spes-touch-boot-fixed-power-volume-keys-async.img"
)
OUTPUT = BASE.with_name("spes-touch-boot-fixed-power-volume-keys-apid.img")
MKBOOTIMG = pathlib.Path("/usr/share/android-tools/mkbootimg/mkbootimg.py")


def run(args):
    subprocess.run([str(arg) for arg in args], check=True)


def wait_for_success(run_id):
    """Poll Actions state; tolerate transient API/network failures."""
    while True:
        result = subprocess.run(
            ["gh", "run", "view", run_id, "-R", REPO,
             "--json", "status,conclusion", "--jq", ".status + \" \" + (.conclusion // \"\")"],
            text=True, capture_output=True,
        )
        if result.returncode == 0:
            status, _, conclusion = result.stdout.strip().partition(" ")
            print(f"Actions {run_id}: {status} {conclusion}".rstrip(), flush=True)
            if status == "completed":
                if conclusion != "success":
                    raise RuntimeError(f"Actions run ended: {conclusion or 'unknown'}")
                return
        else:
            print("Temporary GitHub API error; retrying in 15 seconds", flush=True)
        time.sleep(15)


def payload_info(kernel):
    magic = b"SWEETXNU"
    candidates = []
    start = 0
    while True:
        pos = kernel.find(magic, start)
        if pos < 0:
            break
        start = pos + 1
        if pos + 0x48 + 608 > len(kernel):
            continue
        adt_off, adt_size = struct.unpack_from("<QQ", kernel, pos + 0x20)
        cmdline = kernel[pos + 0x48 : pos + 0x48 + 608].split(b"\0", 1)[0].decode(errors="replace")
        if (adt_off >= 0x1000 and adt_size > 0 and
                pos + adt_off + adt_size <= len(kernel) and cmdline):
            candidates.append((pos, adt_off, adt_size, cmdline))
    if len(candidates) != 1:
        raise RuntimeError(f"expected one valid embedded SWEETXNU payload, found {len(candidates)}")
    pos, adt_off, adt_size, cmdline = candidates[0]
    return pos, kernel[pos + adt_off : pos + adt_off + adt_size], cmdline


def main():
    run_id = sys.argv[1] if len(sys.argv) > 1 else "39464448670"
    if len(sys.argv) > 2:
        raise SystemExit(f"usage: {sys.argv[0]} [github-actions-run-id]")
    if OUTPUT.exists():
        raise SystemExit(f"refusing to overwrite existing output: {OUTPUT}")
    for path in (BASE, MKBOOTIMG, ROOT / "tools/mkpayload.py"):
        if not path.exists():
            raise SystemExit(f"required file not found: {path}")

    print(f"Waiting for successful macOS build {run_id}…", flush=True)
    wait_for_success(run_id)

    with tempfile.TemporaryDirectory(prefix=f"spes-touch-{run_id}-") as td:
        work = pathlib.Path(td)
        artifact = work / "artifact"
        artifact.mkdir()
        run(["gh", "run", "download", run_id, "-R", REPO,
             "-n", "xnu-kernel", "-D", artifact])
        ci_kernel = artifact / "kernel.development.sweet"
        if not ci_kernel.is_file():
            raise RuntimeError(f"CI kernel not found: {ci_kernel}")

        base_dir = work / "base"
        run(["unpack_bootimg", "--boot_img", BASE, "--out", base_dir])
        base_kernel = (base_dir / "kernel").read_bytes()
        ramdisk = base_dir / "ramdisk"
        shim_end, adt, cmdline = payload_info(base_kernel)

        adt_path = work / "spes.adt"
        adt_path.write_bytes(adt)
        payload = work / "payload.bin"
        run([sys.executable, ROOT / "tools/mkpayload.py", ci_kernel,
             adt_path, payload, "--cmdline", cmdline])
        packed_kernel = work / "kernel"
        packed_kernel.write_bytes(base_kernel[:shim_end] + payload.read_bytes())

        run([sys.executable, MKBOOTIMG, "--header_version", "4",
             "--pagesize", "4096", "--kernel", packed_kernel,
             "--ramdisk", ramdisk, "--os_version", "13.0.0",
             "--os_patch_level", "2025-01", "--cmdline", "console=null",
             "--output", OUTPUT])

        check_dir = work / "check"
        run(["unpack_bootimg", "--boot_img", OUTPUT, "--out", check_dir])
        info = subprocess.check_output(
            ["unpack_bootimg", "--boot_img", OUTPUT], text=True
        )
        new_kernel = (check_dir / "kernel").read_bytes()
        new_ramdisk = (check_dir / "ramdisk").read_bytes()
        if "boot image header version: 4" not in info:
            raise RuntimeError("output is not an Android boot-v4 image")
        if new_kernel[:shim_end] != base_kernel[:shim_end]:
            raise RuntimeError("original shim prefix changed")
        new_pos, new_adt, _ = payload_info(new_kernel)
        if new_pos != shim_end or new_adt != adt:
            raise RuntimeError("embedded Spes ADT was not preserved")
        if new_ramdisk != ramdisk.read_bytes():
            raise RuntimeError("original ramdisk changed")
        required = (b"QUP0/SE2 clock init ready", b"SE2 init ready",
                    b"reset asserted", b"L9A uses RPM-SMD",
                    b"FT3418 first contact", b"report-frame-aligned",
                    b"probe sequence begin",
                    b"FT5452 idle frame; release all contacts",
                    b"SWEET KEYS: target=", b"Spes-PM6125",
                    b"SWEET KEYS: async init queued", b"SWEET KEYS: probe begin",
                    b"SWEET KEYS: APID map scan count=",
                    b"SWEET KEYS: key=")
        missing = [item.decode() for item in required if item not in new_kernel]
        if missing:
            raise RuntimeError(f"new touch stage markers missing: {missing}")

    digest = hashlib.sha256(OUTPUT.read_bytes()).hexdigest()
    print(f"Created: {OUTPUT}")
    print(f"SHA256:  {digest}")
    print("Image checks: boot-v4, preserved shim/ADT/ramdisk, Spes touch and PMIC APID-map markers OK")


if __name__ == "__main__":
    main()
