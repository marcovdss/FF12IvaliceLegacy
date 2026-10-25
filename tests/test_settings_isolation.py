import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
import zipfile

from ivalice_legacy.settings_isolation import CONFIG_PATHS, add_settings, metadata, read_settings


class SettingsIsolationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'input'
        self.raw = {}
        for index, relative in enumerate(CONFIG_PATHS):
            raw = (b'return function() return {battleSpeed = 3} end\r\n'
                   if relative.endswith('.lua') else
                   ('\ufeff' + json.dumps({'chosen': index, 'enabled': True,
                                           'unknownFutureOption': [1, 2]}) + '\r\n').encode())
            path = self.root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
            self.raw[relative] = raw

    def test_copy_is_lossless_including_unknown_fields_and_bom(self):
        self.assertEqual(read_settings(self.root), self.raw)
        payload = {'x64/scripts/init/IvaliceLegacySaves.lua': b'own script'}
        before = dict(payload)
        result = add_settings(payload, read_settings(self.root))
        self.assertEqual(payload, before)
        self.assertEqual(set(result), set(payload) | set(CONFIG_PATHS))
        self.assertEqual({p: (self.root / p).read_bytes() for p in CONFIG_PATHS}, self.raw)
        self.assertEqual(metadata(self.raw)['initialFileSha256'],
                         {p: hashlib.sha256(b).hexdigest() for p, b in self.raw.items()})

    def test_missing_or_invalid_input_rejected(self):
        path = self.root / CONFIG_PATHS[0]
        for bad in (b'', b'\xff', b'{bad}', b'[]', b'null'):
            path.write_bytes(bad)
            with self.subTest(raw=bad), self.assertRaises(ValueError):
                read_settings(self.root)
        path.unlink()
        with self.assertRaises(FileNotFoundError):
            read_settings(self.root)

    def test_collision_and_wrong_file_set_rejected_without_mutation(self):
        payload = {CONFIG_PATHS[0]: b'existing owner'}
        with self.assertRaises(ValueError):
            add_settings(payload, self.raw)
        self.assertEqual(payload, {CONFIG_PATHS[0]: b'existing owner'})
        for settings in ({}, {**self.raw, '../unexpected': b'x'},
                         {**self.raw, CONFIG_PATHS[0]: ''}):
            with self.assertRaises(ValueError):
                add_settings({}, settings)

    def test_output_must_not_destroy_source(self):
        with self.assertRaises(ValueError):
            read_settings(self.root, protected_output=self.root)
        with self.assertRaises(ValueError):
            read_settings(self.root, protected_output=self.root.parent)

    def test_later_build_carries_existing_legacy_edits(self):
        path = self.root / CONFIG_PATHS[0]
        edited = b'{"chosen":42,"enabled":false}\n'
        path.write_bytes(edited)
        second = read_settings(self.root)
        self.assertEqual(second[CONFIG_PATHS[0]], edited)
        self.assertNotEqual(metadata(second), metadata(self.raw))
        self.assertEqual({p: second[p] for p in CONFIG_PATHS[1:]},
                         {p: self.raw[p] for p in CONFIG_PATHS[1:]})

    @unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set game path for full package integration')
    def test_personalized_main_has_same_core_and_four_exact_configs(self):
        from ivalice_legacy.__main__ import build
        out = Path(self.temp.name) / 'output'
        _, core = build(os.environ['IVALICE_LEGACY_GAME'], out)
        archive, personalized = build(os.environ['IVALICE_LEGACY_GAME'], out, settings_from=self.root)
        self.assertEqual(len(core['files']), 9)
        self.assertEqual(len(personalized['files']), 13)
        self.assertEqual({p: personalized['files'][p] for p in core['files']}, core['files'])
        self.assertEqual(personalized['settingsIsolation'], metadata(self.raw))
        with zipfile.ZipFile(archive) as packed:
            self.assertEqual(set(packed.namelist()), set(core['files']) | set(CONFIG_PATHS))
            for p, raw in self.raw.items():
                self.assertEqual(packed.read(p), raw)
        initial_zip = archive.read_bytes()
        build(os.environ['IVALICE_LEGACY_GAME'], out, settings_from=self.root)
        self.assertEqual(archive.read_bytes(), initial_zip)


if __name__ == '__main__':
    unittest.main()
