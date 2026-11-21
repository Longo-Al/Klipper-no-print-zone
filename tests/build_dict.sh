#!/bin/bash
# Build the MCU data dictionary used by tests/printer.cfg (STM32F446).
# Needs the Klipper source tree and arm-none-eabi-gcc (installed with Klipper
# on most hosts). Nothing is flashed and Klipper's own out/ is not touched.
#   tests/build_dict.sh [KLIPPER_DIR] [OUTPUT_DIR]
# Prints the path of the dictionary to pass to run_tests.py --dict.
set -e
KLIPPER=${1:-$HOME/klipper}
OUT=${2:-$(cd "$(dirname "$0")" && pwd)/.build}
mkdir -p "$OUT"
OUT=$(cd "$OUT" && pwd)
cat > "$OUT/config" <<EOF
CONFIG_LOW_LEVEL_OPTIONS=y
CONFIG_MACH_STM32=y
CONFIG_MACH_STM32F446=y
EOF
make -C "$KLIPPER" OUT="$OUT/out/" KCONFIG_CONFIG="$OUT/config" olddefconfig > "$OUT/build.log" 2>&1
nice -n 19 make -C "$KLIPPER" OUT="$OUT/out/" KCONFIG_CONFIG="$OUT/config" -j2 >> "$OUT/build.log" 2>&1
cp "$OUT/out/klipper.dict" "$OUT/stm32f446.dict"
echo "$OUT/stm32f446.dict"
