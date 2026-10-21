#!/usr/bin/env python3
"""Build an experimental Redmi Note 8 (ginkgo) XNU boot-v2 image.

This is a packaging/probe image, not a usable iOS build: the current XNU port
does not contain an SDHCI/eMMC root driver for Ginkgo. A live device FDT must
also be used to choose the contiguous RAM window passed with --memory-base and
--memory-size. This script never flashes a device.
"""
import argparse
import json
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys


ROOT = Path(__file__).resolve().parents[1]


def run(args, *, env=None):
    print('+', ' '.join(map(str, args)), flush=True)
    subprocess.run(list(map(str, args)), check=True, cwd=ROOT, env=env)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--boot', required=True, type=Path,
                   help='Ginkgo Android boot-v2 image supplying the hardware DTB')
    p.add_argument('--kernel', type=Path,
                   default=ROOT / 'xnu/out/kernel.development.sweet',
                   help='built arm64 XNU Mach-O (default: xnu/out/kernel.development.sweet)')
    p.add_argument('--memory-base', required=True, type=lambda s: int(s, 0),
                   help='contiguous XNU-usable RAM base from the live device FDT')
    p.add_argument('--memory-size', required=True, type=lambda s: int(s, 0),
                   help='contiguous XNU-usable RAM size from the live device FDT')
    p.add_argument('--output', type=Path,
                   default=Path('out/ginkgo-xnu-probe-boot.img'))
    p.add_argument('--mkbootimg', type=Path, default=Path(
        '/home/dino/android/lineage/system/tools/mkbootimg/mkbootimg.py'))
    a = p.parse_args()

    boot, kernel = a.boot.resolve(), a.kernel.resolve()
    output = a.output.resolve()
    if not boot.is_file() or not kernel.is_file():
        p.error('boot image or XNU kernel does not exist')
    if a.memory_base <= 0 or a.memory_size <= 0 or a.memory_base + a.memory_size > 1 << 40:
        p.error('invalid physical RAM range')
    for tool in ('aarch64-linux-gnu-gcc', 'aarch64-linux-gnu-objcopy',
                 'aarch64-linux-gnu-nm', 'aarch64-linux-gnu-readelf'):
        if not shutil.which(tool):
            p.error(f'missing required tool: {tool}')
    mkbootimg = a.mkbootimg.resolve()
    if not mkbootimg.is_file():
        p.error(f'mkbootimg.py not found: {mkbootimg}; pass --mkbootimg')

    work = output.parent / (output.stem + '-build')
    work.mkdir(parents=True, exist_ok=True)
    unpacked = work / 'donor'
    run([sys.executable, ROOT / 'tools/unpack_bootv2.py', boot, unpacked])
    header = (unpacked / 'header.txt').read_text()
    if 'header_version: 2' not in header or not (unpacked / 'dtb').is_file():
        p.error('donor must be Android boot header v2 and contain an embedded DTB')
    dtb = (unpacked / 'dtb').read_bytes()
    if len(dtb) < 8 or struct.unpack_from('>I', dtb)[0] != 0xD00DFEED:
        p.error('donor DTB is invalid')
    if b'qcom,trinket' not in dtb and b'qcom,sm6125' not in dtb:
        p.error('donor DTB does not identify the Ginkgo SM6125/Trinket SoC')

    adt = work / 'ginkgo.adt'
    payload = work / 'ginkgo-probe.payload'
    run([sys.executable, ROOT / 'tools/mkadt.py', adt, '--device', 'ginkgo',
         '--memory-base', hex(a.memory_base), '--memory-size', hex(a.memory_size)])
    run([sys.executable, ROOT / 'tools/mkpayload.py', kernel, adt, payload,
         '--cmdline', 'debug=0xc serial=3 cpus=1 keepsyms=1 -noprogress rd=sdhcroot0'])

    env = os.environ.copy()
    env.update({
        'HEADER_VERSION': '2',
        'BOOT_DTB': str((unpacked / 'dtb').resolve()),
        'BOOT_OUTPUT': str(output),
        'MKBOOTIMG_SCRIPT': str(mkbootimg),
        'SKIP_HOST_TEST': '1',
    })
    run([ROOT / 'shim/build.sh', payload], env=env)
    manifest = {
        'image': str(output),
        'kind': 'experimental Ginkgo XNU boot-v2 packaging/probe image',
        'donor_boot': str(boot),
        'donor_dtb': str(unpacked / 'dtb'),
        'kernel': str(kernel),
        'memory_base': hex(a.memory_base),
        'memory_size': hex(a.memory_size),
        'known_blocker': 'XNU has no Ginkgo SDHCI/eMMC root driver; iOS will not boot to userland.',
        'flashed': False,
    }
    (output.with_suffix(output.suffix + '.json')).write_text(
        json.dumps(manifest, indent=2) + '\n')
    print('\nEXPERIMENTAL image created; this is not a working iOS boot image.')
    print('No eMMC root driver exists in this XNU build. The image was not flashed.')
    print(output)


if __name__ == '__main__':
    main()
