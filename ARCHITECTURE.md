# ResQNet architecture

## Scope and safety

ResQNet is a course-scale experiment, **not** a real emergency communications service. Delivery and predictor metrics come from synthetic scenarios. No radio mesh, verified distress dispatch, encryption, or real-world safety guarantee exists yet.

## Data path

`Message` enters a node buffer -> at each discrete tick an active contact offers a forwarding opportunity -> candidate links are ranked by destination progress, an online logistic estimate of link success, urgency, latency, congestion and buffer pressure -> one hop attempts delivery -> failures remain buffered -> successful forwarding transfers custody -> recipient acknowledgement is represented by the delivered event. TTL expires old copies. A fixed RNG seed reproduces loss events.

The model currently uses custody transfer (single copy) and per-node seen IDs. These prevent loops and flooding but can strand a message after a topology change. A later protocol should add durable custody acknowledgements, retry-aware deduplication, signed message IDs, and bounded multi-copy strategies. Current link prediction is an online logistic estimator updated from simulated outcomes. It is *not* a trained disaster predictor. Its priors are synthetic link fields; evaluation must separate training and testing scenarios.

## Module boundaries

- `core.py`: data contracts, deterministic simulator, forwarding policy and link-success estimator; standard library only.
- `test_core.py`: deterministic behavior and edge-condition tests.
- Planned `api/`: FastAPI service with scenario validation, run IDs and event/metrics endpoints.
- Planned `web/`: React dashboard showing topology, link failures, message movement and metrics.
- Planned `node/`: socket protocol between Docker nodes, with bounded payloads and acknowledgements.
- Planned `experiments/`: reproducible baselines (shortest available path and epidemic forwarding), seeds, held-out fault schedules and comparable message loads.

## Boundaries and evaluation

Discrete ticks are not wall-clock seconds. A link has a byte budget per tick; its stated latency currently influences choice but not transmission duration. Model congestion currently influences choice but not queue delay. Simulated loss is sampled from `(1-loss)*reliability`; those fields should not be mistaken for independently measured probabilities. One tick permits at most one forwarding attempt per message and preserves the message if a transfer fails. Priority scheduling is strict within one link budget, so sustained SOS load may starve routine messages; measure this and add aging or reservations.

Metrics: generated/delivered count, delivery ratio, delay among delivered messages (report separately from expiry), attempts and losses, grouped by priority and across multiple seeds in later experiments. Compare equal scenarios and resource limits to simple baselines. Avoid claims of better disaster outcomes without external field evidence.

## Threat model and cloud/edge roadmap

The simulator does not protect authenticity, confidentiality, or sender identity. Do not put real distress or personal data into demos. A production design would authenticate nodes and encrypt messages end-to-end, enforce TTL/size and per-sender quotas, and avoid retaining precise location by default. Edge nodes should continue operating when the cloud telemetry service is offline; cloud storage can aggregate opt-in summaries but must never be the required forwarding authority. Socket transport and containers are planned work, not present features.

## Local API milestone

`api.py` runs a fresh scenario per request. It validates node IDs, undirected links, messages, time bounds and a computation budget, rejects unknown fields, and returns an event log. It has no persistence, auth, rate limiting, or deployment configuration and should be bound to loopback only. `test_api.py` covers partition recovery, input validation, reproducibility, isolation and budget limits. The dashboard is still future work.

## Dashboard milestone

`dashboard.html` is served by the FastAPI root route. It builds a small scenario, calls the run endpoint, and animates event frames and metrics in the browser. It is deliberately self-contained for offline/local use; React remains a possible future client but is not part of this build. The HTML UI is not a security boundary and its input checks do not replace backend validation.

## Trusted-lab socket node milestone

`node.py` implements a distinct TCP experiment. Each node has a SQLite WAL store and a configured peer list. A length-bounded newline JSON offer is committed before the receiver sends an ACK. The source drops custody only after a fresh `stored` or `delivered` ACK. Lost ACK can result in duplicate attempts, but the receiver's seen-ID table makes them idempotent. A duplicate ACK does **not** release custody because it can come from an upstream node. `test_node.py` runs real loopback TCP servers and checks restart persistence and recovery. This is not integrated with the simulator's learned policy; peer selection is static. There is no authentication, encryption, node-discovery, multi-hop routing convergence, location service or public-facing deployment; keep traffic inside an isolated trusted lab. Containers can package these nodes later, but Docker itself is not yet shipped.
