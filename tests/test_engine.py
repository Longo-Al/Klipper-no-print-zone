# Unit tests of the geometry and rules of no_print_zone (no Klipper needed).
#   python3 -m unittest discover -s tests -p 'test_*.py'
import importlib.util, os, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location(
    'no_print_zone', os.path.join(HERE, '..', 'no_print_zone.py'))
npz = importlib.util.module_from_spec(spec)
spec.loader.exec_module(npz)

INF = npz.INF
K = (True, True, True)


def dock(**kw):
    rails = [npz.Rail('X10.5 Y27:0'), npz.Rail('Y0 X10.5:0')]
    args = dict(speed=10., accel=500., lock_homing=True, z_moves='idle')
    args.update(kw)
    return npz.Zone('dock', 'rails', (-INF, -INF, -INF), (11, 22, INF),
                    rails, **args)


def purge():
    return npz.Zone('purge', 'reserved', (-INF, 22, -INF), (15, 200, INF))


def clip():
    return npz.Zone('clip', 'keepout', (280, -INF, -INF), (INF, 20, 4))


class Geometry(unittest.TestCase):
    def test_segment_box(self):
        hit = npz.segment_hits_box
        lo, hi = (0, 0, 0), (10, 10, 10)
        self.assertTrue(hit((-5, 5, 5), (15, 5, 5), lo, hi))
        self.assertFalse(hit((-5, 15, 5), (15, 15, 5), lo, hi))
        self.assertTrue(hit((5, 5, 5), (6, 6, 6), lo, hi))
        self.assertFalse(hit((-5, -5, 5), (-1, 20, 5), lo, hi))
        # infinite bounds
        self.assertTrue(hit((5, 5, 100), (5, 5, 200), (0, 0, -INF),
                            (10, 10, INF)))

    def test_open_box_faces_are_outside(self):
        z = purge()
        self.assertFalse(z.contains((15, 100, 0)))
        self.assertFalse(z.hits((15, 30, 0), (15, 190, 0), K))
        self.assertTrue(z.contains((14.9, 100, 0)))

    def test_unknown_axis_spans(self):
        z = clip()
        unknown_z = (True, True, False)
        self.assertFalse(z.hits((290, 10, 10), (250, 10, 10), K))
        self.assertTrue(z.hits((290, 10, 10), (250, 10, 10), unknown_z))
        self.assertTrue(z.contains((290, 10, 50), unknown_z))
        self.assertFalse(z.surely_contains((290, 10, 2), unknown_z))
        self.assertTrue(z.surely_contains((290, 10, 2), K))


class Rails(unittest.TestCase):
    def test_parse(self):
        r = npz.Rail('X10.5  y27:0')
        self.assertEqual((r.axis, r.start, r.end, r.fixed), (1, 27., 0.,
                                                              {0: 10.5}))
        for bad in ('X10.5', 'X1:2 Y3:4', 'Q3 Y1:0', 'X1 X2 Y0:1', 'Y5:5',
                    'Xa Y1:0', 'X1 Y2 Z5:0'):
            self.assertRaises(ValueError, npz.Rail, bad)

    def test_classify(self):
        r = npz.Rail('X10.5 Y27:0')
        c = lambda s, e: r.classify(s, e, 0.01)
        self.assertEqual(c((10.5, 25, 0), (10.5, 0, 0)), 'in')
        self.assertEqual(c((10.5, 0, 0), (10.5, 300, 0)), 'out')
        self.assertIsNone(c((10.5, 30, 0), (10.5, 0, 0)))    # beyond 27
        self.assertIsNone(c((10.4, 25, 0), (10.4, 0, 0)))    # off the line
        self.assertIsNone(c((10.5, 25, 0), (10.5, 0, 1)))    # Z changes
        self.assertEqual(r.classify((10.5, 25, 0), (10.5, 0, .1), .01, .2),
                         'in')

    def test_zone_validation(self):
        mk = lambda rail: npz.Zone('d', 'rails', (-INF,) * 3, (11, 22, INF),
                                   [npz.Rail(rail)])
        self.assertRaises(ValueError, mk, 'X20 Y27:0')
        self.assertRaises(ValueError, mk, 'X10.5 Y50:30')
        mk('X10.5 Y27:0')
        self.assertRaises(ValueError, npz.Zone, 'a', 'keepout', (-INF,) * 3,
                          (INF,) * 3)
        self.assertRaises(ValueError, npz.Zone, 'a', 'keepout', (5, 0, 0),
                          (5, 1, 1))


class Rules(unittest.TestCase):
    def setUp(self):
        self.eng = npz.Engine([dock(), purge(), clip()], 0.01)

    def check(self, s, e, **ctx):
        return self.eng.check(s, e, npz.Context(**ctx))

    def refused(self, s, e, text, **ctx):
        with self.assertRaises(npz.ZoneViolation) as cm:
            self.check(s, e, **ctx)
        self.assertIn(text, str(cm.exception))

    def test_far_and_extrude_only(self):
        self.assertIsNone(self.check((100, 100, 0), (200, 200, 0)))
        self.assertIsNone(self.check((5, 5, 0), (5, 5, 0)))

    def test_reserved(self):
        self.refused((25, 100, 0), (5, 100, 0), "reserved zone 'purge'")
        v = self.check((25, 100, 0), (5, 100, 0), open_zones=['purge'])
        self.assertEqual(v.notes, ['purge: inside open zone'])
        v = self.check((5, 100, 0), (25, 100, 0))
        self.assertEqual(v.notes, ['purge: leaving reserved zone'])

    def test_keepout_with_z(self):
        self.refused((250, 10, 2), (290, 10, 2), "keepout zone 'clip'")
        self.assertIsNone(self.check((250, 10, 5), (290, 10, 5)))
        self.refused((290, 10, 5), (290, 10, 3), "keepout zone 'clip'")
        # Z not homed: zones limited in Z are ignored by default...
        unk_z = (True, True, False)
        self.assertIsNone(self.check((250, 10, 5), (290, 10, 5),
                                     known=unk_z))
        # ...or count as full height
        eng = npz.Engine([clip()], 0.01, 'full_height')
        self.assertRaises(npz.ZoneViolation, eng.check, (250, 10, 5),
                          (290, 10, 5), npz.Context(known=unk_z))

    def test_rails_zone_limited_in_z_always_counts(self):
        z = npz.Zone('d', 'rails', (-INF, -INF, -INF), (11, 22, 50),
                     [npz.Rail('X10.5 Y27:0')])
        eng = npz.Engine([z])
        self.assertRaises(npz.ZoneViolation, eng.check, (30, 10, 5),
                          (5, 10, 5), npz.Context(known=(True, True, False)))

    def test_rails(self):
        o = ['dock', 'purge']
        v = self.check((10.5, 25, 0), (10.5, 0, 0), open_zones=o)
        self.assertEqual((v.speed, v.accel, v.enter), (10., 500., ['dock']))
        self.refused((10.5, 25, 0), (10.5, 0, 0), "which is closed",
                     open_zones=['purge'])
        self.refused((8, 25, 0), (8, 0, 0), "rails zone 'dock'", open_zones=o)
        self.refused((30, 2, 0), (2, 30, 0), "rails zone 'dock'", open_zones=o)
        v = self.check((10.5, 0, 0), (0, 0, 0), open_zones=o)
        self.assertIn("inward on rail 'Y0 X10.5:0'", v.notes[0])
        v = self.check((3, 0, 0), (11, 0, 0))
        self.assertIn("outward", v.notes[0])
        self.assertEqual(v.enter, [])
        self.refused((3, 0, 0), (3, 30, 0), "rails zone 'dock'", open_zones=o)

    def test_rails_z_policy(self):
        s, e = (10.5, 0, 0), (10.5, 0, 5)
        self.assertIn('Z move', self.check(s, e).notes[0])
        self.refused(s, e, 'no print', print_active=True)
        self.refused(s, e, 'Z is not homed', known=(True, True, False))
        eng = npz.Engine([dock(z_moves='deny')])
        self.assertRaises(npz.ZoneViolation, eng.check, s, e, npz.Context())
        eng = npz.Engine([dock(z_moves='allow')])
        eng.check(s, e, npz.Context(print_active=True))

    def test_unknown_xy(self):
        # Z hop before homing: allowed, unless a zone may hold the toolhead
        unk = (False, False, False)
        self.assertIsNone(self.check((0, 0, 0), (0, 0, 5), known=unk))
        self.refused((0, 0, 0), (0, 0, 5), 'may still hold',
                     known=unk, occupied=['dock'])
        self.refused((300, 0, 0), (5, 0, 0), 'home X and Y first',
                     known=(True, False, False))


if __name__ == '__main__':
    unittest.main()
