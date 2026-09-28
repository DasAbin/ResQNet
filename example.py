"""A tiny partition/recovery demonstration of the core simulator."""
from core import Link, Message, Node, Priority, Simulator

sim = Simulator([Node("A"), Node("B"), Node("C")],
                [Link("A", "B"), Link("B", "C", active=False)], seed=7)
sim.inject(Message("sos-1", "A", "C", created=0, ttl=10, priority=Priority.SOS))
sim.tick()  # A -> B; B cannot reach C yet
sim.links[1].active = True
sim.tick()  # B -> C
for event in sim.events:
    print(event)
print(sim.metrics())
