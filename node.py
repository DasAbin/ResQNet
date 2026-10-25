"""Experimental TCP custody-transfer node for trusted local/container labs.

Not an emergency service. Run it only on a private lab network and never expose
its listening port to the Internet. With --key (or RESQNET_LAB_KEY) offers and
ACKs carry an HMAC-SHA256 tag so lab peers share one secret, and message bodies
travel with AES-256-GCM authenticated encryption (the `cryptography` package):
a fresh 12-byte random nonce per offer, the message metadata bound as
associated data, and the body key derived from the lab key with HKDF-SHA256.
The AEAD tag makes tampered or wrong-key ciphertexts fail deterministically,
and the server rejects byte-exact replayed offer frames from a bounded cache of
recent tags. Without a key the wire is unauthenticated and plaintext. Peer
choice per message uses the simulator's own DeliveryPredictor and score_hop
from core.py over configured link priors updated with observed facts (measured
round trips, custody outcomes, peer load from a status probe); the simulator's
topology progress term is absent because the TCP lab has no map. The
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

from core import DeliveryPredictor, Link, score_hop

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

    def get(self, message_id: str) -> dict | None:
        """Full stored payload for one id, from pending or delivered."""
        with self.lock, closing(self._db()) as db:
            for table in ("pending", "delivered"):
                row = db.execute(f"SELECT payload FROM {table} WHERE id=?",
                                 (message_id,)).fetchone()
                if row:
                    return json.loads(row["payload"])
        return None

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
                    "delivered": db.execute("SELECT count(*) FROM delivered").fetchone()[0],
                    "capacity": MAX_PENDING}


class Handler(socketserver.BaseRequestHandler):
    def handle(self):
        self.request.settimeout(3)
        try:
            frame = _read_frame(self.request)
            op = frame.get("op")
            key = self.server.key
            if op == "status":
                if not {"op"} <= set(frame) <= {"op", "tag"}:
                    raise ProtocolError("invalid status probe")
                if key is not None and frame.get("tag") != _tag(key, {"op": "status"}):
                    raise ProtocolError("unauthenticated status probe")
                reply = {"result": "status", **self.server.store.status()}
                if key is not None:
                    reply["tag"] = _tag(key, reply)
                self.request.sendall(_frame(reply))
                return
            body_keys = {"op", "message", "sender"}
            if op != "offer" or not body_keys <= set(frame) <= body_keys | {"tag"}:
                raise ProtocolError("expected offer")
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
            if outcome == "delivered":
                receipt = make_receipt(message, self.server.store.id)
                if receipt is not None:
                    # Silent bookkeeping: not an observed custody event.
                    self.server.store._receive(receipt)
            reply = {"id": frame["message"].get("id"), "result": outcome}
            if key is not None:
                reply["tag"] = _tag(key, reply)
            self.request.sendall(_frame(reply))
        except (ProtocolError, OSError, KeyError, TypeError) as exc:
            try:
                self.request.sendall(_frame({"error": str(exc)[:128]}))
            except OSError:
                pass


RECEIPT_PREFIX = "rcpt-"
CLAIM_PREFIX = "clm-"
RESOLVE_PREFIX = "res-"
SYSTEM_PREFIXES = (RECEIPT_PREFIX, CLAIM_PREFIX, RESOLVE_PREFIX)


def _system_message(prefix: str, request: dict, actor: str, body: str) -> dict | None:
    """Lifecycle system message about a rescue request.

    These are ordinary buffered messages with deterministic ids, so dedup,
    expiry, capacity and encryption all apply unchanged. A deterministic id
    means a second, different claim for the same request is rejected as a
    conflict: the first claim stands, which prevents double-claim confusion.
    """
    try:
        orig = request["id"]
        if not isinstance(orig, str) or not orig or orig.startswith(SYSTEM_PREFIXES):
            return None
        return {"id": prefix + orig[:120],
                "source": actor,
                "destination": request["source"],
                "body": body,
                "priority": request["priority"],
                "expires_at": request["expires_at"]}
    except (KeyError, TypeError):
        return None


def make_receipt(message: dict, receiver_id: str) -> dict | None:
    """Delivery receipt: the destination node RECEIVED this message.

    A receipt is not a rescuer claim, dispatch or resolution. It only records
    that the request arrived at the destination node's store, and travels back
    as an ordinary buffered message through the same custody machinery.
    """
    orig = message.get("id")
    return _system_message(RECEIPT_PREFIX, message, receiver_id,
                           f"delivery receipt: {str(orig)[:80]} received by node "
                           f"{receiver_id} (arrived, not claimed)") if isinstance(orig, str) else None


def make_claim(request: dict, claimer: str) -> dict | None:
    """Claim: an operator at a node takes responsibility for a request.

    Manual action, distinct from automatic delivery receipts. A claim says
    someone accepted the request; it does not say help arrived or the
    situation is resolved.
    """
    orig = request.get("id")
    return _system_message(CLAIM_PREFIX, request, claimer,
                           f"claim: request {str(orig)[:80]} claimed by node "
                           f"{claimer} (accepted, not yet resolved)") if isinstance(orig, str) else None


def make_resolve(request: dict, resolver: str) -> dict | None:
    """Resolve: an operator marks a request handled.

    Manual action. Resolution means the claiming side considers the request
    done; it is the final lifecycle state after received and claimed.
    """
    orig = request.get("id")
    return _system_message(RESOLVE_PREFIX, request, resolver,
                           f"resolved: request {str(orig)[:80]} resolved by node "
                           f"{resolver}") if isinstance(orig, str) else None


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


def peer_status(host: str, port: int, key=None, timeout: float = 2) -> dict:
    """One bounded status probe: reachability plus the peer's observed load."""
    key = _key_bytes(key)
    payload = {"op": "status"}
    if key is not None:
        payload["tag"] = _tag(key, payload)
    with socket.create_connection((host, port), timeout=timeout) as sock:
        sock.settimeout(timeout)
        sock.sendall(_frame(payload))
        result = _read_frame(sock)
    if (result.get("result") != "status" or type(result.get("pending")) is not int
            or type(result.get("capacity")) is not int or result["capacity"] <= 0):
        raise ProtocolError("invalid status reply")
    if key is not None:
        body = {k: result[k] for k in ("node", "pending", "delivered", "capacity", "result")
                if k in result}
        if result.get("tag") != _tag(key, body):
            raise ProtocolError("unauthenticated status reply")
    return result


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


class Peer:
    """A configured transport peer: address, link priors, observed link state.

    Priors (loss/reliability/congestion/latency) come from --peer options; the
    latency feature is then replaced by measured offer round trips and the peer
    load by status-probe replies. Nothing here is invented.
    """

    def __init__(self, host: str, port: int, *, loss: float = 0.0, reliability: float = 1.0,
                 congestion: float = 0.0, latency_ms: float = 20.0) -> None:
        self.host, self.port = host, port
        self.link = Link("self", f"{host}:{port}", latency_ms=latency_ms, loss=loss,
                         congestion=congestion, reliability=reliability)
        self.load: float | None = None  # observed pending/capacity from status probe
        self.up = True  # assumption until the first probe says otherwise

    @property
    def address(self) -> tuple[str, int]:
        return (self.host, self.port)

    def __repr__(self) -> str:
        return f"Peer({self.host}:{self.port})"


def parse_peer(spec: str) -> Peer:
    """host:port[,loss=..,reliability=..,congestion=..,latency_ms=..]"""
    parts = spec.split(",")
    try:
        host, port = parts[0].rsplit(":", 1)
        options = {}
        for item in parts[1:]:
            name, value = item.split("=", 1)
            if name not in ("loss", "reliability", "congestion", "latency_ms"):
                raise ValueError("unknown peer option: " + name)
            options[name] = float(value)
        return Peer(host, int(port), **options)
    except ValueError as exc:
        raise ValueError(f"invalid peer {spec!r}: {exc}") from exc


BEACON_TYPE = "resqnet-beacon"
BEACON_TTL = 10.0  # seconds a heard peer stays eligible


def make_beacon(node_id: str, port: int, key=None) -> bytes:
    body = {"type": BEACON_TYPE, "id": node_id, "port": port}
    kb = _key_bytes(key)
    if kb is not None:
        body["tag"] = hmac.new(kb, f"{node_id}|{port}".encode("utf-8"),
                               hashlib.sha256).hexdigest()
    return json.dumps(body, separators=(",", ":")).encode("utf-8")


def read_beacon(data: bytes, key=None):
    """Parse and authenticate a discovery beacon; None when invalid or foreign.

    Open-lab nodes accept only untagged beacons; keyed nodes only correctly
    tagged ones, so two labs sharing a LAN never mix.
    """
    try:
        body = json.loads(data.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(body, dict) or body.get("type") != BEACON_TYPE:
        return None
    node_id, port, tag = body.get("id"), body.get("port"), body.get("tag")
    if not isinstance(node_id, str) or not node_id or len(node_id) > 64:
        return None
    if type(port) is not int or not 0 < port <= 65535:
        return None
    kb = _key_bytes(key)
    if kb is None:
        return (node_id, port) if tag is None else None
    if not isinstance(tag, str):
        return None
    expected = hmac.new(kb, f"{node_id}|{port}".encode("utf-8"),
                        hashlib.sha256).hexdigest()
    return (node_id, port) if hmac.compare_digest(tag, expected) else None


class Discovery:
    """UDP broadcast beacons so lab nodes find each other without --peer flags.

    Same trust boundary as the wire protocol: with --key every beacon carries
    an HMAC-SHA256 tag and foreign beacons are ignored. Discovery only learns
    candidate TCP addresses; custody transfer still runs the normal
    authenticated offer/ACK path, so a spoofed beacon cannot inject messages.
    """

    def __init__(self, node_id, tcp_port, key=None, beacon_port=9910,
                 broadcast="255.255.255.255", interval=3.0, emit=print):
        self.id, self.port, self.key = node_id, tcp_port, key
        self.beacon_port, self.broadcast, self.interval = beacon_port, broadcast, interval
        self.emit = emit
        self.fresh: dict[tuple[str, int], float] = {}
        self._announced: set[tuple[str, int]] = set()

    def start(self):
        threading.Thread(target=self._send_loop, daemon=True).start()
        threading.Thread(target=self._listen_loop, daemon=True).start()

    def _send_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            packet = make_beacon(self.id, self.port, self.key)
            while True:
                try:
                    sock.sendto(packet, (self.broadcast, self.beacon_port))
                except OSError:
                    pass  # interface down (outage demo); retry next interval
                time.sleep(self.interval)

    def _listen_loop(self):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind(("", self.beacon_port))
            except OSError:
                return
            while True:
                try:
                    data, addr = sock.recvfrom(2048)
                except OSError:
                    return
                heard = read_beacon(data, self.key)
                if not heard or heard[0] == self.id:
                    continue
                peer = (addr[0], heard[1])
                self.fresh[peer] = time.time()
                if peer not in self._announced:
                    self._announced.add(peer)
                    self.emit(json.dumps({"discovered": heard[0],
                                          "at": f"{peer[0]}:{peer[1]}"}), flush=True)

    def live_peers(self, now=None):
        now = time.time() if now is None else now
        return [peer for peer, seen in self.fresh.items() if now - seen < BEACON_TTL]


def flush_peers(store: Store, peers: list, predictor: DeliveryPredictor, key=None) -> list[tuple]:
    """Offer buffered messages using the simulator's own adaptive scoring.

    Each round first probes peers for status (reachability + observed load).
    Each message is then offered to its best-scoring reachable peer, falling
    back down the order on refusal; the score is core.score_hop over the shared
    DeliveryPredictor estimate, message urgency, configured congestion,
    measured latency and observed peer load. The simulator's topology progress
    term is excluded (the TCP lab has no map). Outcomes train the same model.
    Every probe and attempt is logged with its score components for the UI.
    """
    outcomes = []
    for peer in peers:
        try:
            started = time.monotonic()
            status = peer_status(peer.host, peer.port, key=key)
            rtt_ms = (time.monotonic() - started) * 1000.0
            peer.link.latency_ms = round(0.5 * peer.link.latency_ms + 0.5 * rtt_ms, 3)
            peer.load = status["pending"] / status["capacity"]
            peer.up = True
            predictor.observe(peer.link, 1.0, True)
        except (OSError, ProtocolError):
            peer.up = False
            peer.load = None
            predictor.observe(peer.link, 1.0, False)
    for message in store.pending():
        mid = message["id"]
        urgency = 1 + message["priority"]
        candidates = []
        for peer in peers:
            if not peer.up:
                continue
            p_success = predictor.predict(peer.link, 1.0)
            score = score_hop(p_success, urgency, peer.link.congestion,
                              peer.link.latency_ms, peer.load or 0.0)
            components = {"score": round(score, 4), "p_success": round(p_success, 4),
                          "urgency": urgency, "congestion": peer.link.congestion,
                          "latency_ms": round(peer.link.latency_ms, 1),
                          "peer_load": round(peer.load or 0.0, 4), "progress": None,
                          "basis": "core.score_hop + shared DeliveryPredictor; "
                                   "progress term n/a (no topology in TCP lab)"}
            candidates.append((score, f"{peer.host}:{peer.port}", peer, components))
        candidates.sort(key=lambda item: (-item[0], item[1]))
        if not candidates:
            store._log("out", mid, "-", "unreachable",
                       {"basis": "no peer answered this round's status probe"})
            outcomes.append((mid, "unreachable", None))
            continue
        for score, label, peer, components in candidates:
            try:
                result = offer(peer.host, peer.port, message, sender=store.id, key=key)
            except (OSError, ProtocolError):
                result = "unreachable"
                peer.up = False
                predictor.observe(peer.link, 1.0, False)
            store._log("out", mid, label, result, components)
            outcomes.append((mid, result, peer.address))
            if result in ("stored", "delivered"):
                store.release(mid)
                predictor.observe(peer.link, 1.0, True)
                break
        # full/expired/conflict/duplicate: custody retained, no model update
    return outcomes


def main() -> None:
    parser = argparse.ArgumentParser(description="ResQNet trusted-lab TCP node")
    parser.add_argument("--id", required=True)
    parser.add_argument("--db", required=True)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, required=True)
    parser.add_argument("--peer", action="append", default=[],
                        help="host:port[,loss=..,reliability=..,congestion=..,latency_ms=..] priors")
    parser.add_argument("--interval", type=float, default=2)
    parser.add_argument("--allow-container-bind", action="store_true",
                        help="Permit wildcard bind only inside isolated, unpublished container network")
    parser.add_argument("--key", default=os.environ.get("RESQNET_LAB_KEY"),
                        help="Shared lab HMAC key (or RESQNET_LAB_KEY); omit for unauthenticated open-lab mode")
    parser.add_argument("--discover", action="store_true",
                        help="Broadcast beacons and auto-peer with discovered lab nodes")
    parser.add_argument("--discover-port", type=int, default=9910)
    parser.add_argument("--discover-interval", type=float, default=3.0)
    args = parser.parse_args()
    if args.port <= 0 or args.port > 65535 or args.interval < .1:
        parser.error("invalid port or interval")
    if args.host == "0.0.0.0" and not args.allow_container_bind:
        parser.error("wildcard bind requires --allow-container-bind; use only in isolated lab")
    try:
        peers = [parse_peer(spec) for spec in args.peer]
    except ValueError as exc:
        parser.error(str(exc))
    store = Store(args.db, args.id)
    server = Server((args.host, args.port), store, key=args.key)
    predictor = DeliveryPredictor()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(json.dumps({"listening": server.server_address, "id": args.id,
                      "wire": "hmac-sha256-tag+encrypted-body" if args.key else "open-lab-plaintext"}), flush=True)
    discovery = None
    if args.discover:
        if not 0 < args.discover_port <= 65535 or args.discover_interval < .1:
            parser.error("invalid discover port or interval")
        discovery = Discovery(args.id, args.port, key=args.key,
                              beacon_port=args.discover_port,
                              interval=args.discover_interval)
        discovery.start()
    try:
        while True:
            live = peers
            if discovery is not None:
                known = {p.address for p in peers}
                live = peers + [Peer(h, p) for h, p in discovery.live_peers()
                                if (h, p) not in known]
            flush_peers(store, live, predictor, key=args.key)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
