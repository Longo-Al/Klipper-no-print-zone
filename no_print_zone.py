# no_print_zone: keep-out zones, reserved zones and guided paths ("rails")
# enforced by Klipper on every toolhead move.
#
# Copyright (C) 2026  no_print_zone contributors
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# How it works (details in README.md)
# -----------------------------------
# Every toolhead move goes through kinematics.check_move() before it is
# queued: G0/G1, jogs from the web interface, macros, arc and bed mesh
# segments, travel moves of QGL/Z_TILT/BED_MESH_CALIBRATE. This module wraps
# that call and rejects any move that would violate a configured zone. A
# rejected move never starts: Klipper reports the error, and a running print
# stops.
#
# Zones are boxes aligned to the axes, in toolhead coordinates (the position
# Klipper reports as "toolhead.position"). Every bound is optional; a bound
# that is left out is unbounded. A zone is the OPEN box: its faces belong to
# the outside, so a move may run along a face.
#
#   type: keepout   never entered. A move that starts inside may leave.
#   type: reserved  entered only after NPZ_OPEN ZONE=<name>. A move that
#                   starts inside may leave.
#   type: rails     the toolhead may only travel along the declared rails.
#                   A rail is a straight path along one axis, written as
#                   "X10.5 Y27:0" (at X 10.5, from Y 27 to Y 0). Moving
#                   from the first value toward the second is INWARD and
#                   needs NPZ_OPEN; moving back is OUTWARD and is always
#                   allowed, also past the first value. Z stays constant
#                   on a rail. Pure Z moves inside follow "z_moves".
#
# An axis not homed counts as covered by every zone. Exception: keepout and
# reserved zones limited in Z are ignored while Z is not homed (unknown_z).
#
# Homing (all axes) is refused while a zone with lock_homing is marked as
# holding the toolhead, or while the known position is inside such a zone.
# The mark survives restarts and power cycles (state_file).
#
# FORCE_MOVE on the kinematic steppers is refused (it would bypass every
# check). STEPPER_BUZZ is refused while a zone holds the toolhead.
# NOT covered: SET_KINEMATIC_POSITION, lost steps. The module checks the
# COMMANDED position: keep your safety margins in the zone bounds.
import json, logging, os, re, time

EPS = 1e-6
INF = float('inf')
AXES = 'xyz'
ACTIVE_PRINT_STATES = ('printing', 'paused')
ZONE_TYPES = ('keepout', 'reserved', 'rails')
Z_MOVE_POLICIES = ('deny', 'idle', 'allow')
ZONE_NAME_RE = re.compile(r'^[a-z0-9_]+$')


######################################################################
# Geometry and rules (no Klipper objects here: unit-testable)
######################################################################

class ZoneViolation(Exception):
    pass


def segment_hits_box(start, end, lo, hi):
    # Liang-Barsky clipping in 3D: True if the segment touches the closed
    # box lo..hi (bounds may be infinite)
    t0, t1 = 0., 1.
    for i in range(3):
        d = end[i] - start[i]
        for p, q in ((-d, start[i] - lo[i]), (d, hi[i] - start[i])):
            if abs(p) < 1e-12:
                if q < 0.:
                    return False
                continue
            r = q / p
            if p < 0.:
                if r > t1:
                    return False
                if r > t0:
                    t0 = r
            else:
                if r < t0:
                    return False
                if r < t1:
                    t1 = r
    return t0 <= t1


def fmt_pos(p):
    return "X%.2f Y%.2f Z%.2f" % (p[0], p[1], p[2])


class Rail:
    """A straight path along one axis: "X10.5 Y27:0" = at X 10.5, from Y 27
    (outer end) to Y 0 (inner end). Axes that are not given are free, but
    must stay constant during a move along the rail."""
    def __init__(self, text):
        self.text = " ".join(text.split())
        self.axis = None
        self.fixed = {}
        for tok in self.text.split():
            letter = tok[:1].lower()
            if letter not in AXES or len(tok) < 2:
                raise ValueError("'%s' is not <axis><value> or"
                                 " <axis><from>:<to>" % (tok,))
            a = AXES.index(letter)
            if a in self.fixed or a == self.axis:
                raise ValueError("axis %s given twice" % (letter.upper(),))
            val = tok[1:]
            try:
                if ':' in val:
                    if self.axis is not None:
                        raise ValueError("only one axis may have a range")
                    v_from, v_to = val.split(':', 1)
                    self.axis = a
                    self.start = float(v_from)
                    self.end = float(v_to)
                else:
                    self.fixed[a] = float(val)
            except ValueError as e:
                raise ValueError("'%s': %s" % (tok, e))
        if self.axis is None:
            raise ValueError("one axis needs a range <from>:<to>, e.g. Y27:0")
        if abs(self.end - self.start) < 10. * EPS:
            raise ValueError("the range has zero length")
        self.sign = 1. if self.end > self.start else -1.
        self.lo = min(self.start, self.end)
        self.hi = max(self.start, self.end)

    def classify(self, s, e, tol, z_tol=0.):
        # 'in', 'out' or None (the move does not follow this rail)
        a = self.axis
        for i in range(3):
            if i == a:
                continue
            lim = max(z_tol, EPS) if i == 2 else EPS
            if abs(e[i] - s[i]) > lim:
                return None
        d = e[a] - s[a]
        if abs(d) <= EPS:
            return None
        for i, v in self.fixed.items():
            if abs(s[i] - v) > tol:
                return None
        if d * self.sign > 0.:
            # inward: start and end within the rail
            lo, hi = self.lo - tol, self.hi + tol
            if lo <= s[a] <= hi and lo <= e[a] <= hi:
                return 'in'
            return None
        # outward: from anywhere on the rail line not deeper than the inner
        # end; it may continue past the outer end
        if (s[a] - self.end) * self.sign <= tol:
            return 'out'
        return None

    def exit_hint(self):
        where = " ".join("%s%g" % (AXES[i].upper(), v)
                         for i, v in sorted(self.fixed.items()))
        direction = "%s%s" % ('+' if self.sign < 0. else '-',
                              AXES[self.axis].upper())
        if where:
            return "at %s move %s" % (where, direction)
        return "move %s" % (direction,)

    def get_status(self):
        st = {'text': self.text, 'axis': AXES[self.axis],
              'outer': self.start, 'inner': self.end}
        for i in range(3):
            st[AXES[i]] = self.fixed.get(i)
        return st


class Zone:
    def __init__(self, name, kind, lo, hi, rails=(), speed=None, accel=None,
                 lock_homing=False, z_moves='deny', z_tolerance=0.,
                 suspend_runout=()):
        if kind not in ZONE_TYPES:
            raise ValueError("unknown zone type '%s'" % (kind,))
        self.name = name
        self.kind = kind
        self.lo = [float(v) for v in lo]
        self.hi = [float(v) for v in hi]
        for i in range(3):
            if not self.lo[i] < self.hi[i]:
                raise ValueError("%s_min must be below %s_max"
                                 % (AXES[i], AXES[i]))
        if all(v == -INF for v in self.lo) and all(v == INF for v in self.hi):
            raise ValueError("at least one bound (x_min ... z_max) is needed")
        self.rails = list(rails)
        if kind == 'rails' and not self.rails:
            raise ValueError("type rails needs at least one rail")
        if kind != 'rails' and self.rails:
            raise ValueError("rails are only valid with type: rails")
        if z_moves not in Z_MOVE_POLICIES:
            raise ValueError("unknown z_moves '%s'" % (z_moves,))
        self.speed = speed
        self.accel = accel
        self.lock_homing = lock_homing
        self.z_moves = z_moves
        self.z_tolerance = z_tolerance
        self.suspend_runout = list(suspend_runout)
        self.z_bounded = self.lo[2] > -INF or self.hi[2] < INF
        # the open box, shrunk by EPS so that the faces are outside
        self._lo = [v + EPS for v in self.lo]
        self._hi = [v - EPS for v in self.hi]
        for r in self.rails:
            self._check_rail(r)

    def _check_rail(self, r):
        for i, v in r.fixed.items():
            if not self.lo[i] < v < self.hi[i]:
                raise ValueError("rail '%s': %s%g is outside the zone, the"
                                 " rail would never enter it"
                                 % (r.text, AXES[i].upper(), v))
        a = r.axis
        if not self.lo[a] <= r.end <= self.hi[a]:
            raise ValueError("rail '%s': the inner end %s%g must be inside"
                             " the zone" % (r.text, AXES[a].upper(), r.end))

    def box(self, known=(True, True, True)):
        # an axis with unknown position counts as fully covered
        if known[0] and known[1] and known[2]:
            return self._lo, self._hi
        lo, hi = list(self._lo), list(self._hi)
        for i in range(3):
            if not known[i]:
                lo[i], hi[i] = -INF, INF
        return lo, hi

    def contains(self, p, known=(True, True, True)):
        # True if the point is, or MAY be (unknown axes), inside
        lo, hi = self.box(known)
        for i in range(3):
            if not lo[i] <= p[i] <= hi[i]:
                return False
        return True

    def surely_contains(self, p, known=(True, True, True)):
        # True only if the point is certainly inside
        for i in range(3):
            if not known[i] and (self.lo[i] > -INF or self.hi[i] < INF):
                return False
        return self.contains(p)

    def hits(self, s, e, known=(True, True, True)):
        lo, hi = self.box(known)
        return segment_hits_box(s, e, lo, hi)

    def describe(self):
        parts = []
        for i, a in enumerate('XYZ'):
            lo, hi = self.lo[i], self.hi[i]
            if lo > -INF and hi < INF:
                parts.append("%g<%s<%g" % (lo, a, hi))
            elif lo > -INF:
                parts.append("%s>%g" % (a, lo))
            elif hi < INF:
                parts.append("%s<%g" % (a, hi))
        return " ".join(parts)

    def rails_text(self):
        return " | ".join(r.text for r in self.rails)

    def exit_hint(self):
        if self.kind == 'rails':
            return "leave along a rail (%s)" % (
                "; ".join(r.exit_hint() for r in self.rails),)
        return "move out of the zone"


class Context:
    """What the rules need to know about the printer for one move."""
    def __init__(self, known=(True, True, True), open_zones=(),
                 occupied=(), print_active=False):
        self.known = tuple(known)
        self.open = set(open_zones)
        self.occupied = set(occupied)
        self._print_active = print_active

    def print_active(self):
        if callable(self._print_active):
            return self._print_active()
        return self._print_active


class Verdict:
    def __init__(self):
        self.speed = None
        self.accel = None
        self.enter = []
        self.notes = []

    def add(self, zone, note, enters):
        self.notes.append("%s: %s" % (zone.name, note))
        if zone.speed is not None:
            self.speed = (zone.speed if self.speed is None
                          else min(self.speed, zone.speed))
        if zone.accel is not None:
            self.accel = (zone.accel if self.accel is None
                          else min(self.accel, zone.accel))
        if enters:
            self.enter.append(zone.name)


class Engine:
    def __init__(self, zones, tolerance=0.01, unknown_z='ignore'):
        self.zones = list(zones)
        self.tol = tolerance
        # zones limited in Z while Z is not homed: 'ignore' them (like
        # Klipper, which allows X/Y moves before Z homing; needed when a
        # bucket sits on the way to the safe_z_home point) or count them
        # as 'full_height'
        self.unknown_z = unknown_z
        if self.zones:
            self.union_lo = [min(z._lo[i] for z in self.zones)
                             for i in range(3)]
            self.union_hi = [max(z._hi[i] for z in self.zones)
                             for i in range(3)]

    def _far(self, s, e):
        for i in range(3):
            if (max(s[i], e[i]) < self.union_lo[i]
                    or min(s[i], e[i]) > self.union_hi[i]):
                return True
        return False

    def check(self, s, e, ctx):
        """None if no zone is involved, a Verdict if the move is allowed
        through one or more zones; raises ZoneViolation otherwise."""
        if not self.zones:
            return None
        dx, dy, dz = e[0] - s[0], e[1] - s[1], e[2] - s[2]
        if abs(dx) <= EPS and abs(dy) <= EPS and abs(dz) <= EPS:
            return None             # extrude-only: the toolhead stays put
        known = ctx.known
        xy_known = known[0] and known[1]
        pure_z = abs(dx) <= EPS and abs(dy) <= EPS
        if not xy_known and pure_z:
            # X/Y unknown (e.g. the Z hop before homing): nothing to check,
            # unless a zone may still be holding the toolhead
            if ctx.occupied:
                raise ZoneViolation(
                    "Z move refused: the X/Y position is unknown and zone(s)"
                    " %s may still hold the toolhead. Turn the motors off,"
                    " free the toolhead by hand, then NPZ_CLEAR CONFIRM=1"
                    % (", ".join(sorted(ctx.occupied)),))
            return None
        if known[0] and known[1] and known[2] and self._far(s, e):
            return None
        verdict = None
        skip_z_bounded = not known[2] and self.unknown_z == 'ignore'
        for zone in self.zones:
            # rails zones always count, a dock is never "probably below"
            if skip_z_bounded and zone.z_bounded and zone.kind != 'rails':
                continue
            if not zone.hits(s, e, known):
                continue
            note = self._check_zone(zone, s, e, dz, pure_z, xy_known, ctx)
            if verdict is None:
                verdict = Verdict()
            verdict.add(zone, note,
                        zone.lock_homing and zone.contains(e, known))
        return verdict

    def _check_zone(self, zone, s, e, dz, pure_z, xy_known, ctx):
        known = ctx.known
        # the "may leave" exception needs a start that is certainly inside
        start_in = zone.surely_contains(s, known)
        is_open = zone.name in ctx.open
        move = "%s -> %s" % (fmt_pos(s), fmt_pos(e))
        if zone.kind == 'keepout':
            if start_in:
                return "leaving keepout zone"
            raise ZoneViolation("move %s enters keepout zone '%s' (%s)"
                                % (move, zone.name, zone.describe()))
        if zone.kind == 'reserved':
            if is_open:
                return "inside open zone"
            if start_in:
                return "leaving reserved zone"
            raise ZoneViolation(
                "move %s enters reserved zone '%s' (%s), which is closed:"
                " NPZ_OPEN ZONE=%s first" % (move, zone.name,
                                             zone.describe(), zone.name))
        # rails
        if not xy_known:
            raise ZoneViolation(
                "move %s may enter rails zone '%s' (%s) but the X/Y position"
                " is unknown: home X and Y first"
                % (move, zone.name, zone.describe()))
        if pure_z:
            return self._check_z(zone, move, ctx)
        out_rail = in_rail = None
        for rail in zone.rails:
            c = rail.classify(s, e, self.tol, zone.z_tolerance)
            if c == 'out' and out_rail is None:
                out_rail = rail
            elif c == 'in' and in_rail is None:
                in_rail = rail
        if out_rail is not None:
            return "outward on rail '%s'" % (out_rail.text,)
        if in_rail is not None:
            if is_open:
                return "inward on rail '%s'" % (in_rail.text,)
            raise ZoneViolation(
                "move %s follows rail '%s' into zone '%s', which is closed:"
                " NPZ_OPEN ZONE=%s first" % (move, in_rail.text, zone.name,
                                             zone.name))
        extra = ""
        if abs(dz) > EPS:
            extra = (" The move also changes Z, and rails keep Z constant"
                     " (with a bed mesh active over the zone, raise"
                     " z_tolerance).")
        raise ZoneViolation(
            "move %s refused in rails zone '%s' (%s): only moves along the"
            " rails are allowed (%s). To leave: %s.%s" % (
                move, zone.name, zone.describe(), zone.rails_text(),
                zone.exit_hint(), extra))

    def _check_z(self, zone, move, ctx):
        policy = zone.z_moves
        if policy == 'allow':
            return "Z move"
        if policy == 'deny':
            raise ZoneViolation("Z move %s refused in rails zone '%s'"
                                " (z_moves: deny)" % (move, zone.name))
        if ctx.print_active():
            raise ZoneViolation(
                "Z move %s refused in rails zone '%s': allowed only while no"
                " print is running or paused (z_moves: idle)"
                % (move, zone.name))
        if not ctx.known[2]:
            raise ZoneViolation(
                "Z move %s refused in rails zone '%s': Z is not homed"
                " (z_moves: idle)" % (move, zone.name))
        return "Z move (no print, Z homed)"


######################################################################
# Klipper glue
######################################################################

class NoPrintZone:
    """[no_print_zone]: global options, state and commands."""
    def __init__(self, config):
        self.printer = config.get_printer()
        self.reactor = self.printer.get_reactor()
        self.tol = config.getfloat('tolerance', 0.01, minval=0., maxval=0.5)
        cfg_dir = os.path.dirname(os.path.abspath(
            self.printer.get_start_args().get('config_file', 'printer.cfg')))
        self.state_file = os.path.expanduser(config.get(
            'state_file', os.path.join(cfg_dir, 'no_print_zone.json')))
        self.tested_version = config.get('tested_klipper_version', None)
        self.block_force_move = config.getboolean('block_force_move', True)
        self.unknown_z = config.getchoice(
            'unknown_z', {'ignore': 'ignore', 'full_height': 'full_height'},
            'ignore')
        self.zones = []
        self.zone_by_name = {}
        self.engine = Engine([], self.tol, self.unknown_z)
        self.open = set()
        self.prep = False
        self.occupied = {}          # zone name -> note
        self.in_drip = False
        self.in_homing_retract = False
        self.z_provisional = False  # Z marked homed only for a Z hop
        self.z_steppers = set()
        self.runout = {}            # sensor name -> runout helper
        self.runout_saved = {}      # suspended sensor -> previous state
        self.toolhead = self.kin = None
        self.orig_check_move = self.orig_drip_move = None
        self.orig_set_position = None
        self.printer.register_event_handler('klippy:connect',
                                            self._handle_connect)
        self.printer.register_event_handler('klippy:ready',
                                            self._handle_ready)
        self.printer.register_event_handler('homing:home_rails_begin',
                                            self._handle_home_rails_begin)
        self.printer.register_event_handler('homing:homing_move_begin',
                                            self._handle_homing_move_begin)
        self.printer.register_event_handler('gcode:command_error',
                                            self._handle_command_error)
        gcode = self.printer.lookup_object('gcode')
        for cmd in ('STATUS', 'OPEN', 'CLOSE', 'PREP', 'CLEAR'):
            func = getattr(self, 'cmd_NPZ_' + cmd)
            gcode.register_command('NPZ_' + cmd, func,
                                   desc=getattr(self, 'cmd_NPZ_%s_help'
                                                % (cmd,)))

    def add_zone(self, zone, config):
        if zone.name in self.zone_by_name:
            raise config.error("no_print_zone: zone '%s' defined twice"
                               % (zone.name,))
        self.zones.append(zone)
        self.zone_by_name[zone.name] = zone

    # ------------------------------------------------------------------
    # Hooks
    def _handle_connect(self):
        self.toolhead = self.printer.lookup_object('toolhead')
        self.kin = self.toolhead.get_kinematics()
        self.engine = Engine(self.zones, self.tol, self.unknown_z)
        st = self.kin.get_status(self.reactor.monotonic())
        amin, amax = st['axis_minimum'], st['axis_maximum']
        for zone in self.zones:
            for r in zone.rails:
                pts = dict(r.fixed)
                pts[r.axis] = r.end
                for i, v in pts.items():
                    if not amin[i] - self.tol <= v <= amax[i] + self.tol:
                        raise self.printer.config_error(
                            "no_print_zone %s: rail '%s' leaves the travel"
                            " range of %s (%.2f..%.2f)" % (
                                zone.name, r.text, AXES[i].upper(),
                                amin[i], amax[i]))
        # steppers that move when Z moves (to recognise a real Z homing)
        for s in self.kin.get_steppers():
            try:
                p0 = s.calc_position_from_coord([0., 0., 0.])
                p1 = s.calc_position_from_coord([0., 0., 1.])
            except Exception:
                continue
            if abs(p1 - p0) > 1e-9:
                self.z_steppers.add(s.get_name())
        names = []
        for zone in self.zones:
            for n in zone.suspend_runout:
                if n not in names:
                    names.append(n)
        for n in names:
            obj = self.printer.lookup_object('filament_switch_sensor ' + n,
                                             None)
            helper = getattr(obj, 'runout_helper', None)
            if helper is None or not hasattr(helper, 'sensor_enabled'):
                raise self.printer.config_error(
                    "no_print_zone: suspend_runout: no filament_switch_sensor"
                    " named '%s'" % (n,))
            self.runout[n] = helper
        self.orig_check_move = self.kin.check_move
        self.kin.check_move = self._check_move
        self.orig_drip_move = self.toolhead.drip_move
        self.toolhead.drip_move = self._drip_move
        self.orig_set_position = self.kin.set_position
        self.kin.set_position = self._kin_set_position
        self._wrap_stepper_commands()
        self._load_state()

    def _wrap_stepper_commands(self):
        gcode = self.printer.lookup_object('gcode')
        mux = getattr(gcode, 'mux_commands', {})
        kin_names = [s.get_name() for s in self.kin.get_steppers()]
        force = mux.get('FORCE_MOVE')
        if self.block_force_move and force is not None:
            for name in kin_names:
                if name in force[1]:
                    force[1][name] = self._make_force_block(name)
        buzz = mux.get('STEPPER_BUZZ')
        if buzz is not None:
            for name in kin_names:
                if name in buzz[1]:
                    buzz[1][name] = self._make_buzz_guard(name,
                                                          buzz[1][name])

    def _make_force_block(self, name):
        def cmd(gcmd):
            raise gcmd.error("no_print_zone: FORCE_MOVE on %s is blocked: it"
                             " would bypass the zone checks" % (name,))
        return cmd

    def _make_buzz_guard(self, name, orig):
        def cmd(gcmd):
            if self.occupied:
                raise gcmd.error(
                    "no_print_zone: STEPPER_BUZZ on %s is blocked while zone"
                    "(s) %s hold the toolhead" % (
                        name, ", ".join(sorted(self.occupied))))
            return orig(gcmd)
        return cmd

    def _current_version(self):
        # "git describe" of the Klipper repository. software_version is not
        # used: klippy.py appends "-dirty" for every extra module in
        # klippy/extras, this one included.
        try:
            import util
            return util.get_git_version()['version']
        except Exception:
            logging.exception("no_print_zone: cannot read Klipper version")
            return self.printer.get_start_args().get('software_version', '?')

    def _handle_ready(self):
        if self.tested_version:
            cur = self._current_version()
            if cur != self.tested_version:
                self._warn(
                    "no_print_zone: tested with Klipper %s, now running %s."
                    " The module hooks into Klipper internals: run the dry"
                    " tests again before printing, then update"
                    " tested_klipper_version." % (self.tested_version, cur))
        self._update_runout()
        if self.occupied:
            msg = ("no_print_zone: WARNING, zone(s) %s are marked as holding"
                   " the toolhead. Homing is blocked. Turn the motors off,"
                   " free the toolhead by hand, then NPZ_CLEAR CONFIRM=1."
                   % (self._occupied_text(),))
            logging.warning(msg)
            self.printer.lookup_object('gcode').respond_info(msg)

    def _warn(self, msg):
        configfile = self.printer.lookup_object('configfile')
        if hasattr(configfile, 'runtime_warning'):
            configfile.runtime_warning(msg)
        else:
            logging.warning(msg)

    def _drip_move(self, newpos, speed, drip_completion):
        # homing and probing moves use fictitious coordinates: not checked
        self.in_drip = True
        try:
            return self.orig_drip_move(newpos, speed, drip_completion)
        finally:
            self.in_drip = False

    def _kin_set_position(self, newpos, homing_axes=""):
        z_before = self._homed()[2]
        self.orig_set_position(newpos, homing_axes)
        if isinstance(homing_axes, str):
            sets_z = 'z' in homing_axes
        else:
            sets_z = 2 in homing_axes
        if sets_z and not z_before:
            # Z just marked homed without a homing move (yet): a real
            # homing clears this in _handle_homing_move_begin, the Z hop
            # of safe_z_home does not
            self.z_provisional = True

    def _handle_homing_move_begin(self, hmove):
        if not self.z_provisional:
            return
        try:
            names = set(s.get_name() for es in hmove.get_mcu_endstops()
                        for s in es.get_steppers())
        except Exception:
            names = None
        if names is None or not self.z_steppers or names & self.z_steppers:
            self.z_provisional = False

    def _handle_home_rails_begin(self, homing_state, rails):
        reason = self._homing_lock_reason()
        if reason:
            raise self.printer.command_error(
                "Homing blocked by no_print_zone: %s" % (reason,))
        # The retract away from the endstop is a normal move made while the
        # other axes may still be unknown: like the homing moves themselves
        # it is not checked, for the duration of that call only.
        orig = getattr(homing_state, '_retract_move', None)
        if orig is not None and not getattr(orig, 'npz_wrapped', False):
            def retract_move(*args, **kwargs):
                self.in_homing_retract = True
                try:
                    return orig(*args, **kwargs)
                finally:
                    self.in_homing_retract = False
            retract_move.npz_wrapped = True
            homing_state._retract_move = retract_move

    def _handle_command_error(self):
        if self.open or self.prep:
            logging.info("no_print_zone: G-code error, zones closed (open:"
                         " %s, prep: %s)", ", ".join(sorted(self.open)),
                         self.prep)
        self.open.clear()
        self.prep = False
        self._update_runout()

    # ------------------------------------------------------------------
    # Move check
    def _homed(self):
        limits = getattr(self.kin, 'limits', None)
        if limits is not None and len(limits) >= 3:
            return [limits[i][0] <= limits[i][1] for i in range(3)]
        homed = self.kin.get_status(self.reactor.monotonic())['homed_axes']
        return [a in homed for a in AXES]

    def _known(self):
        k = self._homed()
        if self.z_provisional:
            k[2] = False
        return k

    def _print_active(self):
        return self._print_state() in ACTIVE_PRINT_STATES

    def _print_state(self):
        ps = self.printer.lookup_object('print_stats', None)
        if ps is None:
            return 'standby'
        return ps.get_status(self.reactor.monotonic())['state']

    def _check_move(self, move):
        self.orig_check_move(move)          # travel limits first
        if self.in_drip or self.in_homing_retract or not self.zones:
            return
        known = self._known()
        if (known[0] and known[1] and known[2]
                and self.engine._far(move.start_pos, move.end_pos)):
            return                          # fast path: far from all zones
        ctx = Context(known, self.open, self.occupied, self._print_active)
        try:
            verdict = self.engine.check(move.start_pos, move.end_pos, ctx)
        except ZoneViolation as e:
            raise move.move_error("no_print_zone: %s" % (e,))
        if verdict is None:
            return
        if verdict.speed is not None:
            move.limit_speed(verdict.speed,
                             INF if verdict.accel is None else verdict.accel)
        logging.info("no_print_zone: %s -> %s: %s%s",
                     fmt_pos(move.start_pos), fmt_pos(move.end_pos),
                     "; ".join(verdict.notes),
                     "" if verdict.speed is None else
                     ", speed limited to %.1f mm/s" % (verdict.speed,))
        for name in verdict.enter:
            if name not in self.occupied:
                self._set_occupied(name, "entered at %s"
                                   % (fmt_pos(move.end_pos),))

    # ------------------------------------------------------------------
    # Zones holding the toolhead (persistent)
    def _load_state(self):
        lock_zones = [z.name for z in self.zones if z.lock_homing]
        try:
            with open(self.state_file, 'r') as f:
                data = json.load(f)
        except (IOError, OSError):
            return                          # first run
        except ValueError:
            data = None
        occ = data.get('occupied') if isinstance(data, dict) else None
        if not isinstance(occ, dict):
            for n in lock_zones:            # unreadable: assume the worst
                self.occupied[n] = "state file unreadable"
            return
        for name, note in occ.items():
            zone = self.zone_by_name.get(name)
            if zone is not None and zone.lock_homing:
                self.occupied[name] = str(note)
                continue
            # a zone removed or renamed while holding the toolhead
            for n in lock_zones:
                self.occupied[n] = ("state file names zone '%s', which is"
                                    " no longer configured" % (name,))

    def _save_state(self):
        data = {'occupied': self.occupied,
                'time': time.strftime('%Y-%m-%d %H:%M:%S')}
        tmp = self.state_file + '.tmp'
        try:
            with open(tmp, 'w') as f:
                json.dump(data, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_file)
        except Exception:
            logging.exception("no_print_zone: cannot write %s",
                              self.state_file)

    def _set_occupied(self, name, note):
        self.occupied[name] = note
        logging.info("no_print_zone: zone %s holds the toolhead (%s)",
                     name, note)
        self._save_state()
        self._update_runout()

    def _clear_occupied(self, names):
        changed = False
        for n in names:
            if self.occupied.pop(n, None) is not None:
                changed = True
                logging.info("no_print_zone: zone %s released", n)
        if changed:
            self._save_state()
            self._update_runout()

    def _occupied_text(self):
        return ", ".join("%s (%s)" % (n, self.occupied[n])
                         for n in sorted(self.occupied))

    def _homing_lock_reason(self):
        if self.toolhead is None:
            return None
        if self.occupied:
            hints = "; ".join("%s: %s" % (n, self.zone_by_name[n].exit_hint())
                              for n in sorted(self.occupied)
                              if n in self.zone_by_name)
            return ("zone(s) %s hold the toolhead. With the motors on: %s,"
                    " then NPZ_CLOSE. Otherwise turn the motors off, free the"
                    " toolhead by hand and run NPZ_CLEAR CONFIRM=1"
                    % (self._occupied_text(), hints))
        known = self._known()
        if known[0] and known[1]:
            pos = self.toolhead.get_position()
            for z in self.zones:
                if z.lock_homing and z.surely_contains(pos, known):
                    return ("the toolhead (%s) is inside zone %s: %s"
                            % (fmt_pos(pos), z.name, z.exit_hint()))
        return None

    def _update_runout(self):
        if not self.runout:
            return
        want = set()
        for z in self.zones:
            if z.name in self.open or z.name in self.occupied:
                want.update(z.suspend_runout)
        for n in list(self.runout_saved):
            if n not in want:
                self.runout[n].sensor_enabled = self.runout_saved.pop(n)
                logging.info("no_print_zone: runout sensor %s restored", n)
        for n in sorted(want):
            if n not in self.runout_saved:
                self.runout_saved[n] = self.runout[n].sensor_enabled
                self.runout[n].sensor_enabled = False
                logging.info("no_print_zone: runout sensor %s suspended", n)

    def _runout_text(self):
        if not self.runout:
            return "no runout sensor managed"
        return "runout: " + ", ".join(
            "%s %s" % (n, "SUSPENDED" if n in self.runout_saved else "active")
            for n in sorted(self.runout))

    # ------------------------------------------------------------------
    def get_status(self, eventtime):
        return {
            'zones': [z.name for z in self.zones],
            'open_zones': sorted(self.open),
            'occupied_zones': sorted(self.occupied),
            'prep': self.prep,
            'homing_locked': self._homing_lock_reason() is not None,
            'runout_suspended': sorted(self.runout_saved),
        }

    # ------------------------------------------------------------------
    # Commands
    def _parse_zones(self, gcmd, required):
        raw = gcmd.get('ZONE', None)
        if raw is None:
            if required:
                raise gcmd.error("ZONE=<name>[,<name>] is required (zones:"
                                 " %s)" % (", ".join(self.zone_by_name),))
            return None
        names = [n.strip().lower() for n in raw.split(',') if n.strip()]
        bad = [n for n in names if n not in self.zone_by_name]
        if bad or not names:
            raise gcmd.error("unknown zone '%s' (zones: %s)" % (
                ", ".join(bad) or raw, ", ".join(self.zone_by_name)))
        return names

    def _position_text(self):
        known = self._known()
        if not (known[0] and known[1]):
            return "toolhead position unknown (X/Y not homed)"
        pos = self.toolhead.get_position()
        inside = [z.name for z in self.zones if z.surely_contains(pos, known)]
        return "toolhead %s%s, %s" % (
            fmt_pos(pos), "" if known[2] else " (Z not homed)",
            ("inside " + ", ".join(inside)) if inside else "outside all zones")

    cmd_NPZ_STATUS_help = "Report the no_print_zone zones and state"
    def cmd_NPZ_STATUS(self, gcmd):
        names = self._parse_zones(gcmd, False) or [z.name for z in self.zones]
        lines = ["no_print_zone: open=%s prep=%d occupied=%s" % (
            ",".join(sorted(self.open)) or "-", int(self.prep),
            ",".join(sorted(self.occupied)) or "-"), self._position_text()]
        for n in names:
            z = self.zone_by_name[n]
            line = "zone %s [%s] %s" % (z.name, z.kind, z.describe())
            if z.kind == 'rails':
                line += " rails: %s, z_moves %s" % (z.rails_text(), z.z_moves)
            if z.speed is not None:
                line += ", max %.1f mm/s" % (z.speed,)
            flags = []
            if n in self.open:
                flags.append("OPEN")
            if n in self.occupied:
                flags.append("HOLDS TOOLHEAD (%s)" % (self.occupied[n],))
            if flags:
                line += " - " + ", ".join(flags)
            lines.append(line)
        lines.append(self._runout_text())
        gcmd.respond_info("\n".join(lines))

    cmd_NPZ_OPEN_help = ("Open zones: NPZ_OPEN ZONE=<name>[,<name>]. Only"
                         " outside a print or during NPZ_PREP")
    def cmd_NPZ_OPEN(self, gcmd):
        names = self._parse_zones(gcmd, True)
        for n in names:
            if self.zone_by_name[n].kind == 'keepout':
                raise gcmd.error("NPZ_OPEN: zone '%s' is a keepout zone and"
                                 " cannot be opened" % (n,))
        known = self._known()
        if not (known[0] and known[1]):
            raise gcmd.error("NPZ_OPEN: home X and Y first")
        state = self._print_state()
        if state in ACTIVE_PRINT_STATES and not self.prep:
            raise gcmd.error(
                "NPZ_OPEN: zones cannot be opened during a print (state %s),"
                " only outside a print or between NPZ_PREP ENABLE=1 and"
                " NPZ_PREP ENABLE=0" % (state,))
        self.open.update(names)
        self._update_runout()
        gcmd.respond_info("no_print_zone: open %s, %s" % (
            ", ".join(sorted(self.open)), self._runout_text()))

    cmd_NPZ_CLOSE_help = "Close zones (all, or ZONE=<name>[,<name>])"
    def cmd_NPZ_CLOSE(self, gcmd):
        names = self._parse_zones(gcmd, False)
        self.toolhead.wait_moves()
        if names is None:
            self.open.clear()
        else:
            self.open.difference_update(names)
        # release the zones the toolhead has verifiably left
        known = self._known()
        if known[0] and known[1] and self.occupied:
            pos = self.toolhead.get_position()
            self._clear_occupied([n for n in list(self.occupied)
                                  if n in self.zone_by_name and not
                                  self.zone_by_name[n].contains(pos, known)])
        self._update_runout()
        msg = "no_print_zone: open=%s, %s, %s" % (
            ",".join(sorted(self.open)) or "-", self._position_text(),
            self._runout_text())
        if self.occupied:
            msg += (". STILL HOLDING THE TOOLHEAD: %s (homing blocked)"
                    % (self._occupied_text(),))
        gcmd.respond_info(msg)

    cmd_NPZ_PREP_help = ("Print preparation (START_PRINT): ENABLE=1 lets"
                         " NPZ_OPEN work during the print, ENABLE=0 ends it")
    def cmd_NPZ_PREP(self, gcmd):
        self.prep = bool(gcmd.get_int('ENABLE', minval=0, maxval=1))
        if not self.prep:
            self.open.clear()
            self._update_runout()

    cmd_NPZ_CLEAR_help = ("Release zones holding the toolhead after it has"
                          " left them (CONFIRM=1 if X/Y are not homed)")
    def cmd_NPZ_CLEAR(self, gcmd):
        names = self._parse_zones(gcmd, False)
        targets = [n for n in (names or list(self.occupied))
                   if n in self.occupied]
        if not targets:
            gcmd.respond_info("no_print_zone: no zone holds the toolhead")
            return
        known = self._known()
        confirm = gcmd.get_int('CONFIRM', 0) == 1
        if known[0] and known[1]:
            self.toolhead.wait_moves()
            pos = self.toolhead.get_position()
            zones = [self.zone_by_name[n] for n in targets
                     if n in self.zone_by_name]
            inside = [z for z in zones if z.surely_contains(pos, known)]
            if inside:
                raise gcmd.error(
                    "NPZ_CLEAR: the toolhead (%s) is still inside %s: %s. To"
                    " free it by hand: M84, then NPZ_CLEAR CONFIRM=1" % (
                        fmt_pos(pos), ", ".join(z.name for z in inside),
                        inside[0].exit_hint()))
            maybe = [z.name for z in zones if z.contains(pos, known)]
            if maybe and not confirm:
                raise gcmd.error(
                    "NPZ_CLEAR: Z is not homed, the toolhead (%s) may still"
                    " be inside %s. Check it, then NPZ_CLEAR CONFIRM=1"
                    % (fmt_pos(pos), ", ".join(maybe)))
        elif not confirm:
            raise gcmd.error(
                "NPZ_CLEAR: toolhead position unknown. Turn the motors off,"
                " free the toolhead by hand, then NPZ_CLEAR CONFIRM=1")
        self._clear_occupied(targets)
        gcmd.respond_info("no_print_zone: released %s" % (", ".join(targets),))


class ZoneSection:
    """[no_print_zone NAME]: one zone."""
    def __init__(self, config, npz):
        parts = config.get_name().split()
        name = parts[-1].lower()
        if len(parts) != 2 or not ZONE_NAME_RE.match(name):
            raise config.error("no_print_zone: section must be named"
                               " [no_print_zone NAME], NAME with letters,"
                               " digits and _ only (got [%s])"
                               % (config.get_name(),))
        kind = config.getchoice('type', {t: t for t in ZONE_TYPES})
        lo = [config.getfloat(a + '_min', -INF) for a in AXES]
        hi = [config.getfloat(a + '_max', INF) for a in AXES]
        is_rails = kind == 'rails'
        speed = config.getfloat('speed', 10. if is_rails else None,
                                above=0.)
        accel = config.getfloat('accel', 500. if is_rails else None,
                                above=0.)
        lock_homing = config.getboolean('lock_homing', is_rails)
        suspend = config.getlist('suspend_runout', ())
        rails = []
        z_moves, z_tol = 'deny', 0.
        if is_rails:
            text = config.get('rails')
            for line in text.replace(',', '\n').split('\n'):
                if not line.strip():
                    continue
                try:
                    rails.append(Rail(line))
                except ValueError as e:
                    raise config.error("no_print_zone %s: rail '%s': %s"
                                       % (name, line.strip(), e))
            z_moves = config.getchoice('z_moves',
                                       {p: p for p in Z_MOVE_POLICIES},
                                       'deny')
            z_tol = config.getfloat('z_tolerance', 0., minval=0., maxval=1.)
        try:
            self.zone = Zone(name, kind, lo, hi, rails, speed, accel,
                             lock_homing, z_moves, z_tol, suspend)
        except ValueError as e:
            raise config.error("no_print_zone %s: %s" % (name, e))
        self.npz = npz
        npz.add_zone(self.zone, config)
        z = self.zone
        none_inf = lambda v: None if v in (INF, -INF) else v
        self.static = {
            'type': z.kind, 'min': [none_inf(v) for v in z.lo],
            'max': [none_inf(v) for v in z.hi], 'speed': z.speed,
            'accel': z.accel, 'lock_homing': z.lock_homing,
            'z_moves': z.z_moves if is_rails else None,
            'rails': [r.get_status() for r in z.rails],
        }

    def get_status(self, eventtime):
        st = dict(self.static)
        st['open'] = self.zone.name in self.npz.open
        st['occupied'] = self.zone.name in self.npz.occupied
        return st


def load_config(config):
    return NoPrintZone(config)


def load_config_prefix(config):
    npz = config.get_printer().load_object(config, 'no_print_zone')
    return ZoneSection(config, npz)
