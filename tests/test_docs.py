#!/usr/bin/env python3
# Keeps the documentation honest: every zone example in README.md and
# docs/configuration.md is loaded by the real Klipper (batch mode), and the
# macro examples of the guide are run against the zones they use.
#
#   ~/klippy-env/bin/python tests/test_docs.py --klipper ~/klipper --dict DICT
import argparse, os, re, subprocess, sys, tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
DOCS = [os.path.join(REPO, 'README.md'),
        os.path.join(REPO, 'docs', 'configuration.md')]


def ini_blocks(path):
    text = open(path).read()
    return re.findall(r'```ini\n(.*?)```', text, re.S)


def hardware():
    # tests/printer.cfg without its own zones
    text = open(os.path.join(HERE, 'printer.cfg')).read()
    return text.split('#####')[0]


def zone_names(block):
    return re.findall(r'^\[no_print_zone (\w+)\]', block, re.M)


def usable(block):
    # skip the option skeletons of the reference (empty "type:" etc.)
    return (zone_names(block) and not re.search(r'^type:\s*$', block, re.M)
            and 'NAME]' not in block and '...' not in block)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--klipper', required=True)
    ap.add_argument('--dict', required=True)
    a = ap.parse_args()
    blocks = []
    for doc in DOCS:
        for b in ini_blocks(doc):
            if usable(b):
                # the test printer has one runout sensor, named runout1
                b = re.sub(r'^suspend_runout:.*$', 'suspend_runout: runout1',
                           b, flags=re.M)
                blocks.append((os.path.basename(doc), b))
    macros = {}
    for b in ini_blocks(DOCS[1]):
        m = re.match(r'\[gcode_macro (\w+)\]', b.strip())
        if m:
            macros[m.group(1)] = b
    work = tempfile.mkdtemp(prefix='npz_docs_')
    cfg = os.path.join(work, 'cfg')
    os.makedirs(cfg)
    scen = []
    variants = []
    for i, (doc, b) in enumerate(blocks):
        names = zone_names(b)
        fname = 'zones_%02d.cfg' % (i,)
        open(os.path.join(cfg, fname), 'w').write(b)
        variants.append(fname)
        scen.append('### D%02d_%s_%s\n#! include: %s\n'
                    '#! expect: ok: zone %s [\nNPZ_STATUS\n'
                    % (i, doc.split('.')[0], '_'.join(names), fname, names[0]))
    # macro examples, each with the zones it needs
    runs = [('PURGE_LINE', 'purge', 'G28\nPURGE_LINE\nG1 X100 Y100 F6000',
             'purge: inside open zone'),
            ('WIPE_NOZZLE', 'bucket', 'G28\nWIPE_NOZZLE\nG1 X100 Y100 F6000',
             'bucket: inside open zone'),
            ('DOCK_IN_AND_OUT', 'dock', 'G28\nDOCK_IN_AND_OUT',
             'zone dock released')]
    for name, zone, gcode, expect in runs:
        block = next((b for _, b in blocks
                      if '[no_print_zone %s]' % zone in b), None)
        if name not in macros or block is None:
            print('[FAIL] macro example %s or zone %s not found in the docs'
                  % (name, zone))
            return 1
        fname = 'macro_%s.cfg' % (name.lower(),)
        open(os.path.join(cfg, fname), 'w').write(block + '\n' + macros[name])
        scen.append('### M_%s\n#! include: %s\n#! expect: ok: %s\n%s\n'
                    % (name, fname, expect, gcode))
    printer = hardware() + '\n[no_print_zone]\n'
    open(os.path.join(cfg, 'printer.cfg'), 'w').write(printer)
    test = os.path.join(work, 'docs.test')
    open(test, 'w').write('\n'.join(scen))
    py = sys.executable
    cmd = [py, os.path.join(HERE, 'run_tests.py'), '--klipper', a.klipper,
           '--config', os.path.join(cfg, 'printer.cfg'), '--dict', a.dict,
           '--work', os.path.join(work, 'run'), test]
    return subprocess.call(cmd)


if __name__ == '__main__':
    sys.exit(main())
