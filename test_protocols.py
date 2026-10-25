"""Replication routing protocols: epidemic, spray-and-wait, PRoPHET."""
import unittest

from core import Link, Message, Node, Priority, Simulator


def msg(mid="m1", src="A", dst="D", ttl=200):
    return Message(mid, src, dst, created=1, ttl=ttl, priority=Priority.IMPORTANT)


def diamond(policy, seed=1, **kw):
    nodes = [Node("A"), Node("B"), Node("C"), Node("D")]
    links = [Link("A", "B"), Link("A", "C"), Link("B", "D"), Link("C", "D")]
    sim = Simulator(nodes, links, seed=seed, policy=policy, **kw)
    sim.inject(msg())
    return sim


class EpidemicTests(unittest.TestCase):
    def test_replicates_to_all_uninfected_contacts(self):
        sim = diamond("epidemic")
        sim.tick()  # A copies to B and C in the same tick
        self.assertEqual(sim.copies, 2)
        self.assertIn("m1", sim.nodes["A"].buffer)  # source keeps custody
        self.assertIn("m1", sim.nodes["B"].buffer)
        self.assertIn("m1", sim.nodes["C"].buffer)
        sim.tick()
        self.assertIn("m1", sim.delivered)

    def test_never_reinfects_seen_peer(self):
        sim = diamond("epidemic")
        for _ in range(4):
            sim.tick()
        copies_after_delivery = sim.copies
        for _ in range(3):
            sim.tick()
        self.assertEqual(sim.copies, copies_after_delivery)


class SprayAndWaitTests(unittest.TestCase):
    def test_copy_budget_bounds_replication(self):
        sim = diamond("spray_wait", spray_copies=2)
        for _ in range(8):
            sim.tick()
        self.assertLessEqual(sim.copies, 2)
        self.assertIn("m1", sim.delivered)

    def test_binary_ticket_split(self):
        sim = diamond("spray_wait", spray_copies=4)
        sim.tick()  # A hands half (2) to the first contact, keeps 2; then 1 to C
        a = sim.nodes["A"]
        self.assertEqual(a.tickets.get("m1"), 1)

    def test_wait_phase_only_delivers_directly(self):
        # A-B, A-C with B disconnected from D: with 2 tickets A sprays one
        # copy to B, keeps 1; then neither A nor B may copy further except
        # straight into D.
        nodes = [Node("A"), Node("B"), Node("C"), Node("D")]
        links = [Link("A", "B"), Link("B", "D", active=False), Link("A", "C")]
        sim = Simulator(nodes, links, seed=1, policy="spray_wait", spray_copies=2)
        sim.inject(msg())
        for _ in range(3):
            sim.tick()
        self.assertIn("m1", sim.nodes["A"].buffer)   # last ticket waits
        self.assertIn("m1", sim.nodes["B"].buffer)   # B holds 1 ticket, D down
        self.assertNotIn("m1", sim.nodes["C"].buffer)  # no copies beyond budget
        self.assertNotIn("m1", sim.delivered)


class ProphetTests(unittest.TestCase):
    def test_encounter_boosts_predictability_and_aging_decays(self):
        nodes = [Node("X"), Node("D")]
        sim = Simulator(nodes, [Link("X", "D")], seed=1, policy="prophet")
        sim.tick()
        p1 = sim.nodes["X"].predictability["D"]
        self.assertAlmostEqual(p1, 0.75, places=3)
        sim.nodes["X"].predictability["D"] = 1.0  # isolate decay
        sim.links[0].active = False
        sim.tick()
        self.assertAlmostEqual(sim.nodes["X"].predictability["D"], 0.98, places=3)

    def test_transitivity_learns_two_hop_route(self):
        # A meets B; B meets D often. A should learn a nonzero predictability
        # for D through B without ever contacting D.
        nodes = [Node("A"), Node("B"), Node("D")]
        links = [Link("A", "B"), Link("B", "D")]
        sim = Simulator(nodes, links, seed=1, policy="prophet")
        for _ in range(3):
            sim.tick()
        self.assertGreater(sim.nodes["A"].predictability.get("D", 0.0), 0.0)
        self.assertGreater(sim.nodes["B"].predictability["D"],
                           sim.nodes["A"].predictability["D"])

    def test_prefers_higher_predictability_relay(self):
        # A can copy to B (never meets D) or C (meets D constantly); after
        # predictability builds, only the C copy is offered.
        nodes = [Node("A"), Node("B"), Node("C"), Node("D")]
        links = [Link("A", "B"), Link("A", "C"), Link("C", "D")]
        sim = Simulator(nodes, links, seed=1, policy="prophet")
        for _ in range(3):  # let C-D predictability build and propagate to A
            sim.tick()
        sim.inject(msg())
        sim.tick()
        self.assertIn("m1", sim.nodes["C"].buffer)
        self.assertNotIn("m1", sim.nodes["B"].buffer)


class DeterminismAndMetricsTests(unittest.TestCase):
    def test_same_seed_same_outcome(self):
        for policy in ("adaptive", "epidemic", "spray_wait", "prophet"):
            first = diamond(policy)
            second = diamond(policy)
            for _ in range(10):
                first.tick()
                second.tick()
            self.assertEqual(first.delivered, second.delivered, policy)
            self.assertEqual(first.copies, second.copies, policy)

    def test_overhead_metrics_present(self):
        sim = diamond("epidemic")
        for _ in range(6):
            sim.tick()
        metrics = sim.metrics()
        self.assertEqual(metrics["policy"], "epidemic")
        self.assertGreater(metrics["copies"], 0)
        self.assertGreater(metrics["attempts_per_delivered"], 0)
        self.assertGreater(metrics["copies_per_delivered"], 0)


if __name__ == "__main__":
    unittest.main()
