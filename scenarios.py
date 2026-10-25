"""Evaluation scenarios with explicit contact schedules.

Every scenario states WHY contacts happen: the supply vehicle already drives a
fixed round (loading at base, delivering to shelters) - messages ride along,
nobody travels for a message. The 'wait' variant removes the vehicle and adds
one rare direct contact, so relaying can be compared against simply waiting
for a direct connection under the same message load.
"""

def _supply_run_common():
    nodes = [dict(id="BASE"), dict(id="SHELTER-N"), dict(id="SHELTER-S"), dict(id="MULE")]
    messages = [
        # Delayed field reports and supply coordination, not urgent rescue.
        dict(id="rpt-north-road", source="SHELTER-N", destination="BASE", created=2,
             ttl=55, priority=1, size_bytes=120),
        dict(id="rpt-south-water", source="SHELTER-S", destination="BASE", created=3,
             ttl=55, priority=1, size_bytes=120),
        dict(id="supplies-manifest", source="BASE", destination="SHELTER-N", created=2,
             ttl=55, priority=1, size_bytes=200),
        dict(id="rpt-north-count", source="SHELTER-N", destination="BASE", created=18,
             ttl=55, priority=0, size_bytes=100),
    ]
    return nodes, messages


def supply_run_relay():
    """Supply vehicle on its normal round; contacts are its stops."""
    nodes, messages = _supply_run_common()
    link = dict(latency_ms=30, bandwidth_bytes_per_tick=300, loss=0.2,
                congestion=0.1, reliability=0.9, active=False)
    links = [dict(a="MULE", b="BASE", **link),
             dict(a="MULE", b="SHELTER-N", **link),
             dict(a="MULE", b="SHELTER-S", **link)]
    changes = [  # the vehicle's fixed round: base 1-10, north 15-25, south 30-40, base 45-55
        dict(tick=1, a="MULE", b="BASE", active=True),
        dict(tick=11, a="MULE", b="BASE", active=False),
        dict(tick=15, a="MULE", b="SHELTER-N", active=True),
        dict(tick=26, a="MULE", b="SHELTER-N", active=False),
        dict(tick=30, a="MULE", b="SHELTER-S", active=True),
        dict(tick=41, a="MULE", b="SHELTER-S", active=False),
        dict(tick=45, a="MULE", b="BASE", active=True),
        dict(tick=56, a="MULE", b="BASE", active=False),
    ]
    return dict(name="supply-run-relay", nodes=nodes, links=links,
                messages=messages, changes=changes, ticks=60)


def supply_run_direct():
    """Causal variant: the SAME vehicle round PLUS one rare late direct
    contact per shelter. Every policy sees the identical contact trace;
    direct_only refuses intermediate custody and must wait for the direct
    contacts, so the delay difference is attributable to relaying alone."""
    nodes, messages = _supply_run_common()
    link = dict(latency_ms=30, bandwidth_bytes_per_tick=300, loss=0.2,
                congestion=0.1, reliability=0.9, active=False)
    links = [dict(a="MULE", b="BASE", **link),
             dict(a="MULE", b="SHELTER-N", **link),
             dict(a="MULE", b="SHELTER-S", **link),
             dict(a="SHELTER-N", b="BASE", **link),
             dict(a="SHELTER-S", b="BASE", **link)]
    changes = [  # vehicle round first; the direct contacts come only at the end
        dict(tick=1, a="MULE", b="BASE", active=True),
        dict(tick=11, a="MULE", b="BASE", active=False),
        dict(tick=15, a="MULE", b="SHELTER-N", active=True),
        dict(tick=26, a="MULE", b="SHELTER-N", active=False),
        dict(tick=30, a="MULE", b="SHELTER-S", active=True),
        dict(tick=41, a="MULE", b="SHELTER-S", active=False),
        dict(tick=45, a="MULE", b="BASE", active=True),
        dict(tick=50, a="SHELTER-N", b="BASE", active=True),
        dict(tick=50, a="SHELTER-S", b="BASE", active=True),
        dict(tick=56, a="MULE", b="BASE", active=False),
        dict(tick=56, a="SHELTER-N", b="BASE", active=False),
        dict(tick=56, a="SHELTER-S", b="BASE", active=False),
    ]
    return dict(name="supply-run-direct", nodes=nodes, links=links,
                messages=messages, changes=changes, ticks=60)


def lossy_mesh():
    """Static partial mesh with heavy loss: where protocol choice matters most."""
    nodes = [dict(id=c) for c in "ABCDEF"]
    links = [dict(a="A", b="B", loss=0.3, reliability=0.8, bandwidth_bytes_per_tick=300),
             dict(a="A", b="C", loss=0.1, reliability=0.9, bandwidth_bytes_per_tick=300),
             dict(a="B", b="D", loss=0.3, reliability=0.8, bandwidth_bytes_per_tick=300),
             dict(a="C", b="D", loss=0.2, reliability=0.85, bandwidth_bytes_per_tick=300),
             dict(a="C", b="E", loss=0.4, reliability=0.7, bandwidth_bytes_per_tick=300),
             dict(a="D", b="F", loss=0.2, reliability=0.9, bandwidth_bytes_per_tick=300),
             dict(a="E", b="F", loss=0.3, reliability=0.8, bandwidth_bytes_per_tick=300)]
    messages = [dict(id=f"m{i}", source="A", destination="F", created=1 + i,
                     ttl=60, priority=1 if i % 3 else 2, size_bytes=100)
                for i in range(6)]
    return dict(name="lossy-mesh", nodes=nodes, links=links, messages=messages,
                changes=[], ticks=80)


SCENARIOS = {s["name"]: s for s in (supply_run_relay(), supply_run_direct(), lossy_mesh())}
