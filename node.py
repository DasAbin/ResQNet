"""Experimental TCP custody-transfer node for trusted local/container labs.

Not an emergency service. The wire protocol is unauthenticated and intentionally
limited to a private lab network. Never expose its listening port to the Internet.
"""
from __future__ import annotations

import argparse
import json
import socket
import socketserver
import sqlite3
import threading
import time
from pathlib import Path

MAX_FRAME = 8192
MAX_BODY = 4096
MAX_PENDING = 1000


class ProtocolError(ValueError):
    pass


def _frame(payload: dict) -> bytes:
    data = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    if len(data) > MAX_FRAME:
        raise ProtocolError("frame too large")
    return data + b"\n"


def _read_frame(sock: socket.socket) -> dict:
    buf = bytearray()
    while len(buf) <= MAX_FRAME:
        chunk = sock.recv(1)
        if not chunk:
            raise ProtocolError("connection closed before frame")
        if chunk == b"\n":
            try:
                obj = json.loads(buf)
            except (UnicodeError, json.JSONDecodeError) as exc:
                raise ProtocolError("invalid JSON") from exc
            if not isinstance(obj, dict):
                raise ProtocolError("frame must be object")
            return obj
        buf.extend(chunk)
    raise ProtocolError("frame too large")


def _message(data: dict) -> dict:
    if not isinstance(data, dict) or set(data) != {"id", "source", "destination", "body", "priority", "expires_at"}:
        raise ProtocolError("invalid message keys")
    for name, limit in (("id", 128), ("source", 64), ("destination", 64), ("body", MAX_BODY)):
        if not isinstance(data[name], str) or not data[name] or len(data[name].encode("utf-8")) > limit:
            raise ProtocolError("invalid message field: " + name)
    if type(data["priority"]) is not int or data["priority"] not in (0, 1, 2):
        raise ProtocolError("invalid priority")
    if type(data["expires_at"]) not in (int, float) or not 0 < data["expires_at"] < 1e12:
        raise ProtocolError("invalid expiry")
    return data


class Store:
    """SQLite-backed custody and deduplication; one connection per operation.

    ACK follows durable commit. Receiver preserves custody until expiry/delivery
    or a later peer ACK; duplicates get idempotent ACK without a second copy.
    """

    def __init__(self, path: str, node_id: str) -> None:
        self.path = path
        self.id = node_id
        if not node_id or len(node_id) > 64:
            raise ValueError("invalid node id")
        self.lock = threading.RLock()
        self._setup()

    def _db(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL, priority INTEGER NOT NULL, expires_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS delivered (id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT NOT NULL, sender TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(id,sender))")

    def receive(self, message: dict, sender: str | None = None) -> str:
        message = _message(message)
        if sender is not None and (not isinstance(sender,str) or not sender or len(sender.encode("utf-8"))>64 or sender==self.id):
            raise ProtocolError("invalid sender")
        if message["expires_at"] <= time.time():
            return "expired"
        # An ID is immutable. Reject a reused ID with altered contents.
        canonical = json.dumps(message, sort_keys=True, separators=(",", ":"))
        with self.lock, self._db() as db:
            previous = db.execute("SELECT fingerprint FROM seen WHERE id=?", (message["id"],)).fetchone()
            if previous:
                if previous["fingerprint"] != canonical:
                    return "conflict"
                if sender is not None:
                    receipt=db.execute("SELECT result FROM receipts WHERE id=? AND sender=?",(message["id"],sender)).fetchone()
                    if receipt:
                        return receipt["result"]
                return "duplicate"
            result = "delivered" if message["destination"] == self.id else "stored"
            if result == "stored":
                db.execute("DELETE FROM pending WHERE expires_at<=?", (time.time(),))
                if db.execute("SELECT count(*) FROM pending").fetchone()[0] >= MAX_PENDING:
                    return "full"
            db.execute("INSERT INTO seen VALUES (?,?)", (message["id"], canonical))
            if result == "delivered":
                db.execute("INSERT INTO delivered VALUES (?,?,?)", (message["id"], canonical, time.time()))
            else:
                db.execute("INSERT INTO pending VALUES (?,?,?,?)",
                           (message["id"], canonical, message["priority"], message["expires_at"]))
            if sender is not None:
                db.execute("INSERT INTO receipts VALUES (?,?,?)",(message["id"],sender,result))
            return result

    def pending(self) -> list[dict]:
        with self.lock, self._db() as db:
            db.execute("DELETE FROM pending WHERE expires_at<=?", (time.time(),))
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM pending ORDER BY priority DESC, expires_at ASC, id ASC")]

    def release(self, message_id: str):
        with self.lock, self._db() as db:
            db.execute("DELETE FROM pending WHERE id=?", (message_id,))

    def status(self) -> dict:
        with self.lock, self._db() as db:
            return {"node": self.id,
                    "pending": db.execute("SELECT count(*) FROM pending").fetchone()[0],
                    "delivered": db.execute("SELECT count(*) FROM delivered").fetchone()[0]}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            frame = _read_frame(self.request)
            if set(frame) != {"op", "message", "sender"} or frame["op"] != "offer":
                raise ProtocolError("expected offer")
            outcome = self.server.store.receive(frame["message"],sender=frame["sender"])
            self.request.sendall(_frame({"id": frame["message"].get("id"), "result": outcome}))
        except (ProtocolError, OSError, KeyError, TypeError) as exc:
            try:
                self.request.sendall(_frame({"error": str(exc)[:128]}))
            except OSError:
                pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, store: Store):
        self.store = store
        super().__init__(address, Handler)


def offer(host: str, port: int, message: dict, sender: str, timeout: float = 2) -> str:
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(_frame({"op": "offer", "message": _message(message), "sender": sender}))
        result = _read_frame(sock)
    if result.get("id") != message["id"] or result.get("result") not in ("stored", "delivered", "duplicate", "conflict", "expired", "full"):
        raise ProtocolError("invalid ACK")
    return result["result"]


def flush(store: Store, peer: tuple[str, int]) -> list[tuple[str, str]]:
    """Try each buffered message once. Retain custody on transport failure.

    A stored/delivered ACK is tied to this sender's accepted receipt. Retrying
    after a lost ACK returns that receipt, even if the peer has since forwarded.
    A generic duplicate from an upstream node is not custody proof.
    """
    outcomes = []
    for message in store.pending():
        try:
            result = offer(*peer, message, sender=store.id)
        except (OSError, ProtocolError):
            result = "unreachable"
        if result in ("stored", "delivered"):
            store.release(message["id"])
        outcomes.append((message["id"], result))
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="ResQNet trusted-lab TCP node")
    parser.add_argument("--id", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peer", action="append", default=[], help="host:port")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--allow-container-bind", action="store_true",
                        help="Permit wildcard bind only inside isolated, unpublished container network")
    args = parser.parse_args()
    if args.port <= 0 or args.port > 65535 or args.interval < .1:
        parser.error("invalid port or interval")
    if args.host == "0.0.0.0" and not args.allow_container_bind:
        parser.error("wildcard bind requires --allow-container-bind; use only in isolated lab")
    peers = []
    for peer in args.peer:
        try:
            host, port = peer.rsplit(":", 1)
            peers.append((host, int(port)))
        except ValueError:
            parser.error("peer must be host:port")
    store = Store(args.db, args.id)
    server = Server((args.host, args.port), store)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"listening": server.server_address, "id": args.id}), flush=True)
    try:
        while True:
            for peer in peers:
                flush(store, peer)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
