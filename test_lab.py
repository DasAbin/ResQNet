import contextlib
import io
import unittest

import lab
from node import Store


class LabHelperTests(unittest.TestCase):
    def setUp(self):
        self.tmp = __import__("tempfile").TemporaryDirectory()
        self.db = self.tmp.name + "/a.db"

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
            code = lab.main(argv)
        return code, out.getvalue(), err.getvalue()

    def send(self, *extra):
        return self.run_cli(["send", "--db", self.db, "--node", "A",
                             "--to", "B", "--body", "synthetic test", *extra])

    def test_send_stores_and_reports_id(self):
        code, out, _ = self.send("--priority", "2")
        self.assertEqual(code, 0)
        result, msg_id = out.split()
        self.assertEqual(result, "stored")
        self.assertTrue(msg_id.startswith("A-"))
        self.assertEqual(Store(self.db, "A").status()["pending"], 1)

    def test_generated_ids_are_unique(self):
        first, second = self.send(), self.send()
        self.assertNotEqual(first[1].split()[1], second[1].split()[1])
        self.assertEqual(Store(self.db, "A").status()["pending"], 2)

    def test_repeat_explicit_id_conflicts(self):
        # IDs are immutable: a re-send computes a fresh expiry, so the same
        # ID with altered contents is a conflict, not a clean duplicate.
        self.assertEqual(self.send("--id", "m-1")[0], 0)
        code, out, _ = self.send("--id", "m-1")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("conflict"))
        self.assertEqual(Store(self.db, "A").status()["pending"], 1)

    def test_zero_ttl_expires(self):
        code, out, _ = self.send("--ttl", "0")
        self.assertEqual(code, 1)
        self.assertTrue(out.startswith("expired"))

    def test_invalid_priority_rejected(self):
        with self.assertRaises(SystemExit):
            lab.main(["send", "--db", self.db, "--node", "A", "--to", "B",
                      "--body", "x", "--priority", "7"])

    def test_send_to_self_is_delivered(self):
        code, out, _ = self.run_cli(["send", "--db", self.db, "--node", "A",
                                     "--to", "A", "--body", "self test"])
        self.assertEqual(code, 0)
        self.assertTrue(out.startswith("delivered"))

    def test_status_reports_counts_and_log(self):
        self.send("--id", "m-9")
        code, out, _ = self.run_cli(["status", "--db", self.db, "--node", "A"])
        self.assertEqual(code, 0)
        self.assertIn("pending 1", out)
        self.assertIn("delivered 0", out)
        self.assertIn("m-9", out)


if __name__ == "__main__":
    unittest.main()
