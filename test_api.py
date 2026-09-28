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
                         ["created", "forward", "link_changed", "forward", "delivered"])

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

    def test_full_source_buffer_reports_validation_error(self):
        s = {"nodes": [{"id":"A", "capacity":1}, {"id":"B"}],
             "links": [], "messages": [{"id": x, "source":"A", "destination":"B",
                                         "created":0, "ttl":3} for x in ("one", "two")],
             "ticks": 1}
        self.assertEqual(client.post("/v1/simulations", json=s).status_code, 422)


if __name__ == "__main__":
    unittest.main()
