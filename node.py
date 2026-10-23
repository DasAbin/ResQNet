"""Experimental TCP custody-transfer node for trusted local/container labs.

Not an emergency service. Run it only on a private lab network and never expose
its listening port to the Internet. With --key (or RESQNET_LAB_KEY) offers and
ACKs carry an HMAC-SHA256 tag so lab peers share one secret, and message bodies
travel with AES-256-GCM authenticated encryption (the `cryptography` package):
a fresh 12-byte random nonce per offer, the message metadata bound as
associated data, and the body key derived from the lab key with HKDF-SHA256.
The AEAD tag makes tampered or wrong-key ciphertexts fail deterministically,
and the server rejects byte-exact replayed offer frames from a bounded cache of
recent tags. Without a key the wire is unauthenticated and plaintext. The
shared key deters casual forgery and eavesdropping inside a lab; it is one
secret every node holds, so it is not per-node identity, not end-to-end
encryption, not key rotation, and the custody store on disk stays plaintext.
Production would need per-node keys, TLS per link, and end-to-end AEAD.
"""
from __future__ import annotations

import argparse
import base64
import collections
from contextlib import closing
import hashlib
import hmac
import json
import os
import socket
import socketserver
import sqlite3
import threading
import time

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
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


def _tag(key: bytes, obj: dict) -> str:
    canonical = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


def _key_bytes(key) -> bytes | None:
    if key is None:
        return None
    if isinstance(key, str):
        key = key.encode("utf-8")
    if not key or len(key) > 1024:
        raise ValueError("invalid lab key")
    return key


ENC_PREFIX = "enc2:"
NONCE_BYTES = 12  # AES-GCM standard nonce; random per offer, lab-scale volume
REPLAY_CACHE = 4096


def _enc_key(key: bytes) -> bytes:
    # HKDF subkey for bodies so encryption never reuses the auth key material.
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=b"resqnet-node",
                info=b"encryption-v2").derive(key)


def _aad(message: dict) -> bytes:
    # Bind the ciphertext to this message's metadata so it cannot be
    # transplanted onto a different message ID, route, priority or expiry.
    fields = {k: message[k] for k in ("id", "source", "destination", "priority", "expires_at")}
    return json.dumps(fields, sort_keys=True, separators=(",", ":")).encode("utf-8")


def encrypt_body(key: bytes, plaintext: str, aad: bytes = b"") -> str:
    nonce = os.urandom(NONCE_BYTES)
    ciphertext = AESGCM(_enc_key(key)).encrypt(nonce, plaintext.encode("utf-8"), aad)
    return ENC_PREFIX + base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_body(key: bytes, text: str, aad: bytes = b"") -> str:
    try:
        raw = base64.b64decode(text[len(ENC_PREFIX):], validate=True)
    except (ValueError, TypeError) as exc:
        raise ProtocolError("invalid ciphertext encoding") from exc
    if len(raw) <= NONCE_BYTES + 16:
        raise ProtocolError("ciphertext too short")
    nonce, ciphertext = raw[:NONCE_BYTES], raw[NONCE_BYTES:]
    try:
        return AESGCM(_enc_key(key)).decrypt(nonce, ciphertext, aad).decode("utf-8")
    except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
        raise ProtocolError("undecryptable or tampered body") from exc


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
        connection.execute("PRAGMA synchronous=FULL")
        connection.row_factory = sqlite3.Row
        return connection

    def _setup(self):
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        with closing(self._db()) as db, db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("PRAGMA synchronous=FULL")
            db.execute("CREATE TABLE IF NOT EXISTS seen (id TEXT PRIMARY KEY, fingerprint TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS pending (id TEXT PRIMARY KEY, payload TEXT NOT NULL, priority INTEGER NOT NULL, expires_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS delivered (id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered_at REAL NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS receipts (id TEXT NOT NULL, sender TEXT NOT NULL, result TEXT NOT NULL, PRIMARY KEY(id,sender))")
            db.execute("CREATE TABLE IF NOT EXISTS transfer_log (seq INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL NOT NULL, direction TEXT NOT NULL, message_id TEXT NOT NULL, peer TEXT NOT NULL, result TEXT NOT NULL, detail TEXT)")

    def receive(self, message: dict, sender: str | None = None) -> str:
        result = self._receive(message, sender)
        # Observed custody event for the live-lab bridge; facts only, no inference.
        self._log("in", message.get("id", "?"), sender or "local", result, None)
        return result

    def _receive(self, message: dict, sender: str | None = None) -> str:
        message = _message(message)
        if sender is not None and (not isinstance(sender,str) or not sender or len(sender.encode("utf-8"))>64 or sender==self.id):
            raise ProtocolError("invalid sender")
        if message["expires_at"] <= time.time():
            return "expired"
        # An ID is immutable. Reject a reused ID with altered contents.
        canonical = json.dumps(message, sort_keys=True, separators=(",", ":"))
        with self.lock, closing(self._db()) as db, db:
            # Serialize capacity and immutable-ID decisions across processes too.
            db.execute("BEGIN IMMEDIATE")
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

    def _log(self, direction: str, message_id: str, peer: str, result: str, detail) -> None:
        with self.lock, closing(self._db()) as db, db:
            db.execute("INSERT INTO transfer_log (ts, direction, message_id, peer, result, detail) VALUES (?,?,?,?,?,?)",
                       (time.time(), direction, message_id[:128], peer[:96], result[:32],
                        json.dumps(detail, separators=(",", ":")) if detail is not None else None))
            db.execute("DELETE FROM transfer_log WHERE seq < (SELECT MAX(seq) FROM transfer_log) - 1000")

    def transfers(self, limit: int = 50) -> list[dict]:
        with self.lock, closing(self._db()) as db:
            rows = db.execute(
                "SELECT seq, ts, direction, message_id, peer, result, detail FROM transfer_log ORDER BY seq DESC LIMIT ?",
                (max(0, min(int(limit), 200)),)).fetchall()
            return [dict(row) for row in rows]

    def delivered_recent(self, limit: int = 20) -> list[dict]:
        with self.lock, closing(self._db()) as db:
            rows = db.execute("SELECT payload, delivered_at FROM delivered ORDER BY delivered_at DESC LIMIT ?",
                              (max(0, min(int(limit), 100)),)).fetchall()
            out = []
            for row in rows:
                message = json.loads(row["payload"])
                out.append({"id": message["id"], "source": message["source"],
                            "destination": message["destination"], "delivered_at": row["delivered_at"]})
            return out

    def pending(self) -> list[dict]:
        with self.lock, closing(self._db()) as db, db:
            db.execute("DELETE FROM pending WHERE expires_at<=?", (time.time(),))
            return [json.loads(row["payload"]) for row in db.execute(
                "SELECT payload FROM pending ORDER BY priority DESC, expires_at ASC, id ASC")]

    def release(self, message_id: str):
        with self.lock, closing(self._db()) as db, db:
            db.execute("DELETE FROM pending WHERE id=?", (message_id,))

    def status(self) -> dict:
        with self.lock, closing(self._db()) as db, db:
            return {"node": self.id,
                    "pending": db.execute("SELECT count(*) FROM pending").fetchone()[0],
                    "delivered": db.execute("SELECT count(*) FROM delivered").fetchone()[0]}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            frame = _read_frame(self.request)
            body_keys = {"op", "message", "sender"}
            if not body_keys <= set(frame) <= body_keys | {"tag"} or frame["op"] != "offer":
                raise ProtocolError("expected offer")
            key = self.server.key
            if key is not None:
                body = {k: frame[k] for k in body_keys}
                tag = frame.get("tag")
                if tag != _tag(key, body):
                    raise ProtocolError("unauthenticated offer")
                with self.server.recent_lock:
                    if tag in self.server.recent:
                        raise ProtocolError("replayed offer frame")
                    self.server.recent[tag] = None
                    while len(self.server.recent) > REPLAY_CACHE:
                        self.server.recent.popitem(last=False)
            message = frame["message"]
            body = message.get("body")
            if not isinstance(body, str):
                raise ProtocolError("invalid message field: body")
            if key is not None:
                # Tag above already covers the ciphertext (encrypt-then-MAC).
                if not body.startswith(ENC_PREFIX):
                    raise ProtocolError("expected encrypted body")
                message = dict(message)
                message["body"] = decrypt_body(key, body, _aad(message))
            elif body.startswith(ENC_PREFIX):
                raise ProtocolError("encrypted offer but no key")
            outcome = self.server.store.receive(message,sender=frame["sender"])
            reply = {"id": frame["message"].get("id"), "result": outcome}
            if key is not None:
                reply["tag"] = _tag(key, reply)
            self.request.sendall(_frame(reply))
        except (ProtocolError, OSError, KeyError, TypeError) as exc:
            try:
                self.request.sendall(_frame({"error": str(exc)[:128]}))
            except OSError:
                pass


class Server(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True
    request_queue_size = 16

    def __init__(self, address, store: Store, key=None):
        self.store = store
        self.key = _key_bytes(key)
        self.recent = collections.OrderedDict()  # bounded recent offer tags
        self.recent_lock = threading.Lock()
        super().__init__(address, Handler)


def offer(host: str, port: int, message: dict, sender: str, timeout: float = 2, key=None) -> str:
    key = _key_bytes(key)
    wire = _message(message)
    if key is not None:
        # Copy so a flush retry never re-encrypts the caller's plaintext dict.
        wire = dict(wire)
        wire["body"] = encrypt_body(key, wire["body"], _aad(wire))
    payload = {"op": "offer", "message": wire, "sender": sender}
    if key is not None:
        payload["tag"] = _tag(key, payload)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(_frame(payload))
        result = _read_frame(sock)
    if result.get("id") != message["id"] or result.get("result") not in ("stored", "delivered", "duplicate", "conflict", "expired", "full"):
        raise ProtocolError("invalid ACK")
    if key is not None and result.get("tag") != _tag(key, {"id": result["id"], "result": result["result"]}):
        raise ProtocolError("unauthenticated ACK")
    return result["result"]


def flush(store: Store, peer: tuple[str, int], key=None) -> list[tuple[str, str]]:
    """Try each buffered message once. Retain custody on transport failure.

    A stored/delivered ACK is tied to this sender's accepted receipt. Retrying
    after a lost ACK returns that receipt, even if the peer has since forwarded.
    A generic duplicate from an upstream node is not custody proof.
    """
    outcomes = []
    for message in store.pending():
        try:
            result = offer(*peer, message, sender=store.id, key=key)
        except (OSError, ProtocolError):
            result = "unreachable"
        if result in ("stored", "delivered"):
            store.release(message["id"])
        outcomes.append((message["id"], result))
    return outcomes


class PeerTracker:
    """Online per-peer contact estimate (exponential moving average).

    Only custody-accepted ACKs (stored/delivered) raise a peer's estimate and
    only transport failure lowers it; full buffers, expiry, conflicts and
    duplicates are not link evidence. New peers start at the initial estimate,
    so an unknown peer is tried rather than starved. Lab-scale heuristic, not
    the simulator's logistic model.
    """

    def __init__(self, rate: float = 0.3, initial: float = 0.5) -> None:
        if not 0 < rate <= 1 or not 0 <= initial <= 1:
            raise ValueError("invalid tracker parameters")
        self.rate, self.initial, self.scores = rate, initial, {}

    def score(self, peer) -> float:
        return self.scores.get(peer, self.initial)

    def observe(self, peer, success: bool) -> None:
        current = self.score(peer)
        self.scores[peer] = current + self.rate * ((1.0 if success else 0.0) - current)

    def order(self, peers) -> list:
        return sorted(peers, key=lambda p: (-self.score(p), str(p)))


def flush_peers(store: Store, peers: list, tracker: PeerTracker, key=None) -> list[tuple]:
    """Offer buffered messages to peers, best estimated contact first.

    Every attempt is logged to the store with the estimate that ordered it, so
    the live-lab view can show why a peer was tried in that position.
    """
    outcomes = []
    for rank, peer in enumerate(tracker.order(list(peers))):
        score_before = tracker.score(peer)
        tried = peer in tracker.scores
        basis = (f"EMA {score_before:.3f} from past custody outcomes" if tried
                 else f"untried peer, optimistic default {score_before:.3f}")
        for mid, result in flush(store, peer, key=key):
            if result in ("stored", "delivered"):
                tracker.observe(peer, True)
            elif result == "unreachable":
                tracker.observe(peer, False)
            store._log("out", mid, f"{peer[0]}:{peer[1]}", result,
                       {"rank": rank, "score": round(score_before, 4), "basis": basis})
            outcomes.append((mid, result, peer))
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
    parser.add_argument("--key", default=os.environ.get("RESQNET_LAB_KEY"),
                        help="Shared lab HMAC key (or RESQNET_LAB_KEY); omit for unauthenticated open-lab mode")
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
    server = Server((args.host, args.port), store, key=args.key)
    tracker = PeerTracker()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"listening": server.server_address, "id": args.id,
                      "wire": "hmac-sha256-tag+encrypted-body" if args.key else "open-lab-plaintext"}), flush=True)
    try:
        while True:
            flush_peers(store, peers, tracker, key=args.key)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
