# Protocol evaluation

Means (+/- sample standard deviation) over 24 fixed seeds (0..23). Reproduce with `python3 experiments.py --seeds 24`. All scenarios are synthetic with explicit contact schedules (scenarios.py).

## supply-run-relay

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 2.833 +/- 0.668 | 0.0 +/- 0.0 |
| snapshot_shortest | 0.0 +/- 0.0 | n/a | n/a | n/a |
| epidemic | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| spray_wait | 1.0 +/- 0.0 | 32.323 +/- 0.593 | 3.531 +/- 0.808 | 1.5 +/- 0.0 |
| prophet | 0.75 +/- 0.0 | 38.236 +/- 0.677 | 3.319 +/- 0.842 | 1.333 +/- 0.0 |

## supply-run-wait

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.385 +/- 0.353 | 0.0 +/- 0.0 |
| snapshot_shortest | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.385 +/- 0.353 | 0.0 +/- 0.0 |
| epidemic | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.521 +/- 0.42 | 0.083 +/- 0.118 |
| spray_wait | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.521 +/- 0.42 | 0.083 +/- 0.118 |
| prophet | 1.0 +/- 0.0 | 44.531 +/- 0.507 | 1.385 +/- 0.353 | 0.0 +/- 0.0 |

## lossy-mesh

| policy | delivery ratio | mean delay (ticks) | attempts / delivered | copies / delivered |
| --- | --- | --- | --- | --- |
| adaptive | 1.0 +/- 0.0 | 3.167 +/- 0.61 | 4.167 +/- 0.61 | 0.0 +/- 0.0 |
| snapshot_shortest | 1.0 +/- 0.0 | 3.972 +/- 0.692 | 4.958 +/- 0.669 | 0.0 +/- 0.0 |
| epidemic | 1.0 +/- 0.0 | 2.924 +/- 0.391 | 7.722 +/- 0.751 | 3.729 +/- 0.191 |
| spray_wait | 1.0 +/- 0.0 | 3.097 +/- 0.446 | 6.007 +/- 0.641 | 2.965 +/- 0.083 |
| prophet | 1.0 +/- 0.0 | 3.0 +/- 0.376 | 7.597 +/- 0.712 | 3.736 +/- 0.179 |

attempts / delivered counts every link transmission that delivered message needed, including loss-driven retries; copies / delivered counts extra replicas a replication policy created (single-copy policies make none).

## Sensitivity notes (measured, reproducible)

- Spray-and-wait is budget-sensitive: with L=3 on lossy-mesh every copy strands in its wait phase and delivery drops to 0/6; L=4 restores full delivery. Budget choice is a real deployment parameter, not a detail.
- The supply-run pair isolates one variable: identical messages, only the contact schedule differs. Shorten the report TTLs below ~48 ticks and waiting loses messages that relaying still delivers.
- PRoPHET's relay-scenario miss is a cold start, not noise: at creation time no node has encounter history for that destination, so the only useful contact is declined. Once history exists it matches epidemic's overhead on a static mesh.
