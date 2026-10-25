"""Deterministic, tick-based disruption-tolerant communication experiment."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import heapq
import math
import hashlib
from typing import Iterable


class Priority(IntEnum):
    ROUTINE = 0
    IMPORTANT = 1
    SOS = 2


@dataclass(frozen=True)
class Message:
    id: str
    source: str
    destination: str
    created: int
    ttl: int
    priority: Priority = Priority.ROUTINE
    size_bytes: int = 100

    def __post_init__(self) -> None:
        if not self.id or not self.source or not self.destination or self.ttl <= 0 or self.size_bytes <= 0:
            raise ValueError("invalid message")


@dataclass
class Node:
    id: str
    battery: float = 1.0
    capacity: int = 50
    buffer: dict[str, Message] = field(default_factory=dict)
    seen: set[str] = field(default_factory=set)
    tickets: dict[str, int] = field(default_factory=dict)
    predictability: dict[str, float] = field(default_factory=dict)


@dataclass
class Link:
    a: str
    b: str
    active: bool = True
    latency_ms: float = 20.0
    bandwidth_bytes_per_tick: int = 1000
    loss: float = 0.0
    congestion: float = 0.0
    reliability: float = 1.0

    def __post_init__(self) -> None:
        if self.a == self.b or self.latency_ms < 0 or self.bandwidth_bytes_per_tick <= 0:
            raise ValueError("invalid link")
        if any(not 0 <= x <= 1 for x in (self.loss, self.congestion, self.reliability)):
            raise ValueError("link probabilities must be in [0,1]")

    def peer(self, node: str) -> str:
        if node == self.a:
            return self.b
        if node == self.b:
            return self.a
        raise ValueError("node not on link")


REPLICATION_POLICIES = ("epidemic", "spray_wait", "prophet")


def score_hop(p_success: float, urgency: float, congestion: float, latency_ms: float,
              peer_load: float) -> float:
    """Message-level candidate-hop score shared by simulator and socket transport.

    The simulator adds its topology progress term on top; the TCP lab has no
    topology map, so its transport uses this shared core only. Both engines
    therefore rank hops with the same formula over observed/configured facts.
    """
    return (urgency * (2.0 * p_success - 1.0)
            - 0.7 * congestion - latency_ms / 1000.0 - 0.5 * peer_load)


class DeliveryPredictor:
    """Online logistic link-success estimator; updates only from observed attempts.

    Model uncertainty is explicit: until learned, reliability and loss are priors.
    This is a link outcome model, not a proven end-to-end route oracle.
    """

    def __init__(self, learning_rate: float = 0.12) -> None:
        self.weights = [0.0, 1.5, -1.5, -0.8, 0.4, 0.3]
        self.learning_rate = learning_rate
        self.samples = 0

    @staticmethod
    def features(link: Link, battery: float) -> list[float]:
        return [1.0, link.reliability, link.loss, link.congestion, battery,
                1.0 / (1.0 + link.latency_ms / 100.0)]

    def predict(self, link: Link, battery: float) -> float:
        z = sum(w * x for w, x in zip(self.weights, self.features(link, battery)))
        return 1 / (1 + math.exp(-max(-30.0, min(30.0, z))))

    def observe(self, link: Link, battery: float, success: bool) -> None:
        error = int(success) - self.predict(link, battery)
        self.weights = [w + self.learning_rate * error * x
                        for w, x in zip(self.weights, self.features(link, battery))]
        self.samples += 1


class Simulator:
    def __init__(self, nodes: Iterable[Node], links: Iterable[Link], seed: int = 0,
                 policy: str = "adaptive", spray_copies: int = 8) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.links = list(links)
        self._encountered = {frozenset((l.a, l.b)) for l in self.links if l.active}
        if not self.nodes or any(l.a not in self.nodes or l.b not in self.nodes for l in self.links):
            raise ValueError("link endpoint missing or no nodes")
        if policy not in ("adaptive", "adaptive_static", "snapshot_shortest",
                          "direct_only", "epidemic", "spray_wait", "prophet"):
            raise ValueError("unknown forwarding policy")
        self.seed = seed
        self.policy = policy
        self.spray_copies = max(2, int(spray_copies))
        self.predictor = DeliveryPredictor()
        self.tick_number = 0
        self.created: dict[str, Message] = {}
        self.delivered: dict[str, int] = {}
        self.events: list[dict] = []
        self.attempts = 0
        self.failed_attempts = 0
        self.copies = 0

    def inject(self, message: Message) -> None:
        if message.source not in self.nodes or message.destination not in self.nodes:
            raise ValueError("unknown endpoint")
        if message.id in self.created:
            raise ValueError("duplicate message ID")
        self.created[message.id] = message
        if message.source == message.destination:
            self.delivered[message.id] = self.tick_number
            self.events.append(dict(tick=message.created, type="delivered", id=message.id,
                                    node=message.destination))
        else:
            node = self.nodes[message.source]
            if len(node.buffer) >= node.capacity:
                raise ValueError("source buffer full")
            node.buffer[message.id] = message
            node.seen.add(message.id)
            node.tickets[message.id] = self.spray_copies
            self.events.append(dict(tick=message.created, type="created", id=message.id,
                                    node=message.source))

    def _distance(self, start: str, end: str) -> tuple[float, int]:
        """Weighted hop count over the contact map (Dijkstra), plus how many
        never-observed links the best path relies on.

        A link that has come up at least once costs 1 hop; a link that exists
        only on the plan but has never been observed up costs len(nodes) hops:
        still routable, but weak evidence, so a live encounter path always
        beats a never-seen 'direct' edge. Returns (weight, unobserved_links);
        unreachable is (len(nodes)**2+1, 0), keeping arithmetic finite.
        """
        if start == end:
            return 0.0, 0
        n = len(self.nodes)
        best = {start: (0.0, 0)}
        heap = [(0.0, 0, start)]
        while heap:
            d, un, u = heapq.heappop(heap)
            if u == end:
                return d, un
            if (d, un) > best.get(u, (math.inf, 0)):
                continue
            for link in self.links:
                if u not in (link.a, link.b):
                    continue
                seen = frozenset((link.a, link.b)) in self._encountered
                nd, nun = d + (1.0 if seen else float(n)), un + (0 if seen else 1)
                v = link.peer(u)
                if (nd, nun) < best.get(v, (math.inf, 0)):
                    best[v] = (nd, nun)
                    heapq.heappush(heap, (nd, nun, v))
        return float(n * n + 1), 0

    def _active_distance(self, start: str, end: str) -> float:
        """Current active-network hop count; infinity means no complete path."""
        if start == end:
            return 0
        frontier = {start}
        visited = {start}
        for hops in range(1, len(self.nodes) + 1):
            frontier = {link.peer(n) for n in frontier for link in self.links
                        if link.active and n in (link.a, link.b) and link.peer(n) not in visited}
            if end in frontier:
                return float(hops)
            visited |= frontier
            if not frontier:
                break
        return float("inf")

    def _score(self, source: Node, peer: Node, message: Message, link: Link) -> tuple[float, dict]:
        """Score a candidate hop and keep the components so a UI can explain it."""
        src_w, src_unenc = self._distance(source.id, message.destination)
        peer_w, peer_unenc = self._distance(peer.id, message.destination)
        progress = src_w - peer_w
        p_success = self.predictor.predict(link, source.battery)
        urgency = 1 + int(message.priority)
        load = len(peer.buffer) / max(1, peer.capacity)
        score = 2.0 * progress + score_hop(p_success, urgency, link.congestion,
                                           link.latency_ms, load)
        return score, dict(progress=progress, src_unobserved=src_unenc,
                           peer_unobserved=peer_unenc, p_success=round(p_success, 4),
                           urgency=urgency, congestion=link.congestion,
                           latency_ms=link.latency_ms, peer_load=round(load, 4),
                           total=round(score, 4))

    def _prophet_update(self) -> None:
        """Classic PRoPHET bookkeeping: encounter boost, aging, transitivity."""
        gamma, p_init, beta = 0.98, 0.75, 0.25
        for node in self.nodes.values():
            for dest in list(node.predictability):
                node.predictability[dest] *= gamma
                if node.predictability[dest] < 0.01:
                    del node.predictability[dest]
        for link in self.links:
            if not link.active:
                continue
            a, b = self.nodes[link.a], self.nodes[link.b]
            a.predictability[b.id] = (a.predictability.get(b.id, 0.0)
                                      + (1 - a.predictability.get(b.id, 0.0)) * p_init)
            b.predictability[a.id] = (b.predictability.get(a.id, 0.0)
                                      + (1 - b.predictability.get(a.id, 0.0)) * p_init)
            p_ab = a.predictability[b.id]
            for dest, p_bd in list(b.predictability.items()):
                if dest == a.id:
                    continue
                old = a.predictability.get(dest, 0.0)
                a.predictability[dest] = old + (1 - old) * p_ab * p_bd * beta
            p_ba = b.predictability[a.id]
            for dest, p_ad in list(a.predictability.items()):
                if dest == b.id:
                    continue
                old = b.predictability.get(dest, 0.0)
                b.predictability[dest] = old + (1 - old) * p_ba * p_ad * beta

    def tick(self) -> None:
        self.tick_number += 1
        t = self.tick_number
        for link in self.links:
            if link.active:
                self._encountered.add(frozenset((link.a, link.b)))
        for node in self.nodes.values():
            for mid, msg in list(node.buffer.items()):
                if t - msg.created > msg.ttl or mid in self.delivered:
                    del node.buffer[mid]
                    node.tickets.pop(mid, None)
                    self.events.append(dict(tick=t, type="expired" if mid not in self.delivered else "cleared",
                                            id=mid, node=node.id))
        if self.policy == "prophet":
            self._prophet_update()
        # Snapshot prevents a message from traversing multiple hops in one tick.
        candidates = []
        explain: dict[tuple[str, str, str], dict] = {}
        replicate = self.policy in REPLICATION_POLICIES
        for source in self.nodes.values():
            for msg in source.buffer.values():
                if msg.id in self.delivered:
                    continue
                for link in self.links:
                    if not link.active or source.id not in (link.a, link.b):
                        continue
                    peer = self.nodes[link.peer(source.id)]
                    if msg.id in peer.seen or len(peer.buffer) >= peer.capacity and peer.id != msg.destination:
                        continue
                    if self.policy == "direct_only":
                        # No custody transfer: a message moves only when its
                        # destination is the direct contact. Everything else waits.
                        if peer.id != msg.destination:
                            continue
                        candidates.append((-int(msg.priority), 0.0, msg.id, source.id,
                                           peer.id, link))
                        continue
                    if replicate:
                        if self.policy == "spray_wait":
                            # Binary spray: while tickets remain, hand half over;
                            # the last ticket only walks to the destination itself.
                            if source.tickets.get(msg.id, 1) <= 1 and peer.id != msg.destination:
                                continue
                        elif self.policy == "prophet":
                            if (peer.id != msg.destination and not
                                    peer.predictability.get(msg.destination, 0.0)
                                    > source.predictability.get(msg.destination, 0.0)):
                                continue
                        candidates.append((-int(msg.priority), 0.0, msg.id, source.id,
                                           peer.id, link))
                        continue
                    if self.policy == "snapshot_shortest":
                        current = self._active_distance(source.id, msg.destination)
                        onward = self._active_distance(peer.id, msg.destination)
                        if not math.isfinite(current) or onward >= current:
                            continue
                        score = 1.0 / (1.0 + onward)
                        components = dict(onward_hops=onward, total=round(score, 4))
                    else:
                        score, components = self._score(source, peer, msg, link)
                    # Adaptive never hands a message to a node whose route to the
                    # destination looks worse than its own. On a tie it still hands
                    # over when its own best route relies on a link that has never
                    # come up and the peer's route is at least as observed: holding
                    # behind an unproven edge has no evidential advantage over
                    # carrying. The seen-set makes every handoff loop-free.
                    forward_progress = (
                        self.policy == "snapshot_shortest"
                        or components["progress"] > 0
                        or (components["progress"] == 0
                            and components["src_unobserved"] > 0
                            and components["peer_unobserved"] <= components["src_unobserved"]))
                    if (score > 0 and forward_progress) or peer.id == msg.destination:
                        explain[(msg.id, source.id, peer.id)] = components
                        candidates.append((-int(msg.priority), -score, msg.id, source.id,
                                           peer.id, link))
        candidates.sort(key=lambda item: item[:5])
        used_bytes: dict[int, int] = {}
        dispatched: set = set()
        for _, _, mid, sid, pid, link in candidates:
            marker = (mid, pid) if replicate else mid
            if marker in dispatched or mid not in self.nodes[sid].buffer or mid in self.delivered:
                continue
            msg = self.nodes[sid].buffer[mid]
            if (self.policy == "spray_wait" and pid != msg.destination
                    and self.nodes[sid].tickets.get(mid, 1) <= 1):
                continue  # spent its last ticket earlier this same tick
            if not replicate and self.policy != "direct_only":
                rivals = sorted((c for c in candidates if c[2] == mid and c[3] == sid and c[4] != pid),
                                key=lambda c: c[1])
                self.events.append(dict(
                    tick=t, type="decision", id=mid, source=sid, chosen=pid, policy=self.policy,
                    components=explain[(mid, sid, pid)],
                    alternatives=[dict(peer=c[4], **explain[(c[2], c[3], c[4])]) for c in rivals[:3]]))
            key = id(link)
            if used_bytes.get(key, 0) + msg.size_bytes > link.bandwidth_bytes_per_tick:
                continue
            used_bytes[key] = used_bytes.get(key, 0) + msg.size_bytes
            dispatched.add(marker)
            source, peer = self.nodes[sid], self.nodes[pid]
            self.attempts += 1
            # Keyed per opportunity, not traversal order: paired policies see the
            # same loss outcome when they attempt the same contact in the same tick.
            key = f"{self.seed}:{t}:{mid}:{min(link.a, link.b)}:{max(link.a, link.b)}"
            u = int.from_bytes(hashlib.blake2b(key.encode(), digest_size=8).digest(), "big") / 2**64
            success = u < (1 - link.loss) * link.reliability
            if self.policy != "adaptive_static":
                self.predictor.observe(link, source.battery, success)
            self.events.append(dict(tick=t, type="forward" if success else "lost",
                                    id=mid, source=sid, destination=pid))
            if not success:
                self.failed_attempts += 1
                continue
            peer.seen.add(mid)
            if pid == msg.destination:
                if not replicate:
                    del source.buffer[mid]
                self.delivered[mid] = t
                self.events.append(dict(tick=t, type="delivered", id=mid, node=pid))
            elif replicate:
                self.copies += 1
                peer.buffer[mid] = msg
                if self.policy == "spray_wait":
                    held = source.tickets.get(mid, 1)
                    give = held // 2
                    peer.tickets[mid] = give
                    source.tickets[mid] = held - give
            else:
                del source.buffer[mid]
                peer.buffer[mid] = msg

    def metrics(self) -> dict:
        delivered = [self.created[mid] for mid in self.delivered]
        return dict(tick=self.tick_number, created=len(self.created), delivered=len(delivered),
                    delivery_ratio=len(delivered) / len(self.created) if self.created else 0.0,
                    mean_delay_ticks=(sum(self.delivered[m.id] - m.created for m in delivered) /
                                      len(delivered) if delivered else None),
                    attempts=self.attempts, failed_attempts=self.failed_attempts,
                    predictor_samples=self.predictor.samples,
                    predictor_weights=[round(w, 4) for w in self.predictor.weights],
                    predictor_features=["bias", "reliability", "loss", "congestion",
                                        "battery", "latency_term"],
                    policy=self.policy, copies=self.copies,
                    attempts_per_delivered=(round(self.attempts / len(delivered), 3)
                                            if delivered else None),
                    copies_per_delivered=(round(self.copies / len(delivered), 3)
                                          if delivered else None))
