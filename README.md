# ResQNet

An experimental opportunistic disaster-network simulator: messages are buffered across partitions and forwarded when contacts return, with priority scheduling and an adaptive link-success estimate.

**Status:** simulator core, not a deployable emergency network. No sockets, containers, API, UI, or real ML training pipeline are shipped yet. Never use this for a real distress call.

## Quick start

Python 3.10+; no third-party packages needed for the core.

```bash
python3 -m unittest discover -v
python3 example.py
```

`example.py` takes A-B-C, disconnects B-C for one tick, sends an SOS from A to C, then restores B-C. The output contains a forwarding trace and run metrics. Links are bidirectional; transfer is custody-based and one hop per tick. See [ARCHITECTURE.md](ARCHITECTURE.md) for design, limitations and roadmap.

## Next milestones

1. Scenario schema, repeatable benchmark harness, congestion/latency modeling, and baseline comparisons with held-out seeds.
2. FastAPI run control and React topology/event dashboard.
3. Dockerized Python socket nodes with durable buffers, bounded retries and authenticated protocol messages.

The project combines networking behavior with an online predictor and a planned cloud/edge telemetry split. It does not claim novel disaster-network research or production safety.
