# ResQNet architecture

## Scope and safety

ResQNet is a course-scale experiment, **not** a real emergency communications service. Delivery and predictor metrics come from synthetic scenarios. No radio mesh, verified distress dispatch, production-grade security, or real-world safety guarantee exists yet.

## Data path

`Message` enters a node buffer -> at each discrete tick an active contact offers a forwarding opportunity -> candidate links are ranked by destination progress, an online logistic estimate of link success, urgency, latency, congestion and buffer pressure -> one hop attempts delivery -> failures remain buffered -> successful forwarding transfers custody -> recipient acknowledgement is represented by the delivered event. TTL expires old copies. A fixed RNG seed reproduces loss events. The adaptive policy never hands a message to a node physically farther from the destination: with a static topology and no mobility model, backward handoffs only stranded traffic on leaf relays (see BENCHMARK.md, 2026-10-23 policy change). Every dispatch emits a `decision` event with the winning score components and the passed-over alternatives, so the dashboard can explain each hop; the API also returns the estimator's feature weights and sample count.

The model currently uses custody transfer (single copy) and per-node seen IDs. These prevent loops and flooding but can strand a message after a topology change. A later protocol should add durable custody acknowledgements, retry-aware deduplication, signed message IDs, and bounded multi-copy strategies. Current link prediction is an online logistic estimator updated from simulated outcomes. It is *not* a trained disaster predictor. Its priors are synthetic link fields; evaluation must separate training and testing scenarios.

## Module boundaries

- `core.py`: data contracts, deterministic simulator, adaptive and snapshot-shortest forwarding policies, link-success estimator, per-hop decision explanations; standard library only.
- `api.py`: local FastAPI service; validated `POST /v1/simulations` (scenario + `policy`) returning events, buffers, metrics with predictor state; serves the dashboard assets on loopback.
- `dashboard.html` + `dashboard.css` + `dashboard-data.js` + `dashboard-render.js` + `dashboard-app.js`: dependency-free dashboard. Flood-rescue presets (all synthetic people/places), message and outage editors, timeline playback reconstructed from API events, per-hop "why" inspector, policy A/B comparison, estimator panel.
- `node.py`: separate trusted-lab TCP custody-transfer prototype between OS processes, with bounded payloads, SQLite persistence and receipts, optional shared-key HMAC authentication plus encrypted bodies on the wire (`--key` / `RESQNET_LAB_KEY`), and per-message peer choice through the simulator's own `DeliveryPredictor`/`score_hop` over configured priors plus observed round trips, outcomes and peer load.
- `benchmark.py`: paired adaptive-vs-baseline comparison on held-out seeds; results in BENCHMARK.md.
- `test_*.py`: unit, API, benchmark, Compose-static and loopback process tests (35 tests).

## Boundaries and evaluation

Discrete ticks are not wall-clock seconds. A link has a byte budget per tick; its stated latency currently influences choice but not transmission duration. Model congestion currently influences choice but not queue delay. Simulated loss is sampled from `(1-loss)*reliability`; those fields should not be mistaken for independently measured probabilities. One tick permits at most one forwarding attempt per message and preserves the message if a transfer fails. Priority scheduling is strict within one link budget, so sustained SOS load may starve routine messages; measure this and add aging or reservations.

Metrics: generated/delivered count, delivery ratio, delay among delivered messages (report separately from expiry), attempts and losses, grouped by priority and across multiple seeds in later experiments. Compare equal scenarios and resource limits to simple baselines. Avoid claims of better disaster outcomes without external field evidence.

## Threat model and cloud/edge roadmap

The simulator does not protect authenticity, confidentiality, or sender identity. Do not put real distress or personal data into demos. A production design would authenticate nodes and encrypt messages end-to-end, enforce TTL/size and per-sender quotas, and avoid retaining precise location by default. Edge nodes should continue operating when the cloud telemetry service is offline; cloud storage can aggregate opt-in summaries but must never be the required forwarding authority. Socket transport and containers are planned work, not present features.

## Local API milestone

`api.py` runs a fresh scenario per request. It validates node IDs, undirected links, messages, time bounds and a computation budget, rejects unknown fields, and returns an event log. It has no persistence, auth, rate limiting, or deployment configuration and should be bound to loopback only. `test_api.py` covers partition recovery, input validation, reproducibility, isolation and budget limits. The same app serves the dashboard and `POST /v1/lab/observe`, a read-only bridge that opens lab nodes' SQLite stores with `mode=ro` and reports exactly what they hold (counts, pending summaries, delivered history, transfer log), per-node errors when a store is missing or unreadable. It fabricates nothing, cannot see stores on other machines, answers loopback clients only, confines store paths to `RESQNET_LAB_ROOTS` (default `/tmp`) and verifies the store schema before reading; it is a lab aid, not device telemetry.

## Dashboard milestone

`dashboard.html` is served by the FastAPI root route. It builds a small scenario, calls the run endpoint, and animates event frames and metrics in the browser. It is deliberately self-contained for offline/local use; React remains a possible future client but is not part of this build. The HTML UI is not a security boundary and its input checks do not replace backend validation.

## Trusted-lab socket node milestone

`node.py` implements a distinct TCP experiment. Each node has a SQLite WAL store and a configured peer list. A length-bounded newline JSON offer is committed before the receiver sends an ACK. The source drops custody only after a fresh `stored` or `delivered` ACK. Lost ACK can result in duplicate attempts, but the receiver's seen-ID table makes them idempotent. A duplicate ACK does **not** release custody because it can come from an upstream node. Each node keeps a bounded `transfer_log` table recording observed custody events only: received offers, local injections and per-attempt peer offers with the full score components (shared-model p_success, urgency, measured latency, observed peer load; progress explicitly null where no topology exists); `test_node.py` verifies the log contents and the read helpers. `test_node.py` runs real loopback TCP servers and checks restart persistence and recovery. With `--key`, offers and ACKs carry an HMAC-SHA256 tag and message bodies use AES-256-GCM authenticated encryption from the `cryptography` package: a fresh 12-byte random nonce per offer, message metadata bound as associated data, and an HKDF-SHA256 body subkey derived from the lab key. AEAD verification rejects tampered, truncated, transplanted or wrong-key ciphertexts deterministically; a bounded per-server cache of recent offer tags rejects byte-exact replayed frames while legitimate retries (fresh nonce each attempt) still pass; the sender's buffer copy is never mutated by encryption (all covered by tests). Random 96-bit nonces keep collision risk negligible at lab message volumes. This is not integrated with the simulator's learned policy; peer selection is static. Peer choice is explainable (each logged attempt carries its score components) and shares code with the simulator's adaptive policy, but it ranks a static configured peer list without topology: there is no per-node identity, end-to-end or at-rest encryption, node-discovery, multi-hop routing convergence, location service or public-facing deployment; keep traffic inside an isolated trusted lab. The dashboard labels simulator output SIMULATED and node observations OBSERVED rather than mixing them. Containers can package these nodes later, but Docker itself is not yet shipped.

## Benchmark boundary

`benchmark.py` compares `adaptive` with `snapshot_shortest` under identical synthetic fault schedules. Keyed contact outcomes make shared attempts comparable across policies regardless of traversal order. Development seeds 10000-10099 and held-out seeds 20000-20499 are disjoint. Results and caveats are recorded in `BENCHMARK.md`. The observed delivery-ratio difference is small and uncertain, while SOS delivery and delivered-message latency do not favor the adaptive policy in that run. This prevents an unjustified "AI always improves delivery" claim.

## Container lab scaffold

`Dockerfile` and `compose.yaml` define three TCP nodes on an internal Docker network with no host port mappings, per-node volumes, non-root execution, dropped capabilities, and read-only root filesystems. They are a packaging scaffold, **not** a validated cloud deployment. Docker was unavailable in the build environment; the Compose startup and persistent-volume behavior have not been tested. `--host 0.0.0.0` is scoped inside the isolated container network; exposing those ports to a public or untrusted network would be unsafe because one shared lab key is the only barrier, and every holder of it can read and inject traffic.

## Custody receipt fix

The TCP node's SQLite `receipts` table stores an accepted result keyed by message ID and claimed sender. A retry by the same sender returns its durable receipt, including after the receiver has forwarded onward, closing the lost-ACK ambiguity. A different sender sees `duplicate`, so an upstream node does not give false custody proof. Without `--key` the wire's sender field is unauthenticated, so this only preserves correctness among cooperating lab nodes. With `--key` the sender claim is covered by the tag, but one shared key is still not per-node identity. It is not a security guarantee. `test_node.py` covers restart plus forward-before-retry and an upstream duplicate.

## Bounded buffer

The socket node rejects a fresh relay with `full` once its pending buffer reaches 1,000 messages. It does not write a seen ID or receipt for that rejected offer. The source retains custody and can retry later; `test_node.py` verifies it. Expired pending entries are cleared before the capacity check. The bound is per node, not per sender or priority; in an untrusted network a peer could exhaust it. A wildcard bind needs an explicit container-lab flag, and there is still no per-node identity, rate limit, or at-rest encryption.

## SQLite durability setting

Socket-node SQLite connections explicitly request `PRAGMA synchronous=FULL`, including initial WAL setup. That asks SQLite to sync commits before an ACK is sent; it does not prove a specific disk or container volume honors flushes, nor does it address corrupted storage. A durable queue implementation still needs crash/fault-injection tests on its actual deployment substrate.

## Concurrent custody writers

Each socket-store operation now closes its SQLite connection after commit, and an incoming offer begins an immediate transaction before the immutable-ID and pending-capacity checks. This serializes decisions made by separate processes sharing one database file, rather than relying only on a per-object Python lock. A parallel two-store test covers the capacity race and retry of a rejected offer. It does not establish safe shared storage across hosts or prevent a malicious peer from exhausting capacity.
