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

    def test_relay_beats_waiting_on_delay_same_messages(self):
        relay = self.results["supply-run-relay"]["adaptive"]
        wait = self.results["supply-run-wait"]["adaptive"]
        self.assertEqual(relay["delivery_ratio"]["mean"], 1.0)
        self.assertEqual(wait["delivery_ratio"]["mean"], 1.0)
        self.assertLess(relay["mean_delay_ticks"]["mean"],
                        wait["mean_delay_ticks"]["mean"])

    def test_snapshot_routing_collapse_under_partition(self):
        # A routing rule that needs a currently-complete path delivers nothing
        # on the scheduled-contact topology: custody transfer is the point.
        self.assertEqual(
            self.results["supply-run-relay"]["snapshot_shortest"]["delivery_ratio"]["mean"], 0.0)

    def test_epidemic_fastest_but_priciest_on_lossy_mesh(self):
        mesh = self.results["lossy-mesh"]
        self.assertLess(mesh["epidemic"]["mean_delay_ticks"]["mean"],
                        mesh["adaptive"]["mean_delay_ticks"]["mean"])
        self.assertGreater(mesh["epidemic"]["attempts_per_delivered"]["mean"],
                           mesh["adaptive"]["attempts_per_delivered"]["mean"])
        self.assertGreater(mesh["epidemic"]["copies_per_delivered"]["mean"], 0)
        self.assertEqual(mesh["adaptive"]["copies_per_delivered"]["mean"], 0.0)

    def test_prophet_cold_start_loses_one_relay_message(self):
        # Documented limitation: no encounter history yet at creation time.
        self.assertLess(
            self.results["supply-run-relay"]["prophet"]["delivery_ratio"]["mean"], 1.0)


if __name__ == "__main__":
    unittest.main()
