# ResQNet

An experimental opportunistic disaster-network simulator: messages are buffered across partitions and forwarded when contacts return, with priority scheduling and an adaptive link-success estimate.

**Status:** simulator core, local FastAPI/dashboard, and a separate trusted-lab TCP socket prototype. No trained disaster predictor, secure mesh, or cloud service is shipped. Never use this for a real distress call.

## Quick start

Python 3.10+; no third-party packages needed for the core.

```bash
python3 -m unittest discover -v
python3 example.py
```

`example.py` takes A-B-C, disconnects B-C for one tick, sends an SOS from A to C, then restores B-C. The output contains a forwarding trace and run metrics. Links are bidirectional; transfer is custody-based and one hop per tick. See [ARCHITECTURE.md](ARCHITECTURE.md) for design, limitations and roadmap.

## Next milestones

1. Evaluate more graph families and proper DTN baselines with equal resource budgets.
2. Harden local API and dashboard; decide whether a React client adds value.
3. Add authenticated, encrypted, bounded-retry socket nodes before any non-isolated use.

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

## Dashboard

Open `http://127.0.0.1:8000/` after starting the API. The dependency-free dashboard sends a bounded synthetic A-B-C scenario to the API and animates the resulting event trace. Configure the link recovery tick, run length, and RNG seed. The graphic and metrics are derived from the API response, not invented static values. It is a local web UI, not a deployed cloud service.

## Experimental socket nodes

`node.py` is a separate, **trusted-lab-only** TCP custody-transfer prototype. It receives a size-bounded JSON offer, commits it to SQLite before ACK, retries buffered transfers when a configured peer becomes reachable, and keeps an immutable ID history to avoid double delivery. Example with three shells:

```bash
python3 node.py --id A --db /tmp/resq-a.db --port 9101 --peer 127.0.0.1:9102
python3 node.py --id B --db /tmp/resq-b.db --port 9102 --peer 127.0.0.1:9103
python3 node.py --id C --db /tmp/resq-c.db --port 9103
```

To inject a synthetic message into A's local store, in a fourth shell:

```bash
python3 - <<'PY'
from node import Store
import time
print(Store('/tmp/resq-a.db','A').receive({'id':'demo-1','source':'A','destination':'C',
      'body':'Synthetic SOS (not a real distress call)','priority':2,
      'expires_at':time.time()+300}))
PY
```

Start A and B, then start C later to demonstrate buffering across an outage. In another shell, inspect `Store('/tmp/resq-c.db','C').status()` from Python. Run `python3 -m unittest discover -v` to exercise protocol, restart, expiry, malformed input, and custody tests. This prototype has **no encryption, peer authentication, anti-replay across IDs, admission quotas, or disaster-radio transport**. Bind only to localhost/private isolated labs, never public interfaces or real users. Its predictor is in the simulator, not yet wired to live socket peer selection.

## Comparative benchmark

`python3 benchmark.py --first-seed 20000 --seeds 500 > benchmark-results.json` compares the adaptive policy to an active-path shortest-hop comparator on matched synthetic scenarios. See [BENCHMARK.md](BENCHMARK.md) for exact held-out results, uncertainty, and why they do **not** establish superiority or real-world safety. The benchmark only uses the simulator, not the socket prototype.

## Isolated container lab (manifest, not runtime-verified here)

`Dockerfile` and `compose.yaml` package three synthetic socket nodes on an internal Docker network. No host ports are published; each node has its own persistent named SQLite volume. The containers drop Linux capabilities, run as non-root, and have read-only root filesystems. This environment did not have Docker, so the build and Compose startup were **not** run or verified. Check them locally before using this sample. Even on an internal Docker network the protocol is unauthenticated and unencrypted; only run on a trusted machine with synthetic messages. Do not attach these nodes to a public network or expose ports.

After installing Docker Compose, try `docker compose up --build` on a local lab machine. To demonstrate a temporary partition, start `docker compose up --build node-a node-b`, inject a synthetic message into A's store with `docker compose exec node-a python -c "from node import Store; import time; print(Store('/data/node.db','A').receive({'id':'demo-container-1','source':'A','destination':'C','body':'Synthetic only','priority':2,'expires_at':time.time()+300}))"`, then start node-c with `docker compose up -d node-c`. Inspect C with `docker compose exec node-c python -c "from node import Store; print(Store('/data/node.db','C').status())"`. The project has not run this Compose sequence in a Docker-enabled environment, so treat these commands as a starting point, not a verified demo.

The socket receipt protocol persists `(message ID, sender node, accepted result)` alongside each accepted message. A retry after a lost ACK gets the same `stored`/`delivered` receipt, even after that peer forwards onward; an unrelated sender still gets a generic duplicate. This fixes a lab failure where the sender otherwise retained the message until expiry after a lost ACK. Sender IDs are *not authenticated*, so a malicious node could forge one; this is not suitable for an untrusted network.
