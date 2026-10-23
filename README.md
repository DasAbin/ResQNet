# ResQNet

An experimental opportunistic disaster-network simulator: messages are buffered across partitions and forwarded when contacts return, with priority scheduling and an adaptive link-success estimate.

**Status:** simulator core with explainable forwarding, local FastAPI/dashboard with flood-rescue scenarios, and a separate trusted-lab TCP socket prototype. No trained disaster predictor, secure mesh, or cloud service is shipped. Never use this for a real distress call.

## Quick start

Python 3.10+; no third-party packages needed for the core.

```bash
python3 -m unittest discover -v
python3 example.py
```

`example.py` takes A-B-C, disconnects B-C for one tick, sends an SOS from A to C, then restores B-C. The output contains a forwarding trace and run metrics. Links are bidirectional; transfer is custody-based and one hop per tick. See [ARCHITECTURE.md](ARCHITECTURE.md) for design, limitations and roadmap.

## Next milestones

1. Evaluate more graph families and proper DTN baselines with equal resource budgets.
2. Wire the socket node's peer selection to the estimator (currently separate).
3. Move the socket node beyond one shared lab key (per-node identity, end-to-end and at-rest encryption, bounded retries) before any non-isolated use.

The project combines networking behavior with an online predictor and a planned cloud/edge telemetry split. It does not claim novel disaster-network research or production safety.

## Local API

Install dependencies into a virtual environment and launch only on loopback:

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt httpx
.venv/bin/python -m unittest discover -v
.venv/bin/uvicorn api:app --host 127.0.0.1 --port 8000
```

The interactive local API docs are at `http://127.0.0.1:8000/docs`. Send a JSON scenario to `POST /v1/simulations`: `nodes`, `links`, `messages`, optional `changes`, `policy` (`adaptive` or `snapshot_shortest`), `ticks`, and `seed`. The response has events (including per-hop `decision` explanations with score components), buffers, and metrics including predictor state. Request sizes and computational work are bounded. The API has no authentication or abuse controls and must **not** be exposed on a public network. `httpx` is a test-only dependency for FastAPI's TestClient.

## Dashboard (flood rescue relay lab)

Open `http://127.0.0.1:8000/` after starting the API. The dependency-free dashboard illustrates the intended use: during a flood, a stranded resident's phone cannot reach the relief camp, so messages are stored, carried on nearby devices, and forwarded when a link returns. Pick a synthetic preset (footbridge outage, two routes to camp, flapping causeway), edit messages and link outage ticks, then run and scrub the timeline. Packets, metrics, the event log, per-hop "why this hop" score breakdowns, the estimator's learned weights, and an optional adaptive-vs-baseline comparison all come from real `/v1/simulations` responses, not canned values. All people, places and events are synthetic illustrations; this is a local web UI, not a deployed cloud service or a field-tested rescue system.

## Experimental socket nodes

`node.py` is a separate, **trusted-lab-only** TCP custody-transfer prototype. It receives a size-bounded JSON offer, commits it to SQLite before ACK, retries buffered transfers when a configured peer becomes reachable, and keeps an immutable ID history to avoid double delivery. With several peers it orders contact attempts by an online per-peer success estimate (a simple EMA; the simulator's logistic model is separate). Pass `--key` (or set `RESQNET_LAB_KEY`) to authenticate offers and ACKs with HMAC-SHA256 and protect message bodies with AES-256-GCM authenticated encryption (the maintained `cryptography` package): a fresh 12-byte random nonce per offer, message metadata (ID, route, priority, expiry) bound as associated data so ciphertexts cannot be transplanted, and the body key derived from the lab key via HKDF-SHA256. Keyed servers also reject byte-exact replayed offer frames from a bounded recent-tag cache; legitimate retries always use a fresh nonce and still pass. Peers without the shared key are rejected, a forged or untagged ACK no longer convinces a sender to release custody, and tampered, truncated, transplanted or wrong-key ciphertexts fail deterministically (all covered by tests). Without a key the node runs unauthenticated and plaintext ("open-lab"). Example with three shells:

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

Start A and B, then start C later to demonstrate buffering across an outage. In another shell, inspect `Store('/tmp/resq-c.db','C').status()` from Python. Run `python3 -m unittest discover -v` to exercise protocol, restart, expiry, malformed input, authentication, wire-encryption, peer-ordering, and custody tests. This prototype has **no per-node identity beyond the shared lab key, no end-to-end or at-rest encryption (custody stores hold plaintext), replay rejection limited to byte-exact frames, no admission quotas, and no disaster-radio transport**. The shared key is lab-grade only: every lab node can decrypt every message, and one leaked key compromises every lab node. Bind only to localhost/private isolated labs, never public interfaces or real users.

## Comparative benchmark

`python3 benchmark.py --first-seed 20000 --seeds 500 > benchmark-results.json` compares the adaptive policy to an active-path shortest-hop comparator on matched synthetic scenarios. See [BENCHMARK.md](BENCHMARK.md) for exact held-out results, uncertainty, and why they do **not** establish superiority or real-world safety. The benchmark only uses the simulator, not the socket prototype.

## Isolated container lab (manifest, not runtime-verified here)

`Dockerfile` and `compose.yaml` package three synthetic socket nodes on an internal Docker network. No host ports are published; each node has its own persistent named SQLite volume. The containers drop Linux capabilities, run as non-root, and have read-only root filesystems. This environment did not have Docker, so the build and Compose startup were **not** run or verified. Check them locally before using this sample. The shared lab key authenticates and encrypts the wire, but only run on a trusted machine with synthetic messages. Do not attach these nodes to a public network or expose ports.

After installing Docker Compose, try `docker compose up --build` on a local lab machine. To demonstrate a temporary partition, start `docker compose up --build node-a node-b`, inject a synthetic message into A's store with `docker compose exec node-a python -c "from node import Store; import time; print(Store('/data/node.db','A').receive({'id':'demo-container-1','source':'A','destination':'C','body':'Synthetic only','priority':2,'expires_at':time.time()+300}))"`, then start node-c with `docker compose up -d node-c`. Inspect C with `docker compose exec node-c python -c "from node import Store; print(Store('/data/node.db','C').status())"`. The project has not run this Compose sequence in a Docker-enabled environment, so treat these commands as a starting point, not a verified demo.

The socket receipt protocol persists `(message ID, sender node, accepted result)` alongside each accepted message. A retry after a lost ACK gets the same `stored`/`delivered` receipt, even after that peer forwards onward; an unrelated sender still gets a generic duplicate. This fixes a lab failure where the sender otherwise retained the message until expiry after a lost ACK. With `--key`, sender IDs and receipts are authenticated by the shared lab key; without it, a malicious node could still forge one. Either way this is not suitable for an untrusted network.

The socket node now bounds its pending buffer at 1,000 messages: a full buffer responds `full` without recording the rejected ID, so a later retry can succeed. The sender retains custody on `full`. Wildcard bind requires the explicit `--allow-container-bind` flag and is used only by the internal Compose lab; this flag is a reminder, **not** a security mechanism. The server still lacks admission rate limits, per-node identity, end-to-end and at-rest encryption, and a verified Docker runtime.

## Loopback process integration

`test_e2e.py` starts three actual TCP node processes on loopback using separate temporary SQLite files. It injects a synthetic A-to-C message while B and C are offline, waits for B to accept custody after it starts, then brings C online and checks delivery and release at B. `python3 -m unittest test_e2e -v` runs this process-level check. It does not exercise Docker, radios, malicious peers, crash consistency or network-wide routing.
