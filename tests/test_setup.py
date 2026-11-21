# Tests of tools/npz_setup.py (installer helpers and zone wizard), with a fake
# Moonraker: no printer, no Klipper needed.
#   python3 -m unittest discover -s tests -p 'test_*.py'
import importlib.util, io, json, os, shutil, sys, tempfile, threading, unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'npz_setup', os.path.join(HERE, '..', 'tools', 'npz_setup.py'))
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

PRINTER = ("[printer]\nkinematics: corexy\n[include sub/*.cfg]\n\n"
           "#*# <---------------------- SAVE_CONFIG ---------------------->\n"
           "#*# DO NOT EDIT THIS BLOCK OR BELOW.\n#*#\n")
MOONRAKER = ("[server]\nhost: 0.0.0.0\nport: 7999\n\n[update_manager]\n"
             "refresh_interval: 168\n\n[update_manager mainsail]\ntype: web\n")


class Files(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, 'config')
        os.makedirs(os.path.join(self.cfg, 'sub'))
        self.bk = os.path.join(self.d, 'backup')
        self.pc = self.write('printer.cfg', PRINTER)

    def tearDown(self):
        shutil.rmtree(self.d)

    def write(self, rel, text):
        p = os.path.join(self.cfg, rel)
        with open(p, 'w', newline='') as f:
            f.write(text)
        return p

    def read(self, p):
        with open(p, newline='') as f:
            return f.read()

    def test_include_add_remove_roundtrip(self):
        self.assertTrue(setup.add_include(self.pc, 'no_print_zone.cfg',
                                          self.bk))
        t = self.read(self.pc)
        self.assertLess(t.index('[include no_print_zone.cfg]'),
                        t.index(setup.SAVE_MARK))
        self.assertFalse(setup.add_include(self.pc, 'no_print_zone.cfg',
                                           self.bk))
        self.assertTrue(setup.remove_include(self.pc, self.bk))
        self.assertEqual(self.read(self.pc), PRINTER)
        self.assertTrue(os.listdir(self.bk))

    def test_include_crlf_and_no_save_config(self):
        p = self.write('printer.cfg', '[printer]\r\nkinematics: corexy\r\n')
        setup.add_include(p, 'no_print_zone.cfg', self.bk)
        t = self.read(p)
        self.assertIn('[include no_print_zone.cfg]  ' + setup.MARK + '\r\n', t)
        self.assertNotIn('\n\n', t.replace('\r\n', 'X'))
        setup.remove_include(p, self.bk)
        self.assertEqual(self.read(p), '[printer]\r\nkinematics: corexy\r\n')

    def test_include_through_symlink(self):
        real = self.write('real_printer.cfg', PRINTER)
        os.remove(self.pc)
        os.symlink(real, self.pc)
        setup.add_include(self.pc, 'no_print_zone.cfg', self.bk)
        self.assertTrue(os.path.islink(self.pc))
        self.assertIn('no_print_zone.cfg', self.read(real))

    def test_already_included_by_glob(self):
        self.write('sub/zones.cfg', '[no_print_zone]\n[no_print_zone a]\n'
                   'type: keepout\nx_max: 5\n')
        self.assertFalse(setup.add_include(self.pc, 'sub/zones.cfg', self.bk))
        z = setup.find_zones(self.pc)
        self.assertEqual(list(z.values()), [[None, 'a']])
        # a file with zones that is NOT included does not count
        self.write('old.cfg', '[no_print_zone b]\n')
        self.assertEqual(len(setup.find_zones(self.pc)), 1)

    def test_update_manager_roundtrip(self):
        p = self.write('moonraker.conf', MOONRAKER)
        self.assertEqual(setup.moonraker_url(self.cfg), 'http://localhost:7999')
        self.assertTrue(setup.add_update_manager(
            p, '~/klipper-no-print-zone', 'https://x/y.git', self.bk))
        self.assertFalse(setup.add_update_manager(p, 'a', 'b', self.bk))
        self.assertIn('[update_manager no_print_zone]\ntype: git_repo',
                      self.read(p))
        self.assertTrue(setup.remove_update_manager(p, self.bk))
        self.assertEqual(self.read(p), MOONRAKER)

    def test_update_manager_in_the_middle(self):
        p = self.write('moonraker.conf', MOONRAKER)
        setup.add_update_manager(p, 'a', 'b', self.bk)
        with open(p, 'a') as f:
            f.write('\n# webcam\n[webcam x]\nservice: y\n')
        setup.remove_update_manager(p, self.bk)
        t = self.read(p)
        self.assertNotIn('no_print_zone', t)
        self.assertIn('type: web\n\n# webcam\n[webcam x]', t)

    def test_remove_zone_keeps_neighbours(self):
        text = ('[no_print_zone]\n\n[no_print_zone a]\n# note\ntype: keepout\n'
                'x_max: 5\n\n# comment of b\n[no_print_zone b]\ntype: keepout\n'
                'x_min: 9\n')
        p = self.write('z.cfg', text)
        self.assertTrue(setup.remove_zone(p, 'a', self.bk))
        self.assertEqual(self.read(p), '[no_print_zone]\n\n# comment of b\n'
                         '[no_print_zone b]\ntype: keepout\nx_min: 9\n')
        self.assertFalse(setup.remove_zone(p, 'zz', self.bk))

    def test_rail_from_points(self):
        r = setup.rail_from_points([10.5, 27, 5], [10.5, 0, 5])
        self.assertEqual(r, 'X10.5 Y27:0')
        self.assertEqual(setup.rail_from_points([10.5, 0, 5], [0, 0.01, 5]),
                         'Y0 X10.5:0')
        self.assertRaises(ValueError, setup.rail_from_points, [0, 0, 0],
                          [5, 5, 0])
        self.assertRaises(ValueError, setup.rail_from_points, [0, 0, 0],
                          [0, 5, 2])


######################################################################
# Fake Moonraker

class FakePrinter:
    def __init__(self):
        self.position = [10.5, 27., 5., 0.]
        self.homed = 'xyz'
        self.print_state = 'standby'
        self.pid = 100
        self.state_after_restart = 'ready'
        self.state = 'ready'
        self.restarts = 0
        self.zones = []


def fake_server(printer):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def reply(self, result, code=200):
            body = json.dumps({'result': result}).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query, keep_blank_values=True)
            if u.path == '/server/info':
                return self.reply({'klippy_state': printer.state})
            if u.path == '/printer/info':
                return self.reply({'state': printer.state, 'process_id':
                                   printer.pid, 'state_message': 'msg %s'
                                   % (printer.state,)})
            if u.path == '/printer/objects/list':
                return self.reply({'objects': [
                    'toolhead', 'filament_switch_sensor fs1', 'no_print_zone'
                ] + ['no_print_zone ' + z for z in printer.zones]})
            if u.path == '/machine/system_info':
                return self.reply({'system_info': {'available_services': [
                    'klipper', 'moonraker', 'klipper_mcu']}})
            if u.path == '/printer/objects/query':
                st = {}
                for o in q:
                    if o == 'toolhead':
                        st[o] = {'position': printer.position,
                                 'homed_axes': printer.homed,
                                 'axis_minimum': [0, 0, -10, 0],
                                 'axis_maximum': [350, 358, 272, 0]}
                    elif o == 'print_stats':
                        st[o] = {'state': printer.print_state}
                    elif o == 'no_print_zone':
                        st[o] = {'zones': printer.zones}
                    elif o.startswith('no_print_zone '):
                        st[o] = {'type': 'rails'}
                return self.reply({'status': st})
            self.reply({}, 404)

        def do_POST(self):
            u = urlparse(self.path)
            if u.path == '/machine/services/restart':
                printer.restarts += 1
                printer.pid += 1
                printer.state = printer.state_after_restart
                return self.reply('ok')
            self.reply({}, 404)

    srv = HTTPServer(('127.0.0.1', 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


class Wizard(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.cfg = os.path.join(self.d, 'config')
        os.makedirs(self.cfg)
        with open(os.path.join(self.cfg, 'printer.cfg'), 'w') as f:
            f.write('[printer]\n[include no_print_zone.cfg]\n')
        self.zfile = os.path.join(self.cfg, 'no_print_zone.cfg')
        shutil.copy(os.path.join(HERE, '..', 'examples', 'starter.cfg'),
                    self.zfile)
        self.printer = FakePrinter()
        self.srv = fake_server(self.printer)
        setup.time.sleep = lambda s: None          # no waiting in tests

    def tearDown(self):
        self.srv.shutdown()
        self.srv.server_close()
        shutil.rmtree(self.d)

    def run_wizard(self, answers, moonraker=True, positions=()):
        mr = (setup.Moonraker('http://127.0.0.1:%d' % self.srv.server_port)
              if moonraker else None)
        out = io.StringIO()
        inp = io.StringIO('\n'.join(answers) + '\n')
        w = setup.Wizard(setup.UI(inp, out), mr, self.cfg)
        if positions:
            # every position read takes the next (position, homed_axes)
            pos = list(positions)
            orig = mr.toolhead

            def toolhead():
                th = orig()
                if pos:
                    th['position'], th['homed_axes'] = pos.pop(0)
                return th
            mr.toolhead = toolhead
        try:
            w.run()
        except setup.Abort:
            self.fail('wizard ran out of answers:\n' + out.getvalue()[-1500:])
        return out.getvalue()

    def zones_text(self):
        with open(self.zfile) as f:
            return f.read()

    def test_rails_zone_typed_without_moonraker(self):
        out = self.run_wizard([
            '1', 'dock2', '3',
            '0,0', '11,22', '1',              # corners, margin
            '10.5,27', '10.5,0', 'y',         # rail 1, another
            'y', '0,0', 'n',                  # rail 2 from the end of 1
            '10', 'y', '2',                   # speed, lock homing, z idle
            'y', '3'], moonraker=False)       # write, quit
        t = self.zones_text()
        self.assertIn('[no_print_zone dock2]\n', t)
        for line in ('type: rails', 'x_min: -1', 'x_max: 12', 'y_min: -1',
                     'y_max: 23', '    X10.5 Y27:0', '    Y0 X10.5:0',
                     'speed: 10', 'lock_homing: True', 'z_moves: idle'):
            self.assertIn(line + '\n', t, out)

    def test_reserved_zone_with_height_and_live_positions(self):
        out = self.run_wizard([
            '1', 'bucket', '2',
            '', '',                           # corners read from the printer
            'y',                              # extend to the frame (Y max)
            'y', '', '',                      # low obstacle; Z not homed, again
            '1',                              # margin
            '',                               # no speed limit
            'fs1',                            # suspend runout
            'y',                              # write
            'n',                              # do not restart now
            '3'],
            positions=[([230, 285, 20, 0], 'xy'), ([300, 358, 20, 0], 'xy'),
                       ([260, 300, 12, 0], 'xy'), ([260, 300, 12, 0], 'xyz')])
        self.assertIn('Z not homed', out)
        t = self.zones_text()
        for line in ('type: reserved', 'x_min: 229', 'x_max: 301',
                     'y_min: 284', 'z_max: 13', 'suspend_runout: fs1'):
            self.assertIn(line + '\n', t, out)
        self.assertNotIn('y_max', t.split('[no_print_zone bucket]')[1])

    def test_unhomed_position_refused(self):
        self.printer.homed = ''
        self.printer.position = [1, 2, 3, 0]
        out = self.run_wizard(['1', 'k', '1', '', '5,5', '9,9', 'n', '0',
                               'y', 'n', '3'])
        self.assertIn('X/Y not homed', out)
        self.assertIn('x_min: 5\n', self.zones_text())

    def test_bad_rail_then_retry(self):
        out = self.run_wizard([
            '1', 'd', '3', '0,0', '11,22', '1',
            '20,27', '20,0',                  # X20 is outside the zone: again
            '10.5,27', '10.5,0', 'n',         # this one is fine
            '10', 'y', '1',
            'y', '3'], moonraker=False)
        self.assertIn('X20 is outside the zone', out)
        t = self.zones_text()
        self.assertIn('    X10.5 Y27:0\n', t)
        self.assertNotIn('X20', t)

    def test_restart_after_write(self):
        self.printer.zones = ['k']
        out = self.run_wizard(['1', 'k', '1', '5,5', '9,9', 'n', '0', 'y',
                               'y', '3'])
        self.assertEqual(self.printer.restarts, 1)
        self.assertIn('Klipper: ready', out)
        self.assertIn('zone k active', out)

    def test_klipper_fails_zone_removed_again(self):
        self.printer.state_after_restart = 'error'
        out = self.run_wizard(['1', 'k', '1', '5,5', '9,9', 'n', '0', 'y',
                               'y', 'y', '3'])
        self.assertIn('Klipper: error', out)
        self.assertEqual(self.printer.restarts, 2)
        self.assertNotIn('[no_print_zone k]', self.zones_text())

    def test_no_restart_during_print(self):
        self.printer.print_state = 'printing'
        out = self.run_wizard(['1', 'k', '1', '5,5', '9,9', 'n', '0', 'y',
                               'y', '3'])
        self.assertIn('Klipper NOT restarted', out)
        self.assertEqual(self.printer.restarts, 0)

    def test_remove_zone(self):
        self.run_wizard(['1', 'k', '1', '5,5', '9,9', 'n', '0', 'y', 'n',
                         '3'])
        out = self.run_wizard(['2', '1', 'y', 'n', '3'])
        self.assertNotIn('[no_print_zone k]', self.zones_text())
        self.assertIn('removed', out)


if __name__ == '__main__':
    unittest.main()
