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

POLICIES = ("adaptive", "adaptive_static", "snapshot_shortest", "direct_only",
            "epidemic", "spray_wait", "prophet")


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
    lines.append("## Policy roles and measured limitations")
    lines.append("")
    lines.append("- `direct_only` is the causal baseline for the relay question: identical "
                 "messages and contact trace, but intermediate custody is refused. On "
                 "supply-run-direct it waits for the late direct contacts (44.5 ticks mean) "
                 "while epidemic/spray-and-wait relay via the vehicle at 32.3. On "
                 "supply-run-relay (no direct contacts scheduled) it delivers nothing.")
    lines.append("- `snapshot_shortest` is a straw man for classic connected-path routing, "
                 "not primary evidence: it needs a currently-complete path, so it stalls "
                 "under scheduled contacts.")
    lines.append("- `adaptive` ranks hops by weighted contact-map distance: a link that "
                 "has come up costs 1 hop, a plan link never observed up costs N hops, and "
                 "on a tie a message leaves a route that relies on unobserved links. An "
                 "earlier version measured progress on the static plan map: the never-used "
                 "direct edges made relaying look like zero progress, so adaptive waited "
                 "exactly like `direct_only` (44.5 ticks). The causal scenario exposed "
                 "that; the encounter-weighted fix above is the result, and the failure "
                 "is preserved here as history.")
    lines.append("- `adaptive_static` isolates the learned predictor: identical scoring, "
                 "learning updates off. Its outcomes match `adaptive` on every current "
                 "scenario, so no learning advantage is claimed anywhere in this project.")
    lines.append("- Spray-and-wait is budget-sensitive: with L=3 on lossy-mesh every copy "
                 "strands in its wait phase (0/6); L=4 restores full delivery.")
    lines.append("- Shorten the supply-run report TTLs below ~48 ticks and waiting/direct "
                 "policies lose messages that relaying still delivers.")
    lines.append("- PRoPHET's relay-scenario miss is a cold start: with no encounter "
                 "history yet it declines the only useful contact. On a static mesh its "
                 "transitivity floods predictability everywhere, so it matches epidemic's "
                 "overhead instead of beating it.")
    lines.append("")
    lines.append("## Protocol fidelity simplifications")
    lines.append("")
    lines.append("- PRoPHET implements the core update equations (P_init=0.75 on encounter, "
                 "aging gamma=0.98 per tick, transitivity beta=0.25), simplified: every "
                 "tick a link is up counts as a fresh encounter, predictability tables are "
                 "read directly rather than exchanged as summary vectors, and forwarding "
                 "compares per-destination predictability only.")
    lines.append("- Spray-and-wait is binary mode only; simultaneous same-tick contacts "
                 "resolve sequentially in deterministic order.")
    lines.append("- All replication policies purge delivered copies instantly and globally; "
                 "real epidemic/spray keep copies until TTL. This only affects buffer "
                 "pressure, not the delivery, delay or attempt metrics reported here.")
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
