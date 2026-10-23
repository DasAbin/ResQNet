import unittest
from core import Link, Message, Node, Priority, Simulator


def line(seed=0):
    return Simulator([Node("A"), Node("B"), Node("C")],
                     [Link("A", "B"), Link("B", "C")], seed)


class SimulationTests(unittest.TestCase):
    def test_store_carry_forward_after_partition(self):
        sim = line()
        sim.links[1].active = False
        sim.inject(Message("sos", "A", "C", 0, 10, Priority.SOS))
        sim.tick()
        self.assertIn("sos", sim.nodes["B"].buffer)
        self.assertNotIn("sos", sim.delivered)
        sim.links[1].active = True
        sim.tick()
        self.assertEqual(sim.delivered["sos"], 2)
        self.assertEqual(sim.metrics()["mean_delay_ticks"], 2)

    def test_priority_and_bandwidth(self):
        sim = Simulator([Node("A"), Node("B")], [Link("A", "B", bandwidth_bytes_per_tick=100)])
        sim.inject(Message("low", "A", "B", 0, 5, size_bytes=100))
        sim.inject(Message("high", "A", "B", 0, 5, Priority.SOS, size_bytes=100))
        sim.tick()
        self.assertIn("high", sim.delivered)
        self.assertNotIn("low", sim.delivered)
        sim.tick()
        self.assertIn("low", sim.delivered)

    def test_loss_retains_message_and_updates_model(self):
        sim = Simulator([Node("A"), Node("B")], [Link("A", "B", loss=1)])
        sim.inject(Message("x", "A", "B", 0, 3))
        sim.tick()
        self.assertIn("x", sim.nodes["A"].buffer)
        self.assertEqual(sim.metrics()["predictor_samples"], 1)
        self.assertEqual(sim.metrics()["failed_attempts"], 1)

    def test_expiry_and_deduplication(self):
        sim = line()
        sim.links[0].active = False
        sim.inject(Message("x", "A", "C", 0, 2))
        with self.assertRaises(ValueError):
            sim.inject(Message("x", "A", "C", 0, 2))
        sim.tick()
        sim.tick()
        self.assertIn("x", sim.nodes["A"].buffer)
        sim.tick()
        self.assertFalse(sim.nodes["A"].buffer)
        self.assertFalse(sim.delivered)

    def test_invalid_inputs_and_empty_metrics(self):
        sim = line()
        self.assertEqual(sim.metrics()["delivery_ratio"], 0)
        with self.assertRaises(ValueError):
            sim.inject(Message("x", "unknown", "C", 0, 3))
        with self.assertRaises(ValueError):
            Link("A", "B", loss=1.2)

    def test_ttl_one_allows_first_hop(self):
        sim = Simulator([Node("A"), Node("B")], [Link("A", "B")])
        sim.inject(Message("urgent", "A", "B", 0, 1))
        sim.tick()
        self.assertEqual(sim.delivered["urgent"], 1)

    def test_decision_event_records_components_and_rivals(self):
        # A can reach D via B or C; both hops are scored and the loser is a rival.
        sim = Simulator([Node(x) for x in "ABCD"],
                        [Link("A", "B"), Link("A", "C"), Link("B", "D"), Link("C", "D")])
        sim.inject(Message("sos", "A", "D", 0, 10, Priority.SOS))
        sim.tick()
        decision = next(e for e in sim.events if e["type"] == "decision")
        self.assertEqual(decision["source"], "A")
        self.assertIn(decision["chosen"], ("B", "C"))
        self.assertEqual(decision["policy"], "adaptive")
        self.assertEqual(decision["components"]["progress"], 1.0)
        self.assertEqual(len(decision["alternatives"]), 1)
        rival = decision["alternatives"][0]
        self.assertEqual(rival["peer"], "C" if decision["chosen"] == "B" else "B")
        self.assertIn("p_success", rival)
        # Deterministic: identical run, identical explanation.
        sim2 = Simulator([Node(x) for x in "ABCD"],
                         [Link("A", "B"), Link("A", "C"), Link("B", "D"), Link("C", "D")])
        sim2.inject(Message("sos", "A", "D", 0, 10, Priority.SOS))
        sim2.tick()
        self.assertEqual(decision, next(e for e in sim2.events if e["type"] == "decision"))

    def test_no_backward_handoff_onto_leaf(self):
        # Regression: urgency must not push an SOS onto a leaf relay during an
        # outage, where the seen-set would strand it permanently.
        sim = Simulator([Node(x) for x in ("A", "L", "R", "C")],
                        [Link("A", "L"), Link("L", "R"), Link("L", "C", active=False)])
        sim.inject(Message("sos", "A", "C", 0, 20, Priority.SOS))
        sim.tick()  # A -> L is progress
        self.assertIn("sos", sim.nodes["L"].buffer)
        sim.tick()  # L-C down; R is a leaf: no handoff
        self.assertIn("sos", sim.nodes["L"].buffer)
        self.assertNotIn("sos", sim.nodes["R"].buffer)
        sim.links[2].active = True
        sim.tick()
        self.assertIn("sos", sim.delivered)

    def test_reproducible_seed(self):
        def run():
            sim = Simulator([Node("A"), Node("B")], [Link("A", "B", loss=.5)], seed=3)
            sim.inject(Message("x", "A", "B", 0, 10))
            for _ in range(4):
                sim.tick()
            return sim.events
        self.assertEqual(run(), run())


if __name__ == "__main__":
    unittest.main()
