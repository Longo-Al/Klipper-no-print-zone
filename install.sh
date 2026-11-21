#!/bin/bash
# Link no_print_zone.py into Klipper's extras directory.
#   ./install.sh [KLIPPER_DIR]        (default: ~/klipper)
# Run it again after a Klipper reinstall or a "hard" recovery: without the
# link Klipper refuses to start ([no_print_zone] is then an unknown section).
# Then restart the service: a RESTART command does not reload Python modules.
set -e
REPO=$(cd "$(dirname "$0")" && pwd)
KLIPPER=${1:-$HOME/klipper}
SRC="$REPO/no_print_zone.py"
DST="$KLIPPER/klippy/extras/no_print_zone.py"
ENTRY=klippy/extras/no_print_zone.py

if [ ! -d "$KLIPPER/klippy/extras" ]; then
    echo "Klipper not found in $KLIPPER (pass its directory as argument)"
    exit 1
fi
if [ -e "$DST" ] && [ ! -L "$DST" ]; then
    echo "$DST exists and is not a link: move it away first"
    exit 1
fi
ln -sfn "$SRC" "$DST"
echo "linked: $DST -> $SRC"

# keep Klipper's git tree clean (Moonraker reports untracked files)
if [ -d "$KLIPPER/.git" ]; then
    EXC="$KLIPPER/.git/info/exclude"
    mkdir -p "$(dirname "$EXC")"
    grep -qxF "$ENTRY" "$EXC" 2>/dev/null || echo "$ENTRY" >> "$EXC"
fi

echo "now restart Klipper: sudo systemctl restart klipper"
