# klipper-no-print-zone

Keep-out zones, reserved zones and guided paths ("rails") for
[Klipper](https://www.klipper3d.org/), enforced on **every** toolhead move.

Purge buckets, brushes, bed clips, docks, a filament cutter lever pressed
against the frame: things the toolhead must never hit, or may reach only in a
precise way. Slicer exclusions only cover printed objects. Macros, jogs from
the web interface and start G-code can still drive the toolhead into them.
Klipper has no native keep-out zones. This module adds them.

```ini
[no_print_zone bucket]        # purge bucket 12 mm tall: fly over it,
type: reserved                # get in only after NPZ_OPEN ZONE=bucket
x_min: 230
x_max: 300
y_min: 285
z_max: 12
```

**Install** with one line ([below](#install)). **Configure** with the
[configuration guide](docs/configuration.md), or let the wizard write the
zones for you.

## Features

- **Any number of zones**, each a box aligned to the axes, in X, Y and **Z**.
  Bounds you leave out are unbounded.
- Three types:
  - `keepout`: never entered;
  - `reserved`: entered only between `NPZ_OPEN` and `NPZ_CLOSE` (purge lines,
    nozzle wipe);
  - `rails`: the toolhead may only travel along declared straight paths. Use
    it for docks, cutter levers, anything reached in one precise way.
- **Z**: a zone limited in height can be flown over but not entered from above.
  Inside a `rails` zone, pure Z moves follow a policy: `deny`, `allow`, or
  `idle` (only with no print running and Z homed). Klipper's own travel
  limits are always checked first.
- Checks every move before it is queued: G-code files, macros, web interface
  jogs, arcs, bed mesh segments, probing travel. A refused move never starts.
- **During a print, moves go around the zones** instead of stopping the
  print: the part of a move that would run inside a closed zone is replaced
  by a path along its faces. A square printed at the edges of the bed comes
  out with a notch shaped like the zone. The zone faces are extruded once per
  layer, so infill lines crossing a zone do not pile material on its edge.
  Outside a print, in a paused print and during `NPZ_PREP`, a move into a
  closed zone is still an error ([details](docs/configuration.md#print-moves-going-around-the-zones)).
- Speed and acceleration limits inside a zone.
- **Homing lock**: while a `rails` zone holds the toolhead (lever pressed
  against the frame, tool docked), `G28` and the Z hop of `safe_z_home` are
  refused. The mark is saved to disk and survives restarts and power cycles.
- Suspends filament runout sensors while working in a zone. A `PAUSE`
  triggered there would park along a forbidden path.
- `FORCE_MOVE` on the kinematic steppers is blocked, because it bypasses every
  check. `STEPPER_BUZZ` is blocked while a zone holds the toolhead.
- Status variables for macros and web interfaces.
- A dry-run test suite that runs the **real Klipper** in batch mode, with no
  hardware and nothing moving.

## Limits: read this

- The module checks the **commanded** position. Lost steps, a wrong homing or
  `SET_KINEMATIC_POSITION` are not covered: keep safety margins in the bounds.
- It is a software guard, not a substitute for mechanical design.
- A print that went around a zone **differs from the G-code file** there.
  The console says so at the first rerouted move of each zone. Exclude the
  zones in the slicer anyway (`bed_exclude_area`).
- It hooks into Klipper internals: `kinematics.check_move`,
  `toolhead.drip_move`, `kinematics.set_position`, the move transform chain
  of `gcode_move` (like `bed_mesh`) and the homing events. It
  was written and tested on **Klipper v0.13.0-743**. Set
  `tested_klipper_version`, and run the tests again after every Klipper
  update. A hook that silently stops being called is the real risk, and only
  the tests find it.

## Install

Log in to the Klipper host (for example `ssh pi@<printer address>`) as the
user that runs Klipper, and paste one line:

```sh
curl -fsSL https://raw.githubusercontent.com/Longo-Al/Klipper-no-print-zone/main/install.sh | bash
```

The installer:

1. downloads the repository into `~/klipper-no-print-zone`;
2. links `no_print_zone.py` into Klipper's extras and keeps Klipper's git
   tree clean;
3. if no zone is configured yet, creates `no_print_zone.cfg` next to
   `printer.cfg` and includes it. Without zones the module does nothing;
4. asks whether to show updates in the web interface (Moonraker update
   manager);
5. restarts Klipper only if needed and **never during a print**, then checks
   that it comes back ready;
6. offers the zone wizard.

Every file it edits is backed up first, in `~/printer_data/backup/no_print_zone/`.
It finds the printer by itself, also with several printers on one host (it
asks). See `install.sh --help` for the options.

- **Update**: run the same line again. Klipper is restarted only if the
  module itself changed. Or use the web interface, if you enabled updates
  there.
- **Uninstall**: `~/klipper-no-print-zone/install.sh --uninstall`. Your zone
  files are kept. If zones are still defined in your config, it tells you
  where, because Klipper would not start without the module.
- **Check on your Klipper version**: `~/klipper-no-print-zone/install.sh --test`
  builds an MCU dictionary and runs the dry tests. It takes a few minutes and
  nothing moves.

### Define zones: the wizard

```sh
~/klipper-no-print-zone/install.sh --wizard
```

The wizard **never moves the printer**. You move the toolhead from the web
interface, press Enter, and it reads the position:

- **area**: two opposite corners. A side at the end of travel can reach the
  frame;
- **height**: for a low obstacle, the nozzle just above its top, so the
  toolhead may fly over it;
- **margin** added around the zone;
- **rails**: for each one, its outer end and then its inner end;
- speed, homing lock, Z policy and runout sensors.

It checks the zone with the module's own code, writes the section, restarts
Klipper and confirms that the zone is active. If Klipper does not start, it
offers to remove the zone again. You can also type coordinates instead of
reading them, and remove zones.

### Manual install

```sh
cd ~
git clone https://github.com/Longo-Al/Klipper-no-print-zone.git klipper-no-print-zone
./klipper-no-print-zone/install.sh
```

Or only link the file, write the zones yourself (see [`examples/`](examples/))
and restart the **service** with `sudo systemctl restart klipper`.

A `RESTART` / `FIRMWARE_RESTART` command does **not** reload Python modules:
after changing `no_print_zone.py`, restart the service.

The update manager entry the installer adds to `moonraker.conf`:

```ini
[update_manager no_print_zone]
type: git_repo
path: ~/klipper-no-print-zone
origin: https://github.com/Longo-Al/Klipper-no-print-zone.git
primary_branch: main
managed_services: klipper
```

If Klipper is reinstalled, run the installer again. Until you do, Klipper
refuses to start (unknown section), so it fails safe.

## Configuration

**Full guide: [docs/configuration.md](docs/configuration.md).** It covers
step by step setup, every option, rails, Z, macros and status variables,
complete examples, and every error message with its fix.

Zones go in `no_print_zone.cfg`, which the installer creates next to
`printer.cfg`. That file is a commented reference of every option. Use one
section per zone:

```ini
[no_print_zone purge]          # strip for purge lines: only from your macros
type: reserved
x_max: 15                      # no x_min: it reaches the end of travel
y_min: 22
y_max: 340

[no_print_zone clips]          # bed clips 3 mm tall: fly over, never through
type: keepout
y_max: 12
z_max: 4

[no_print_zone dock]           # reached only along fixed paths
type: rails
x_min: 130
x_max: 170
y_max: 20
rails:
    X150 Y30:5                 # at X150, inward from Y30 to Y5
lock_homing: True              # no G28 while the toolhead is in the dock
z_moves: deny
```

The rules in short:

- Coordinates are the **toolhead** position (`GET_POSITION`, line
  `toolhead:`). Measure with the printer homed and add a margin.
- A bound you leave out is unbounded. The faces are outside: with
  `x_max: 11`, X 11 is allowed.
- The whole move is checked, not only its end: a diagonal that cuts a corner
  is refused.
- `keepout` is never entered. `reserved` is entered between
  `NPZ_OPEN ZONE=name` and `NPZ_CLOSE`. `rails` is entered only along its
  rails: inward needs `NPZ_OPEN`, outward is always allowed.
- A move that starts inside a keepout/reserved zone may leave it.
- During a print, moves into closed zones go around them
  (`print_moves: reroute`, the default); `print_moves: stop` stops the print
  instead.
- After a change, restart the Klipper service. The wizard does it for you.

| command | |
|---|---|
| `NPZ_STATUS [ZONE=a,b]` | zones, state, toolhead position |
| `NPZ_OPEN ZONE=a[,b]` | open zones (not during a print, except inside `NPZ_PREP`) |
| `NPZ_CLOSE [ZONE=a,b]` | close zones (all by default), release the ones the toolhead left |
| `NPZ_PREP ENABLE=1\|0` | preparation window in `START_PRINT` |
| `NPZ_CLEAR [CONFIRM=1]` | release the homing lock after freeing the toolhead by hand |

Guide sections:

- [Step by step](docs/configuration.md#step-by-step)
- [Choosing the zone type](docs/configuration.md#choosing-the-zone-type)
- [Option reference](docs/configuration.md#reference-no_print_zone-name)
- [Rails in detail](docs/configuration.md#rails-in-detail)
- [Z](docs/configuration.md#z-height-limits-and-z-moves)
- [Print moves: going around the zones](docs/configuration.md#print-moves-going-around-the-zones)
- [Macros](docs/configuration.md#using-zones-in-macros)
- [Status variables](docs/configuration.md#status-variables)
- [Errors when Klipper starts](docs/configuration.md#errors-when-klipper-starts)
- [Errors on a move](docs/configuration.md#errors-on-a-move)
- [Pitfalls and FAQ](docs/configuration.md#pitfalls-and-faq)

Examples: [`examples/basic.cfg`](examples/basic.cfg) (bucket and brush,
clips, dock), and
[`examples/voron-jabberwocky-cutter.cfg`](examples/voron-jabberwocky-cutter.cfg).
The second is a real Voron 2.4 whose JabberWocky cutter lever is pressed
against the front left frame corner, with its cut and purge macros.

## Tests

Unit tests of the rules, of the installer helpers and of the wizard (with a
fake Moonraker). No Klipper needed:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
```

Installer in a sandbox: install, a second run that changes nothing, uninstall
back to the original files, and the one-line path. With a Klipper tree and an
MCU dictionary it also loads the generated configuration in Klipper:

```sh
tests/test_install.sh [~/klipper tests/.build/stm32f446.dict]
```

The documentation itself: every zone example in this README and in
[docs/configuration.md](docs/configuration.md) is loaded by Klipper, and the
macro examples of the guide are run against their zones:

```sh
~/klippy-env/bin/python tests/test_docs.py --klipper ~/klipper --dict tests/.build/stm32f446.dict
```

Dry-run tests with the real Klipper in batch mode. Klipper reads G-code from
a file and no board is involved. It needs the data dictionary of the MCU
named in the config, which `make` produces:

```sh
DICT=$(tests/build_dict.sh)          # STM32F446, for tests/printer.cfg
~/klippy-env/bin/python tests/run_tests.py --klipper ~/klipper \
    --config tests/printer.cfg --dict $DICT tests/scenarios/*.test
```

Test **your own** configuration the same way. The runner copies it to
`/tmp`, so nothing on the printer changes. Pass one `--dict` per MCU, using
`NAME=path` for `[mcu NAME]`. Then write scenarios for your machine: the
format is documented at the top of `tests/run_tests.py`, and
`tests/scenarios/` has examples.

```sh
~/klippy-env/bin/python tests/run_tests.py --klipper ~/klipper \
    --config ~/printer_data/config/printer.cfg \
    --dict ~/klipper/out/klipper.dict my_tests.test
```

## License

GNU GPLv3, like Klipper. See [LICENSE](LICENSE).
