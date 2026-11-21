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
  During a print, the print stops with the error.
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
- It hooks into Klipper internals: `kinematics.check_move`,
  `toolhead.drip_move`, `kinematics.set_position` and the homing events. It
  was written and tested on **Klipper v0.13.0-743**. Set
  `tested_klipper_version`, and run the tests again after every Klipper
  update. A hook that silently stops being called is the real risk, and only
  the tests find it.

## Install

```sh
cd ~
git clone https://github.com/Longo-Al/Klipper-no-print-zone.git klipper-no-print-zone
./klipper-no-print-zone/install.sh
```

`install.sh` links `no_print_zone.py` into `~/klipper/klippy/extras/` and
hides it from Klipper's git status. Add your zones to `printer.cfg` (see
[`examples/`](examples/)), then restart the service:

```sh
sudo systemctl restart klipper
```

A `RESTART` / `FIRMWARE_RESTART` command does **not** reload Python modules:
after changing `no_print_zone.py`, restart the service.

Optional, Moonraker updates (`moonraker.conf`):

```ini
[update_manager no_print_zone]
type: git_repo
path: ~/klipper-no-print-zone
origin: https://github.com/Longo-Al/Klipper-no-print-zone.git
primary_branch: main
managed_services: klipper
```

If Klipper is reinstalled, run `install.sh` again. Until you do, Klipper
refuses to start (unknown section), so it fails safe.

## Coordinates and geometry

- Zones use **toolhead coordinates**: the position Klipper reports as
  `toolhead.position`, after G-code offsets and bed mesh compensation.
- A zone is the **open** box: its faces belong to the outside, so a move may
  run along a face. With `x_max: 11`, X 11 is outside.
- The whole segment of each move is checked, not just its end point. A
  diagonal that cuts a corner is caught.
- **Unknown position.** Before an axis is homed, a zone counts as covering
  that whole axis: an X move with Y unknown cannot touch the X range of a
  zone.
  - **Z not homed.** Klipper allows X/Y moves before Z homing, and
    `safe_z_home` travels to its probing point with Z unknown, often over a
    purge bucket in the homing corner. So by default, `keepout` and
    `reserved` zones limited in Z (`z_min`/`z_max`) are ignored until Z is
    homed. Set `unknown_z: full_height` to count them as full height
    instead. `rails` zones always count as full height.
  - Homing and probing moves, and the retract after homing, are not checked:
    their coordinates are fictitious.

## Zone types

| type       | entering                         | a move starting inside       |
|------------|----------------------------------|------------------------------|
| `keepout`  | never                            | may leave                    |
| `reserved` | only while open (`NPZ_OPEN`)     | may leave                    |
| `rails`    | only along a rail, inward only while open | only along a rail; outward is always allowed |

### Rails

A rail is a straight path along one axis:

```
X10.5 Y27:0     at X 10.5, from Y 27 (outer end) to Y 0 (inner end)
Y0 X10.5:0      at Y 0, from X 10.5 to X 0
```

- Moving from the outer end toward the inner end is **inward**. It needs the
  zone open, and the move must start and end within the rail.
- Moving back is **outward** and always allowed. It may start anywhere on the
  rail line that is not deeper than the inner end, and it may continue past
  the outer end, out of the zone.
- Axes not written in the rail are free but must stay constant during the
  move. Z in particular: a rail move that also changes Z is refused. If a bed
  mesh is active over the zone, set `z_tolerance`.
- Anything else in a `rails` zone is refused: diagonals, moves off the rails,
  sideways moves. If the toolhead ends up off the rails, nothing moves: turn
  the motors off, free it by hand, then `NPZ_CLEAR CONFIRM=1`.

```
   Y
   ^         outer end Y27
   |   .  .  .  .|.  .  .  .  .  .     <- zone boundary y_max 22
   |             |  inward: -Y (open)
   |    zone     |  outward: +Y (always)
   |             |
   +-------------+------------------> X
   X0  <-- press  X10.5   (rail "Y0 X10.5:0": inward -X, outward +X)
```

## Configuration reference

```ini
[no_print_zone]
#state_file: <config dir>/no_print_zone.json
#   Where zones holding the toolhead are remembered across restarts.
#tolerance: 0.01
#   mm: how close to a rail a move must be.
#tested_klipper_version:
#   The Klipper version (git describe) you ran the tests on. When it
#   differs, a warning is shown until you test again and update it.
#block_force_move: True
#   Refuse FORCE_MOVE on the steppers of the kinematics.
#unknown_z: ignore
#   keepout/reserved zones limited in Z, while Z is not homed:
#   ignore | full_height (see "Unknown position").

[no_print_zone NAME]
#   NAME: letters, digits and _. Used in NPZ_OPEN ZONE=NAME.
type:
#   keepout | reserved | rails (required)
#x_min:
#x_max:
#y_min:
#y_max:
#z_min:
#z_max:
#   Bounds of the zone, toolhead coordinates. Default: unbounded.
#   At least one is required.
#speed:
#accel:
#   Limits (mm/s, mm/s^2) on every move that touches the zone.
#   Default 10 and 500 for rails zones, none otherwise.
#lock_homing:
#   Refuse homing while this zone holds the toolhead (it entered and has
#   not been released by NPZ_CLOSE/NPZ_CLEAR), or while the known position
#   is inside. Default True for rails zones, False otherwise.
#suspend_runout:
#   Comma separated filament_switch_sensor names, disabled while the zone
#   is open or holds the toolhead, then restored.
#
# rails zones only:
rails:
#   One rail per line (or comma separated), see above.
#z_moves: deny
#   Pure Z moves inside the zone: deny | idle | allow.
#   idle = only with no print running or paused, and Z homed (a real
#   homing, not the provisional Z of the safe_z_home hop).
#z_tolerance: 0
#   mm of Z change accepted on a rail move (bed mesh).
```

## Commands

| command | |
|---|---|
| `NPZ_STATUS [ZONE=a,b]` | zones, state, toolhead position, runout sensors |
| `NPZ_OPEN ZONE=a[,b]` | open zones. Refused during a print, except between `NPZ_PREP ENABLE=1` and `ENABLE=0`. Needs X/Y homed. |
| `NPZ_CLOSE [ZONE=a,b]` | close zones (all by default). Waits for the moves to finish and releases the zones the toolhead has left. |
| `NPZ_PREP ENABLE=1\|0` | print preparation window (START_PRINT): allows `NPZ_OPEN` during the print. `ENABLE=0` closes everything. |
| `NPZ_CLEAR [ZONE=a,b] [CONFIRM=1]` | release a zone holding the toolhead after it left. `CONFIRM=1` when the position is unknown (motors were off, toolhead freed by hand). |

Any G-code error closes all zones and ends the preparation window.

## Status variables

`printer.no_print_zone`: `zones`, `open_zones`, `occupied_zones`, `prep`,
`homing_locked` (true if `G28` would be refused), `runout_suspended`.

`printer["no_print_zone NAME"]`: `type`, `min`, `max` (null = unbounded),
`open`, `occupied`, `speed`, `accel`, `lock_homing`, `z_moves`, `rails`.
Each rail has `text`, `axis`, `outer`, `inner`, and `x`/`y`/`z` (fixed
coordinates or null). Macros can follow the rails instead of repeating the
numbers:

```jinja
{% set zone = printer["no_print_zone cutter"] %}
{% set down = zone.rails[0] %}
G1 X{down.x} F3000
G1 Y{down.inner} F{zone.speed * 60}
```

## Using it

- **Purge lines and wipes**: `NPZ_OPEN ZONE=...` at the start of the macro,
  `NPZ_CLOSE` at the end. In `START_PRINT`, wrap the preparation in
  `NPZ_PREP ENABLE=1` ... `NPZ_PREP ENABLE=0`.
- **Slicer**: also exclude the zones from the bed in the slicer
  (`bed_exclude_area` in OrcaSlicer/PrusaSlicer). If an object lands in a
  zone, the module stops the print at the first move into it.
- **G28 macros** that do things before homing (lower motor currents,
  sensorless): check `printer.no_print_zone.homing_locked` first.
- [`examples/basic.cfg`](examples/basic.cfg): bucket and brush limited in Z,
  bed clips, a dock with rails.
- [`examples/voron-jabberwocky-cutter.cfg`](examples/voron-jabberwocky-cutter.cfg):
  a real machine. Voron 2.4 with a JabberWocky toolhead whose cutter lever is
  pressed against the front left frame corner, with the cut and purge
  macros.

## Tests

Unit tests of the rules, no Klipper needed:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
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
