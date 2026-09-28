import pathlib
import unittest


class ComposeStaticTests(unittest.TestCase):
    def test_manifest_has_no_host_port_publication(self):
        text=pathlib.Path('compose.yaml').read_text()
        self.assertNotIn('ports:',text)
        self.assertIn('internal: true',text)
        self.assertEqual(text.count('read_only: true'),3)
        self.assertEqual(text.count('cap_drop: [ALL]'),3)
        self.assertEqual(text.count('no-new-privileges:true'),3)
        self.assertEqual(text.count('volumes: ["node-'),3)
        self.assertEqual(text.count('"--allow-container-bind"'),3)


if __name__=='__main__':unittest.main()
