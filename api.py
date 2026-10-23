"""Small, isolated API to run reproducible synthetic scenarios.

No distress dispatch or live network I/O is performed. Each request builds a fresh
simulator; the service deliberately retains no message bodies or sender identity.
"""
from __future__ import annotations

import os
import sqlite3
import time

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pathlib import Path
from typing import Literal
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
    policy: Literal["adaptive", "snapshot_shortest"] = "adaptive"
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
                    [Link(**l.model_dump()) for l in scenario.links], seed=scenario.seed,
                    policy=scenario.policy)
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


MAX_LAB_NODES = 8


class LabNodeInput(StrictModel):
    id: str = Field(min_length=1, max_length=64)
    db: str = Field(min_length=1, max_length=400)


class LabObserveInput(StrictModel):
    nodes: list[LabNodeInput] = Field(min_length=1, max_length=MAX_LAB_NODES)
    transfers: int = Field(default=30, ge=0, le=100)


def _lab_roots() -> list[Path]:
    """Directories lab stores may live in; default /tmp, override via env."""
    raw = os.environ.get("RESQNET_LAB_ROOTS", "/tmp")
    return [Path(item).resolve() for item in raw.split(os.pathsep) if item]


def _observe_node(node_id: str, db_path: str, transfer_limit: int) -> dict:
    """Read one running node's SQLite store, read-only. Facts only: what the
    store holds is reported; unavailable stores are errors, never invented.
    Paths are confined to the lab roots so this cannot read arbitrary files."""
    base = {"id": node_id, "db": db_path}
    path = Path(db_path).resolve()
    if not any(path == root or root in path.parents for root in _lab_roots()):
        return base | {"ok": False, "error": "path outside allowed lab roots"}
    if not path.is_file():
        return base | {"ok": False, "error": "store file not found (node not started here?)"}
    try:
        with sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=2) as con:
            con.row_factory = sqlite3.Row
            tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not {"seen", "pending", "delivered"} <= tables:
                return base | {"ok": False, "error": "not a ResQNet node store"}
            pending = [dict(r) for r in con.execute(
                "SELECT payload FROM pending ORDER BY priority DESC, expires_at ASC, id ASC LIMIT 50")]
            out_pending = []
            import json as _json
            for row in pending:
                message = _json.loads(row["payload"])
                out_pending.append({"id": message["id"], "source": message["source"],
                                    "destination": message["destination"],
                                    "priority": message["priority"],
                                    "expires_at": message["expires_at"],
                                    "body_preview": message["body"][:120]})
            delivered = [dict(r) for r in con.execute(
                "SELECT payload, delivered_at FROM delivered ORDER BY delivered_at DESC LIMIT 20")] if "delivered" in tables else []
            out_delivered = []
            for row in delivered:
                message = _json.loads(row["payload"])
                out_delivered.append({"id": message["id"], "source": message["source"],
                                      "destination": message["destination"],
                                      "delivered_at": row["delivered_at"]})
            transfers = []
            if "transfer_log" in tables and transfer_limit:
                transfers = [dict(r) for r in con.execute(
                    "SELECT seq, ts, direction, message_id, peer, result, detail FROM transfer_log ORDER BY seq DESC LIMIT ?",
                    (transfer_limit,))]
            counts = con.execute(
                "SELECT (SELECT count(*) FROM pending) AS pending, (SELECT count(*) FROM delivered) AS delivered").fetchone()
            return base | {"ok": True, "pending_count": counts["pending"],
                           "delivered_count": counts["delivered"], "pending": out_pending,
                           "delivered_recent": out_delivered, "transfers": transfers}
    except sqlite3.Error as exc:
        return base | {"ok": False, "error": str(exc)[:120]}


@app.post("/v1/lab/observe")
def lab_observe(request: LabObserveInput, raw: Request):
    """Observed state of trusted-lab socket nodes whose stores this machine can
    read. Loopback-only lab aid; stores hold plaintext synthetic messages."""
    client_host = raw.client.host if raw.client else ""
    # "testclient" is the ASGI test transport's marker, never a real socket peer.
    if client_host not in ("127.0.0.1", "::1", "testclient"):
        raise HTTPException(status_code=403, detail="lab observation is loopback-only")
    return {"observed_at": time.time(),
            "nodes": [_observe_node(n.id, n.db, request.transfers) for n in request.nodes],
            "warning": "Observed from local lab node stores, not simulated; synthetic data only."}


@app.get("/health")
def health():
    return {"status": "ok", "mode": "simulation_only"}


@app.get("/", include_in_schema=False)
def dashboard():
    return FileResponse(Path(__file__).with_name("dashboard.html"))


@app.get("/{asset}", include_in_schema=False)
def dashboard_asset(asset: str):
    if asset not in ("dashboard.css", "dashboard-data.js", "dashboard-render.js", "dashboard-app.js"):
        raise HTTPException(status_code=404)
    return FileResponse(Path(__file__).with_name(asset))
