#!/bin/bash
# Build the shim and wrap it in an Android boot image. Header v4 is the
# default for Topaz. Ginkgo's A-only boot chain uses header v2 and carries its
# DTB in boot.img; select that with HEADER_VERSION=2 and BOOT_DTB=<file>.
set -euo pipefail
cd "$(dirname "$0")"
CC=aarch64-linux-gnu-gcc
OBJCOPY=aarch64-linux-gnu-objcopy
CFLAGS="-O2 -ffreestanding -nostdlib -fno-pic -mgeneral-regs-only -mstrict-align -fno-stack-protector -Wall -Wextra"
mkdir -p out
HEADER_VERSION=${HEADER_VERSION:-4}
BOOT_OUTPUT=${BOOT_OUTPUT:-out/shim-boot.img}
for f in start.S shim.c console.c mmu.c fdt.c loader.c ufs.c; do
	$CC $CFLAGS -c $f -o out/${f%.*}.o
done
# The image runs wherever ABL puts it with no relocation pass: only the
# header's image_size (start.o, linked at 0) may be an absolute address.
abs=$(aarch64-linux-gnu-readelf -r out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o | grep -c ABS64 || true)
[ "$abs" = 0 ] || { echo "error: $abs absolute relocations in C objects" >&2; exit 1; }
$CC $CFLAGS -static -T shim.ld -Wl,--build-id=none -Wl,--no-warn-rwx-segments -Wl,-N \
	out/start.o out/shim.o out/console.o out/mmu.o out/fdt.o out/loader.o out/ufs.o -o out/shim.elf
$OBJCOPY -O binary out/shim.elf out/Image
# ABL copies the file; pad to image_size so .bss/stacks exist as zeroed bytes.
size=$((16#$(aarch64-linux-gnu-nm out/shim.elf | awk '$3=="_end"{print $1}')))
truncate -s "$size" out/Image
# Build a nonempty ramdisk (some ABL builds reject a zero-size ramdisk).
# The shim locates the payload appended to Image, independent of whether the
# bootloader passes a compressed initrd (v2) or vendor ramdisk (v4).
if [ -n "${1:-}" ] && { [ "$HEADER_VERSION" = 2 ] || [ "$HEADER_VERSION" = 4 ]; }; then
	# The shim finds this payload immediately after its ELF _end in Image;
	# this works with v2 and v4 even when the bootloader compresses its ramdisk.
	truncate -s $(( (size + 0xfff) & ~0xfff )) out/Image
	cat "$1" >> out/Image
	python3 -c 'import os,struct,sys; f=open(sys.argv[1],"r+b"); f.seek(16); f.write(struct.pack("<Q", os.path.getsize(sys.argv[1])))' out/Image
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
else
	(cd out && rm -rf rd && mkdir rd && cd rd && find . | cpio -o -H newc --quiet | gzip -9 > ../ramdisk.bin)
fi
if [ -n "${MKBOOTIMG_SCRIPT:-}" ]; then
	MKBOOTIMG_CMD=(python3 "$MKBOOTIMG_SCRIPT")
else
	MKBOOTIMG_CMD=(python3 /home/dino/android/lineage/system/tools/mkbootimg/mkbootimg.py)
fi
if [ "$HEADER_VERSION" = 2 ]; then
	[ -n "${BOOT_DTB:-}" ] && [ -f "$BOOT_DTB" ] || { echo "HEADER_VERSION=2 requires BOOT_DTB=<Ginkgo DTB>" >&2; exit 1; }
	"${MKBOOTIMG_CMD[@]}" --header_version 2 --kernel out/Image --ramdisk out/ramdisk.bin --dtb "$BOOT_DTB" \
		--pagesize 4096 --base 0 --kernel_offset 0x8000 --ramdisk_offset 0x1000000 \
		--tags_offset 0x100 --dtb_offset 0x1f00000 --cmdline "console=null androidboot.hardware=qcom androidboot.boot_devices=soc/4744000.sdhci" \
		-o "$BOOT_OUTPUT"
elif [ "$HEADER_VERSION" = 4 ]; then
	"${MKBOOTIMG_CMD[@]}" --header_version 4 --kernel out/Image --ramdisk out/ramdisk.bin \
		--os_version 13.0.0 --os_patch_level 2025-01 \
		--cmdline "console=null" -o "$BOOT_OUTPUT"
else
	echo "unsupported HEADER_VERSION=$HEADER_VERSION (expected 2 or 4)" >&2
	exit 1
fi
# Host render of the console layout (optional for packaging-only builds).
if [ "${SKIP_HOST_TEST:-0}" != 1 ]; then
	gcc -O1 -Wall -o out/host_test console.c host_test.c && ./out/host_test
fi
ls -l out/Image "$BOOT_OUTPUT"
