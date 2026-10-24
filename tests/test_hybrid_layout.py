import copy
import hashlib
import struct
import unittest
from ivalice_legacy.hybrid_layout import validate_recipe


def board(entries):
    ids = [65535]*576
    for x, y, value in entries:
        ids[y*24+x] = value
    return b'licd'+struct.pack('<HH576H', 24, 24, *ids)


class HybridLayoutTest(unittest.TestCase):
    def setUp(self):
        self.sources = {0: board([(0, 0, 31), (1, 0, 18), (2, 0, 40)]),
                        4: board([(0, 0, 31), (0, 1, 41)])}
        self.licenses = b'own synthetic license table fixture'
        # Integrated shape: ordinary root component {31,41}, gate18, island40.
        entries = [(5, 5, 31), (6, 5, 41), (7, 5, 18), (8, 5, 40)]
        self.recipe = {'schemaVersion': 1, 'pair': [0, 4], 'width': 24, 'height': 24,
                       'sourceBoards': [{'job': job, 'sha256': hashlib.sha256(data).hexdigest()}
                                        for job, data in self.sources.items()],
                       'licenseTableSha256': hashlib.sha256(self.licenses).hexdigest(),
                       'cells': [{'x': x, 'y': y, 'licenseId': value,
                                  'branch': 'fixture', 'purpose': 'topology control'}
                                 for x, y, value in entries]}

    def test_integrated_layout_preserves_gate_contract(self):
        result = validate_recipe(self.recipe, self.sources, self.licenses)
        self.assertEqual(result, board([(5, 5, 31), (6, 5, 41), (7, 5, 18), (8, 5, 40)]))

    def test_rejects_gate_bypass(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['cells'][3].update(x=5, y=6)  # Joins formerly gated40 directly to31.
        with self.assertRaisesRegex(ValueError, 'gate topology'):
            validate_recipe(recipe, self.sources, self.licenses)

    def test_rejects_split_ordinary_component(self):
        recipe = copy.deepcopy(self.recipe)
        recipe['cells'][0].update(x=1, y=1)
        with self.assertRaisesRegex(ValueError, 'component disconnected'):
            validate_recipe(recipe, self.sources, self.licenses)

    def test_rejects_corrupt_identity_and_nodes(self):
        changes = [lambda r: r.update(pair=[4, 0]), lambda r: r.update(cells=[]),
                   lambda r: r.update(licenseTableSha256='0'*64),
                   lambda r: r['cells'][0].update(x=24),
                   lambda r: r['cells'][0].update(x=True),
                   lambda r: r['cells'][0].update(licenseId=360),
                   lambda r: r['cells'][0].update(branch=''),
                   lambda r: r['cells'].append(copy.deepcopy(r['cells'][0])),
                   lambda r: r['cells'].pop()]
        for change in changes:
            with self.subTest(change=change):
                recipe = copy.deepcopy(self.recipe)
                change(recipe)
                with self.assertRaises(ValueError):
                    validate_recipe(recipe, self.sources, self.licenses)
