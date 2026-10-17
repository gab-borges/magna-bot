import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from magna.storage import data_file, load_json, save_json


class StorageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.dir = Path(tmp.name)
        self.path = str(self.dir / 'data.json')

    def test_round_trip(self):
        save_json(self.path, {'a': 1})

        self.assertEqual(load_json(self.path, {}), {'a': 1})

    def test_missing_file_returns_the_default(self):
        self.assertEqual(load_json(self.path, {'empty': True}), {'empty': True})

    def test_failed_write_keeps_the_previous_contents(self):
        save_json(self.path, {'birthdays': 'intact'})

        def crash_mid_write(data, f, **kwargs):
            f.write('{"birth')
            raise OSError("disk full")

        with patch('magna.storage.json.dump', side_effect=crash_mid_write):
            with self.assertRaises(OSError):
                save_json(self.path, {'birthdays': 'new'})

        self.assertEqual(load_json(self.path, {}), {'birthdays': 'intact'})
        self.assertEqual(os.listdir(self.dir), ['data.json'])

    def test_data_files_live_in_data_dir(self):
        target = self.dir / 'nested'
        with patch.dict(os.environ, {'DATA_DIR': str(target)}):
            path = data_file('birthdays.json')

        self.assertEqual(path, str(target / 'birthdays.json'))
        self.assertTrue(target.is_dir())

    def test_data_files_default_to_a_data_directory(self):
        env = {key: value for key, value in os.environ.items() if key != 'DATA_DIR'}
        with patch.dict(os.environ, env, clear=True), patch('magna.storage.os.makedirs') as makedirs:
            path = data_file('voice_active')

        self.assertEqual(path, os.path.join('data', 'voice_active'))
        makedirs.assert_called_once_with('data', exist_ok=True)

    def test_writes_plain_json(self):
        save_json(self.path, {'x': [1, 2]})

        with open(self.path) as f:
            self.assertEqual(json.load(f), {'x': [1, 2]})


if __name__ == "__main__":
    unittest.main()
