"""Synthetic rules exercise isolation, not proposed combat balance."""
import copy
import unittest

from ivalice_legacy.clan_progression import ClassIdentity as Class, Gear, Ledger, Rule, resolve


class ClanProgressionTests(unittest.TestCase):
    def setUp(self):
        self.paladin = Class.from_jobs(4, 0)
        self.other = Class.from_jobs(4, 9)
        self.sword = Gear('weapon', 'weapon', 4097)
        self.rules = (
            Rule('paladin.guard', self.paladin, 'weapon', frozenset({4097}), 2, frozenset({'hunt'})),
            Rule('other.focus', self.other, 'weapon', frozenset({4097})),
            Rule('knight.guard', Class.from_jobs(4), 'weapon', frozenset({4097})),
        )
        self.document = {'schema': 1, 'characters': {
            '0': {'0:4': ['paladin.guard', 'unknown.retained'], '4': ['knight.guard']},
            '1': {'4:9': ['other.focus']}}}
        self.ledger = Ledger.from_document(self.document)

    def result(self, actor=0, identity=None, equipment=None, ledger=None):
        return resolve(self.ledger if ledger is None else ledger, actor,
                       self.paladin if identity is None else identity,
                       [self.sword] if equipment is None else equipment, self.rules)

    def test_same_item_distinct_actor_and_class(self):
        self.assertEqual(self.result(), {'paladin.guard'})
        self.assertEqual(self.result(1, self.other), {'other.focus'})
        self.assertFalse(self.result(1))
        self.assertFalse(self.result(0, self.other))

    def test_all_66_pairs_are_order_independent(self):
        identities = set()
        for a in range(12):
            for b in range(a + 1, 12):
                self.assertEqual(Class.from_jobs(a, b), Class.from_jobs(b, a))
                identities.add(Class.from_jobs(a, b))
        self.assertEqual(len(identities), 66)
        self.assertEqual(Class.from_jobs(4, 255), Class.from_jobs(4))

    def test_base_does_not_inherit_into_hybrid(self):
        ledger = Ledger.from_document({'schema': 1, 'characters': {'0': {'4': ['knight.guard']}}})
        self.assertFalse(self.result(ledger=ledger))
        self.assertEqual(self.result(identity=Class.from_jobs(4), ledger=ledger), {'knight.guard'})

    def test_swaps_remove_and_restore_without_mutation(self):
        before = copy.deepcopy(self.document)
        for _ in range(10):
            self.assertFalse(self.result(equipment=[Gear('weapon', 'weapon', 4098)]))
            self.assertFalse(self.result(identity=self.other))
            self.assertEqual(self.result(), {'paladin.guard'})
        self.assertEqual(self.document, before)
        self.assertEqual(self.ledger.to_document(), before)

    def test_both_accessories_and_nonshield_offhand_excluded(self):
        for slot, kind in [('accessory', 'accessory'), ('accessory2', 'accessory'),
                           ('offhand', 'weapon'), ('weapon', 'accessory')]:
            self.assertFalse(self.result(equipment=[Gear(slot, kind, 4097)]))
        shield_rule = Rule('shield.guard', self.paladin, 'offhand', frozenset({4097}))
        ledger = Ledger(((0, self.paladin, frozenset({'shield.guard'})),))
        self.assertEqual(resolve(ledger, 0, self.paladin,
                                 [Gear('offhand', 'shield', 4097)], [shield_rule]), {'shield.guard'})
        self.assertFalse(resolve(ledger, 0, self.paladin,
                                 [Gear('offhand', 'weapon', 4097)], [shield_rule]))

    def test_each_armor_slot_is_eligible(self):
        for slot in ('head', 'body'):
            rule = Rule('armor.guard', self.paladin, slot, frozenset({4097}))
            ledger = Ledger(((0, self.paladin, frozenset({'armor.guard'})),))
            self.assertEqual(resolve(ledger, 0, self.paladin,
                                     [Gear(slot, 'armor', 4097)], [rule]), {'armor.guard'})

    def test_nonparty_and_invalid_gear_confer_nothing(self):
        for actor in (-1, 6, 39, True, '0'):
            self.assertFalse(self.result(actor))
        for item_id in (-1, 65535, True):
            self.assertFalse(self.result(equipment=[Gear('weapon', 'weapon', item_id)]))

    def test_missing_document_clears_previous_save(self):
        self.assertTrue(self.result())
        self.assertFalse(self.result(ledger=Ledger.from_document(None)))
        self.assertEqual(Ledger.from_document(None).to_document(), {'schema': 1, 'characters': {}})

    def test_unlocks_do_not_purchase_or_revoke(self):
        rule = self.rules[0]
        self.assertFalse(rule.available(1, {'hunt'}))
        self.assertFalse(rule.available(2, set()))
        self.assertTrue(rule.available(2, {'hunt', 'other'}))
        self.assertFalse(self.result(ledger=Ledger()))
        self.assertEqual(self.result(), {'paladin.guard'})

    def test_roundtrip_unknown_ids_preserved_without_effect(self):
        self.assertEqual(Ledger.from_document(self.ledger.to_document()), self.ledger)
        self.assertEqual(self.result(), {'paladin.guard'})

    def test_bad_documents_rejected(self):
        for document in ({}, {'schema': True, 'characters': {}},
                         {'schema': 2, 'characters': {}},
                         {'schema': 1, 'characters': {'6': {}}},
                         {'schema': 1, 'characters': {'0': {'4:0': ['x']}}},
                         {'schema': 1, 'characters': {'0': {'0:4': ['x', 'x']}}},
                         {'schema': 1, 'characters': {'0': {'0:4': []}}}):
            with self.subTest(document=document), self.assertRaises(ValueError):
                Ledger.from_document(document)

    def test_bad_jobs_and_duplicate_inputs_rejected(self):
        for jobs in ((4, 4), (True, None), (12, None), (4, '255')):
            with self.subTest(jobs=jobs), self.assertRaises(ValueError):
                Class.from_jobs(*jobs)
        with self.assertRaises(ValueError):
            resolve(self.ledger, 0, self.paladin, [self.sword] * 2, self.rules)
        with self.assertRaises(ValueError):
            resolve(self.ledger, 0, self.paladin, [self.sword], self.rules * 2)

    def test_mutable_entries_cannot_transfer_actor_ownership(self):
        entry = [0, self.paladin, frozenset({'paladin.guard'})]
        with self.assertRaises(ValueError):
            Ledger((entry,))
        for entry in ((), (0, self.paladin), (0, self.paladin, frozenset({'x'}), 1)):
            with self.assertRaises(ValueError):
                Ledger((entry,))


if __name__ == '__main__':
    unittest.main()
