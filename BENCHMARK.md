# Reproducible synthetic baseline comparison

This is an experiment on generated seven-node networks, **not** evidence that ResQNet works in disasters. The simulator policy `adaptive` is a heuristic using an online logistic estimate of simulated link success. The comparator `snapshot_shortest` follows only an *active complete path* with minimum hop count and waits during partitions. It is a deliberately simple baseline; it is not a mature DTN protocol.

Run locally after setting up Python as in README:

```bash
python3 benchmark.py --first-seed 20000 --seeds 500 > benchmark-results.json
python3 -m unittest discover -v
```

The development smoke range was seeds 10000-10099. A separate held-out evaluation used seeds 20000-20499, 12 messages and 35 discrete ticks per seed, same fault schedules and keyed link outcome draws for both policies. Total 6000 messages per policy.

**Policy change (2026-10-23):** the adaptive policy no longer hands a message to a node that is physically farther from the destination. The earlier scoring rule let the urgency bonus push traffic onto leaf relays during a partition, where the seen-set then stranded it (caught in the dashboard's causeway scenario; regression test `test_no_backward_handoff_onto_leaf`). All numbers below are from a fresh held-out run after that fix; the earlier table is obsolete.

| Measure | Adaptive | Snapshot-shortest |
|---|---:|---:|
| Delivered | 5558 / 6000 | 5501 / 6000 |
| Delivery ratio | 92.63% | 91.68% |
| SOS delivery ratio | 94.30% | 93.36% |
| Mean forwarding attempts per scenario | 29.84 | 31.84 |
| Mean delay among delivered messages only (ticks) | 6.74 | 6.11 |

The paired delivery-ratio difference (adaptive minus baseline) was +0.95 percentage points; a seeded 2000-resample scenario-level bootstrap gave an approximate 95% interval from +0.35 to +1.55 percentage points, which excludes zero on this graph family. Adaptive won on delivery count in 121 scenarios, tied 306, and lost 73. Mean delay among delivered messages was still **worse** for adaptive (6.74 vs 6.11 ticks): waiting through partitions for a better hop trades speed for completion. One synthetic graph family with one fault model cannot show a general advantage; do not claim field superiority from these results.

**Limitations:** All graph features, failures, message loads and stochastic outcomes are synthetic. Simulated reliability and loss are priors, and the predictor learns only simulated attempt outcomes within each run. Delay is conditional on delivery and ignores undelivered messages. This benchmark does not test real radio behavior, adversarial nodes, packet payload transport, deployment failure, or safety under disaster conditions. To generalize, add more baselines (epidemic/PRoPHET with matched buffer and bandwidth budgets), multiple graph families, real or carefully sourced traces, independent test regimes, and stronger uncertainty estimates. Publishing this outcome is an honest baseline, not a production claim.
