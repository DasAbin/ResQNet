"""Loopback-only process integration for synthetic custody transfer."""
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

from node import Store


def free_port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


class ProcessLabTests(unittest.TestCase):
    def test_partition_store_carry_and_late_destination(self):
        with tempfile.TemporaryDirectory() as folder:
            ports = []
            while len(ports) < 3:
                port = free_port()
                if port not in ports:
                    ports.append(port)
            root = Path(folder)
            stores = [root / f'{node}.db' for node in 'ABC']
            processes = []
            def start(idx, peers=()):
                cmd = [sys.executable, '-u', str(Path(__file__).with_name('node.py')),
                       '--id', 'ABC'[idx], '--db', str(stores[idx]),
                       '--host', '127.0.0.1', '--port', str(ports[idx]), '--interval', '0.1']
                for peer in peers:
                    cmd += ['--peer', f'127.0.0.1:{ports[peer]}']
                proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(proc)
                deadline = time.monotonic() + 5
                while time.monotonic() < deadline:
                    if proc.poll() is not None:
                        self.fail(f'node exited: {proc.communicate()}')
                    try:
                        with socket.create_connection(('127.0.0.1', ports[idx]), timeout=.1):
                            return
                    except OSError:
                        time.sleep(.025)
                self.fail('node did not listen')

            def until(predicate, label):
                deadline = time.monotonic() + 6
                while time.monotonic() < deadline:
                    if predicate():
                        return
                    time.sleep(.05)
                self.fail(f'timed out waiting for {label}')

            try:
                start(0, (1,))
                source = Store(str(stores[0]), 'A')
                message = {'id': 'integration-synthetic', 'source': 'A', 'destination': 'C',
                           'body': 'Synthetic test only', 'priority': 2,
                           'expires_at': time.time() + 60}
                self.assertEqual(source.receive(message), 'stored')
                start(1, (2,))
                until(lambda: Store(str(stores[1]), 'B').status()['pending'] == 1 and
                      source.status()['pending'] == 0, 'custody at B')
                self.assertEqual(source.status()['pending'], 0)
                self.assertEqual(Store(str(stores[1]), 'B').status()['pending'], 1)
                start(2)
                until(lambda: Store(str(stores[2]), 'C').status()['delivered'] == 1 and
                      Store(str(stores[1]), 'B').status()['pending'] == 0, 'delivery at C')
                self.assertEqual(Store(str(stores[2]), 'C').status()['pending'], 0)
            finally:
                for proc in processes:
                    proc.terminate()
                for proc in processes:
                    try:
                        proc.communicate(timeout=3)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                        proc.communicate(timeout=3)


if __name__ == '__main__':
    unittest.main()
