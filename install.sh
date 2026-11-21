#!/bin/bash
# no_print_zone: install, update or remove the module.
#
# One line, on the Klipper host, as the user that runs Klipper (not root):
#   curl -fsSL https://raw.githubusercontent.com/Longo-Al/Klipper-no-print-zone/main/install.sh | bash
# Run the same line again to update.
#
# From a clone:   ./install.sh [options]
#   --wizard            only run the zone wizard
#   --uninstall         remove the module (your zone files are kept)
#   --test              run the dry tests (builds an MCU dictionary, minutes)
#   --yes               answer yes to every question (unattended)
#   --no-restart        never restart Klipper or Moonraker
#   --no-moonraker      do not add [update_manager no_print_zone]
#   --no-wizard         do not offer the zone wizard at the end
#   --klipper-dir DIR   Klipper source (default ~/klipper)
#   --config-dir DIR    folder of printer.cfg (default: found automatically)
#   --repo-dir DIR      where the one-line install clones (~/klipper-no-print-zone)
#   --moonraker URL     default: from moonraker.conf, else http://localhost:7125
#
# What it does: links no_print_zone.py into Klipper's extras, creates
# no_print_zone.cfg (only if no zone is configured yet) and includes it from
# printer.cfg, adds Moonraker updates, restarts Klipper only if needed and
# never during a print, checks that Klipper comes back ready.
# Backups of every edited file: <printer_data>/backup/no_print_zone/

REPO_URL=${NPZ_REPO_URL:-https://github.com/Longo-Al/Klipper-no-print-zone.git}
BRANCH=${NPZ_BRANCH:-main}

info() { echo "==> $*"; }
warn() { echo "!!  $*"; }
die()  { echo "!!  $*" >&2; exit 1; }

main() {
    local ACTION=install YES=0 NO_RESTART=0 NO_MOONRAKER=0 NO_WIZARD=0
    local KLIPPER_DIR=$HOME/klipper CONFIG_DIR='' REPO_DIR=$HOME/klipper-no-print-zone
    local MOONRAKER='' BOOTSTRAPPED=0 ARGS=("$@")
    while [ $# -gt 0 ]; do
        case "$1" in
            --wizard) ACTION=wizard ;;
            --uninstall) ACTION=uninstall ;;
            --test) ACTION=test ;;
            --yes|-y) YES=1 ;;
            --no-restart) NO_RESTART=1 ;;
            --no-moonraker) NO_MOONRAKER=1 ;;
            --no-wizard) NO_WIZARD=1 ;;
            --klipper-dir) KLIPPER_DIR=$2; shift ;;
            --config-dir) CONFIG_DIR=$2; shift ;;
            --repo-dir) REPO_DIR=$2; shift ;;
            --moonraker) MOONRAKER=$2; shift ;;
            --bootstrapped) BOOTSTRAPPED=1 ;;
            -h|--help) sed -n '2,26p' "${BASH_SOURCE[0]}" 2>/dev/null | sed 's/^# \{0,1\}//'; exit 0 ;;
            *) die "unknown option $1 (see --help)" ;;
        esac
        shift
    done
    [ "$(id -u)" = 0 ] && die "run it as the user that runs Klipper, not as root or with sudo"

    # a terminal to ask questions, also when the script comes from curl | bash
    TTY=''
    if { : < /dev/tty; } 2>/dev/null; then TTY=/dev/tty; fi

    # ---- the repository: this clone, or clone/update it (one-line install)
    local SELF=${BASH_SOURCE[0]:-} REPO
    if [ -n "$SELF" ] && [ -f "$SELF" ] && [ -f "$(dirname "$SELF")/no_print_zone.py" ]; then
        REPO=$(cd "$(dirname "$SELF")" && pwd)
    else
        command -v git > /dev/null || die "git is needed: sudo apt install git"
        local OLD='' NEW
        if [ -d "$REPO_DIR/.git" ]; then
            OLD=$(git -C "$REPO_DIR" rev-parse HEAD)
            info "updating $REPO_DIR"
            git -C "$REPO_DIR" pull -q --ff-only || warn "could not update, going on with the version already there"
        elif [ -e "$REPO_DIR" ]; then
            die "$REPO_DIR exists and is not a git clone: move it away, or pass --repo-dir"
        else
            info "downloading into $REPO_DIR"
            git clone -q --branch "$BRANCH" "$REPO_URL" "$REPO_DIR" || die "git clone failed"
        fi
        NEW=$(git -C "$REPO_DIR" rev-parse HEAD)
        # Klipper must reload only if the module itself changed
        if [ -n "$OLD" ] && ! git -C "$REPO_DIR" diff --quiet "$OLD" "$NEW" -- no_print_zone.py; then
            export NPZ_UPDATED=1
        fi
        exec bash "$REPO_DIR/install.sh" --bootstrapped "${ARGS[@]}"
    fi
    local PY=python3 SETUP="$REPO/tools/npz_setup.py"
    command -v python3 > /dev/null || die "python3 is needed"

    # ---- where things are
    if [ -z "$CONFIG_DIR" ]; then
        local c=() d
        for d in "$HOME/printer_data/config" "$HOME"/*_data/config "$HOME/klipper_config"; do
            [ -f "$d/printer.cfg" ] || continue
            [[ " ${c[*]} " == *" $d "* ]] || c+=("$d")
        done
        if [ ${#c[@]} -eq 1 ]; then
            CONFIG_DIR=${c[0]}
        elif [ ${#c[@]} -eq 0 ]; then
            die "printer.cfg not found: pass --config-dir DIR"
        elif [ -n "$TTY" ]; then
            echo "Several printers found:"; local i=1
            for d in "${c[@]}"; do echo "  $i) $d"; i=$((i+1)); done
            read -r -p "Which one? " i < "$TTY"
            CONFIG_DIR=${c[$((i-1))]:-}
            [ -n "$CONFIG_DIR" ] || die "no printer chosen"
        else
            die "several printers found (${c[*]}): pass --config-dir DIR"
        fi
    fi
    CONFIG_DIR=$(cd "$CONFIG_DIR" && pwd) || die "no folder $CONFIG_DIR"
    local PRINTER_CFG=$CONFIG_DIR/printer.cfg
    local MOONRAKER_CONF=$CONFIG_DIR/moonraker.conf
    local BACKUP_DIR
    BACKUP_DIR=$(dirname "$CONFIG_DIR")/backup/no_print_zone
    [ -f "$PRINTER_CFG" ] || die "no printer.cfg in $CONFIG_DIR"
    [ -n "$MOONRAKER" ] || MOONRAKER=$($PY "$SETUP" moonraker-url "$CONFIG_DIR")
    local LINK=$KLIPPER_DIR/klippy/extras/no_print_zone.py
    local ENTRY=klippy/extras/no_print_zone.py

    ask() {     # ask QUESTION DEFAULT(y|n) [UNATTENDED_DEFAULT]
        local def=$2 un=${3:-$2} ans hint="[Y/n]"
        [ "$YES" = 1 ] && return 0
        if [ -z "$TTY" ]; then [ "$un" = y ]; return; fi
        [ "$def" = n ] && hint="[y/N]"
        read -r -p "$1 $hint " ans < "$TTY" || ans=''
        case "${ans:-$def}" in [YySs]*) return 0 ;; *) return 1 ;; esac
    }
    printing() {
        local s
        s=$($PY "$SETUP" printer-state "$MOONRAKER")
        [ "$s" = printing ] || [ "$s" = paused ]
    }
    restart() {     # restart klipper|moonraker, asking first
        local what=$1 why=$2
        if [ "$NO_RESTART" = 1 ]; then warn "$why: restart $what yourself (--no-restart)"; return 1; fi
        if [ "$($PY "$SETUP" printer-state "$MOONRAKER")" = unreachable ]; then
            warn "$why, but Moonraker is not reachable at $MOONRAKER: restart $what yourself (sudo systemctl restart $what)"
            return 1
        fi
        if printing; then warn "$why, but a print is running: restart $what after the print"; return 1; fi
        if ask "$why. Restart $what now?$([ "$what" = klipper ] && echo ' (homing is lost)')" y n; then
            $PY "$SETUP" restart "$MOONRAKER" "$what"
        else
            warn "not restarted: the change works after restarting $what"
            return 1
        fi
    }

    case "$ACTION" in
        wizard)
            exec $PY "$SETUP" wizard --config-dir "$CONFIG_DIR" --moonraker "$MOONRAKER" < "${TTY:-/dev/stdin}" ;;
        test)
            local KPY=$HOME/klippy-env/bin/python DICT
            [ -x "$KPY" ] || KPY=python3
            info "unit tests"
            (cd "$REPO" && python3 -m unittest discover -s tests -p 'test_*.py') || die "unit tests failed"
            info "building an MCU data dictionary for the test printer (needs arm-none-eabi-gcc)"
            DICT=$("$REPO/tests/build_dict.sh" "$KLIPPER_DIR") || die "build failed: see $REPO/tests/.build/build.log"
            info "dry tests with Klipper in batch mode (nothing moves)"
            exec "$KPY" "$REPO/tests/run_tests.py" --klipper "$KLIPPER_DIR" \
                --config "$REPO/tests/printer.cfg" --dict "$DICT" "$REPO"/tests/scenarios/*.test ;;
        uninstall)
            do_uninstall; exit $? ;;
    esac

    # ================================================================ install
    [ -d "$KLIPPER_DIR/klippy/extras" ] || die "Klipper not found in $KLIPPER_DIR: pass --klipper-dir DIR"
    if printing; then die "a print is running: install when the printer is idle"; fi
    info "printer configuration: $CONFIG_DIR"
    local NEED_KLIPPER=0 NEED_MOONRAKER=0
    [ "${NPZ_UPDATED:-0}" = 1 ] && NEED_KLIPPER=1 && info "module updated"

    # 1) the module, linked into Klipper
    if [ -e "$LINK" ] && [ ! -L "$LINK" ]; then
        die "$LINK exists and is not a link: move it away first"
    fi
    if [ "$(readlink -f "$LINK" 2>/dev/null)" != "$REPO/no_print_zone.py" ]; then
        ln -sfn "$REPO/no_print_zone.py" "$LINK"
        NEED_KLIPPER=1
        info "module linked: $LINK"
    else
        info "module already linked"
    fi
    if [ -d "$KLIPPER_DIR/.git" ]; then     # keep Klipper's git tree clean for Moonraker
        local EXC=$KLIPPER_DIR/.git/info/exclude
        mkdir -p "$(dirname "$EXC")"
        grep -qxF "$ENTRY" "$EXC" 2>/dev/null || echo "$ENTRY" >> "$EXC"
    fi

    # 2) configuration: a starter file, only if no zone is configured yet
    local FOUND
    if FOUND=$($PY "$SETUP" find-zones "$PRINTER_CFG"); then
        info "existing no_print_zone configuration kept:"
        echo "$FOUND" | sed "s|$CONFIG_DIR/|    |"
    else
        if [ ! -f "$CONFIG_DIR/no_print_zone.cfg" ]; then
            cp "$REPO/examples/starter.cfg" "$CONFIG_DIR/no_print_zone.cfg"
            info "created no_print_zone.cfg (no zone yet: the module does nothing until you add one)"
        fi
        if [ "$($PY "$SETUP" add-include "$PRINTER_CFG" no_print_zone.cfg "$BACKUP_DIR")" = added ]; then
            info "printer.cfg now includes no_print_zone.cfg (backup in $BACKUP_DIR)"
        fi
        NEED_KLIPPER=1
    fi

    # 3) updates from Moonraker (the web interface shows them)
    if [ "$NO_MOONRAKER" = 0 ] && [ -f "$MOONRAKER_CONF" ] && [ -d "$REPO/.git" ]; then
        if grep -q '^\[update_manager no_print_zone\]' "$MOONRAKER_CONF"; then
            info "Moonraker updates already configured"
        elif ask "Show no_print_zone updates in the web interface (Moonraker update manager)?" y y; then
            local ORIGIN RPATH=$REPO
            ORIGIN=$(git -C "$REPO" remote get-url origin 2>/dev/null || echo "$REPO_URL")
            [[ "$RPATH" == "$HOME"/* ]] && RPATH="~/${RPATH#"$HOME"/}"
            $PY "$SETUP" add-update-manager "$MOONRAKER_CONF" "$RPATH" "$ORIGIN" "$BACKUP_DIR" > /dev/null
            info "added [update_manager no_print_zone] to moonraker.conf"
            NEED_MOONRAKER=1
        fi
    fi

    # 4) Klipper version: the module hooks into Klipper internals
    local KV TV
    KV=$(git -C "$KLIPPER_DIR" describe --always --tags --long --dirty 2>/dev/null)
    TV=$(cat "$REPO/TESTED_KLIPPER_VERSION" 2>/dev/null)
    if [ -n "$KV" ] && [ -n "$TV" ] && [ "${KV%-dirty}" != "$TV" ]; then
        warn "tested on Klipper $TV, yours is $KV. The module hooks into Klipper internals:"
        warn "to check it on your version run: $REPO/install.sh --test"
    fi

    # 5) restarts (only what is needed, never during a print)
    local LOADED
    LOADED=$($PY "$SETUP" npz-loaded "$MOONRAKER")
    if [ "$NEED_KLIPPER" = 0 ] && [ "$LOADED" = no ]; then
        NEED_KLIPPER=1      # configured but not loaded yet
    fi
    local KLIPPER_OK=1
    if [ "$NEED_KLIPPER" = 1 ]; then
        restart klipper "Klipper must restart to load the module" || KLIPPER_OK=0
    elif [ "$LOADED" = yes ]; then
        info "Klipper already runs this module: no restart needed"
    else
        info "nothing changed (Moonraker not reachable: cannot check that Klipper runs the module)"
    fi
    [ "$NEED_MOONRAKER" = 1 ] && restart moonraker "Moonraker must restart to show the updates"

    echo
    info "done. Zones are defined in: $(echo "${FOUND:-$CONFIG_DIR/no_print_zone.cfg}" | head -1 | cut -d: -f1)"
    echo "    add zones with the wizard:  $REPO/install.sh --wizard"
    echo "    documentation:              $REPO/README.md"
    if [ "$NO_WIZARD" = 0 ] && [ -n "$TTY" ] && [ "$YES" = 0 ] && [ "$KLIPPER_OK" = 1 ]; then
        if ask "Define a zone now with the wizard?" n n; then
            exec $PY "$SETUP" wizard --config-dir "$CONFIG_DIR" --moonraker "$MOONRAKER" < "$TTY"
        fi
    fi
}

do_uninstall() {
    info "removing no_print_zone from $CONFIG_DIR"
    if [ -L "$LINK" ]; then rm -f "$LINK"; info "link removed: $LINK"; fi
    local EXC=$KLIPPER_DIR/.git/info/exclude
    if [ -f "$EXC" ] && grep -qxF "$ENTRY" "$EXC"; then
        grep -vxF "$ENTRY" "$EXC" > "$EXC.tmp"; mv "$EXC.tmp" "$EXC"
    fi
    if [ "$($PY "$SETUP" remove-include "$PRINTER_CFG" "$BACKUP_DIR")" = removed ]; then
        info "include of no_print_zone.cfg removed from printer.cfg (the file itself is kept)"
    fi
    local MR=0
    if [ -f "$MOONRAKER_CONF" ] && [ "$($PY "$SETUP" remove-update-manager "$MOONRAKER_CONF" "$BACKUP_DIR")" = removed ]; then
        info "[update_manager no_print_zone] removed from moonraker.conf"; MR=1
    fi
    local LEFT
    if LEFT=$($PY "$SETUP" find-zones "$PRINTER_CFG"); then
        warn "these files still define no_print_zone sections, and without the module"
        warn "Klipper will NOT start. Remove or comment them, then restart Klipper:"
        echo "$LEFT" | sed 's/^/    /'
    else
        restart klipper "Klipper must restart to unload the module"
    fi
    [ "$MR" = 1 ] && restart moonraker "Moonraker must restart"
    info "the repository is still in $REPO (delete it with: rm -rf $REPO)"
    info "backups of the edited files: $BACKUP_DIR"
}

main "$@"
