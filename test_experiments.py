"""The evaluation harness itself: deterministic, and its headline claims hold."""
import unittest

from experiments import aggregate, evaluate, run_once
from scenarios import SCENARIOS


class HarnessTests(unittest.TestCase):
    def test_run_once_deterministic(self):
        scn = SCENARIOS["lossy-mesh"]
        first = run_once(scn, "epidemic", 7)
        second = run_once(scn, "epidemic", 7)
        self.assertEqual(first["delivered"], second["delivered"])
        self.assertEqual(first["attempts"], second["attempts"])
        self.assertEqual(first["copies"], second["copies"])

    def test_aggregate_means(self):
        rows = [dict(delivery_ratio=1.0, mean_delay_ticks=2.0, attempts_per_delivered=4.0,
                     copies_per_delivered=0.0, failed_attempts=1),
                dict(delivery_ratio=0.5, mean_delay_ticks=4.0, attempts_per_delivered=2.0,
                     copies_per_delivered=1.0, failed_attempts=3)]
        stats = aggregate(rows)
        self.assertEqual(stats["delivery_ratio"]["mean"], 0.75)
        self.assertEqual(stats["mean_delay_ticks"]["mean"], 3.0)
        self.assertEqual(stats["failed_attempts"]["mean"], 2.0)


class HeadlineClaimTests(unittest.TestCase):
    """Guard the claims README/EXPERIMENTS make, so docs cannot drift from truth."""

    @classmethod
    def setUpClass(cls):
        cls.results = evaluate(seeds=8)

    def test_without_relay_custody_nothing_moves(self):
        # Mule-only topology: refusing intermediate custody means zero delivery.
        relay = self.results["supply-run-relay"]
        self.assertEqual(relay["direct_only"]["delivery_ratio"]["mean"], 0.0)
        self.assertEqual(relay["adaptive"]["delivery_ratio"]["mean"], 1.0)

    def test_relaying_beats_waiting_on_the_same_contact_trace(self):
        # Causal pair: identical messages AND identical contact trace (mule
        # stops plus one late direct contact); only custody policy differs.
        direct = self.results["supply-run-direct"]
        self.assertEqual(direct["direct_only"]["delivery_ratio"]["mean"], 1.0)
        self.assertEqual(direct["epidemic"]["delivery_ratio"]["mean"], 1.0)
        self.assertLess(direct["epidemic"]["mean_delay_ticks"]["mean"],
                        direct["direct_only"]["mean_delay_ticks"]["mean"])
        # The tradeoff is real: relaying costs more transmissions.
        self.assertGreater(direct["epidemic"]["attempts_per_delivered"]["mean"],
                           direct["direct_only"]["attempts_per_delivered"]["mean"])

    def test_adaptive_relays_instead_of_waiting_on_direct_trace(self):
        # Regression guard: an earlier greedy version measured progress on the
        # static plan map, so never-used direct edges zeroed the progress term
        # and adaptive waited exactly like direct_only (44.5 ticks). The
        # encounter-weighted distance fix must not regress: adaptive now
        # relays like flooding but keeps single-copy efficiency.
        direct = self.results["supply-run-direct"]
        self.assertEqual(direct["adaptive"]["delivery_ratio"]["mean"], 1.0)
        self.assertLess(direct["adaptive"]["mean_delay_ticks"]["mean"],
                        direct["direct_only"]["mean_delay_ticks"]["mean"])
        self.assertLess(direct["adaptive"]["attempts_per_delivered"]["mean"],
                        direct["epidemic"]["attempts_per_delivered"]["mean"])
        self.assertEqual(direct["adaptive"]["copies_per_delivered"]["mean"], 0.0)

    def test_learning_isolation_no_measured_contribution(self):
        # adaptive_static scores identically but never learns. On every current
        # scenario the outcomes match: no ML advantage is claimed anywhere.
        for scenario in self.results.values():
            for key in ("delivery_ratio", "mean_delay_ticks", "attempts_per_delivered"):
                self.assertEqual(scenario["adaptive"][key]["mean"],
                                 scenario["adaptive_static"][key]["mean"])

    def test_epidemic_fastest_but_priciest_on_lossy_mesh(self):
        mesh = self.results["lossy-mesh"]
        self.assertLess(mesh["epidemic"]["mean_delay_ticks"]["mean"],
                        mesh["adaptive"]["mean_delay_ticks"]["mean"])
        self.assertGreater(mesh["epidemic"]["attempts_per_delivered"]["mean"],
                           mesh["adaptive"]["attempts_per_delivered"]["mean"])
        self.assertEqual(mesh["direct_only"]["delivery_ratio"]["mean"], 0.0)

    def test_prophet_cold_start_loses_one_relay_message(self):
        # Documented limitation: no encounter history yet at creation time.
        self.assertLess(
            self.results["supply-run-relay"]["prophet"]["delivery_ratio"]["mean"], 1.0)


if __name__ == "__main__":
    unittest.main()
