# Unit tests of the rerouting of print moves (no Klipper needed).
#   python3 -m unittest discover -s tests -p 'test_*.py'
import importlib.util, math, os, random, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'no_print_zone', os.path.join(HERE, '..', 'no_print_zone.py'))
npz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(npz)

INF = npz.INF
K = (True, True, True)
LIMITS = (0., 350., 0., 355.)


def corner():
    # purge bucket and brush behind the back right corner of the bed
    return npz.Zone('brush', 'reserved', (240, 328, -INF), (INF, INF, INF))


def cutter():
    return npz.Zone('cutter', 'rails', (-INF, -INF, -INF), (11, 22, INF),
                    [npz.Rail('X10.5 Y27:0'), npz.Rail('Y0 X10.5:0')])


def purge():
    return npz.Zone('purge', 'reserved', (-INF, 22, -INF), (15, 340, INF))


def clip():
    return npz.Zone('clip', 'keepout', (280, -INF, -INF), (INF, 20, 4))


class FakeNext:
    """The next transform of the chain: records what reaches it."""
    def __init__(self, pos):
        self.pos = list(pos)
        self.moves = []

    def get_position(self):
        return list(self.pos)

    def move(self, newpos, speed):
        self.moves.append((list(self.pos), list(newpos)))
        self.pos = list(newpos)


class Harness:
    def __init__(self, zones, start, extrude='first', limits=LIMITS):
        self.state = {'active': True, 'open': set()}
        self.zones = zones
        self.r = npz.PrintRerouter(
            zones, limits, active=lambda: self.state['active'],
            closed=lambda z: z.kind == 'keepout' or
            z.name not in self.state['open'],
            known=lambda: K, error=lambda m: RuntimeError(m),
            extrude=extrude, notify=self._note)
        self.notes = []
        self.next = FakeNext(start)
        self.r.next = self.next
        self.r.get_position()

    def _note(self, name, text):
        self.notes.append(name)

    def move(self, x, y, z, e, speed=100.):
        self.next.moves = []
        self.r.move([x, y, z, e], speed)
        return [m[1] for m in self.next.moves]

    def entered(self):
        # True if a move that reached the next transform enters a closed zone
        for a, b in self.next.moves:
            for zone in self.zones:
                if zone.kind != 'keepout' and zone.name in self.state['open']:
                    continue
                if zone.surely_contains(a, K):
                    continue
                if zone.hits(a, b, K):
                    return zone.name
        return None


def close(a, b, tol=1e-6):
    return all(abs(x - y) <= tol for x, y in zip(a, b))


class Square(unittest.TestCase):
    def test_square_gets_a_notch(self):
        h = Harness([corner()], (5, 345, 0.2, 0))
        out = h.move(345, 345, 0.2, 10)           # back edge, into the zone
        self.assertIsNone(h.entered())
        pts = [m[:3] for m in out]
        self.assertTrue(close(pts[0], (240, 345, 0.2)))
        self.assertTrue(close(pts[1], (240, 328, 0.2)))
        self.assertTrue(close(pts[2], (345, 328, 0.2)))
        rate = 10. / 340.
        self.assertAlmostEqual(out[0][3], 235 * rate)
        self.assertAlmostEqual(out[2][3], (235 + 17 + 105) * rate)
        self.assertEqual(h.notes, ['brush'])
        out = h.move(345, 5, 0.2, 20)             # right edge, leaves it
        self.assertIsNone(h.entered())
        self.assertEqual(len(out), 1)
        self.assertTrue(close(out[0][:3], (345, 5, 0.2)))
        # requested E of the right edge: 10 over 340 mm, 323 printed
        self.assertAlmostEqual(out[0][3] - (235 + 17 + 105) * rate,
                               10. * 323. / 340.)
        # in step again
        out = h.move(5, 5, 0.2, 30)
        self.assertEqual(len(out), 1)
        self.assertTrue(close(h.r.act, (5, 5, 0.2)))

    def test_far_moves_untouched(self):
        h = Harness([corner()], (10, 10, 0.2, 0))
        out = h.move(100, 100, 0.2, 5)
        self.assertEqual(out, [[100, 100, 0.2, 5]])


class Extrusion(unittest.TestCase):
    def crossing(self, h, e0):
        # an infill line cutting the corner of the zone
        h.move(230, 340, 0.2, e0)
        return h.move(260, 320, 0.2, e0 + 1)

    def test_first_pass_extrudes_then_travel(self):
        h = Harness([corner()], (230, 340, 0.2, 0))
        out = self.crossing(h, 0)
        self.assertIsNone(h.entered())
        # line to X240, along X240 down to the corner, along Y328, line out
        self.assertEqual(len(out), 4)
        self.assertTrue(close(out[1][:3], (240, 328, 0.2)))
        self.assertTrue(close(out[2][:3], (248, 328, 0.2)))
        e_walk_1 = out[-2][3] - out[0][3]
        self.assertGreater(e_walk_1, 0)
        out2 = self.crossing(h, 5)
        # same layer: the faces were already extruded, so no extrusion there
        self.assertAlmostEqual(out2[-2][3] - out2[0][3], 0.)
        # next layer: extruded again
        h.move(230, 340, 0.4, 10)
        out3 = h.move(260, 320, 0.4, 11)
        self.assertGreater(out3[-2][3] - out3[0][3], 0)

    def test_never_extrude_on_faces(self):
        h = Harness([corner()], (230, 340, 0.2, 0), extrude='never')
        out = self.crossing(h, 0)
        self.assertAlmostEqual(out[-2][3] - out[0][3], 0.)

    def test_retraction_kept_on_reroute(self):
        h = Harness([corner()], (230, 340, 0.2, 10))
        out = h.move(260, 320, 0.2, 9)           # travel with 1 mm retract
        self.assertIsNone(h.entered())
        self.assertAlmostEqual(out[-1][3], 9.)
        self.assertAlmostEqual(h.r.e_off, 0.)

    def test_extruding_inside_drops_material(self):
        h = Harness([corner()], (300, 320, 0.2, 0))
        h.move(300, 340, 0.2, 1)                  # ends inside
        out = h.move(310, 340, 0.2, 2)            # fully inside
        self.assertIsNone(h.entered())
        # no blob on a single point: the real position slides on the face
        for m in out:
            self.assertAlmostEqual(m[1], 328.)


class Rules(unittest.TestCase):
    def test_not_printing_passes_through(self):
        h = Harness([corner()], (5, 345, 0.2, 0))
        h.state['active'] = False
        out = h.move(345, 345, 0.2, 10)
        self.assertEqual(out, [[345, 345, 0.2, 10]])
        self.assertEqual(h.entered(), 'brush')   # the safety net decides

    def test_open_zone_not_rerouted(self):
        h = Harness([corner()], (5, 345, 0.2, 0))
        h.state['open'].add('brush')
        out = h.move(345, 345, 0.2, 10)
        self.assertEqual(out, [[345, 345, 0.2, 10]])

    def test_resume_returns_to_real_point(self):
        h = Harness([corner()], (5, 345, 0.2, 0))
        h.move(345, 345, 0.2, 10)                 # real end (345, 328)
        h.state['active'] = False                 # paused
        out = h.move(345, 345, 10.2, 9)           # lift: Z only
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (345, 328, 10.2)))
        h.move(175, 100, 10.2, 9)                 # park
        self.assertIsNone(h.entered())
        out = h.move(345, 345, 0.2, 9)            # RESUME back to the print
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (345, 328, 0.2)))
        h.state['active'] = True
        out = h.move(345, 5, 0.2, 19)
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (345, 5, 0.2)))

    def test_adjacent_zones(self):
        h = Harness([cutter(), purge()], (50, 20, 0.2, 0))
        out = h.move(5, 20, 0.2, 1)               # into the cutter zone
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (5, 22, 0.2)))
        out = h.move(5, 100, 0.2, 2)              # up through the purge strip
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (15, 100, 0.2)))
        out = h.move(50, 100, 0.2, 3)             # out
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (50, 100, 0.2)))

    def test_height_limited_zone(self):
        h = Harness([clip()], (250, 10, 2, 0))
        out = h.move(300, 10, 2, 1)
        self.assertIsNone(h.entered())
        self.assertTrue(close(out[-1][:3], (300, 20, 2)))
        # well above it: untouched (Z margin 1 mm over z_max 4)
        h2 = Harness([clip()], (250, 10, 6, 0))
        self.assertEqual(h2.move(300, 10, 6, 1), [[300, 10, 6, 1]])

    def test_from_inside_may_leave(self):
        # the toolhead is really inside (a macro left it there): leaving is
        # allowed, as for the checks
        h = Harness([purge()], (5, 100, 0.2, 0))
        out = h.move(50, 100, 0.2, 1)
        self.assertEqual(out, [[50, 100, 0.2, 1]])

    def test_wall_cannot_be_passed(self):
        wall = npz.Zone('wall', 'keepout', (-INF, 40, -INF), (INF, 60, INF))
        h = Harness([wall], (10, 10, 0.2, 0), limits=(0., 100., 0., 100.))
        with self.assertRaises(RuntimeError) as cm:
            h.move(10, 90, 0.2, 1)
        self.assertIn('cannot keep the print move', str(cm.exception))


class Fuzz(unittest.TestCase):
    def test_never_enters(self):
        rnd = random.Random(1234)
        zones = [corner(), cutter(), purge(), clip()]
        h = Harness(zones, (100, 100, 0.2, 0))
        e = 0.
        for i in range(3000):
            z = rnd.choice((0.2, 0.2, 0.4, 3.0, 6.0))
            x = rnd.uniform(0, 350)
            y = rnd.uniform(0, 355)
            e += rnd.choice((0., 0.5, -0.2))
            h.move(x, y, z, e)
            name = h.entered()
            self.assertIsNone(name, 'move %d entered %s' % (i, name))
            # the real position is never inside a closed zone
            for zone in zones:
                self.assertFalse(zone.surely_contains(h.r.act, K),
                                 'move %d: real position in %s'
                                 % (i, zone.name))
        self.assertAlmostEqual(h.next.pos[3] - h.r.e_off, e)


if __name__ == '__main__':
    unittest.main()
