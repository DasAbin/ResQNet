import os
import tempfile
import unittest
from fastapi.testclient import TestClient
from api import app, MAX_TICKS

client = TestClient(app)


def scenario():
    return {"nodes": [{"id": x} for x in "ABC"],
            "links": [{"a": "A", "b": "B"}, {"a": "B", "b": "C", "active": False}],
            "messages": [{"id": "sos", "source": "A", "destination": "C",
                          "priority": 2, "ttl": 10}],
            "changes": [{"tick": 2, "a": "B", "b": "C", "active": True}],
            "ticks": 2, "seed": 1}


class ApiTests(unittest.TestCase):
    def test_health_and_recovery(self):
        self.assertEqual(client.get("/health").json()["mode"], "simulation_only")
        r = client.post("/v1/simulations", json=scenario())
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["metrics"]["delivered"], 1)
        self.assertEqual([e["type"] for e in r.json()["events"]],
                         ["created", "decision", "forward", "link_changed",
                          "decision", "forward", "delivered"])

    def test_reproducible_and_request_isolation(self):
        s = scenario()
        first = client.post("/v1/simulations", json=s).json()
        self.assertEqual(first, client.post("/v1/simulations", json=s).json())
        s["changes"] = []
        second = client.post("/v1/simulations", json=s).json()
        self.assertEqual(second["metrics"]["delivered"], 0)

    def test_reject_unknown_endpoint_duplicate_and_bad_probability(self):
        s = scenario(); s["links"][0]["b"] = "UNKNOWN"
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)
        s = scenario(); s["nodes"].append({"id": "A"})
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)
        s = scenario(); s["links"][0]["loss"] = 1.5
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)
        s = scenario(); s["ticks"] = MAX_TICKS + 1
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)
        s = scenario(); s["nodes"][0]["unexpected"] = "ignored?"
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)

    def test_zero_tick_and_late_creation(self):
        s = scenario(); s["ticks"] = 0; s["changes"] = []
        self.assertEqual(client.post("/v1/simulations", json=s).json()["metrics"]["created"], 1)
        s["messages"][0]["created"] = 2
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)

    def test_late_message_creation_timestamp(self):
        s = scenario(); s["messages"][0]["created"] = 1
        r = client.post("/v1/simulations", json=s)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["events"][0]["tick"], 1)
        self.assertEqual(r.json()["metrics"]["mean_delay_ticks"], 1)

    def test_computation_budget(self):
        s = scenario(); s["ticks"] = 500
        s["messages"] *= 100
        # Duplicate IDs are also invalid; either way oversized work is rejected.
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)

    def test_policy_selection_and_rejection(self):
        s = scenario()
        s["changes"] = []
        r = client.post("/v1/simulations", json=s)
        self.assertEqual(r.json()["events"][1]["policy"], "adaptive")
        s["policy"] = "snapshot_shortest"
        r = client.post("/v1/simulations", json=s)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertTrue(all(e.get("policy") == "snapshot_shortest"
                            for e in r.json()["events"] if e["type"] == "decision"))
        # Baseline waits for a full active path, so nothing moves while B-C is down.
        self.assertEqual(r.json()["metrics"]["attempts"], 0)
        s["policy"] = "random"
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)

    def test_decision_events_explain_each_attempt(self):
        r = client.post("/v1/simulations", json=scenario())
        decisions = [e for e in r.json()["events"] if e["type"] == "decision"]
        self.assertEqual(len(decisions), 2)
        first = decisions[0]
        self.assertEqual((first["id"], first["source"], first["chosen"]), ("sos", "A", "B"))
        for key in ("progress", "p_success", "urgency", "congestion",
                    "latency_ms", "peer_load", "total"):
            self.assertIn(key, first["components"])
        m = r.json()["metrics"]
        self.assertEqual(len(m["predictor_weights"]), len(m["predictor_features"]))
        self.assertGreater(m["predictor_samples"], 0)

    def test_full_source_buffer_reports_validation_error(self):
        s = {"nodes": [{"id":"A", "capacity":1}, {"id":"B"}],
             "links": [], "messages": [{"id": x, "source":"A", "destination":"B",
                                         "created":0, "ttl":3} for x in ("one", "two")],
             "ticks": 1}
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)


    def test_lab_observe_reports_real_stores_and_errors(self):
        import tempfile, time
        from pathlib import Path
        from node import Store
        with tempfile.TemporaryDirectory() as tmp:
            db = str(Path(tmp) / 'a.db')
            store = Store(db, 'A')
            store.receive({'id':'m1','source':'A','destination':'C','body':'synthetic sos',
                           'priority':2,'expires_at':time.time()+300}, sender=None)
            result = client.post('/v1/lab/observe', json={
                'nodes':[{'id':'A','db':db},{'id':'B','db':str(Path(tmp)/'missing.db')}]})
            self.assertEqual(result.status_code, 200)
            nodes = {n['id']: n for n in result.json()['nodes']}
            self.assertTrue(nodes['A']['ok'])
            self.assertEqual(nodes['A']['pending_count'], 1)
            self.assertEqual(nodes['A']['pending'][0]['body_preview'], 'synthetic sos')
            self.assertEqual(nodes['A']['transfers'][0]['result'], 'stored')
            self.assertFalse(nodes['B']['ok'])
            self.assertIn('not found', nodes['B']['error'])

    def test_lab_observe_bounds_and_validation(self):
        too_many = [{'id':f'n{i}','db':'/tmp/x.db'} for i in range(9)]
        self.assertEqual(client.post('/v1/lab/observe', json={'nodes': too_many}).status_code, 422)
        self.assertEqual(client.post('/v1/lab/observe', json={'nodes':[]}).status_code, 422)


    def test_lab_observe_rejects_unsafe_paths_and_non_stores(self):
        import sqlite3, tempfile
        from pathlib import Path
        # Outside the allowed lab roots.
        result = client.post('/v1/lab/observe', json={'nodes':[{'id':'X','db':'/etc/passwd'}]})
        self.assertEqual(result.status_code, 200)
        node = result.json()['nodes'][0]
        self.assertFalse(node['ok'])
        self.assertIn('outside allowed lab roots', node['error'])
        # A SQLite file that is not a ResQNet store.
        with tempfile.TemporaryDirectory() as tmp:
            foreign = str(Path(tmp) / 'foreign.db')
            with sqlite3.connect(foreign) as con:
                con.execute('CREATE TABLE secrets (v TEXT)')
                con.execute("INSERT INTO secrets VALUES ('should-not-leak')")
            result = client.post('/v1/lab/observe', json={'nodes':[{'id':'F','db':foreign}]})
            node = result.json()['nodes'][0]
            self.assertFalse(node['ok'])
            self.assertIn('not a ResQNet node store', node['error'])
            self.assertNotIn('should-not-leak', result.text)
        # A file that is not SQLite at all.
        with tempfile.TemporaryDirectory() as tmp:
            plain = Path(tmp) / 'plain.db'
            plain.write_text('just text')
            result = client.post('/v1/lab/observe', json={'nodes':[{'id':'P','db':str(plain)}]})
            self.assertFalse(result.json()['nodes'][0]['ok'])

    def test_lab_observe_is_loopback_only(self):
        from fastapi.testclient import TestClient as TC
        remote = TC(app, client=('10.9.8.7', 4321))
        result = remote.post('/v1/lab/observe', json={'nodes':[{'id':'A','db':'/tmp/x.db'}]})
        self.assertEqual(result.status_code, 403)


if __name__ == "__main__":
    unittest.main()


class LabControlTests(unittest.TestCase):
    def setUp(self):
        os.environ["RESQNET_LAB_ROOTS"] = "/tmp"
        self.dir = tempfile.mkdtemp(dir="/tmp")
        self.db = os.path.join(self.dir, "a.db")

    def test_send_claim_resolve_roundtrip(self):
        sent = client.post("/v1/lab/send", json={"id": "A", "db": self.db, "to": "B",
                                                 "body": "synthetic report", "priority": 1})
        self.assertEqual(sent.status_code, 200)
        mid = sent.json()["id"]
        self.assertEqual(sent.json()["result"], "stored")
        claim = client.post("/v1/lab/claim", json={"id": "A", "db": self.db, "request": mid})
        self.assertEqual(claim.status_code, 200)
        self.assertTrue(claim.json()["id"].startswith("clm-"))
        resolve = client.post("/v1/lab/resolve", json={"id": "A", "db": self.db, "request": mid})
        self.assertTrue(resolve.json()["id"].startswith("res-"))

    def test_claim_missing_request_404(self):
        missing = client.post("/v1/lab/claim", json={"id": "A", "db": self.db,
                                                     "request": "no-such-id"})
        self.assertEqual(missing.status_code, 404)

    def test_store_path_confined(self):
        bad = client.post("/v1/lab/send", json={"id": "A", "db": "/etc/passwd",
                                                "to": "B", "body": "x"})
        self.assertEqual(bad.status_code, 422)
