"""Repeatable protocol evaluation: delivery ratio, delay and overhead under loss.

    python3 experiments.py [--seeds N] [--json results.json] [--md EXPERIMENTS.md]

Fixed default seeds make every run reproduce the same table. Each scenario is
run under every forwarding policy; the table reports means and sample stddev
across seeds. Scenarios state their contact assumptions explicitly (see
scenarios.py); the supply-run pair compares relaying via scheduled vehicle
contacts against waiting for a rare direct link under identical message load.
"""
from __future__ import annotations

import argparse
import json
import statistics
from pathlib import Path

from core import Link, Message, Node, Priority, Simulator
from scenarios import SCENARIOS

POLICIES = ("adaptive", "snapshot_shortest", "epidemic", "spray_wait", "prophet")


def run_once(scenario: dict, policy: str, seed: int) -> dict:
    sim = Simulator([Node(**n) for n in scenario["nodes"]],
                    [Link(**l) for l in scenario["links"]], seed=seed, policy=policy,
                    spray_copies=4)  # L=4: full delivery here; L=3 strands (see EXPERIMENTS.md)
    changes = {}
    for c in scenario["changes"]:
        changes.setdefault(c["tick"], []).append(c)
    messages = {}
    for m in scenario["messages"]:
        messages.setdefault(m["created"], []).append(m)
    for m in messages.get(0, []):
        sim.inject(Message(**{**m, "priority": Priority(m["priority"])}))
    for tick in range(1, scenario["ticks"] + 1):
        for c in changes.get(tick, []):
            link = next(l for l in sim.links if {l.a, l.b} == {c["a"], c["b"]})
            link.active = c["active"]
        for m in messages.get(tick, []):
            sim.inject(Message(**{**m, "priority": Priority(m["priority"])}))
        sim.tick()
    return sim.metrics()


def aggregate(rows: list[dict]) -> dict:
    def stats(values):
        values = [v for v in values if v is not None]
        if not values:
            return dict(mean=None, sd=None)
        return dict(mean=round(statistics.fmean(values), 3),
                    sd=round(statistics.pstdev(values), 3) if len(values) > 1 else 0.0)
    return dict(runs=len(rows),
                delivery_ratio=stats([r["delivery_ratio"] for r in rows]),
                mean_delay_ticks=stats([r["mean_delay_ticks"] for r in rows]),
                attempts_per_delivered=stats([r["attempts_per_delivered"] for r in rows]),
                copies_per_delivered=stats([r["copies_per_delivered"] for r in rows]),
                failed_attempts=stats([r["failed_attempts"] for r in rows]))


def evaluate(seeds: int) -> dict:
    out = {}
    for name, scenario in SCENARIOS.items():
        out[name] = {}
        for policy in POLICIES:
            rows = [run_once(scenario, policy, seed) for seed in range(seeds)]
            out[name][policy] = aggregate(rows)
    return out


def markdown(results: dict, seeds: int) -> str:
    lines = ["# Protocol evaluation",
             "",
             f"Means (+/- sample standard deviation) over {seeds} fixed seeds "
             f"(0..{seeds - 1}). Reproduce with `python3 experiments.py --seeds {seeds}`. "
             "All scenarios are synthetic with explicit contact schedules (scenarios.py).",
             ""]
    for name, policies in results.items():
        lines.append(f"## {name}")
        lines.append("")
        lines.append("| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |")
        lines.append("| --- | --- | --- | --- | --- |")
        for policy, stats in policies.items():
            def cell(s):
                return "n/a" if s["mean"] is None else f"{s['mean']} +/- {s['sd']}"
            lines.append(f"| {policy} | {cell(stats['delivery_ratio'])} | "
                         f"{cell(stats['mean_delay_ticks'])} | "
                         f"{cell(stats['attempts_per_delivered'])} | "
                         f"{cell(stats['copies_per_delivered'])} |")
        lines.append("")
    lines.append("attempts / delivered counts every link transmission that delivered "
                 "message needed, including loss-driven retries; copies / delivered counts "
                 "extra replicas a replication policy created (single-copy policies make none).")
    lines.append("")
    lines.append("## Sensitivity notes (measured, reproducible)")
    lines.append("")
    lines.append("- Spray-and-wait is budget-sensitive: with L=3 on lossy-mesh every copy "
                 "strands in its wait phase and delivery drops to 0/6; L=4 restores full "
                 "delivery. Budget choice is a real deployment parameter, not a detail.")
    lines.append("- The supply-run pair isolates one variable: identical messages, only the "
                 "contact schedule differs. Shorten the report TTLs below ~48 ticks and "
                 "waiting loses messages that relaying still delivers.")
    lines.append("- PRoPHET's relay-scenario miss is a cold start, not noise: at creation "
                 "time no node has encounter history for that destination, so the only "
                 "useful contact is declined. Once history exists it matches epidemic's "
                 "overhead on a static mesh.")
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="ResQNet protocol evaluation")
    parser.add_argument("--seeds", type=int, default=24)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--md", type=Path, default=None)
    args = parser.parse_args()
    results = evaluate(args.seeds)
    if args.json:
        args.json.write_text(json.dumps(results, indent=2))
    if args.md:
        args.md.write_text(markdown(results, args.seeds))
    print(markdown(results, args.seeds))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
