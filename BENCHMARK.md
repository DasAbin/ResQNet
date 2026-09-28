# Reproducible synthetic baseline comparison

This is an experiment on generated seven-node networks, **not** evidence that ResQNet works in disasters. The simulator policy `adaptive` is a heuristic using an online logistic estimate of simulated link success. The comparator `snapshot_shortest` follows only an *active complete path* with minimum hop count and waits during partitions. It is a deliberately simple baseline; it is not a mature DTN protocol.

Run locally after setting up Python as in README:

```bash
python3 benchmark.py --first-seed 20000 --seeds 500 > benchmark-results.json
python3 -m unittest discover -v
```

The development smoke range was seeds 10000-10099. A separate held-out evaluation used seeds 20000-20499, 12 messages and 35 discrete ticks per seed, same fault schedules and keyed link outcome draws for both policies. Total 6000 messages per policy. On the held-out range:

| Measure | Adaptive | Snapshot-shortest |
|---|---:|---:|
| Delivered | 5536 / 6000 | 5501 / 6000 |
| Delivery ratio | 92.27% | 91.68% |
| SOS delivery ratio | 93.12% | 93.36% |
| Mean forwarding attempts per scenario | 30.08 | 31.84 |
| Mean delay among delivered messages only (ticks) | 6.68 | 6.11 |

The paired delivery-ratio difference (adaptive minus baseline) was +0.58 percentage points; a seeded 2000-resample scenario-level bootstrap gave an approximate 95% interval from -0.05 to +1.18 percentage points. It overlaps zero. Adaptive won on delivery count in 115 scenarios, tied 302, and lost 83. The adaptive policy did **not** improve the SOS delivery ratio or latency here. Do not claim a reliable improvement from these results.

**Limitations:** All graph features, failures, message loads and stochastic outcomes are synthetic. Simulated reliability and loss are priors, and the predictor learns only simulated attempt outcomes within each run. Delay is conditional on delivery and ignores undelivered messages. This benchmark does not test real radio behavior, adversarial nodes, packet payload transport, deployment failure, or safety under disaster conditions. To generalize, add more baselines (epidemic/PRoPHET with matched buffer and bandwidth budgets), multiple graph families, real or carefully sourced traces, independent test regimes, and stronger uncertainty estimates. Publishing this outcome is an honest baseline, not a production claim.
