#!/usr/bin/env python3
# no_print_zone setup helper: the pieces of install.sh that edit files or talk
# to Moonraker, and the interactive zone wizard. Python 3 standard library only.
#
#   npz_setup.py wizard [--config-dir DIR] [--moonraker URL] [--no-restart]
#   npz_setup.py find-zones PRINTER_CFG
#   npz_setup.py add-include PRINTER_CFG FILE BACKUP_DIR
#   npz_setup.py remove-include PRINTER_CFG BACKUP_DIR
#   npz_setup.py add-update-manager MOONRAKER_CONF REPO_PATH ORIGIN BACKUP_DIR
#   npz_setup.py remove-update-manager MOONRAKER_CONF BACKUP_DIR
#   npz_setup.py moonraker-url CONFIG_DIR
#   npz_setup.py printer-state URL
#   npz_setup.py npz-loaded URL
#   npz_setup.py restart URL klipper|moonraker
#
# Copyright (C) 2026  no_print_zone contributors
# This file may be distributed under the terms of the GNU GPLv3 license.
import argparse, glob, importlib.util, json, os, re, shutil, sys, time
import urllib.error, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
MARK = '# added by the no_print_zone installer'
UM_HEADER = '[update_manager no_print_zone]'
SAVE_MARK = '#*# <---------------------- SAVE_CONFIG'
INCLUDE_RE = re.compile(r'^\[include\s+([^\]]+)\]')
SECTION_RE = re.compile(r'^\[([^\]]+)\]')
ZONE_RE = re.compile(r'^\[no_print_zone(?:\s+([^\]\s]+))?\s*\]')
NAME_RE = re.compile(r'^[a-z0-9_]+$')
INF = float('inf')
ACTIVE = ('printing', 'paused')


class Abort(Exception):
    pass


######################################################################
# Files
######################################################################

def read_text(path):
    with open(path, 'r', newline='') as f:
        return f.read()


def write_text(path, text):
    # write through symlinks (config files are often links): replacing the
    # link itself would detach the file from where it really lives
    path = os.path.realpath(path)
    tmp = path + '.npz-tmp'
    with open(tmp, 'w', newline='') as f:
        f.write(text)
    if os.path.exists(path):
        shutil.copymode(path, tmp)
    os.replace(tmp, path)


def eol(text):
    return '\r\n' if '\r\n' in text else '\n'


def backup(path, backup_dir):
    if not os.path.exists(path):
        return None
    os.makedirs(backup_dir, exist_ok=True)
    dst = os.path.join(backup_dir, '%s.%s' % (
        os.path.basename(path), time.strftime('%Y%m%d_%H%M%S')))
    shutil.copy2(path, dst)
    return dst


def included_files(main_cfg):
    """printer.cfg and every file it includes, recursively (Klipper rules:
    paths relative to the including file, glob patterns allowed)."""
    seen, stack = [], [os.path.abspath(main_cfg)]
    while stack:
        f = stack.pop(0)
        if f in seen or not os.path.isfile(f):
            continue
        seen.append(f)
        for line in read_text(f).splitlines():
            m = INCLUDE_RE.match(line.strip())
            if m:
                pattern = os.path.join(os.path.dirname(f), m.group(1).strip())
                for g in sorted(glob.glob(pattern)):
                    stack.append(os.path.abspath(g))
    return seen


def zone_sections(path):
    """[(name or None, line index)] of the no_print_zone sections of a file."""
    out = []
    for i, line in enumerate(read_text(path).splitlines()):
        m = ZONE_RE.match(line.strip())
        if m:
            out.append((m.group(1).lower() if m.group(1) else None, i))
    return out


def find_zones(printer_cfg):
    """{file: [section names]} for the included files with no_print_zone."""
    res = {}
    for f in included_files(printer_cfg):
        secs = zone_sections(f)
        if secs:
            res[f] = [n for n, _ in secs]
    return res


def add_include(printer_cfg, target, backup_dir):
    """Include target (path relative to printer.cfg) before SAVE_CONFIG."""
    text = read_text(printer_cfg)
    base = os.path.dirname(os.path.abspath(printer_cfg))
    target_abs = os.path.normpath(os.path.join(base, target))
    for line in text.splitlines():          # also when the file is missing
        m = INCLUDE_RE.match(line.strip())
        if m and os.path.normpath(os.path.join(
                base, m.group(1).strip())) == target_abs:
            return False
    if target_abs in included_files(printer_cfg):
        return False
    nl = eol(text)
    line = '[include %s]  %s' % (target, MARK)
    i = text.find(SAVE_MARK)
    if i >= 0:
        text = text[:i] + line + nl + nl + text[i:]
    else:
        text = text.rstrip('\r\n') + nl + nl + line + nl
    backup(printer_cfg, backup_dir)
    write_text(printer_cfg, text)
    return True


def remove_include(printer_cfg, backup_dir):
    text = read_text(printer_cfg)
    nl = eol(text)
    lines = text.split(nl)
    keep, i, removed = [], 0, False
    while i < len(lines):
        if lines[i].startswith('[include') and MARK in lines[i]:
            removed = True
            i += 1
            if i < len(lines) and not lines[i].strip():
                i += 1          # the blank line add_include put after it
            continue
        keep.append(lines[i])
        i += 1
    if not removed:
        return False
    backup(printer_cfg, backup_dir)
    write_text(printer_cfg, nl.join(keep))
    return True


def section_span(lines, start):
    """End (exclusive) of the section starting at 'start': up to the next
    section header, leaving to the next section the comment lines that
    directly precede it after a blank line."""
    end = start + 1
    while end < len(lines) and not SECTION_RE.match(lines[end].strip()):
        end += 1
    if end < len(lines):
        j = end
        while j > start + 1 and lines[j - 1].lstrip().startswith('#'):
            j -= 1
        if j < end and j > start + 1 and not lines[j - 1].strip():
            end = j
    return end


def add_update_manager(conf, repo_path, origin, backup_dir):
    text = read_text(conf)
    if any(l.strip() == UM_HEADER for l in text.splitlines()):
        return False
    nl = eol(text)
    block = [MARK, UM_HEADER, 'type: git_repo', 'path: %s' % (repo_path,),
             'origin: %s' % (origin,), 'primary_branch: main',
             'managed_services: klipper']
    backup(conf, backup_dir)
    write_text(conf, text.rstrip('\r\n') + nl + nl + nl.join(block) + nl)
    return True


def remove_update_manager(conf, backup_dir):
    text = read_text(conf)
    nl = eol(text)
    lines = text.split(nl)
    idx = [i for i, l in enumerate(lines) if l.strip() == UM_HEADER]
    if not idx:
        return False
    start = idx[0]
    end = section_span(lines, start)
    if start > 0 and lines[start - 1].strip() == MARK:
        start -= 1
    while start > 0 and not lines[start - 1].strip():
        start -= 1
    backup(conf, backup_dir)
    write_text(conf, nl.join(lines[:start] + [''] + lines[end:]))
    return True


def remove_zone(path, name, backup_dir):
    text = read_text(path)
    nl = eol(text)
    lines = text.split(nl)
    starts = [i for n, i in zone_sections(path) if n == name]
    if not starts:
        return False
    start = starts[0]
    end = section_span(lines, start)
    # the section takes its trailing blank line with it; at the end of the
    # file it takes the blank lines before it instead
    if not any(l.strip() for l in lines[end:]):
        end = len(lines)
        while start > 0 and not lines[start - 1].strip():
            start -= 1
    rest = lines[end:] or ['']          # keep the final newline
    backup(path, backup_dir)
    write_text(path, nl.join(lines[:start] + rest))
    return True


def append_section(path, section, backup_dir):
    text = read_text(path) if os.path.exists(path) else ''
    nl = eol(text) if text else '\n'
    backup(path, backup_dir)
    body = text.rstrip('\r\n') + nl if text else ''
    write_text(path, body + nl + section.replace('\n', nl))


def moonraker_url(config_dir):
    port = 7125
    conf = os.path.join(config_dir, 'moonraker.conf')
    if os.path.exists(conf):
        sec = None
        for line in read_text(conf).splitlines():
            m = SECTION_RE.match(line.strip())
            if m:
                sec = m.group(1).strip()
                continue
            if sec == 'server':
                m = re.match(r'^\s*port\s*[:=]\s*(\d+)', line)
                if m:
                    port = int(m.group(1))
    return 'http://localhost:%d' % (port,)


######################################################################
# Moonraker
######################################################################

class Moonraker:
    def __init__(self, url, timeout=5.):
        self.url = url.rstrip('/')
        self.timeout = timeout

    def _req(self, method, path, timeout=None):
        req = urllib.request.Request(self.url + path, method=method,
                                     data=b'' if method == 'POST' else None)
        with urllib.request.urlopen(req, timeout=timeout or self.timeout) as r:
            return json.loads(r.read().decode())['result']

    def get(self, path):
        return self._req('GET', path)

    def post(self, path, timeout=None):
        return self._req('POST', path, timeout)

    def reachable(self):
        try:
            self.get('/server/info')
            return True
        except Exception:
            return False

    def query(self, *objects):
        q = '&'.join(urllib.request.quote(o) for o in objects)
        return self.get('/printer/objects/query?' + q)['status']

    def print_state(self):
        try:
            return self.query('print_stats')['print_stats']['state']
        except Exception:
            return None

    def info(self):
        try:
            return self.get('/printer/info')
        except Exception:
            return {}

    def toolhead(self):
        return self.query('toolhead')['toolhead']

    def objects(self):
        return self.get('/printer/objects/list')['objects']

    def runout_sensors(self):
        p = 'filament_switch_sensor '
        try:
            return [o[len(p):] for o in self.objects() if o.startswith(p)]
        except Exception:
            return []

    def klipper_service(self):
        try:
            svcs = self.get('/machine/system_info')['system_info'][
                'available_services']
        except Exception:
            return 'klipper'
        cands = [s for s in svcs if s.startswith('klipper')
                 and not s.startswith('klipper_mcu')]
        if 'klipper' in cands or not cands:
            return 'klipper'
        return cands[0]


def restart_service(mr, service, out=print):
    """Restart klipper or moonraker through Moonraker and wait for it.
    Returns 'ok' (klipper: state ready), 'failed', or 'skipped' (a print is
    running: nothing was restarted)."""
    if service == 'klipper':
        state = mr.print_state()
        if state in ACTIVE:
            out("!! a print is %s: Klipper NOT restarted. Restart it when the"
                " print is over." % (state,))
            return 'skipped'
        old_pid = mr.info().get('process_id')
        name = mr.klipper_service()
        out("==> restarting the %s service (homing is lost)..." % (name,))
        mr.post('/machine/services/restart?service=' + name, timeout=30)
        deadline = time.time() + 150
        info = {}
        while time.time() < deadline:
            time.sleep(2)
            info = mr.info()
            if (info.get('state') in ('ready', 'error', 'shutdown')
                    and info.get('process_id') != old_pid):
                break
        state = info.get('state', 'unreachable')
        out("    Klipper: %s" % (state,))
        if state != 'ready':
            out("    " + str(info.get('state_message', '')).replace(
                '\n', '\n    '))
            return 'failed'
        try:
            st = mr.query('no_print_zone')['no_print_zone']
            out("    no_print_zone loaded, zones: %s" % (
                ", ".join(st['zones']) or "none yet"))
        except Exception:
            out("    no_print_zone is not loaded (no [no_print_zone] section"
                " in the configuration?)")
        return 'ok'
    out("==> restarting Moonraker...")
    try:
        mr.post('/machine/services/restart?service=moonraker', timeout=10)
    except Exception:
        pass                    # the connection may drop while restarting
    time.sleep(3)
    deadline = time.time() + 90
    while time.time() < deadline:
        if mr.reachable():
            out("    Moonraker: back")
            return 'ok'
        time.sleep(2)
    out("!! Moonraker did not come back in 90 s: check it with"
        " 'systemctl status moonraker'")
    return 'failed'


######################################################################
# Wizard
######################################################################

def load_module():
    spec = importlib.util.spec_from_file_location(
        'no_print_zone', os.path.join(REPO, 'no_print_zone.py'))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def fmt(v):
    s = ('%.3f' % v).rstrip('0').rstrip('.')
    return '0' if s in ('-0', '') else s


def rail_from_points(p0, p1, tol=0.05):
    """Rail text from its outer end p0 and inner end p1 (X/Y only)."""
    if abs(p1[2] - p0[2]) > tol:
        raise ValueError("Z changed between the two points (%s -> %s): a"
                         " rail keeps Z constant" % (fmt(p0[2]), fmt(p1[2])))
    moving = [i for i in (0, 1) if abs(p1[i] - p0[i]) > tol]
    if len(moving) != 1:
        raise ValueError("between the two points the toolhead must move"
                         " along ONE axis only, X or Y (moved: %s)"
                         % (", ".join('XY'[i] for i in moving) or "none",))
    a = moving[0]
    o = 1 - a
    return "%s%s %s%s:%s" % ('XY'[o], fmt(p0[o]), 'XY'[a], fmt(p0[a]),
                             fmt(p1[a]))


def zone_section(name, kind, lo, hi, rails=(), speed=None, accel=None,
                 lock_homing=None, z_moves=None, suspend=(), note=''):
    lines = ['[no_print_zone %s]' % (name,)]
    if note:
        lines.append('# ' + note)
    lines.append('type: %s' % (kind,))
    for i, a in enumerate('xyz'):
        if lo[i] > -INF:
            lines.append('%s_min: %s' % (a, fmt(lo[i])))
        if hi[i] < INF:
            lines.append('%s_max: %s' % (a, fmt(hi[i])))
    if rails:
        lines.append('rails:')
        lines.extend('    ' + r for r in rails)
    if speed is not None:
        lines.append('speed: %s' % (fmt(speed),))
    if accel is not None:
        lines.append('accel: %s' % (fmt(accel),))
    if lock_homing is not None:
        lines.append('lock_homing: %s' % (lock_homing,))
    if z_moves is not None:
        lines.append('z_moves: %s' % (z_moves,))
    if suspend:
        lines.append('suspend_runout: %s' % (", ".join(suspend),))
    return '\n'.join(lines) + '\n'


class UI:
    def __init__(self, inp=None, out=None):
        self.inp = inp or sys.stdin
        self.out = out or sys.stdout

    def say(self, *lines):
        for l in lines:
            self.out.write(l + '\n')
        self.out.flush()

    def ask(self, q, default=''):
        hint = (' [%s]' % (default,)) if default not in ('', None) else ''
        self.out.write('%s%s: ' % (q, hint))
        self.out.flush()
        line = self.inp.readline()
        if not line:
            raise Abort()
        ans = line.strip()
        if self.inp is not sys.stdin or not self.inp.isatty():
            self.out.write(ans + '\n')          # echo scripted answers
        return ans if ans else (default if default is not None else '')

    def yes(self, q, default=True):
        while True:
            a = self.ask(q + (' [Y/n]' if default else ' [y/N]'), None)
            if not a:
                return default
            if a.lower() in ('y', 'yes', 's', 'si'):
                return True
            if a.lower() in ('n', 'no'):
                return False

    def choose(self, q, options, default):
        self.say(q)
        for i, (_, label) in enumerate(options, 1):
            self.say('  %d) %s' % (i, label))
        while True:
            a = self.ask('Choice', str(default))
            if a.isdigit() and 1 <= int(a) <= len(options):
                return options[int(a) - 1][0]

    def number(self, q, default=None, allow_empty=False, minval=None):
        while True:
            a = self.ask(q, '' if default is None else fmt(default))
            if a == '' and allow_empty:
                return None
            try:
                v = float(a.replace(',', '.'))
            except ValueError:
                self.say('  a number, please')
                continue
            if minval is not None and v < minval:
                self.say('  at least %s' % (fmt(minval),))
                continue
            return v


class Wizard:
    def __init__(self, ui, mr, config_dir, target=None, restart=True):
        self.ui = ui
        self.mr = mr
        self.config_dir = config_dir
        self.printer_cfg = os.path.join(config_dir, 'printer.cfg')
        self.backup_dir = os.path.join(os.path.dirname(config_dir), 'backup',
                                       'no_print_zone')
        self.restart = restart
        self.mod = load_module()
        self.target = target or self._pick_target()
        self.limits = None
        if mr is not None:
            try:
                th = mr.toolhead()
                self.limits = (th['axis_minimum'][:3], th['axis_maximum'][:3])
            except Exception:
                pass

    def _pick_target(self):
        files = find_zones(self.printer_cfg)
        if len(files) == 1:
            return list(files)[0]
        if len(files) > 1:
            names = sorted(files)
            k = self.ui.choose('Zones are defined in several files: which one'
                               ' should the wizard edit?',
                               [(n, os.path.relpath(n, self.config_dir))
                                for n in names], 1)
            return k
        return os.path.join(self.config_dir, 'no_print_zone.cfg')

    def existing(self):
        if not os.path.exists(self.target):
            return []
        return [n for n, _ in zone_sections(self.target) if n]

    def run(self):
        ui = self.ui
        ui.say('', 'no_print_zone zone wizard',
               'Zones file: %s' % (os.path.relpath(self.target,
                                                   self.config_dir),),
               'The wizard never moves the printer: you move the toolhead'
               ' from the web interface, it reads the position.')
        if self.mr is None:
            ui.say('Moonraker is not reachable: type the coordinates.')
        if self.target not in included_files(self.printer_cfg):
            ui.say('!! %s is not included by printer.cfg: run install.sh'
                   ' first, or zones written here are ignored.' % (
                       os.path.basename(self.target),))
        while True:
            names = self.existing()
            ui.say('', 'Zones in this file: %s' % (", ".join(names) or
                                                   "none",))
            action = ui.choose('What do you want to do?',
                               [('add', 'add a zone'),
                                ('remove', 'remove a zone'),
                                ('quit', 'quit')], 1 if not names else 3)
            if action == 'quit':
                return
            if action == 'remove':
                self.remove(names)
            else:
                self.add(names)

    # --------------------------------------------------------------
    def point(self, what, need_z=False):
        ui = self.ui
        axes = 'xyz' if need_z else 'xy'
        while True:
            ui.say('  ' + what)
            a = ui.ask('  Enter = read the toolhead position, or type %s'
                       % ('X,Y,Z' if need_z else 'X,Y'), '')
            if a == '':
                if self.mr is None:
                    ui.say('  Moonraker not reachable: type the coordinates')
                    continue
                try:
                    th = self.mr.toolhead()
                except Exception as e:
                    ui.say('  cannot read the position (%s)' % (e,))
                    continue
                missing = [x.upper() for x in axes
                           if x not in th['homed_axes']]
                if missing:
                    ui.say('  %s not homed: the position would be wrong'
                           ' (with the motors off Klipper keeps showing the'
                           ' last one). Home, go there again, press Enter.'
                           % ('/'.join(missing),))
                    continue
                p = [float(v) for v in th['position'][:3]]
                ui.say('  read X%.2f Y%.2f Z%.2f' % tuple(p))
                return p
            try:
                vals = [float(v) for v in re.split(r'[,;\s]+', a) if v]
            except ValueError:
                vals = []
            if len(vals) == len(axes) or (len(vals) == 3 and not need_z):
                return (vals + [0.])[:3]
            ui.say('  expected %d numbers' % (len(axes),))

    def bounds(self, kind):
        ui = self.ui
        ui.say('', 'AREA. Bring the toolhead over one corner of the zone,'
               ' then over the opposite corner (the nozzle position counts).')
        c1 = self.point('Corner 1:')
        c2 = self.point('Corner 2 (opposite):')
        lo, hi = [-INF] * 3, [INF] * 3
        for i in (0, 1):
            a, b = sorted((c1[i], c2[i]))
            if b - a < 0.5:
                raise ValueError('the two corners have the same %s: the zone'
                                 ' would have no width' % ('XY'[i],))
            lo[i], hi[i] = a, b
            if self.limits:
                amin, amax = self.limits[0][i], self.limits[1][i]
                ax = 'XY'[i]
                if a - amin <= 1.:
                    if ui.yes('  %s %s is at the end of travel: should the'
                              ' zone reach the frame on that side?'
                              % (ax, fmt(a)), True):
                        lo[i] = -INF
                if amax - b <= 1.:
                    if ui.yes('  %s %s is at the end of travel: should the'
                              ' zone reach the frame on that side?'
                              % (ax, fmt(b)), True):
                        hi[i] = INF
        if kind != 'rails' and ui.yes(
                'HEIGHT. Is the obstacle low, so that the nozzle may pass over'
                ' it?', False):
            p = self.point('Bring the nozzle just above the top of the'
                           ' obstacle:', need_z=True)
            hi[2] = p[2]
        m = ui.number('MARGIN added on every side with a limit (mm)', 1.,
                      minval=0.)
        for i in range(3):
            if lo[i] > -INF:
                lo[i] -= m
            if hi[i] < INF:
                hi[i] += m
        return lo, hi

    def rails(self, lo, hi):
        ui = self.ui
        ui.say('', 'RAILS. A rail is a straight path along X or Y: the only'
               ' way into the zone.',
               'For each rail: put the toolhead at its OUTER end (outside the'
               ' zone, where it is safe to start), then move along ONE axis to'
               ' its INNER end.')
        rails, prev = [], None
        while True:
            n = len(rails) + 1
            p0 = None
            if prev is not None and ui.yes(
                    'Rail %d starts where rail %d ends (X%.2f Y%.2f)?'
                    % (n, n - 1, prev[0], prev[1]), True):
                p0 = prev
            if p0 is None:
                p0 = self.point('Rail %d, OUTER end:' % (n,))
            p1 = self.point('Rail %d, INNER end (move along one axis only):'
                            % (n,))
            try:
                r = rail_from_points(p0, p1)
                # checked right away against the zone (same code as Klipper)
                self.mod.Zone('check', 'rails', lo, hi, [self.mod.Rail(r)])
            except ValueError as e:
                ui.say('  !! %s. Again.' % (e,))
                continue
            ui.say('  rail %d: %s' % (n, r))
            rails.append(r)
            prev = p1
            if not ui.yes('Add another rail?', False):
                return rails

    def add(self, names):
        ui = self.ui
        while True:
            name = ui.ask('Zone name (letters, digits, _)').lower()
            if not NAME_RE.match(name):
                ui.say('  only a-z, 0-9 and _')
            elif name in names:
                ui.say('  a zone named %s exists already' % (name,))
            else:
                break
        kind = ui.choose('Type of zone:', [
            ('keepout', 'keepout:  never entered (clips, sensors, fixed'
                        ' obstacles)'),
            ('reserved', 'reserved: entered only by your macros, between'
                         ' NPZ_OPEN and NPZ_CLOSE (purge, brush, bucket)'),
            ('rails', 'rails:    entered only along fixed straight paths'
                      ' (docks, a cutter lever pushed on the frame)')], 1)
        while True:
            try:
                lo, hi = self.bounds(kind)
                rails = self.rails(lo, hi) if kind == 'rails' else []
                speed = accel = lock = z_moves = None
                suspend = []
                if kind == 'rails':
                    speed = ui.number('Maximum speed inside the zone (mm/s)',
                                      10., minval=1.)
                    accel = 500.
                    lock = ui.yes('Block homing while the toolhead is inside?'
                                  ' (recommended)', True)
                    z_moves = ui.choose(
                        'Moves of Z alone with the toolhead inside:', [
                            ('deny', 'never (safest)'),
                            ('idle', 'only with no print running and Z'
                                     ' homed'),
                            ('allow', 'always')], 1)
                elif kind == 'reserved':
                    speed = ui.number('Maximum speed inside the zone (mm/s,'
                                      ' Enter = no limit)', None,
                                      allow_empty=True, minval=1.)
                if kind != 'keepout':
                    sensors = (self.mr.runout_sensors() if self.mr else [])
                    if sensors:
                        ui.say('Filament runout sensors: %s. A PAUSE triggered'
                               ' inside the zone could park along a forbidden'
                               ' path.' % (", ".join(sensors),))
                        a = ui.ask('Sensors to suspend while working in the'
                                   ' zone (comma separated, Enter = none)', '')
                        suspend = [s.strip() for s in a.split(',')
                                   if s.strip()]
                        bad = [s for s in suspend if s not in sensors]
                        if bad:
                            raise ValueError('unknown sensor %s'
                                             % (", ".join(bad),))
                zone = self.mod.Zone(name, kind, lo, hi,
                                     [self.mod.Rail(r) for r in rails],
                                     speed, accel, bool(lock),
                                     z_moves or 'deny', 0., suspend)
                if self.limits:
                    self._check_travel(zone)
                break
            except ValueError as e:
                ui.say('  !! %s' % (e,))
                if not ui.yes('Start this zone again?', True):
                    return
        section = zone_section(
            name, kind, lo, hi, rails, speed, accel, lock, z_moves, suspend,
            'created by the no_print_zone wizard, %s' % (
                time.strftime('%Y-%m-%d'),))
        ui.say('', section)
        if not ui.yes('Write this zone to %s?' % (
                os.path.relpath(self.target, self.config_dir),), True):
            return
        append_section(self.target, section, self.backup_dir)
        ui.say('==> written')
        if kind == 'reserved':
            ui.say('Your macros open it with NPZ_OPEN ZONE=%s and close it with'
                   ' NPZ_CLOSE ZONE=%s.' % (name, name))
        elif kind == 'rails':
            ui.say('Moves inward along the rails need NPZ_OPEN ZONE=%s;'
                   ' moving back out is always allowed.' % (name,))
        self.apply(name)

    def _check_travel(self, zone):
        amin, amax = self.limits
        for r in zone.rails:
            pts = dict(r.fixed)
            pts[r.axis] = r.end
            for i, v in pts.items():
                if not amin[i] - 0.01 <= v <= amax[i] + 0.01:
                    raise ValueError("rail '%s' leaves the travel range of %s"
                                     % (r.text, 'XYZ'[i]))

    def apply(self, added=None):
        ui = self.ui
        if not self.restart or self.mr is None:
            ui.say('Restart Klipper to load the change (the service, not the'
                   ' RESTART command).')
            return
        if not ui.yes('Restart Klipper now to load it? (homing is lost)',
                      True):
            ui.say('Remember: the change works only after restarting the'
                   ' Klipper service.')
            return
        res = restart_service(self.mr, 'klipper', ui.say)
        if res == 'ok':
            if added:
                try:
                    st = self.mr.query('no_print_zone ' + added)
                    ui.say('    zone %s active (%s)' % (
                        added, st['no_print_zone ' + added]['type']))
                except Exception:
                    pass
            return
        if res == 'skipped':
            ui.say('The change is written: it works after the next restart of'
                   ' the Klipper service.')
            return
        if added and ui.yes('Klipper did not start: remove the zone %s again'
                            ' and restart?' % (added,), True):
            remove_zone(self.target, added, self.backup_dir)
            restart_service(self.mr, 'klipper', ui.say)

    def remove(self, names):
        ui = self.ui
        if not names:
            ui.say('  no zone in this file')
            return
        name = ui.choose('Zone to remove:', [(n, n) for n in names] +
                         [('', 'none')], len(names) + 1)
        if not name or not ui.yes('Remove zone %s?' % (name,), False):
            return
        remove_zone(self.target, name, self.backup_dir)
        ui.say('==> removed (backup in %s)' % (self.backup_dir,))
        self.apply()


######################################################################

def find_config_dir():
    home = os.path.expanduser('~')
    cands = []
    for d in ([os.path.join(home, 'printer_data', 'config')] +
              sorted(glob.glob(os.path.join(home, '*_data', 'config'))) +
              [os.path.join(home, 'klipper_config')]):
        if os.path.isfile(os.path.join(d, 'printer.cfg')) and d not in cands:
            cands.append(d)
    return cands


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest='cmd')
    w = sub.add_parser('wizard')
    w.add_argument('--config-dir')
    w.add_argument('--moonraker')
    w.add_argument('--file')
    w.add_argument('--no-restart', action='store_true')
    for name, nargs in (('find-zones', 1), ('add-include', 3),
                        ('remove-include', 2), ('add-update-manager', 4),
                        ('remove-update-manager', 2), ('moonraker-url', 1),
                        ('printer-state', 1), ('npz-loaded', 1),
                        ('restart', 2)):
        p = sub.add_parser(name)
        p.add_argument('args', nargs=nargs)
    a = ap.parse_args(argv)
    if a.cmd == 'wizard':
        cfg = a.config_dir
        if not cfg:
            c = find_config_dir()
            if len(c) != 1:
                sys.exit('printer.cfg not found automatically (%s): pass'
                         ' --config-dir' % (", ".join(c) or "none",))
            cfg = c[0]
        cfg = os.path.abspath(os.path.expanduser(cfg))
        mr = Moonraker(a.moonraker or moonraker_url(cfg))
        if not mr.reachable():
            mr = None
        try:
            Wizard(UI(), mr, cfg, a.file and os.path.abspath(a.file),
                   not a.no_restart).run()
        except (Abort, KeyboardInterrupt):
            print('\nwizard closed')
        return 0
    args = a.args if a.cmd else []
    if a.cmd == 'find-zones':
        res = find_zones(args[0])
        for f, secs in res.items():
            print('%s: %s' % (f, ", ".join(s or '(global)' for s in secs)))
        return 0 if res else 1
    if a.cmd == 'add-include':
        print('added' if add_include(*args) else 'present')
    elif a.cmd == 'remove-include':
        print('removed' if remove_include(*args) else 'absent')
    elif a.cmd == 'add-update-manager':
        print('added' if add_update_manager(*args) else 'present')
    elif a.cmd == 'remove-update-manager':
        print('removed' if remove_update_manager(*args) else 'absent')
    elif a.cmd == 'moonraker-url':
        print(moonraker_url(args[0]))
    elif a.cmd == 'printer-state':
        print(Moonraker(args[0]).print_state() or 'unreachable')
    elif a.cmd == 'npz-loaded':
        try:
            print('yes' if 'no_print_zone' in Moonraker(args[0]).objects()
                  else 'no')
        except Exception:
            print('unreachable')
    elif a.cmd == 'restart':
        return 0 if restart_service(Moonraker(args[0]), args[1]) == 'ok' else 1
    else:
        ap.print_help()
        return 2
    return 0


if __name__ == '__main__':
    sys.exit(main())
