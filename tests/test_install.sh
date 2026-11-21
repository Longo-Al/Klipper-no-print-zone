#!/bin/bash
# Test of install.sh in a sandbox (fake Klipper tree, fake printer_data, no
# Moonraker): install, second run changes nothing, uninstall restores files.
# With a real Klipper and an MCU dictionary it also loads the starter config
# and a zone written by the wizard in Klipper batch mode:
#   tests/test_install.sh [KLIPPER_DIR DICT]
REPO=$(cd "$(dirname "$0")/.." && pwd)
T=$(mktemp -d)
trap 'rm -rf "$T"' EXIT
FAIL=0
ok()   { echo "[PASS] $*"; }
bad()  { echo "[FAIL] $*"; FAIL=$((FAIL+1)); }
check() { if eval "$2"; then ok "$1"; else bad "$1"; fi; }

mkdir -p "$T/klipper/klippy/extras" "$T/printer_data/config"
git -C "$T/klipper" init -q
# the working files under test, as a git repository (like a real clone)
mkdir "$T/src"
(cd "$REPO" && tar --exclude=.git --exclude=__pycache__ --exclude=tests/.build -cf - .) | tar -xf - -C "$T/src"
git -C "$T/src" init -q -b main
git -C "$T/src" add -A
git -C "$T/src" -c user.name=test -c user.email=test@invalid commit -qm test
git -C "$T/src" remote add origin https://github.com/Longo-Al/Klipper-no-print-zone.git
REPO=$T/src
CFG=$T/printer_data/config
printf '[printer]\nkinematics: corexy\n\n#*# <---------------------- SAVE_CONFIG ---------------------->\n#*# DO NOT EDIT THIS BLOCK OR BELOW. The contents are auto-generated.\n#*#\n' > "$CFG/printer.cfg"
printf '[server]\nhost: 0.0.0.0\nport: 7999\n\n[update_manager]\nrefresh_interval: 168\n' > "$CFG/moonraker.conf"
cp "$CFG/printer.cfg" "$T/printer.orig"; cp "$CFG/moonraker.conf" "$T/moonraker.orig"
RUN=(bash "$REPO/install.sh" --klipper-dir "$T/klipper" --config-dir "$CFG"
     --moonraker http://127.0.0.1:9 --yes --no-wizard)
LINK=$T/klipper/klippy/extras/no_print_zone.py

"${RUN[@]}" > "$T/run1.log" 2>&1
check "install: exit 0" "[ $? -eq 0 ]"
check "install: module linked" "[ \"\$(readlink -f '$LINK')\" = '$REPO/no_print_zone.py' ]"
check "install: excluded from Klipper git" "grep -qxF klippy/extras/no_print_zone.py '$T/klipper/.git/info/exclude'"
check "install: git status of Klipper clean" "[ -z \"\$(git -C '$T/klipper' status --porcelain)\" ]"
check "install: starter config created" "cmp -s '$REPO/examples/starter.cfg' '$CFG/no_print_zone.cfg'"
check "install: include before SAVE_CONFIG" "awk '/\\[include no_print_zone.cfg\\]/{i=NR} /SAVE_CONFIG/{s=NR} END{exit !(i && s && i<s)}' '$CFG/printer.cfg'"
check "install: update_manager added" "grep -q '^\[update_manager no_print_zone\]' '$CFG/moonraker.conf' && grep -qxF 'origin: https://github.com/Longo-Al/Klipper-no-print-zone.git' '$CFG/moonraker.conf'"
check "install: backups written" "[ -n \"\$(ls '$T/printer_data/backup/no_print_zone' 2>/dev/null)\" ]"
check "install: no restart without Moonraker" "grep -q 'not reachable' '$T/run1.log'"

md5sum "$CFG"/* > "$T/md5.1"
"${RUN[@]}" > "$T/run2.log" 2>&1
check "second run: exit 0" "[ $? -eq 0 ]"
md5sum "$CFG"/* > "$T/md5.2"
check "second run: files unchanged" "cmp -s '$T/md5.1' '$T/md5.2'"
check "second run: existing config kept" "grep -q 'existing no_print_zone configuration kept' '$T/run2.log'"
check "second run: module already linked" "grep -q 'module already linked' '$T/run2.log'"

"${RUN[@]}" --uninstall > "$T/run3.log" 2>&1
check "uninstall: exit 0" "[ $? -eq 0 ]"
check "uninstall: link removed" "[ ! -e '$LINK' ]"
check "uninstall: printer.cfg as before" "cmp -s '$T/printer.orig' '$CFG/printer.cfg'"
check "uninstall: moonraker.conf as before" "cmp -s '$T/moonraker.orig' '$CFG/moonraker.conf'"
check "uninstall: exclude entry removed" "! grep -qxF klippy/extras/no_print_zone.py '$T/klipper/.git/info/exclude'"

# zones configured elsewhere: no starter, and uninstall warns
mkdir -p "$CFG/addon"
printf '[no_print_zone]\n[no_print_zone a]\ntype: keepout\nx_max: 5\n' > "$CFG/addon/npz.cfg"
sed -i 's|^\[printer\]|[include addon/npz.cfg]\n[printer]|' "$CFG/printer.cfg"
rm -f "$CFG/no_print_zone.cfg"
"${RUN[@]}" --no-moonraker > "$T/run4.log" 2>&1
check "existing zones: no starter created" "[ ! -e '$CFG/no_print_zone.cfg' ]"
check "existing zones: reported" "grep -q 'addon/npz.cfg' '$T/run4.log'"
"${RUN[@]}" --uninstall > "$T/run5.log" 2>&1
check "uninstall with zones left: warns" "grep -q 'Klipper will NOT start' '$T/run5.log'"
check "uninstall with zones left: no restart" "! grep -q 'restarting' '$T/run5.log'"

# the one-line install: the script arrives on stdin (curl | bash), clones
# the repository, then runs the clone's install.sh
CFG2=$T/printer2_data/config; mkdir -p "$CFG2"
cp "$T/printer.orig" "$CFG2/printer.cfg"; cp "$T/moonraker.orig" "$CFG2/moonraker.conf"
rm -f "$LINK"
ONE=(--repo-dir "$T/clone" --klipper-dir "$T/klipper" --config-dir "$CFG2"
     --moonraker http://127.0.0.1:9 --yes --no-wizard)
cat "$REPO/install.sh" | NPZ_REPO_URL="$REPO" bash -s -- "${ONE[@]}" > "$T/run6.log" 2>&1
check "one-line: exit 0" "[ $? -eq 0 ]"
check "one-line: cloned" "[ -f '$T/clone/no_print_zone.py' ] && [ -d '$T/clone/.git' ]"
check "one-line: module linked to the clone" "[ \"\$(readlink -f '$LINK')\" = '$T/clone/no_print_zone.py' ]"
check "one-line: starter config + include" "[ -f '$CFG2/no_print_zone.cfg' ] && grep -q 'include no_print_zone.cfg' '$CFG2/printer.cfg'"
check "one-line: update_manager points to the clone" "grep -qxF 'path: $T/clone' '$CFG2/moonraker.conf'"
# an update that changes only the README must not ask for a Klipper restart
echo "x" >> "$REPO/README.md"; git -C "$REPO" -c user.name=test -c user.email=test@invalid commit -qam readme
cat "$REPO/install.sh" | NPZ_REPO_URL="$REPO" bash -s -- "${ONE[@]}" > "$T/run7.log" 2>&1
check "one-line again: updated" "grep -q 'updating $T/clone' '$T/run7.log' && grep -qxF x '$T/clone/README.md'"
check "one-line again: README only, no restart needed" "! grep -q 'must restart' '$T/run7.log'"
echo "# changed" >> "$REPO/no_print_zone.py"; git -C "$REPO" -c user.name=test -c user.email=test@invalid commit -qam module
cat "$REPO/install.sh" | NPZ_REPO_URL="$REPO" bash -s -- "${ONE[@]}" > "$T/run8.log" 2>&1
check "one-line again: module changed, restart needed" "grep -q 'module updated' '$T/run8.log' && grep -q 'must restart' '$T/run8.log'"

# Klipper batch: starter + a wizard zone are accepted by the real Klipper
if [ -n "$1" ] && [ -n "$2" ]; then
    B=$T/batch; mkdir -p "$B/cfg"
    awk '/^# Zones under test/{exit} {print}' "$REPO/tests/printer.cfg" | sed '$d' > "$B/cfg/printer.cfg"
    echo '[include no_print_zone.cfg]' >> "$B/cfg/printer.cfg"
    cp "$REPO/examples/starter.cfg" "$B/cfg/no_print_zone.cfg"
    printf '1\ndock2\n3\n0,0\n11,22\n1\n10.5,27\n10.5,0\ny\ny\n0,0\nn\n10\ny\n2\ny\n1\nclip\n1\n280,0\n300,20\ny\n3,3,4\n1\ny\n3\n' \
        | python3 "$REPO/tools/npz_setup.py" wizard --config-dir "$B/cfg" --moonraker http://127.0.0.1:9 --no-restart > "$B/wizard.log" 2>&1
    check "wizard (scripted): zones written" "grep -q 'no_print_zone dock2' '$B/cfg/no_print_zone.cfg' && grep -q 'no_print_zone clip' '$B/cfg/no_print_zone.cfg'"
    cat > "$B/t.test" <<'EOF'
### W01_starter_and_wizard_zones_load
#! expect: ok: zone dock2 [rails]
#! log_has: zone clip [keepout]
NPZ_STATUS
G28
NPZ_OPEN ZONE=dock2
G1 X25 Y25 F6000
G1 X10.5 F3000
G1 Y0 F600
G1 X0 F600
G1 X10.5 F600
G1 Y25 F600
NPZ_CLOSE

### W02_wizard_clip_refuses
#! expect: error: enters keepout zone 'clip'
G28
G1 Z3 F600
G1 X290 Y10 F6000
EOF
    KPY=$HOME/klippy-env/bin/python; [ -x "$KPY" ] || KPY=python3
    if "$KPY" "$REPO/tests/run_tests.py" --klipper "$1" --config "$B/cfg/printer.cfg" \
            --dict "$2" --work "$T/batch_work" "$B/t.test" > "$B/batch.log" 2>&1; then
        ok "Klipper batch: starter + wizard zones load and work"
    else
        bad "Klipper batch"; tail -20 "$B/batch.log"; tail -30 "$B/wizard.log"
    fi
fi

echo
[ "$FAIL" = 0 ] && echo "RESULT: all passed" || { echo "RESULT: $FAIL failed"; for f in "$T"/run*.log; do echo "--- $f"; cat "$f"; done; }
exit $FAIL
