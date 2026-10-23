"""Independent exact-diff and unchanged-section checks for the optional pilot."""
from pathlib import Path
import copy
import json
import os
import struct
import tempfile
import unittest
from ivalice_legacy.formats import ard_section, ai_rule_offset
from ivalice_legacy.patches import enemy_ai, APPLIERS
from ivalice_legacy.vbf import VBF
ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT / 'patches/rampager_pilot.json').read_text())

class FormatGuardsTest(unittest.TestCase):
    def test_rejects_bad_containers_and_rules(self):
        for raw in (b'', b'FF12AR03' + bytes(40)):
            with self.assertRaises(ValueError): ard_section(raw, 3)
        for raw in (b'', struct.pack('<HH2i', 1, 0, 12, 100)):
            with self.assertRaises(ValueError): ai_rule_offset(raw, 0, 0)
    def test_pilot_is_optional(self):
        self.assertEqual(set(APPLIERS), {'opening_stock', 'ability_chests', 'kings'})

@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source checks')
class RampagerSourceTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.raw = VBF(Path(os.environ['IVALICE_LEGACY_GAME']) / 'FFXII_TZA.vbf').read(PATCH['file'])
        # Independent directory slicing, deliberately not the production AR03 parser.
        cls.directory = struct.unpack_from('<10I', cls.raw, 8)
        cls.bounds = sorted(set(o for o in cls.directory if o) | {len(cls.raw)})
        cls.parts = {o: cls.raw[o:e] for o, e in zip(cls.bounds, cls.bounds[1:])}
        cls.original = cls.parts[cls.directory[3]]
    def test_noop_roundtrip_and_full_section(self):
        rebuilt = self.raw[:self.bounds[0]] + b''.join(self.parts[o] for o in self.bounds[:-1])
        self.assertEqual(rebuilt, self.raw)
        self.assertEqual(ard_section(self.raw, 3), self.original)
        self.assertEqual(len(self.original), 16176)
        output = bytearray(self.original)
        count = struct.unpack_from('<H', self.original)[0]
        offsets = struct.unpack_from(f'<{count + 1}i', self.original, 4)
        for start in offsets[:-1]:
            for row in range(sum(self.original[start:start + 32])):
                at = start + 32 + 24 * row
                struct.pack_into('<12H', output, at, *struct.unpack_from('<12H', self.original, at))
        self.assertEqual(bytes(output), self.original)
    def test_exact_conditions_and_untouched_sections(self):
        files = enemy_ai(lambda _: self.raw, PATCH)
        self.assertEqual(list(files), [PATCH['file'] + '.dir/section_003.bin'])
        result = next(iter(files.values()))
        expected = bytearray(self.original)
        for at, before, after in ((9204, 0x0F04, 0x0F03), (9468, 0x0E03, 0x0E02), (9972, 0x0E03, 0x0E02)):
            self.assertEqual(struct.unpack_from('<H', expected, at)[0], before)
            struct.pack_into('<H', expected, at, after)
        self.assertEqual(result, bytes(expected))
        self.assertEqual([i for i, (a, b) in enumerate(zip(self.original, result)) if a != b], [9204, 9468, 9972])
        rebuilt = bytearray(self.raw)
        start = self.directory[3]
        rebuilt[start:start + len(result)] = result
        for i, o in enumerate(self.directory):
            if o and i != 3: self.assertEqual(rebuilt[o:o + len(self.parts[o])], self.parts[o])
        self.assertEqual(rebuilt[:48], self.raw[:48])
    def test_rejects_changed_sources_rules_and_duplicates(self):
        altered = bytearray(self.raw)
        altered[-1] ^= 1
        with self.assertRaises(ValueError): enemy_ai(lambda _: bytes(altered), PATCH)
        for key, value in (('row', 18), ('conditionSlot', 3), ('before', 0), ('ruleBeforeHex', '00' * 24)):
            patch = copy.deepcopy(PATCH)
            patch['conditions'][0][key] = value
            with self.assertRaises(ValueError): enemy_ai(lambda _: self.raw, patch)
        patch = copy.deepcopy(PATCH)
        patch['conditions'].append(copy.deepcopy(patch['conditions'][0]))
        with self.assertRaises(ValueError): enemy_ai(lambda _: self.raw, patch)
    def test_pilot_package_is_single_section_and_reproducible(self):
        from ivalice_legacy.__main__ import build_rampager_pilot
        with tempfile.TemporaryDirectory() as out:
            archive, manifest = build_rampager_pilot(os.environ['IVALICE_LEGACY_GAME'], out)
            self.assertEqual(len(manifest['files']), 1)
            first = archive.read_bytes()
            build_rampager_pilot(os.environ['IVALICE_LEGACY_GAME'], out)
            self.assertEqual(first, archive.read_bytes())
