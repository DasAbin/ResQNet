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

## Local API

Install dependencies into a virtual environment and launch only on loopback:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -v
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000
```

The interactive local API docs are at `http://127.0.0.1:8000/docs`. Send a JSON scenario to `POST /v1/simulations`: `nodes`, `links`, `messages`, optional `changes`, `ticks`, and `seed`. The response has events, buffers, and metrics. Request sizes and computational work are bounded. The API has no authentication or abuse controls and must **not** be exposed on a public network. `httpx` is a test-only dependency for FastAPI's TestClient.
