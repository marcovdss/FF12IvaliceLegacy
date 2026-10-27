"""Preservation, guarded edits and whole-game source round trips."""
import copy
import hashlib
import json
import os
from pathlib import Path
import struct
import unittest

from ivalice_legacy.enemy_ai_bank import Bank, Rule, Script, edit_bank, enemy_ai_bank
from ivalice_legacy.formats import ard_section
from ivalice_legacy.patches import enemy_ai
from ivalice_legacy.vbf import VBF

ROOT = Path(__file__).resolve().parents[1]


def digest(raw):
    return hashlib.sha256(raw).hexdigest()


def groups(count):
    return [count] + [0] * 31


def container(bank, links=(0, 0, 0, 0)):
    header = bytearray(b'FF12AR03' + bytes(40))
    struct.pack_into('<I', header, 8 + 3 * 4, 48)
    struct.pack_into('<I', header, 8 + 4 * 4, 48 + len(bank))
    unit = bytearray(88)
    struct.pack_into('<4H', unit, 80, *links)
    return bytes(header) + bank + b'st2e' + struct.pack('<III', 1, 88, 16) + bytes(unit) + b'opaque-tail'


def patch_for(raw, edits, result):
    return {'file': 'example.ard', 'section': 3, 'sourceFileSha256': digest(raw),
            'sourceSectionSha256': digest(ard_section(raw, 3)),
            'resultSectionSha256': digest(result), 'scripts': edits}


class BankTest(unittest.TestCase):
    def setUp(self):
        self.bank = Bank([Script(groups(1), [Rule(150)], b'opaque-script-tail'),
                          Script(groups(1), [Rule(16417, 1)])], 123, b'prefix', b'bank-tail')
        self.source = self.bank.to_bytes()

    def edit(self, operations, after):
        return [{'index': 0, 'groupSizesBefore': groups(1), 'groupSizesAfter': after,
                 'operations': operations}]

    def test_roundtrip_preserves_unknown_header_prefix_and_tails(self):
        self.assertEqual(Bank.from_bytes(self.source).to_bytes(), self.source)
        empty = Bank([], 100, b'gap', b'tail').to_bytes()
        self.assertEqual(Bank.from_bytes(empty).to_bytes(), empty)

    def test_insert_and_delete_update_groups_and_offsets(self):
        new_rule = Rule(16417, 2).to_bytes()
        edits = self.edit([{'op': 'insert', 'index': 0, 'beforeHex': Rule(150).to_bytes().hex(),
                            'afterHex': new_rule.hex()}], groups(2))
        result = edit_bank(self.source, edits)
        after = Bank.from_bytes(result)
        self.assertEqual([r.to_bytes() for r in after.scripts[0].rules], [new_rule, Rule(150).to_bytes()])
        self.assertEqual(after.scripts[1].to_bytes(), self.bank.scripts[1].to_bytes())
        self.assertEqual(struct.unpack_from('<i', result, 8)[0],
                         struct.unpack_from('<i', self.source, 8)[0] + 24)
        deletes = [{'index': 0, 'groupSizesBefore': groups(2), 'groupSizesAfter': groups(1),
                    'operations': [{'op': 'delete', 'index': 0, 'beforeHex': new_rule.hex()}]}]
        self.assertEqual(edit_bank(result, deletes), self.source)

    def test_append_and_replace(self):
        result = edit_bank(self.source, self.edit([
            {'op': 'insert', 'index': 1, 'beforeHex': '', 'afterHex': Rule(16417, 3).to_bytes().hex()}], groups(2)))
        self.assertEqual(len(Bank.from_bytes(result).scripts[0].rules), 2)
        replaced = edit_bank(self.source, self.edit([
            {'op': 'replace', 'index': 0, 'beforeHex': Rule(150).to_bytes().hex(),
             'afterHex': Rule(16417, 4).to_bytes().hex()}], groups(1)))
        self.assertEqual(Bank.from_bytes(replaced).scripts[0].rules[0].parameter, 4)

    def test_rejects_before_bytes_groups_duplicates_and_bad_rules(self):
        op = {'op': 'replace', 'index': 0, 'beforeHex': '00' * 24,
              'afterHex': Rule(150).to_bytes().hex()}
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op], groups(1)))
        op['beforeHex'] = Rule(150).to_bytes().hex()
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op], groups(2)))
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op, op], groups(1)))
        with self.assertRaises(ValueError): Rule(150, flags=65535).to_bytes()
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([dict(op, index=-1)], groups(1)))

    def test_invalid_group_and_unresolved_slot_jumps_rejected(self):
        op = {'op': 'replace', 'index': 0, 'beforeHex': Rule(150).to_bytes().hex(),
              'afterHex': Rule(16673, 100).to_bytes().hex()}
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op], groups(1)))
        op['afterHex'] = Rule(16659, 100).to_bytes().hex()
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op], groups(1)))
        result = edit_bank(self.source, self.edit([op], groups(1)), [(0, 1, 1, 1)])
        self.assertEqual(Bank.from_bytes(result).scripts[0].rules[0].action_object, 16659)
        with self.assertRaises(ValueError): edit_bank(self.source, self.edit([op], groups(1)), [(0, 2, 1, 1)])

    def test_rejects_malformed_directory_and_count(self):
        for raw in (b'', b'\x01\x00\x64\x00' + bytes(8),
                    struct.pack('<HH3i', 2, 100, 16, 16, 16)):
            with self.assertRaises(ValueError): Bank.from_bytes(raw)
        bad = bytearray(self.source); bad[16 + len(self.bank.prefix)] = 255
        with self.assertRaises(ValueError): Bank.from_bytes(bad)

    def test_pinned_overlay_only_and_wrong_hashes_rejected(self):
        raw = container(self.source)
        patch = patch_for(raw, [], self.source)
        self.assertEqual(enemy_ai_bank(lambda _: raw, patch), {'example.ard.dir/section_003.bin': self.source})
        for key in ('sourceFileSha256', 'sourceSectionSha256', 'resultSectionSha256'):
            bad = dict(patch, **{key: '0' * 64})
            with self.assertRaises(ValueError): enemy_ai_bank(lambda _: raw, bad)

    def test_flag_usage_distinguishes_namespaces_and_unresolved_writes(self):
        bank = Bank([Script(groups(4), [
            Rule(16674, 25, 0, ((4, 14083, 25), (0, 0, 0), (0, 0, 0))),
            Rule(16725, 1, 0, ((36877, 14081, 99), (0, 0, 0), (0, 0, 0))),
            Rule(16713, 2, 0, ((12288, 13824, 0), (0, 0, 0), (0, 0, 0))),
            Rule(16725, 1)])])
        usage = bank.flag_usage()['scripts'][0]
        self.assertEqual(usage['aiFlagsRead'], [25]); self.assertEqual(usage['aiFlagsWritten'], [25])
        self.assertEqual(usage['numericFlagsRead'], ['battleLogic:13', 'global:0'])
        self.assertEqual(usage['numericFlagWriteTargets'], ['battleLogic:13', 'global:0'])
        self.assertEqual(usage['unresolvedNumericWrites'], [3])


@unittest.skipUnless(os.environ.get('IVALICE_LEGACY_GAME'), 'set IVALICE_LEGACY_GAME for source checks')
class GameBankTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.game = Path(os.environ['IVALICE_LEGACY_GAME'])
        cls.vbf = VBF(cls.game / 'FFXII_TZA.vbf')

    def test_all_clean_area_banks_roundtrip(self):
        areas = [n for n in self.vbf.names if n.endswith('.ard') and '/in/' in n]
        self.assertEqual(len(areas), 166)
        for name in areas:
            with self.subTest(area=name):
                source = ard_section(self.vbf.read(name), 3)
                self.assertEqual(Bank.from_bytes(source).to_bytes(), source)

    def test_rampager_regenerated_by_complete_writer(self):
        old = json.loads((ROOT / 'patches/rampager_pilot.json').read_text())
        raw = self.vbf.read(old['file']); source = ard_section(raw, 3)
        bank = Bank.from_bytes(source); operations = []
        for edit in old['conditions']:
            before = bytes.fromhex(edit['ruleBeforeHex']); after = bytearray(before)
            struct.pack_into('<H', after, 8 + 6 * edit['conditionSlot'], edit['after'])
            operations.append({'op': 'replace', 'index': edit['row'],
                               'beforeHex': before.hex(), 'afterHex': bytes(after).hex()})
        patch = {k: old[k] for k in ('file', 'section', 'sourceFileSha256',
                                     'sourceSectionSha256', 'resultSectionSha256')}
        patch['scripts'] = [{'index': 9, 'groupSizesBefore': bank.scripts[9].group_sizes,
                             'groupSizesAfter': bank.scripts[9].group_sizes, 'operations': operations}]
        result = next(iter(enemy_ai_bank(lambda _: raw, patch).values()))
        self.assertEqual(result, next(iter(enemy_ai(lambda _: raw, old).values())))
        self.assertEqual(digest(result), '81a35c6548248ad2d8d7141481dbbb018f2e2376fbace828b0f1d97e37ba70d0')
        self.assertEqual(Bank.from_bytes(result).to_bytes(), result)
        for i in range(10):
            if i not in (3,) and struct.unpack_from('<I', raw, 8 + i * 4)[0]:
                self.assertNotIn(old['file'] + f'.dir/section_{i:03}.bin', enemy_ai_bank(lambda _: raw, patch))

    def test_deployed_rampager_bank_roundtrip(self):
        old = json.loads((ROOT / 'patches/rampager_pilot.json').read_text())
        path = self.game / 'mods/deploy/ff12data' / (old['file'] + '.dir/section_003.bin')
        if not path.is_file():self.skipTest('Rampager pilot is not deployed')
        source = path.read_bytes()
        self.assertEqual(digest(source), old['resultSectionSha256'])
        self.assertEqual(Bank.from_bytes(source).to_bytes(), source)
