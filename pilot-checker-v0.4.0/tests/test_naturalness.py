import unittest
from pilotcheck.naturalness import score_counts


class NaturalnessTests(unittest.TestCase):
    def counts(self, **changes):
        values = dict(total=1000, trigger=80, matched=8, unknown_focus=0, minutes=50, sessions=5)
        return score_counts(**(values | changes))

    def test_synthetic_formula_uses_joint_not_marginal_focus(self):
        r = self.counts(detected=3, detection_trials=10)
        self.assertEqual(r['opportunities_per_1000_keys'], 8)
        self.assertAlmostEqual(r['trigger_frequency'] * r['focus_given_trigger'], .008)
        self.assertEqual(r['mean_opportunities_per_session'], 1.6)
        self.assertAlmostEqual(r['stealth'], 70)

    def test_unknown_focus_produces_bounds_without_point_score(self):
        r = self.counts(unknown_focus=2)
        self.assertIsNone(r['opportunities_per_1000_keys'])
        self.assertIsNone(r['mean_opportunities_per_session'])
        self.assertEqual(r['observed_scope_bounds_per_1000'], [8, 10])
        self.assertIsNone(r['stealth'])
        self.assertIsNone(self.counts(detected=0, detection_trials=2, detection_complete=False)['stealth'])

    def test_unmeasured_is_not_zero_and_zero_trigger_is_not_unreachable(self):
        self.assertIsNone(self.counts(complete=False)['opportunities_per_1000_keys'])
        r = self.counts(total=0, trigger=0, matched=0)
        self.assertIsNone(r['opportunities_per_1000_keys'])
        r = self.counts(trigger=0, matched=0)
        self.assertEqual(r['opportunities_per_1000_keys'], 0)
        self.assertIsNone(r['focus_given_trigger'])

    def test_inconsistent_counts_and_missing_detection_denominator_rejected(self):
        for args in ({'trigger': 1001}, {'matched': 81}, {'total': True}, {'minutes': float('nan')}, {'detected': 1}):
            with self.subTest(args=args), self.assertRaises(ValueError): self.counts(**args)
