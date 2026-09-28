"""Deterministic, tick-based disruption-tolerant communication experiment."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import IntEnum
import math
import random
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
    def __init__(self, nodes: Iterable[Node], links: Iterable[Link], seed: int = 0) -> None:
        self.nodes = {n.id: n for n in nodes}
        self.links = list(links)
        if not self.nodes or any(l.a not in self.nodes or l.b not in self.nodes for l in self.links):
            raise ValueError("link endpoint missing or no nodes")
        self.rng = random.Random(seed)
        self.predictor = DeliveryPredictor()
        self.tick_number = 0
        self.created: dict[str, Message] = {}
        self.delivered: dict[str, int] = {}
        self.events: list[dict] = []
        self.attempts = 0
        self.failed_attempts = 0

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
            self.events.append(dict(tick=message.created, type="created", id=message.id,
                                    node=message.source))

    def _distance(self, start: str, end: str) -> float:
        """Hop count on physical topology, including temporarily down links."""
        if start == end:
            return 0
        frontier = {start}
        visited = {start}
        for hops in range(1, len(self.nodes) + 1):
            frontier = {link.peer(n) for n in frontier for link in self.links
                        if n in (link.a, link.b) and link.peer(n) not in visited}
            if end in frontier:
                return float(hops)
            visited |= frontier
            if not frontier:
                break
        return float(len(self.nodes) + 1)

    def _score(self, source: Node, peer: Node, message: Message, link: Link) -> float:
        progress = self._distance(source.id, message.destination) - self._distance(peer.id, message.destination)
        p_success = self.predictor.predict(link, source.battery)
        urgency = 1 + int(message.priority)
        return (2.0 * progress + urgency * (2.0 * p_success - 1.0)
                - 0.7 * link.congestion - link.latency_ms / 1000.0
                - 0.5 * len(peer.buffer) / max(1, peer.capacity))

    def tick(self) -> None:
        self.tick_number += 1
        t = self.tick_number
        for node in self.nodes.values():
            for mid, msg in list(node.buffer.items()):
                if t - msg.created >= msg.ttl or mid in self.delivered:
                    del node.buffer[mid]
                    self.events.append(dict(tick=t, type="expired" if mid not in self.delivered else "cleared",
                                            id=mid, node=node.id))
        # Snapshot prevents a message from traversing multiple hops in one tick.
        candidates = []
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
                    score = self._score(source, peer, msg, link)
                    if score > 0 or peer.id == msg.destination:
                        candidates.append((-int(msg.priority), -score, msg.id, source.id,
                                           peer.id, link))
        candidates.sort(key=lambda item: item[:5])
        used_bytes: dict[int, int] = {}
        dispatched: set[str] = set()
        for _, _, mid, sid, pid, link in candidates:
            if mid in dispatched or mid not in self.nodes[sid].buffer or mid in self.delivered:
                continue
            msg = self.nodes[sid].buffer[mid]
            key = id(link)
            if used_bytes.get(key, 0) + msg.size_bytes > link.bandwidth_bytes_per_tick:
                continue
            used_bytes[key] = used_bytes.get(key, 0) + msg.size_bytes
            dispatched.add(mid)
            source, peer = self.nodes[sid], self.nodes[pid]
            self.attempts += 1
            success = self.rng.random() < (1 - link.loss) * link.reliability
            self.predictor.observe(link, source.battery, success)
            self.events.append(dict(tick=t, type="forward" if success else "lost",
                                    id=mid, source=sid, destination=pid))
            if not success:
                self.failed_attempts += 1
                continue
            peer.seen.add(mid)
            del source.buffer[mid]
            if pid == msg.destination:
                self.delivered[mid] = t
                self.events.append(dict(tick=t, type="delivered", id=mid, node=pid))
            else:
                peer.buffer[mid] = msg

    def metrics(self) -> dict:
        delivered = [self.created[mid] for mid in self.delivered]
        return dict(tick=self.tick_number, created=len(self.created), delivered=len(delivered),
                    delivery_ratio=len(delivered) / len(self.created) if self.created else 0.0,
                    mean_delay_ticks=(sum(self.delivered[m.id] - m.created for m in delivered) /
                                      len(delivered) if delivered else None),
                    attempts=self.attempts, failed_attempts=self.failed_attempts,
                    predictor_samples=self.predictor.samples)
