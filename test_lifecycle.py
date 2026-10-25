"""Rescue-request lifecycle: received -> claimed -> resolved, distinct states."""
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import node
from node import Store, make_claim, make_receipt, make_resolve


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def req(req_id="sos-1", source="A", dest="C"):
    return {"id": req_id, "source": source, "destination": dest,
            "body": "synthetic request", "priority": 2, "expires_at": 1e11}


class LifecycleUnitTests(unittest.TestCase):
    def test_claim_and_resolve_shape(self):
        claim = make_claim(req(), "R1")
        self.assertEqual(claim["id"], "clm-sos-1")
        self.assertEqual(claim["destination"], "A")
        self.assertIn("not yet resolved", claim["body"])
        resolve = make_resolve(req(), "R1")
        self.assertEqual(resolve["id"], "res-sos-1")
        self.assertIn("resolved", resolve["body"])

    def test_system_messages_have_no_receipts(self):
        # A delivered claim must not spawn a receipt-of-claim loop.
        self.assertIsNone(make_receipt(make_claim(req(), "R1"), "A"))
        self.assertIsNone(make_claim(make_claim(req(), "R1"), "R2"))
        self.assertIsNone(make_resolve(make_receipt(req(), "C"), "A"))

    def test_claim_and_resolve_a_delivered_request(self):
        # Regression: claim/resolve must work on requests already delivered
        # (slim delivered rows once lacked priority/expires_at).
        with tempfile.TemporaryDirectory() as folder:
            store = Store(str(Path(folder) / "c.db"), "C")
            self.assertEqual(store.receive(req(dest="C")), "delivered")
            full = store.get("sos-1")
            self.assertEqual(full["priority"], 2)
            self.assertEqual(store.receive(make_claim(full, "C")), "stored")
            self.assertEqual(store.receive(make_resolve(full, "C")), "stored")
            self.assertIsNone(store.get("missing"))

    def test_conflicting_claim_rejected(self):
        with tempfile.TemporaryDirectory() as folder:
            store = Store(str(Path(folder) / "a.db"), "A")
            self.assertEqual(store.receive(make_claim(req(), "R1")), "delivered")
            self.assertEqual(store.receive(make_claim(req(), "R2")), "conflict")
            self.assertEqual(store.receive(make_claim(req(), "R1")), "duplicate")


class LifecycleProcessTests(unittest.TestCase):
    """Full loop over real processes: A - B - C, each hop a custody transfer."""

    def test_request_received_claimed_resolved(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            ports = [free_port() for _ in range(3)]
            procs = []
            def start(idx, peers):
                cmd = [sys.executable, "-u", str(Path(__file__).with_name("node.py")),
                       "--id", "ABC"[idx], "--db", str(root / f"{'ABC'[idx]}.db"),
                       "--host", "127.0.0.1", "--port", str(ports[idx]),
                       "--interval", "0.1", "--key", "lifecycle-key"]
                for peer in peers:
                    cmd += ["--peer", f"127.0.0.1:{ports[peer]}"]
                procs.append(subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                              stderr=subprocess.STDOUT, text=True))
            def store(idx):
                return Store(str(root / f"{'ABC'[idx]}.db"), "ABC"[idx])
            def until(pred, label, timeout=15.0):
                deadline = time.monotonic() + timeout
                while time.monotonic() < deadline:
                    if pred():
                        return
                    time.sleep(0.1)
                self.fail(f"timed out waiting for {label}")
            def delivered_ids(idx):
                return [m["id"] for m in store(idx).delivered_recent(50)]
            try:
                start(0, (1,)); start(1, (0, 2)); start(2, (1,))
                time.sleep(1.0)
                message = req(dest="C")
                message["expires_at"] = time.time() + 600
                self.assertEqual(store(0).receive(message), "stored")

                until(lambda: "sos-1" in delivered_ids(2), "request delivered at C")
                until(lambda: "rcpt-sos-1" in delivered_ids(0), "receipt back at A")
                # Distinct semantics: received, but not claimed or resolved.
                self.assertNotIn("clm-sos-1", delivered_ids(0))
                self.assertNotIn("res-sos-1", delivered_ids(0))

                # Operator at C claims the request; the claim travels back.
                self.assertEqual(store(2).receive(make_claim(message, "C")), "stored")
                until(lambda: "clm-sos-1" in delivered_ids(0), "claim back at A")
                self.assertNotIn("res-sos-1", delivered_ids(0))

                # Operator at C resolves it; resolution travels back.
                self.assertEqual(store(2).receive(make_resolve(message, "C")), "stored")
                until(lambda: "res-sos-1" in delivered_ids(0), "resolution back at A")
            finally:
                for proc in procs:
                    proc.kill()
                for proc in procs:
                    proc.wait()


if __name__ == "__main__":
    unittest.main()
