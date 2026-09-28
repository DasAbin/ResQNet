import unittest
from fastapi.testclient import TestClient
from api import app


class DashboardTests(unittest.TestCase):
    def test_serves_dashboard(self):
        result = TestClient(app).get('/')
        self.assertEqual(result.status_code, 200)
        self.assertIn('text/html', result.headers['content-type'])
        for label in ['Network topology', 'Run scenario', 'Event trace', 'Synthetic experiment only']:
            self.assertIn(label, result.text)
        self.assertIn("fetch('/v1/simulations'", result.text)


if __name__ == '__main__':
    unittest.main()
