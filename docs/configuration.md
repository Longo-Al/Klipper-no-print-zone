# Configuring no_print_zone

This page explains how to describe the zones of **your** printer: what each
option does, what values it accepts, and what the module does in every case.
If you only want to get started, follow the [step by step](#step-by-step)
section, or let the wizard write the zone for you
(`~/klipper-no-print-zone/install.sh --wizard`).

- [Where the configuration lives](#where-the-configuration-lives)
- [Step by step](#step-by-step)
- [Coordinates and geometry](#coordinates-and-geometry)
- [Choosing the zone type](#choosing-the-zone-type)
- [Reference: `[no_print_zone]`](#reference-no_print_zone)
- [Reference: `[no_print_zone NAME]`](#reference-no_print_zone-name)
- [Rails in detail](#rails-in-detail)
- [Z: height limits and Z moves](#z-height-limits-and-z-moves)
- [Opening and closing zones](#opening-and-closing-zones)
- [Homing lock and the state file](#homing-lock-and-the-state-file)
- [Using zones in macros](#using-zones-in-macros)
- [Status variables](#status-variables)
- [Complete examples](#complete-examples)
- [Errors when Klipper starts](#errors-when-klipper-starts)
- [Errors on a move](#errors-on-a-move)
- [Pitfalls and FAQ](#pitfalls-and-faq)

---

## Where the configuration lives

The installer creates `no_print_zone.cfg` next to `printer.cfg` and adds this
line to `printer.cfg`:

```ini
[include no_print_zone.cfg]  # added by the no_print_zone installer
```

You can keep the zones anywhere else, in any file included by `printer.cfg`.
The installer and the wizard look for them in every included file.

The file has two kinds of sections:

```ini
[no_print_zone]            # global options: once (optional)

[no_print_zone purge]      # one section per zone, as many as you need
type: reserved
...

[no_print_zone clip]
type: keepout
...
```

Next to them you will usually put the macros that use the zones (purge line,
wipe, cut...). They are ordinary `[gcode_macro]` sections.

**After every change, restart the Klipper service**:

```sh
sudo systemctl restart klipper
```

You can also use the power menu of Mainsail/Fluidd and restart the `klipper`
service, or let the wizard do it. The `RESTART` and `FIRMWARE_RESTART` commands
reload the configuration but **not** the module code. That only matters after
an update of `no_print_zone.py`, but restarting the service is always correct.

A zone does nothing until Klipper has been restarted with it. With no zone at
all, the module does nothing.

---

## Step by step

### 1. Decide what to protect and how

For each obstacle, ask yourself: may the toolhead **ever** go there?

- **Never**: the obstacle is a `keepout` zone (bed clips, a sensor, a part
  of the frame, cables).
- **Only from my macros**: a `reserved` zone (purge bucket, brush, the strip
  where purge lines are printed).
- **Only in one precise way**: a `rails` zone. Examples are a dock, or a
  filament cutter lever pushed against the frame, where a move from the
  wrong side breaks something.

More in [Choosing the zone type](#choosing-the-zone-type).

### 2. Measure the zone on the printer

You need the **toolhead coordinates** of the edges of the obstacle.

1. Home the printer (`G28`). Positions read from an unhomed printer are
   meaningless. After the motors turn off (`M84`, idle timeout) Klipper keeps
   showing the last position, but it is no longer true.
2. Jog the toolhead, slowly and with Z raised, until the nozzle is at the
   edge of the obstacle. Do it on each side that matters.
3. Read the position with `GET_POSITION`: use the `toolhead:` line. The web
   interface shows the same values in the toolhead panel. `M114` gives the
   G-code position, which differs as soon as there is a G-code offset or a
   bed mesh.
4. Add a **safety margin** on every side: 1–2 mm is typical. Add more if an
   axis homes sensorless, since its position repeats less precisely.
5. For an obstacle the nozzle may fly over, also note the Z just above its
   top.

### 3. Write the section

```ini
[no_print_zone bucket]
type: reserved
x_min: 228        # measured 230, minus 2 mm of margin
x_max: 302
y_min: 283
z_max: 14         # top of the brush at 12, plus 2 mm
```

Leave out the bounds where the zone reaches the end of travel. With no
`y_max` above, the zone goes up to the back of the machine, so nothing can
slip past it there.

### 4. Restart and check

Restart the Klipper service, then run `NPZ_STATUS` in the console:

```
no_print_zone: open=- prep=0 occupied=-
toolhead X150.00 Y150.00 Z10.00, outside all zones
zone bucket [reserved] 228<X<302 Y>283 Z<14
no runout sensor managed
```

If Klipper does not start, the error says which option is wrong. See
[Errors when Klipper starts](#errors-when-klipper-starts).

### 5. Try it, safely

With Z raised and a low speed, try a move that should be refused:

```
G1 Z30 F600
G1 X250 Y290 F1200      # above the bucket (Z 30 > 14): allowed
G1 Z10 F300             # down into it while closed: refused
```

The refused move does not start, and the console shows why. Nothing is
checked physically: the module trusts the position that Klipper believes. So
test the first moves with a hand near the emergency stop.

### 6. Open the zone in your macros

Reserved and rails zones are used by macros:

```ini
[gcode_macro WIPE_NOZZLE]
gcode:
  NPZ_OPEN ZONE=bucket
  ...the wipe moves...
  NPZ_CLOSE ZONE=bucket
```

See [Using zones in macros](#using-zones-in-macros).

### 7. Tell the slicer

Also mark the zones as excluded in the slicer: `bed_exclude_area` in
OrcaSlicer and PrusaSlicer. If an object lands in a zone anyway, the print
stops with an error at the first move into it, instead of printing into the
obstacle.

---

## Coordinates and geometry

**Toolhead coordinates.** Bounds are compared with the position Klipper
gives as `toolhead.position` (the `toolhead:` line of `GET_POSITION`). This
position already includes G-code offsets (`SET_GCODE_OFFSET`) and the bed
mesh correction. `G92` does not change it.

**A zone is a box aligned to the axes**, with six optional bounds:
`x_min x_max y_min y_max z_min z_max`.

- A bound you leave out is **unbounded**: the zone extends to the end of
  travel, and beyond.
- At least one bound is required.
- For every axis, `min` must be below `max`.

**The faces are outside.** The zone is the space strictly between its
bounds. With `x_max: 11`:

- X 10.99 is inside;
- X 11 is outside, so a move may run exactly along the face.

**The whole move is checked, not only where it ends.** A straight move from
A to B is refused if any point of the segment touches the zone. A diagonal
that cuts a corner is caught:

```
   Y
   ^
   |  A (2,30)
   |   \
   |  +-\------+
   |  |  \ zone|        A -> B crosses the corner of the zone: refused,
   |  |   \    |        even though A and B are both outside.
   |  +----\---+
   |        B (30,2)
   +-------------------> X
```

**Arcs and bed mesh.** `G2`/`G3` arcs and moves split by the bed mesh reach
the module as short straight segments, each checked.

**What is not checked.** These moves use fictitious coordinates and are not
checked:

- homing and probing moves;
- the short retract after an endstop is hit.

The module only knows the **commanded** position. Missed steps and a wrong
homing are invisible to it, and so is a `SET_KINEMATIC_POSITION` with wrong
values. That is what the margins are for.

**Unknown position.** Before an axis is homed, every zone counts as covering
that whole axis.

- An X move with Y not homed is refused if its X range overlaps a zone.
- Exception for **Z**: `keepout` and `reserved` zones limited in Z are
  ignored until Z is homed. Klipper itself allows X/Y moves before Z homing,
  and `safe_z_home` travels to its probing point with Z unknown, often over a
  purge bucket in the homing corner. Set `unknown_z: full_height` in
  `[no_print_zone]` to count them as full height instead. `rails` zones
  always count as full height.
- A move that starts inside a zone may leave it (see the table below), but
  only if the toolhead is **certainly** inside. With an axis unknown it may
  not be.

**Several zones.** Zones are independent: a move must be allowed by **every**
zone it touches. Zones may overlap or touch. A macro that passes through two
zones must open both (`NPZ_OPEN ZONE=cutter,purge`).

---

## Choosing the zone type

| | `keepout` | `reserved` | `rails` |
|---|---|---|---|
| typical use | bed clips, sensors, frame parts | purge bucket, brush, purge strip | dock, cutter lever, wiper arm |
| entering, zone closed | refused | refused | refused |
| entering, zone open | (cannot be opened) | allowed | only inward along a rail |
| moving inside | allowed when the move started certainly inside | allowed if open, or if started certainly inside | only along a rail |
| leaving | allowed (started inside) | allowed (started inside) | only outward along a rail, always allowed |
| Z alone, inside | as any move | as any move | `z_moves` policy |
| `speed`/`accel` default | none | none | 10 mm/s, 500 mm/s² |
| `lock_homing` default | False | False | True |
| can be height-limited | yes, fly over it | yes, fly over it | yes, but always full height until Z is homed |

Guidelines:

- **Purge bucket or brush** that the nozzle can clear: `reserved` with
  `z_max`. The toolhead flies over it freely and dips into it only from your
  wipe macro.
- **Strip on the bed for purge lines**: `reserved`. No `z_max` if you want
  nothing ever printed or travelled there at low height. With a `z_max` of a
  few mm, travels above it stay allowed.
- **Bed clips, magnets, a probe dock on the bed**: `keepout` with `z_max`.
- **Something that breaks if approached from the wrong side** (a lever
  pressed against the frame, a tool dock): `rails`, with `lock_homing`.

---

## Reference: `[no_print_zone]`

All options are optional. The section itself may be left out.

```ini
[no_print_zone]
#state_file: <config folder>/no_print_zone.json
#tolerance: 0.01
#tested_klipper_version:
#block_force_move: True
#unknown_z: ignore
```

| option | values | default | meaning |
|---|---|---|---|
| `state_file` | path, `~` allowed | `no_print_zone.json` next to `printer.cfg` | file where the module remembers which zones hold the toolhead, so the homing lock survives restarts and power cycles. Its folder must exist. |
| `tolerance` | 0–0.5 mm | 0.01 | how close to a rail a move must start (fixed coordinate and ends of the rail). Raise it only if your moves never land exactly on the rail. |
| `tested_klipper_version` | text | none | the Klipper version (as `git describe` prints it, shown by the web interface) you ran the tests on. When Klipper runs another version, a warning appears in the web interface until you test again and update the value. |
| `block_force_move` | True/False | True | refuse `FORCE_MOVE` on the steppers of the kinematics (X, Y, Z...). `FORCE_MOVE` would move the toolhead without any check. The extruder is never blocked. |
| `unknown_z` | `ignore`, `full_height` | `ignore` | `keepout`/`reserved` zones with a Z bound, while Z is not homed: ignore them, or count them as full height (stricter, may block `G28`). |

---

## Reference: `[no_print_zone NAME]`

```ini
[no_print_zone NAME]
type:
#x_min:
#x_max:
#y_min:
#y_max:
#z_min:
#z_max:
#speed:
#accel:
#lock_homing:
#suspend_runout:
# rails zones only:
#rails:
#z_moves: deny
#z_tolerance: 0
```

### `NAME`

Lower case letters, digits and `_`, for example `purge`, `clip_front`,
`cutter`. It is the name used by `NPZ_OPEN ZONE=NAME`, in messages and in the
status variables. Write it in lower case: macros find the zone as
`printer["no_print_zone NAME"]` exactly as the section is written.

### `type` (required)

`keepout`, `reserved` or `rails`. See
[Choosing the zone type](#choosing-the-zone-type).

### `x_min`, `x_max`, `y_min`, `y_max`, `z_min`, `z_max`

Bounds in mm, toolhead coordinates. Each is optional. A missing bound is
unbounded. At least one bound is needed, and `min` must be below `max` on
every axis. Bounds may lie outside the travel of the axis: the part outside
simply cannot be reached.

`z_max` is the height up to which the obstacle exists: the toolhead may pass
above it. `z_min` is rarely useful: something hanging above the bed, that
the nozzle may pass under.

### `speed`, `accel`

Upper limits, in mm/s and mm/s², on every **allowed** move that touches the
zone. That includes moves crossing an open zone, and moves leaving it. When a
move touches several zones, the lowest limit wins. They never make a move
faster than requested.

Default: 10 mm/s and 500 mm/s² for `rails` zones, no limit for the others.

### `lock_homing`

True/False. Default True for `rails` zones, False for the others.

When a move ends inside such a zone, the zone is marked as **holding the
toolhead**, and that mark is saved to the state file. While it is set:

- `G28` of any axis is refused;
- the Z hop of `safe_z_home` before homing is refused;
- `STEPPER_BUZZ` on the axes is refused.

`G28` is also refused when the known position is inside such a zone. See
[Homing lock and the state file](#homing-lock-and-the-state-file).

### `suspend_runout`

Comma separated names of `[filament_switch_sensor]` sections. The sensors
are disabled while the zone is open or holds the toolhead, then restored to
their previous state.

The reason: a runout triggers `PAUSE`, and a pause parks the toolhead with a
straight move. From inside a zone that move may be refused, and the print
ends up stuck in an error. Use it for zones where filament moves (a cutter)
or that the toolhead visits during a print.

Klipper refuses to start if a name does not exist.

### `rails` (`rails` zones only, required)

One rail per line, or comma separated:

```ini
rails:
    X10.5 Y27:0
    Y0 X10.5:0
```

See [Rails in detail](#rails-in-detail).

### `z_moves` (`rails` zones only)

What a move of **Z alone** may do when it touches the zone:

| value | Z alone inside the zone |
|---|---|
| `deny` (default) | always refused |
| `idle` | allowed only with no print running or paused, and with Z really homed |
| `allow` | always allowed |

"Really homed" means a real homing or probing of Z. The moment when
`safe_z_home` marks Z as homed only to lift it before homing does not count.
Klipper's travel limits (`position_min`/`position_max`) are always checked
first. A move that changes X or Y **and** Z inside a rails zone is never
allowed (see `z_tolerance`).

### `z_tolerance` (`rails` zones only)

0–1 mm, default 0. How much Z may change during a move along a rail. You only
need it if a bed mesh is active over the zone, because the mesh then adds
small Z changes to X/Y moves. Outside the mesh area, Klipper keeps the edge
correction constant and no tolerance is needed.

---

## Rails in detail

A `rails` zone can only be travelled along straight paths that you declare.
Use it for anything that must be approached in one exact way.

### Syntax

```
X10.5 Y27:0
│     └──── the moving axis, from 27 (outer end) to 0 (inner end)
└────────── a fixed coordinate: the rail runs at X 10.5
```

- Exactly **one** axis has a range `from:to`. That is the direction of the
  rail, and it must be **X or Y**.
- The **first** value is the **outer** end, where you come from. The
  **second** is the **inner** end, deep in the zone.
- The other horizontal axis is normally fixed (`X10.5`). An axis you do not
  write is free, but must stay constant during the move. Z is normally not
  written: the rail works at any height, as long as Z does not change while
  moving.
- Values are in mm; negative values are fine (`Y-5:-20`).

### Direction

```
   Y
   27 ┤ outer end           moving 27 -> 0 is INWARD:
      │   ↓  ↑                 allowed only while the zone is open (NPZ_OPEN),
   22 ┤ ─ ─│─ ─ zone face      start and end must lie on the rail (0..27).
      │   ↓  ↑              moving 0 -> 27 is OUTWARD:
      │   ↓  ↑                 always allowed, also with the zone closed,
    0 ┤ inner end              from anywhere on the rail line not deeper
      └───┼──────── X          than the inner end, even past the outer end.
         10.5
```

- **Inward**: the zone must be open, and the move must start **and** end
  within the rail, between its two ends. A move from Y 30 to Y 0 along X 10.5
  is refused because it starts beyond the outer end, at 27.
- **Outward**: always allowed, so the toolhead can always come out the way
  it went in. It may start anywhere on the rail line that is not deeper than
  the inner end, and it may go past the outer end, out of the zone. Jogging
  back in steps (1 mm, 10 mm) works.

A move is "on a rail" if it changes only the rail's axis, starts within
`tolerance` of the fixed coordinates, and keeps every other axis constant
(Z within `z_tolerance`).

### Chained rails

Several rails can connect, each starting where the previous one ends. The
JabberWocky cutter on a Voron:

```ini
rails:
    X10.5 Y27:0      # 1: down along X 10.5 next to the frame, to Y 0
    Y0 X10.5:0       # 2: at Y 0, press the lever in -X down to X 0
```

```
   Y
   27 ┼   ↓ rail 1
   22 ┤ ─ ┼ ─ ─ ─ ─ zone face (y_max 22)
      │   ↓
    0 ┤ ←─┘ rail 2 (press -X, release +X)
      └─┼─┼─────── X
        0 10.5    11 = zone face (x_max 11)
```

The cut is: rail 1 inward, rail 2 inward, rail 2 outward, rail 1 outward.
Anywhere else in the zone **nothing** moves. That includes diagonals,
sideways moves, and Y moves while the lever is pressed (at X < 10.5).

### Checks when Klipper starts

- the fixed coordinates of a rail must be **strictly inside** the zone on
  their axis, otherwise the rail would never enter it;
- the inner end must be inside the zone, or on its face;
- the inner end and the fixed coordinates must lie within the travel range
  of the axes.

### When the toolhead is stuck off the rails

If the toolhead is inside a rails zone but not on a rail, every move is
refused. That happens after `SET_KINEMATIC_POSITION`, missed steps, or a
config change. Free it by hand:

1. `M84` (motors off);
2. move the toolhead out of the zone by hand, the safe way;
3. `NPZ_CLEAR CONFIRM=1`;
4. `G28`.

---

## Z: height limits and Z moves

**Height-limited zones** (`z_max`) let the toolhead fly over an obstacle:

```ini
[no_print_zone clip]
type: keepout
x_min: 280
y_max: 20
z_max: 4          # a clip 3 mm tall + 1 mm
```

- `G1 X290 Y10` at Z 10: allowed (above the zone).
- `G1 Z3` over the clip: refused (it would enter the zone from above).
- `G1 X290 Y10 Z2` from outside: refused.

**Before Z is homed**, keepout/reserved zones with Z bounds are ignored by
default (`unknown_z`). Rails zones always count as full height.

**Z inside rails zones** follows `z_moves`. Pick by asking whether the
toolhead can move vertically while it is in the zone:

- lever resting against a smooth frame post: `idle` is a good compromise;
- tool held in a dock: `deny`.

**Travel limits** of Klipper are checked before the zones: `G1 Z300` beyond
`position_max` is refused by Klipper itself ("Move out of range").

---

## Opening and closing zones

| command | what it does |
|---|---|
| `NPZ_OPEN ZONE=a[,b...]` | opens reserved/rails zones. Refused for keepout zones, with X/Y not homed, and during a print (except between `NPZ_PREP ENABLE=1` and `ENABLE=0`). |
| `NPZ_CLOSE [ZONE=a,b]` | closes the named zones, or all of them. Waits for the queued moves to finish, then releases every zone the toolhead has verifiably left. |
| `NPZ_PREP ENABLE=1` | start of the print preparation (in `START_PRINT`): `NPZ_OPEN` works even though a print is running. |
| `NPZ_PREP ENABLE=0` | end of the preparation: closes all zones; from now on `NPZ_OPEN` is refused until the print ends. |
| `NPZ_STATUS [ZONE=a,b]` | prints zones, bounds, rails, open/occupied state, toolhead position, runout sensors. |
| `NPZ_CLEAR [ZONE=a,b] [CONFIRM=1]` | releases zones that hold the toolhead, after it has left them. With X/Y not homed, `CONFIRM=1` is required: you confirm that you moved it out by hand. |

**Any G-code error closes all zones** and ends the preparation window. A
macro that fails half way therefore never leaves a zone open.

Zones open only from commands, never on their own. A zone is not "opened" by
being entered from inside.

---

## Homing lock and the state file

For zones with `lock_homing` (all rails zones by default):

- An allowed move that **ends inside** the zone marks it as holding the
  toolhead. The mark is written at once to `state_file`.
- While a zone holds the toolhead:
  - `G28` (any axis) is refused: "Homing blocked by no_print_zone";
  - the Z hop of `safe_z_home` is refused when X/Y are unknown;
  - `STEPPER_BUZZ` on the axes is refused;
  - the zone's runout sensors stay suspended.
- The mark is cleared by `NPZ_CLOSE` once the toolhead is known to be
  outside, or by `NPZ_CLEAR`.
- After a restart or power cycle the mark is still there: Klipper warns at
  startup, and homing stays blocked until you `NPZ_CLEAR CONFIRM=1`, after
  checking the toolhead by hand.
- If the state file is unreadable, or names a zone that no longer exists,
  every lock zone is considered as holding the toolhead. That is the safe
  side. Remove the file only if you are sure the toolhead is free.

Even without the mark, `G28` is refused while the **known** position is
inside a lock zone.

Homing from inside a lock zone is dangerous. Sensorless or endstop homing
would drag the toolhead sideways out of the zone, against whatever the zone
protects.

---

## Using zones in macros

### Reserved zone: purge line

```ini
[gcode_macro PURGE_LINE]
gcode:
  NPZ_OPEN ZONE=purge
  SAVE_GCODE_STATE NAME=_purge
  G90
  M83
  G1 Z5 F900
  G1 X25 Y30 F6000          # approach from outside the zone
  G1 X5 F6000               # into the zone (open)
  G1 Z0.4 F600
  G1 Y300 E30 F1200         # the line
  G1 Z2 F600
  G1 X25 F6000              # out of the zone
  M400
  NPZ_CLOSE ZONE=purge
  RESTORE_GCODE_STATE NAME=_purge
```

### Reserved zone limited in Z: wipe on a brush

```ini
[gcode_macro WIPE_NOZZLE]
gcode:
  NPZ_OPEN ZONE=bucket
  SAVE_GCODE_STATE NAME=_wipe
  G90
  G1 Z15 F1200              # above the zone (z_max 14)
  G1 X240 Y295 F12000
  G1 Z5 F1200               # down into it: allowed, the zone is open
  G1 X290 F6000
  G1 X240 F6000
  G1 Z15 F1200
  G1 Y270 F12000            # away
  RESTORE_GCODE_STATE NAME=_wipe
  NPZ_CLOSE ZONE=bucket
```

### Rails zone: follow the rails from the configuration

Read the coordinates from the zone instead of repeating them in the macro.
If you change a rail, the macro follows it:

```ini
[gcode_macro DOCK_IN_AND_OUT]
gcode:
  {% set zone = printer["no_print_zone dock"] %}
  {% set r1 = zone.rails[0] %}          # e.g. X150 Y30:5
  {% set f = zone.speed * 60 %}
  NPZ_OPEN ZONE=dock
  G90
  G1 X{r1.x} Y{r1.outer} F6000          # to the outer end of the rail
  G1 Y{r1.inner} F{f}                   # inward
  G4 P500
  G1 Y{r1.outer} F{f}                   # outward
  M400
  NPZ_CLOSE ZONE=dock
```

An inward move must **start on the rail**, between its two ends. Coming from
further away along the same line (`Y{r1.outer + 5}` to `Y{r1.inner}`) is
refused. Reach the outer end first, as above.

### START_PRINT

During a print `NPZ_OPEN` is refused. Wrap the preparation:

```ini
[gcode_macro START_PRINT]
gcode:
  ...homing, heating, mesh...
  NPZ_PREP ENABLE=1
  PURGE_LINE
  NPZ_PREP ENABLE=0       # closes everything: the print itself cannot open zones
  ...
```

### A G28 macro that does things before homing

If your `G28` macro lowers motor currents or moves before the real homing
(sensorless setups), check the lock first:

```ini
[gcode_macro G28]
rename_existing: G28.0
gcode:
  {% if printer.no_print_zone.homing_locked %}
    { action_raise_error("Homing blocked by no_print_zone: run NPZ_STATUS") }
  {% endif %}
  ...
```

### Jinja pitfalls

- Inside `{ }`, `{-x}` is **not** minus x: `{-` is whitespace control and the
  sign disappears. Write `{0 - x}`.
- Klipper cuts every config line at the first `#`, even inside strings in a
  macro. Write `'\x23'` if you need the character.

---

## Status variables

For macros (`printer.no_print_zone`, `printer["no_print_zone NAME"]`) and for
the web interface:

`printer.no_print_zone`

| field | type | |
|---|---|---|
| `zones` | list | names of all zones |
| `open_zones` | list | zones currently open |
| `occupied_zones` | list | zones holding the toolhead |
| `prep` | bool | inside `NPZ_PREP ENABLE=1` |
| `homing_locked` | bool | `G28` would be refused now |
| `runout_suspended` | list | runout sensors suspended by the module |

`printer["no_print_zone NAME"]`

| field | type | |
|---|---|---|
| `type` | text | keepout / reserved / rails |
| `min`, `max` | list of 3 | bounds X, Y, Z; `null`/`None` = unbounded |
| `speed`, `accel` | number or none | |
| `lock_homing` | bool | |
| `z_moves` | text or none | rails zones only |
| `rails` | list | each rail: `text`, `axis` (`x`/`y`), `outer`, `inner`, `x`, `y`, `z` (fixed coordinates, none if free) |
| `open` | bool | |
| `occupied` | bool | holds the toolhead |

---

## Complete examples

### Voron 2.4 with a purge bucket and brush at the back

```ini
[no_print_zone bucket]
type: reserved
x_min: 228
x_max: 302
y_min: 300          # up to the back of the frame
z_max: 14
speed: 150
```

### Bed clips

```ini
[no_print_zone clips_front]
type: keepout
y_max: 12           # the whole front edge, every X
z_max: 4
```

### A purge strip that must never be printed on

```ini
[no_print_zone purge]
type: reserved
x_max: 15           # from the left end of travel to X 15
y_min: 22
y_max: 340
```

### A filament cutter lever pressed against the frame (JabberWocky)

The complete, commented configuration of a real machine, with the cut and
purge macros:
[`examples/voron-jabberwocky-cutter.cfg`](../examples/voron-jabberwocky-cutter.cfg).

```ini
[no_print_zone cutter]
type: rails
x_max: 11
y_max: 22
rails:
    X10.5 Y27:0
    Y0 X10.5:0
speed: 10
accel: 500
lock_homing: True
z_moves: idle
suspend_runout: ToolHead_FS
```

More in [`examples/basic.cfg`](../examples/basic.cfg).

---

## Errors when Klipper starts

Klipper refuses to start and shows the message in the web interface.

| message | cause and fix |
|---|---|
| `Section 'no_print_zone ...' is not a valid config section` / `Unable to load module 'no_print_zone'` | the module is not linked into Klipper: run the installer again |
| `section must be named [no_print_zone NAME]` | the name has characters other than letters, digits, `_` |
| `Choice '...' for option 'type' ... is not a valid choice` | `type` must be keepout, reserved or rails |
| `Option 'type' in section ... must be specified` | `type` is missing |
| `at least one bound (x_min ... z_max) is needed` | the zone has no bound at all |
| `x_min must be below x_max` (or y, z) | swapped or equal bounds |
| `Option 'rails' is not valid in section ...` | `rails`, `z_moves`, `z_tolerance` are for `type: rails` only |
| `Option 'rails' in section ... must be specified` | a rails zone without rails |
| `rail '...': '...' is not <axis><value> or <axis><from>:<to>` | typo in a rail (use `X10.5`, `Y27:0`, no spaces inside a token) |
| `only one axis may have a range` / `one axis needs a range` | each rail has exactly one `from:to` |
| `a rail runs along X or Y` | the range is on Z: rails are horizontal, Z alone follows `z_moves` |
| `X.. is outside the zone, the rail would never enter it` | the fixed coordinate is not strictly inside the zone |
| `the inner end Y.. must be inside the zone` | the second value of the range is outside the zone |
| `rail '...' leaves the travel range of Y` | the rail goes beyond `position_min`/`position_max` |
| `suspend_runout: no filament_switch_sensor named '...'` | wrong sensor name (it is the part after `filament_switch_sensor`) |
| `Option '...' is not valid in section ...` | misspelled option |

---

## Errors on a move

The move does not start. During a print, the print stops.

| message (shortened) | meaning | what to do |
|---|---|---|
| `enters keepout zone 'X'` | the move touches a keepout zone | it is never allowed: go around it or above it |
| `enters reserved zone 'X', which is closed` | entering a reserved zone without `NPZ_OPEN` | open it in your macro; a slicer object there needs `bed_exclude_area` |
| `follows rail '...' into zone 'X', which is closed` | an inward rail move without `NPZ_OPEN` | `NPZ_OPEN ZONE=X` first |
| `refused in rails zone 'X': only moves along the rails` | inside or into a rails zone, not along a rail | the message lists the rails and the way out |
| `... The move also changes Z` | a rail move with a Z change | keep Z constant; with a bed mesh over the zone, set `z_tolerance` |
| `Z move ... (z_moves: deny)` | Z alone inside a rails zone that forbids it | leave the zone along a rail first |
| `Z move ... allowed only while no print is running or paused` | `z_moves: idle` during a print | |
| `Z move ... Z is not homed` | `z_moves: idle` with Z unknown, often the Z hop of `G28` | leave the zone along a rail, then home |
| `may enter rails zone 'X' but the X/Y position is unknown` | a move near a rails zone before homing X and Y | home X and Y first |
| `Z move refused: the X/Y position is unknown and zone(s) X may still hold the toolhead` | after a restart, a zone is still marked: the Z hop before homing is refused | motors off, free the toolhead, `NPZ_CLEAR CONFIRM=1` |
| `Homing blocked by no_print_zone: ...` | a zone holds the toolhead, or the toolhead is inside a lock zone | leave along the rails and `NPZ_CLOSE`, or free it by hand and `NPZ_CLEAR CONFIRM=1` |
| `FORCE_MOVE on stepper_x is blocked` | `block_force_move` | set `block_force_move: False` only if you really need it |
| `NPZ_OPEN: zones cannot be opened during a print` | `NPZ_OPEN` in the print file or a macro called during the print | use `NPZ_PREP` in START_PRINT |

---

## Pitfalls and FAQ

**I changed the config and nothing happened.** Restart the Klipper
**service**. `RESTART` is enough for config changes but not for module
updates. The service restart always works.

**The toolhead cannot leave a rails zone with the zones closed.** The way
out along a rail is always allowed, but the move may then enter **another**
zone that is closed. On the Voron example, climbing out of the cutter zone
along X 10.5 enters the purge strip above Y 22. Stop on the face (Y 22), then
move sideways, or open the other zone too.

**A zone blocks G28.** Look at the path of `G28`. With `safe_z_home`, the
toolhead travels from the homing corner to `home_xy_position` in a straight
line. A keepout/reserved zone **without** a Z bound on that line refuses the
move. Give the zone a `z_max`, or move `home_xy_position`.

**Margins.** The module checks the position Klipper believes. Belts, missed
steps and sensorless homing make the real one differ by a fraction of a mm,
or more. Measure, then add a margin.

**Faces.** `x_max: 11` means X 11 is outside. If the edge itself is
dangerous, put the face beyond it.

**Bed mesh.** It changes Z during X/Y moves only inside the mesh area. A
rails zone over the mesh area needs `z_tolerance`.

**Slicer objects in a zone.** The print stops at the first move into the
zone. Exclude the zones in the slicer.

**PAUSE inside a zone.** A pause parks with a straight move, which may be
refused from inside a zone. Use `suspend_runout` for zones visited during a
print, and do not pause there.

**What the module does not see.** `SET_KINEMATIC_POSITION` (it trusts the
new position), missed steps, a wrong homing. The real movement of
`STEPPER_BUZZ` is also unchecked, but it is refused while a zone holds the
toolhead.

**Klipper updates.** The module hooks into Klipper internals. Set
`tested_klipper_version`, and after an update run
`~/klipper-no-print-zone/install.sh --test` before printing.
