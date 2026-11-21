#!/usr/bin/env python3
# Dry-run tests of no_print_zone with the real Klipper in batch mode: Klipper
# reads a G-code file instead of talking to the boards, nothing moves.
#
# Usage (from the repository root):
#   python3 tests/run_tests.py --klipper ~/klipper \
#       --config tests/printer.cfg --dict /path/to/mcu.dict \
#       tests/scenarios/*.test
#
# Options:
#   --dict PATH | NAME=PATH   MCU data dictionaries (klipper.dict produced by
#                             "make" for the MCU named in [mcu] / [mcu NAME])
#   --module PATH             no_print_zone.py to test (default: repo copy)
#   --replace REL=PATH        replace a file of the copied config directory
#   --overrides FILE          extra config appended to the main config file
#   --work DIR                scratch directory (default /tmp/npz_test)
#   -v                        show the module's log lines for every scenario
#
# Scenario files (*.test): one scenario, or several each starting with a
# "### NAME" line. G-code lines, plus directives in comments:
#   #! expect: ok                  Klipper must finish without errors
#   #! expect: ok: TEXT            ... and TEXT must appear in the log
#   #! expect: error: TEXT         Klipper must stop with an error and TEXT
#                                  must appear in the log
#   #! log_has: TEXT / #! log_not: TEXT   extra checks on the log
#   #! set: SECTION | OPTION | VALUE       config variant for this scenario
#   #! keep_state                  keep the state file of the previous run
#   #! state: JSON                 write this content into the state file
#   #! sdfile: NAME ... #! end     lines in between go to a virtual SD file
#   @NAME                          insert tests/scenarios/_NAME.gcode
# {VERSION} in a directive is replaced with the Klipper version under test.
# In batch mode Klipper logs a G-code error and goes on: for "expect: error"
# the failing command must be the last line (or the last line of the printed
# SD file), and the runner checks that everything before it runs cleanly.
import argparse, glob, os, re, shutil, subprocess, sys

MARKER = '#*# <---------------------- SAVE_CONFIG'
LOG_FILTER = re.compile(r'no_print_zone|Homing blocked|FORCE_MOVE|'
                        r'STEPPER_BUZZ|Move out of range|Must home|NPZ_|'
                        r'[Ee]rror|zone')


def git_version(klipper):
    try:
        out = subprocess.check_output(
            ['git', '-C', klipper, 'describe', '--always', '--tags',
             '--long', '--dirty'], stderr=subprocess.DEVNULL)
        return out.decode().strip()
    except Exception:
        return 'unknown'


def insert_config(text, extra):
    i = text.find(MARKER)
    if i < 0:
        return text + '\n' + extra + '\n'
    return text[:i] + extra + '\n' + text[i:]


def load_scenarios(path, version):
    # one scenario per file, or several separated by "### NAME" lines
    inc_dir = os.path.dirname(os.path.abspath(path))
    blocks, name, lines = [], None, []
    for raw in open(path).read().splitlines():
        if raw.startswith('### '):
            if name is not None:
                blocks.append((name, lines))
            name, lines = raw[4:].strip(), []
        else:
            lines.append(raw)
    if name is None:
        name = os.path.splitext(os.path.basename(path))[0]
    blocks.append((name, lines))
    return [Scenario(path, n, l, inc_dir, version) for n, l in blocks
            if any(x.strip() for x in l)]


class Scenario:
    def __init__(self, path, name, lines, inc_dir, version):
        self.path = path
        self.name = name
        self.expect = None
        self.log_has, self.log_not, self.sets = [], [], []
        self.keep_state = False
        self.state = None
        self.sdfiles = {}
        self.gcode = []
        cur_sd = None
        for raw in lines:
            line = raw.replace('{VERSION}', version)
            s = line.strip()
            if s.startswith('#!'):
                d = s[2:].strip()
                key, _, val = d.partition(':')
                key, val = key.strip(), val.strip()
                if key == 'expect':
                    self.expect = val
                elif key == 'log_has':
                    self.log_has.append(val)
                elif key == 'log_not':
                    self.log_not.append(val)
                elif key == 'set':
                    parts = [p.strip() for p in val.split('|')]
                    if len(parts) != 3:
                        raise ValueError('%s: bad set: %s' % (path, val))
                    self.sets.append(parts)
                elif key == 'keep_state':
                    self.keep_state = True
                elif key == 'state':
                    self.state = val
                elif key == 'sdfile':
                    cur_sd = val
                    self.sdfiles[cur_sd] = []
                elif key == 'end':
                    cur_sd = None
                else:
                    raise ValueError('%s: unknown directive %s' % (path, d))
                continue
            if s.startswith('@'):
                inc = os.path.join(inc_dir, '_%s.gcode' % (s[1:],))
                lines = open(inc).read().splitlines()
            elif not s or s.startswith('#'):
                continue
            else:
                lines = [line]
            if cur_sd is not None:
                self.sdfiles[cur_sd].extend(lines)
            else:
                self.gcode.extend(lines)
        if self.expect is None:
            raise ValueError('%s %s: missing "#! expect:"' % (path, name))

    def prefix(self):
        # (gcode, sdfiles) without the command expected to fail, or None
        if self.gcode and self.gcode[-1].strip().upper().startswith(
                'SDCARD_PRINT_FILE') and self.sdfiles:
            sd = dict(self.sdfiles)
            name = list(sd)[-1]
            if not sd[name]:
                return None
            sd[name] = sd[name][:-1]
            return self.gcode, sd
        if len(self.gcode) < 2:
            return None             # nothing before it but the startup
        return self.gcode[:-1], self.sdfiles


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--klipper', required=True)
    ap.add_argument('--config', required=True)
    ap.add_argument('--dict', action='append', required=True)
    ap.add_argument('--module')
    ap.add_argument('--replace', action='append', default=[])
    ap.add_argument('--overrides')
    ap.add_argument('--work', default='/tmp/npz_test')
    ap.add_argument('-v', action='store_true')
    ap.add_argument('scenarios', nargs='+')
    args = ap.parse_args()
    try:
        sys.stdout.reconfigure(line_buffering=True)     # progress in logs
    except AttributeError:
        pass
    here = os.path.dirname(os.path.abspath(__file__))
    module = args.module or os.path.join(here, '..', 'no_print_zone.py')
    klipper = os.path.expanduser(args.klipper)
    work = args.work
    version = git_version(klipper)
    print('Klipper under test: %s' % (version,))
    # copy of klippy with the module under test
    shutil.rmtree(work, ignore_errors=True)
    for d in ('state', 'gcodes', 'logs', 'scen', 'out'):
        os.makedirs(os.path.join(work, d))
    # for [save_variables] in --overrides: filename: {WORK}/variables.cfg
    open(os.path.join(work, 'variables.cfg'), 'a').close()
    shutil.copytree(os.path.join(klipper, 'klippy'),
                    os.path.join(work, 'klippy'),
                    ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
    dst = os.path.join(work, 'klippy', 'extras', 'no_print_zone.py')
    if os.path.lexists(dst):
        os.remove(dst)
    shutil.copy(module, dst)
    with open(os.path.join(work, 'klippy', '.version'), 'w') as f:
        f.write(version)
    # copy of the config directory (symlinks resolved)
    cfg_src = os.path.dirname(os.path.abspath(os.path.expanduser(
        args.config)))
    cfg_dir = os.path.join(work, 'cfg')
    shutil.copytree(cfg_src, cfg_dir, symlinks=False,
                    ignore=shutil.ignore_patterns('*.bak*', '*.tgz', '.git'))
    for rep in args.replace:
        rel, _, src = rep.partition('=')
        shutil.copy(os.path.expanduser(src), os.path.join(cfg_dir, rel))
    main_cfg = os.path.join(cfg_dir, os.path.basename(args.config))
    state_file = os.path.join(work, 'state', 'no_print_zone.json')
    extra = ('## --- test only ---\n[no_print_zone]\nstate_file: %s\n'
             '[virtual_sdcard]\npath: %s\n' % (
                 state_file, os.path.join(work, 'gcodes')))
    if args.overrides:
        extra += open(os.path.expanduser(args.overrides)).read().replace(
            '{WORK}', work)
    base_text = insert_config(open(main_cfg).read(), extra)
    with open(main_cfg, 'w') as f:
        f.write(base_text)
    dict_args = []
    for d in args.dict:
        dict_args += ['-d', os.path.expanduser(d) if '=' not in d else
                      d.split('=', 1)[0] + '=' + os.path.expanduser(
                          d.split('=', 1)[1])]
    paths = []
    for pattern in args.scenarios:
        paths.extend(sorted(glob.glob(pattern)) or [pattern])
    python = sys.executable
    passed = failed = 0
    results = []
    scenarios = [sc for p in paths for sc in load_scenarios(p, version)]
    names = [sc.name for sc in scenarios]
    dup = sorted(set(n for n in names if names.count(n) > 1))
    if dup:
        sys.exit('duplicate scenario names: %s' % (', '.join(dup),))
    for sc in scenarios:
        cfg = main_cfg
        if sc.sets:
            add = ''.join('[%s]\n%s: %s\n' % tuple(s) for s in sc.sets)
            cfg = os.path.join(cfg_dir, '_variant_%s.cfg' % (sc.name,))
            with open(cfg, 'w') as f:
                f.write(insert_config(base_text, add))
        if not sc.keep_state and os.path.exists(state_file):
            os.remove(state_file)
        if sc.state is not None:
            with open(state_file, 'w') as f:
                f.write(sc.state)

        def run(tag, gcode, sdfiles):
            for name, lines in sdfiles.items():
                with open(os.path.join(work, 'gcodes', name), 'w') as f:
                    f.write('\n'.join(lines) + '\n')
            gfile = os.path.join(work, 'scen', sc.name + tag + '.gcode')
            with open(gfile, 'w') as f:
                f.write('\n'.join(gcode) + '\n')
            log = os.path.join(work, 'logs', sc.name + tag + '.log')
            cmd = [python, os.path.join(work, 'klippy', 'klippy.py'), cfg,
                   '-i', gfile, '-o', os.path.join(work, 'out', sc.name),
                   '-l', log] + dict_args
            try:
                rc = subprocess.call(cmd, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL, timeout=300)
            except subprocess.TimeoutExpired:
                rc = 'timeout'
            text = open(log, errors='replace').read() \
                if os.path.exists(log) else ''
            # skip the config dump that Klipper writes at the top of the log
            i = text.rfind('\n=======================\n')
            return rc, (text[i:] if i >= 0 else text)

        problems = []
        exp = sc.expect
        # Klipper in batch mode logs an error and goes on with the next
        # lines. For an expected error, everything but the failing command
        # (the last line, or the last line of the printed file) must run
        # without errors: otherwise the error could come from elsewhere.
        prefix = sc.prefix()
        if exp.startswith('error:') and prefix is not None:
            saved = (open(state_file).read()
                     if os.path.exists(state_file) else None)
            prc, pbody = run('.prefix', *prefix)
            if prc != 0:
                problems.append('error before the last command (rc=%s): %s'
                                % (prc, ' / '.join(
                                    l for l in pbody.splitlines()
                                    if LOG_FILTER.search(l))[-400:]))
            if saved is None:
                if os.path.exists(state_file):
                    os.remove(state_file)
            else:
                with open(state_file, 'w') as f:
                    f.write(saved)
        rc, body = run('', sc.gcode, sc.sdfiles)
        if exp == 'ok' or exp.startswith('ok:'):
            if rc != 0:
                problems.append('Klipper stopped (rc=%s)' % (rc,))
            want = exp[3:].strip()
            if want and want not in body:
                problems.append('missing in log: %s' % (want,))
        elif exp.startswith('error:'):
            want = exp[6:].strip()
            if rc == 0:
                problems.append('no error (rc=0)')
            if want not in body:
                problems.append('missing in log: %s' % (want,))
        else:
            problems.append('bad expect: %s' % (exp,))
        for t in sc.log_has:
            if t not in body:
                problems.append('missing in log: %s' % (t,))
        for t in sc.log_not:
            if t in body:
                problems.append('unexpected in log: %s' % (t,))
        ok = not problems
        passed += ok
        failed += not ok
        line = '[%s] %s (rc=%s, expect %s)' % ('PASS' if ok else 'FAIL',
                                              sc.name, rc, exp)
        print(line)
        results.append(line)
        if not ok or args.v:
            for p in problems:
                print('      !! ' + p)
            shown = [l for l in body.splitlines() if LOG_FILTER.search(l)]
            for l in shown[-12:]:
                print('      ' + l[:300])
    summary = 'RESULT: %d PASS, %d FAIL' % (passed, failed)
    print('\n' + summary)
    with open(os.path.join(work, 'results.txt'), 'w') as f:
        f.write('Klipper %s\n%s\n%s\n' % (version, '\n'.join(results),
                                          summary))
    sys.exit(1 if failed else 0)


if __name__ == '__main__':
    main()
