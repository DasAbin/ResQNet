import unittest
from benchmark import evaluate, scenario
from core import Link, Message, Node, Simulator


class BenchmarkTests(unittest.TestCase):
    def test_paired_reproducible(self):
        a=evaluate(range(10000,10003))
        self.assertEqual(a,evaluate(range(10000,10003)))
        self.assertEqual(a['scenario_count'],3)
        self.assertEqual(a['summary']['adaptive']['created'],36)
        self.assertEqual(a['summary']['snapshot_shortest']['created'],36)

    def test_snapshot_shortest_waits_for_end_to_end_path(self):
        sim=Simulator([Node('A'),Node('B'),Node('C')],
                      [Link('A','B'),Link('B','C',active=False)],policy='snapshot_shortest')
        sim.inject(Message('x','A','C',0,5))
        sim.tick()
        self.assertIn('x',sim.nodes['A'].buffer)
        self.assertNotIn('x',sim.nodes['B'].buffer)
        sim.links[1].active=True
        sim.tick();sim.tick()
        self.assertIn('x',sim.delivered)

    def test_keyed_success_independent_of_other_attempt_order(self):
        def result(extra):
            sim=Simulator([Node('A'),Node('B')],[Link('A','B',loss=.4)],seed=73)
            sim.inject(Message('z','A','B',0,5))
            if extra:sim.inject(Message('a','A','B',0,5))
            sim.tick()
            return next(e['type'] for e in sim.events if e.get('id')=='z' and e['type'] in ('forward','lost'))
        self.assertEqual(result(False),result(True))

    def test_validation(self):
        with self.assertRaises(ValueError):Simulator([Node('A')],[],policy='random')
        with self.assertRaises(ValueError):evaluate(range(0))


if __name__=='__main__':unittest.main()
