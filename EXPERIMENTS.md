# Protocol evaluation

Means (+/- sample standard deviation) over 24 fixed seeds (0..23). Reproduce with `python3 experiments.py --seeds 24`. All scenarios are synthetic with explicit contact schedules (scenarios.py).

## supply-run-relay

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 2.833 +/- 0.668 | 0.0 +/- 0.0 |
| adaptive_static | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 2.833 +/- 0.668 | 0.0 +/- 0.0 |
| snapshot_shortest | 0.0 +/- 0.0 | n/a | n/a | n/a |
| direct_only | 0.0 +/- 0.0 | n/a | n/a | n/a |
| epidemic | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| spray_wait | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| prophet | 0.75 +/- 0.0 | 38.236 +/- 0.677 | 3.319 +/- 0.842 | 1.333 +/- 0.0 |

## supply-run-direct

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 2.833 +/- 0.668 | 0.0 +/- 0.0 |
| adaptive_static | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 2.833 +/- 0.668 | 0.0 +/- 0.0 |
| snapshot_shortest | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.385 +/- 0.353 | 0.0 +/- 0.0 |
| direct_only | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.385 +/- 0.353 | 0.0 +/- 0.0 |
| epidemic | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| spray_wait | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| prophet | 1.0 +/- 0.0 | 40.76 +/- 0.497 | 2.823 +/- 0.631 | 1.0 +/- 0.0 |

## lossy-mesh

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 3.167 +/- 0.61 | 4.167 +/- 0.61 | 0.0 +/- 0.0 |
| adaptive_static | 1.0 +/- 0.0 | 3.167 +/- 0.61 | 4.167 +/- 0.61 | 0.0 +/- 0.0 |
| snapshot_shortest | 1.0 +/- 0.0 | 3.972 +/- 0.692 | 4.958 +/- 0.669 | 0.0 +/- 0.0 |
| direct_only | 0.0 +/- 0.0 | n/a | n/a | n/a |
| epidemic | 1.0 +/- 0.0 | 2.924 +/- 0.391 | 7.722 +/- 0.751 | 3.729 +/- 0.191 |
| spray_wait | 1.0 +/- 0.0 | 3.097 +/- 0.446 | 6.007 +/- 0.641 | 2.965 +/- 0.083 |
| prophet | 1.0 +/- 0.0 | 3.0 +/- 0.376 | 7.597 +/- 0.712 | 3.736 +/- 0.179 |

attempts / delivered counts every link transmission that delivered message needed, including loss-driven retries; copies / delivered counts extra replicas a replication policy created (single-copy policies make none).

## Policy roles and measured limitations

- `direct_only` is the causal baseline for the relay question: identical messages and contact trace, but intermediate custody is refused. On supply-run-direct it waits for the late direct contacts (44.5 ticks mean) while epidemic/spray-and-wait relay via the vehicle at 32.3. On supply-run-relay (no direct contacts scheduled) it delivers nothing.
- `snapshot_shortest` is a straw man for classic connected-path routing, not primary evidence: it needs a currently-complete path, so it stalls under scheduled contacts.
- `adaptive` ranks hops by weighted contact-map distance: a link that has come up costs 1 hop, a plan link never observed up costs N hops, and on a tie a message leaves a route that relies on unobserved links. An earlier version measured progress on the static plan map: the never-used direct edges made relaying look like zero progress, so adaptive waited exactly like `direct_only` (44.5 ticks). The causal scenario exposed that; the encounter-weighted fix above is the result, and the failure is preserved here as history.
- `adaptive_static` isolates the learned predictor: identical scoring, learning updates off. Its outcomes match `adaptive` on every current scenario, so no learning advantage is claimed anywhere in this project.
- Spray-and-wait is budget-sensitive: with L=3 on lossy-mesh every copy strands in its wait phase (0/6); L=4 restores full delivery.
- Shorten the supply-run report TTLs below ~48 ticks and waiting/direct policies lose messages that relaying still delivers.
- PRoPHET's relay-scenario miss is a cold start: with no encounter history yet it declines the only useful contact. On a static mesh its transitivity floods predictability everywhere, so it matches epidemic's overhead instead of beating it.

## Protocol fidelity simplifications

- PRoPHET implements the core update equations (P_init=0.75 on encounter, aging gamma=0.98 per tick, transitivity beta=0.25), simplified: every tick a link is up counts as a fresh encounter, predictability tables are read directly rather than exchanged as summary vectors, and forwarding compares per-destination predictability only.
- Spray-and-wait is binary mode only; simultaneous same-tick contacts resolve sequentially in deterministic order.
- All replication policies purge delivered copies instantly and globally; real epidemic/spray keep copies until TTL. This only affects buffer pressure, not the delivery, delay or attempt metrics reported here.
