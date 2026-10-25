import unittest
from fastapi.testclient import TestClient
from api import app


class DashboardTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_serves_dashboard(self):
        result = self.client.get('/')
        self.assertEqual(result.status_code, 200)
        self.assertIn('text/html', result.headers['content-type'])
        for label in ['offline field-report relay lab', 'RUN SIMULATION', 'WHY THIS HOP?',
                      'EVENT LOG', 'NOT A LIVE INCIDENT', 'HOW THIS SOLVES IT',
                      'LIVE LAB', 'OBSERVED', '/v1/lab/observe']:
            self.assertIn(label, result.text)

    def test_serves_assets_and_rejects_others(self):
        for asset in ('dashboard.css', 'dashboard-data.js',
                      'dashboard-render.js', 'dashboard-app.js'):
            self.assertEqual(self.client.get('/' + asset).status_code, 200, asset)
        app_js = self.client.get('/dashboard-app.js').text.replace("'", '"')
        self.assertIn("fetch('/v1/simulations'".replace("'", '"'), app_js)
        self.assertIn('/v1/lab/observe', app_js)
        self.assertEqual(self.client.get('/etc/passwd').status_code, 404)
        self.assertEqual(self.client.get('/api.py').status_code, 404)

    def test_presets_are_synthetic_and_bounded(self):
        text = self.client.get('/dashboard-data.js').text
        self.assertIn('synthetic', text)
        for preset_id in ('footbridge', 'two-routes', 'causeway'):
            self.assertIn(preset_id, text)


if __name__ == '__main__':
    unittest.main()
