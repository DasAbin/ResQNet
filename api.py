"""Small, isolated API to run reproducible synthetic scenarios.

No distress dispatch or live network I/O is performed. Each request builds a fresh
simulator; the service deliberately retains no message bodies or sender identity.
"""
from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field, model_validator
from core import Link, Message, Node, Priority, Simulator

app = FastAPI(title="ResQNet experiment API", version="0.1.0")
MAX_TICKS = 500
MAX_NODES = 100
MAX_MESSAGES = 1000


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NodeInput(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    battery: float = Field(default=1.0, ge=0, le=1)
    capacity: int = Field(default=50, ge=1, le=10000)


class LinkInput(StrictModel):
    a: str
    b: str
    active: bool = True
    latency_ms: float = Field(default=20, ge=0, le=100000)
    bandwidth_bytes_per_tick: int = Field(default=1000, ge=1, le=10000000)
    loss: float = Field(default=0, ge=0, le=1)
    congestion: float = Field(default=0, ge=0, le=1)
    reliability: float = Field(default=1, ge=0, le=1)


class MessageInput(StrictModel):
    id: str = Field(min_length=1, max_length=128)
    source: str
    destination: str
    created: int = Field(default=0, ge=0)
    ttl: int = Field(default=50, ge=1, le=MAX_TICKS)
    priority: Priority = Priority.ROUTINE
    size_bytes: int = Field(default=100, ge=1, le=10000000)


class LinkChange(StrictModel):
    tick: int = Field(ge=1, le=MAX_TICKS)
    a: str
    b: str
    active: bool


class RunInput(StrictModel):
    nodes: list[NodeInput] = Field(min_length=1, max_length=MAX_NODES)
    links: list[LinkInput] = Field(max_length=2000)
    messages: list[MessageInput] = Field(max_length=MAX_MESSAGES)
    changes: list[LinkChange] = Field(default_factory=list, max_length=2000)
    ticks: int = Field(default=20, ge=0, le=MAX_TICKS)
    seed: int = Field(default=0, ge=0, le=2**32-1)

    @model_validator(mode="after")
    def validate_graph(self):
        if self.ticks * max(1, len(self.messages)) * max(1, len(self.links)) > 100000:
            raise ValueError("scenario computation budget exceeded")
        ids = [n.id for n in self.nodes]
        if len(set(ids)) != len(ids):
            raise ValueError("node IDs must be unique")
        ids_set = set(ids)
        pairs = []
        for link in self.links:
            if link.a not in ids_set or link.b not in ids_set or link.a == link.b:
                raise ValueError("invalid link endpoint")
            pairs.append(frozenset((link.a, link.b)))
        if len(set(pairs)) != len(pairs):
            raise ValueError("duplicate undirected link")
        mids = [m.id for m in self.messages]
        if len(set(mids)) != len(mids):
            raise ValueError("message IDs must be unique")
        if any(m.source not in ids_set or m.destination not in ids_set or m.created > self.ticks
               for m in self.messages):
            raise ValueError("message endpoint or creation tick invalid")
        if any(change.tick > self.ticks or frozenset((change.a, change.b)) not in pairs
               for change in self.changes):
            raise ValueError("link change references unknown link or out-of-range tick")
        if len({(c.tick, frozenset((c.a, c.b))) for c in self.changes}) != len(self.changes):
            raise ValueError("duplicate link change in one tick")
        return self


@app.post("/v1/simulations")
def run_scenario(scenario: RunInput):
    """Simulate a bounded scenario. Changes take effect before sends in that tick."""
    sim = Simulator([Node(**n.model_dump()) for n in scenario.nodes],
                    [Link(**l.model_dump()) for l in scenario.links], seed=scenario.seed)
    changes = {}
    for c in scenario.changes:
        changes.setdefault(c.tick, []).append(c)
    messages = {}
    for m in scenario.messages:
        messages.setdefault(m.created, []).append(m)
    try:
        for m in messages.get(0, []):
            sim.inject(Message(**m.model_dump()))
        for tick in range(1, scenario.ticks + 1):
            for c in changes.get(tick, []):
                link = next(l for l in sim.links if {l.a, l.b} == {c.a, c.b})
                link.active = c.active
                sim.events.append(dict(tick=tick, type="link_changed", a=c.a, b=c.b,
                                       active=c.active))
            for m in messages.get(tick, []):
                sim.inject(Message(**m.model_dump()))
            sim.tick()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return {"metrics": sim.metrics(), "events": sim.events,
            "buffers": {n.id: list(n.buffer) for n in sim.nodes.values()},
            "warning": "Synthetic experiment only; not a real emergency service."}


@app.get("/health")
def health():
    return {"status": "ok", "mode": "simulation_only"}
