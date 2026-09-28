"""Paired, reproducible synthetic fault-schedule comparison.

No field data: this compares two policies on generated graphs and identical
contact-success draws. Training and evaluation seed ranges are deliberately
separate to prevent scoring a policy only on its development scenarios.
"""
from __future__ import annotations

import argparse
import json
import random
import statistics
from core import Link, Message, Node, Priority, Simulator

POLICIES = ("adaptive", "snapshot_shortest")


def scenario(seed: int, policy: str, count: int = 12, ticks: int = 35) -> dict:
    rng = random.Random(seed)
    nodes = [Node(str(i), capacity=count * 2) for i in range(7)]
    # A backbone plus two cross-links. One temporary failure blocks immediate
    # delivery but permits relay through another eventual contact.
    edges = [(0,1),(1,2),(2,3),(3,4),(4,5),(5,6),(1,4),(0,3)]
    links = [Link(str(a),str(b),active=True,loss=rng.uniform(.02,.22),
                  reliability=rng.uniform(.75,.98),
                  latency_ms=rng.uniform(15,150),
                  congestion=rng.uniform(0,.6),
                  bandwidth_bytes_per_tick=300) for a,b in edges]
    sim = Simulator(nodes,links,seed=seed,policy=policy)
    arrivals={}
    for i in range(count):
        source,dest=rng.sample(range(7),2)
        created=rng.randrange(0,max(1,ticks//3))
        msg=Message(f"m{i}",str(source),str(dest),created,ttl=rng.randint(ticks//2,ticks),
                    priority=Priority(rng.randrange(3)),size_bytes=100)
        arrivals.setdefault(created,[]).append(msg)
    fault_events={}
    for idx,link in enumerate(links):
        # Changing contact opportunities; generated once per seed.
        down=rng.randint(2,ticks//2)
        up=min(ticks,rng.randint(down+1,ticks-2))
        fault_events.setdefault(down,[]).append((idx,False))
        fault_events.setdefault(up,[]).append((idx,True))
    for m in arrivals.get(0,[]):sim.inject(m)
    for t in range(1,ticks+1):
        for idx,active in fault_events.get(t,[]):sim.links[idx].active=active
        for m in arrivals.get(t,[]):sim.inject(m)
        sim.tick()
    return {"policy":policy,"seed":seed,"metrics":sim.metrics(),
            "delivered_ids":sorted(sim.delivered),
            "total_sos":sum(m.priority==Priority.SOS for batch in arrivals.values() for m in batch),
            "delivered_sos":sum(sim.created[mid].priority==Priority.SOS for mid in sim.delivered)}


def evaluate(seeds: range, count: int = 12, ticks: int = 35) -> dict:
    if not seeds or count < 1 or ticks < 10:
        raise ValueError("invalid benchmark size")
    runs=[scenario(seed,policy,count,ticks) for seed in seeds for policy in POLICIES]
    groups={policy:[r for r in runs if r["policy"]==policy] for policy in POLICIES}
    summary={}
    for policy,group in groups.items():
        delivered=sum(r["metrics"]["delivered"] for r in group)
        created=sum(r["metrics"]["created"] for r in group)
        sos=sum(r["total_sos"] for r in group)
        summary[policy]={"delivered":delivered,"created":created,
                         "delivery_ratio":delivered/created,
                         "sos_delivery_ratio":sum(r["delivered_sos"] for r in group)/sos if sos else None,
                         "mean_attempts":statistics.mean(r["metrics"]["attempts"] for r in group),
                         "mean_delay_delivered_only":statistics.mean(
                             r["metrics"]["mean_delay_ticks"] for r in group
                             if r["metrics"]["mean_delay_ticks"] is not None)}
    return {"seeds":list(seeds),"scenario_count":len(seeds),"messages_per_scenario":count,
            "ticks":ticks,"summary":summary,"runs":runs}


if __name__=="__main__":
    parser=argparse.ArgumentParser()
    parser.add_argument('--first-seed',type=int,default=10000)
    parser.add_argument('--seeds',type=int,default=100)
    parser.add_argument('--messages',type=int,default=12)
    parser.add_argument('--ticks',type=int,default=35)
    args=parser.parse_args()
    if args.first_seed<0 or not 1<=args.seeds<=10000 or not 1<=args.messages<=1000 or not 10<=args.ticks<=500:
        parser.error('seed/count/tick out of range')
    print(json.dumps(evaluate(range(args.first_seed,args.first_seed+args.seeds),args.messages,args.ticks),indent=2))
