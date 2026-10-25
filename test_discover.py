"""Discovery beacons and delivery receipts: unit and live-process tests."""
import json
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import node
from node import Discovery, Store, make_beacon, make_receipt, read_beacon


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class BeaconTests(unittest.TestCase):
    def test_keyed_roundtrip(self):
        packet = make_beacon("A", 9101, key="lab-key")
        self.assertEqual(read_beacon(packet, key="lab-key"), ("A", 9101))

    def test_tampered_and_wrong_key_rejected(self):
        packet = make_beacon("A", 9101, key="lab-key")
        body = json.loads(packet)
        body["port"] = 9999
        tampered = json.dumps(body).encode()
        self.assertIsNone(read_beacon(tampered, key="lab-key"))
        self.assertIsNone(read_beacon(packet, key="other-key"))
        self.assertIsNone(read_beacon(packet, key=None))

    def test_open_lab_rules(self):
        packet = make_beacon("B", 9102)
        self.assertEqual(read_beacon(packet), ("B", 9102))
        self.assertIsNone(read_beacon(packet, key="lab-key"))

    def test_garbage_rejected(self):
        self.assertIsNone(read_beacon(b"not json"))
        self.assertIsNone(read_beacon(json.dumps({"type": "other", "id": "A", "port": 1}).encode()))
        self.assertIsNone(read_beacon(make_beacon("A", 70000)))


class ReceiptTests(unittest.TestCase):
    def test_make_receipt(self):
        msg = {"id": "sos-1", "source": "A", "destination": "B", "body": "x",
               "priority": 2, "expires_at": 1e11}
        receipt = make_receipt(msg, "B")
        self.assertEqual(receipt["id"], "rcpt-sos-1")
        self.assertEqual(receipt["destination"], "A")
        self.assertIn("not claimed", receipt["body"])

    def test_no_receipt_of_receipt(self):
        msg = {"id": "rcpt-sos-1", "source": "B", "destination": "A", "body": "x",
               "priority": 2, "expires_at": 1e11}
        self.assertIsNone(make_receipt(msg, "A"))

    def test_long_id_truncated(self):
        msg = {"id": "x" * 128, "source": "A", "destination": "B", "body": "x",
               "priority": 0, "expires_at": 1e11}
        self.assertLessEqual(len(make_receipt(msg, "B")["id"]), 128)


class DiscoveryStateTests(unittest.TestCase):
    def test_live_peers_expiry(self):
        disc = Discovery("A", 9101, emit=lambda *a, **k: None)
        disc.fresh[("10.0.0.2", 9102)] = 1000.0
        disc.fresh[("10.0.0.3", 9103)] = 1005.0
        self.assertEqual(disc.live_peers(now=1011.0), [("10.0.0.3", 9103)])
        self.assertEqual(disc.live_peers(now=1016.0), [])


class DiscoveryProcessTests(unittest.TestCase):
    """Real zero-config messaging over broadcast discovery, then receipt back."""

    def start_node(self, folder, name, port, key, discover_port, extra=()):
        cmd = [sys.executable, "-u", str(Path(__file__).with_name("node.py")),
               "--id", name, "--db", str(folder / f"{name}.db"),
               "--host", "0.0.0.0", "--allow-container-bind",
               "--port", str(port), "--interval", "0.1",
               "--discover", "--discover-port", str(discover_port),
               "--discover-interval", "0.3", *extra]
        if key:
            cmd += ["--key", key]
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        self.processes.append(proc)
        return proc

    def setUp(self):
        self.processes = []

    def tearDown(self):
        for proc in self.processes:
            proc.kill()
        for proc in self.processes:
            proc.wait()

    def wait_for(self, predicate, timeout=20.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.25)
        return False

    def test_zero_config_delivery_and_receipt(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            port_a, port_b, dport = free_port(), free_port(), free_port()
            self.start_node(root, "A", port_a, "disc-key", dport)
            self.start_node(root, "B", port_b, "disc-key", dport)
            time.sleep(1.0)  # let beacons fly before the message exists
            message = {"id": "sos-live", "source": "A", "destination": "B",
                       "body": "synthetic request", "priority": 2,
                       "expires_at": time.time() + 600}
            self.assertEqual(Store(str(root / "A.db"), "A").receive(message), "stored")
            delivered_b = self.wait_for(
                lambda: Store(str(root / "B.db"), "B").status()["delivered"] == 1)
            self.assertTrue(delivered_b, "B never received the message via discovery")
            # Delivery receipt travels back as an ordinary buffered message.
            receipt_back = self.wait_for(
                lambda: any(row["id"] == "rcpt-sos-live"
                            for row in Store(str(root / "A.db"), "A").delivered_recent()))
            self.assertTrue(receipt_back, "delivery receipt never returned to A")

    def test_wrong_key_not_discovered(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            port_a, port_b, dport = free_port(), free_port(), free_port()
            self.start_node(root, "A", port_a, "key-one", dport)
            self.start_node(root, "B", port_b, "key-two", dport)
            time.sleep(1.0)
            message = {"id": "m-iso", "source": "A", "destination": "B",
                       "body": "synthetic", "priority": 1,
                       "expires_at": time.time() + 600}
            Store(str(root / "A.db"), "A").receive(message)
            time.sleep(6.0)
            self.assertEqual(Store(str(root / "B.db"), "B").status()["delivered"], 0)
            self.assertEqual(Store(str(root / "A.db"), "A").status()["pending"], 1)


if __name__ == "__main__":
    unittest.main()
