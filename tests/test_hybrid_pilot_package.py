from pathlib import Path
import copy
import json
import struct
import tempfile
import unittest
import zipfile
from ivalice_legacy.hybrid_pilot import (
    PILOT_SITES, canonical, hook_templates, package_files, prepare_bundle, sha, write_package)


def board(entries):
    ids = [65535]*576
    for x, y, value in entries:
        ids[y*24+x] = value
    return b'licd'+struct.pack('<HH576H', 24, 24, *ids)


class HybridPilotPackageTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.native = Path(self.temp.name)/'native'
        self.native.mkdir()
        (self.native/'loader.lua').write_text('return {install=function() end}\n')
        (self.native/'hooks.lua').write_text('return {}\n')
        self.sources = {0: board([(0, 0, 31)]), 4: board([(0, 0, 31), (1, 0, 40)])}
        self.licenses = b'synthetic license table'
        self.recipe = {'schemaVersion': 1, 'pair': [0, 4], 'width': 24, 'height': 24,
                       'sourceBoards': [{'job': job, 'sha256': sha(data)} for job, data in self.sources.items()],
                       'licenseTableSha256': sha(self.licenses),
                       'cells': [{'x': i, 'y': 0, 'licenseId': value,
                                  'branch': 'fixture', 'purpose': 'packaging control'}
                                 for i, value in enumerate((31, 40))]}
        self.image = bytes(range(64))
        self.exports = {name: i for i, name in enumerate(
            ('HBX_size', 'HBX_init', 'HBX_core', 'HBX_config', 'HBX_route', 'HB_put'))}
        self.image_manifest = {'imageSize': len(self.image), 'sha256': sha(self.image), 'exports': self.exports}
        self.hooks = [{'site': i, 'name': f'fixture{i}', 'source': 0x200000+i*16,
                       'expected': [1]*5, 'template': [2]*8, 'fixups': []}
                      for i in sorted(PILOT_SITES)]
        self.gate = {'completeHookSet': True, 'sites': sorted(PILOT_SITES), 'adapterSize': 4096,
                     'adapterImageSha256': sha(self.image), 'layoutSha256': sha(self.sources[4]),
                     'hookPlanSha256': sha(canonical(self.hooks)),
                     'loaderSha256': sha((self.native/'loader.lua').read_bytes()),
                     'hooksSha256': sha((self.native/'hooks.lua').read_bytes())}

    def bundle(self, gate=None, hooks=None):
        return prepare_bundle(self.recipe, self.sources, self.licenses,
                              self.image, self.image_manifest,
                              self.hooks if hooks is None else hooks,
                              self.gate if gate is None else gate, self.native)

    def test_package_shape_and_manifest_exclude_protected_files(self):
        files, manifest = package_files(self.bundle(), '0.1.0', self.native)
        self.assertEqual(set(files), {'x64/scripts/IvaliceLegacyPaladin.lua',
                                     'x64/scripts/IvaliceLegacyHybrids/loader.lua',
                                     'x64/scripts/IvaliceLegacyHybrids/hooks.lua',
                                     'x64/scripts/IvaliceLegacyHybrids/bundle.lua',
                                     'x64/scripts/IvaliceLegacyHybrids/README.md',
                                     'x64/scripts/IvaliceLegacyHybrids/CHANGELOG.md',
                                     'x64/scripts/IvaliceLegacyHybrids/manifest.json'})
        for path, digest in manifest['files'].items():
            self.assertEqual(sha(files[path]), digest)
        self.assertEqual(json.loads(files['x64/scripts/IvaliceLegacyHybrids/manifest.json']), manifest)
        self.assertNotIn(b'IvaliceLegacySaves', b''.join(files.values()))
        self.assertTrue(files['x64/scripts/IvaliceLegacyHybrids/bundle.lua'].startswith(b'return '))

    def test_gate_refuses_incomplete_or_stale_inputs(self):
        for field, value in (('completeHookSet', False), ('adapterImageSha256', '0'*64),
                             ('layoutSha256', '0'*64), ('loaderSha256', '0'*64),
                             ('hookPlanSha256', '0'*64), ('adapterSize', True)):
            with self.subTest(field=field):
                gate = copy.deepcopy(self.gate)
                gate[field] = value
                with self.assertRaises(ValueError):
                    self.bundle(gate=gate)
        with self.assertRaises(ValueError):
            self.bundle(hooks=self.hooks[:-1])

    def test_deterministic_archive_and_immutable_version(self):
        files, manifest = package_files(self.bundle(), '0.1.0', self.native)
        out = Path(self.temp.name)/'out'
        archive, first = write_package(files, manifest, out)
        original = archive.read_bytes()
        self.assertEqual(write_package(files, manifest, out)[1], first)
        self.assertEqual(archive.read_bytes(), original)
        with zipfile.ZipFile(archive) as zipped:
            self.assertEqual(zipped.namelist(), sorted(files))
            for name in files:
                self.assertEqual(zipped.read(name), files[name])
        files['x64/scripts/IvaliceLegacyHybrids/README.md'] += b'changed'
        with self.assertRaisesRegex(ValueError, 'increment version'):
            write_package(files, manifest, out)
        self.assertEqual(archive.read_bytes(), original)

    def test_package_writer_rejects_path_traversal(self):
        for name in ('../escape', '/absolute', 'C:/outside', 'nested\\outside'):
            with self.subTest(name=name), self.assertRaises(ValueError):
                write_package({name: b'bad'}, {'version': '0.1.0'}, Path(self.temp.name)/'out')

    def test_actual_pilot_template_contract(self):
        hooks = hook_templates()
        self.assertEqual({hook['site'] for hook in hooks}, PILOT_SITES)
        self.assertEqual(len(hooks), len(PILOT_SITES))
        for hook in hooks:
            self.assertGreaterEqual(len(hook['expected']), 5)
            for fixup in hook['fixups']:
                self.assertIn(fixup['kind'], {'continuation', 'reject', 'state', 'route', 'fallback', 'fallbackCall'})
                self.assertLessEqual(fixup['offset']+fixup['size'], len(hook['template']))
                if fixup['kind'] == 'fallback':
                    self.assertLess(fixup['targetOffset'], len(hook['template']))
