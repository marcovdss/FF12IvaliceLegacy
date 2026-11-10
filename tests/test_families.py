import json
import unittest
from pathlib import Path

from ivalice_legacy.enemy_ai_bank import Bank, Rule, Script
from ivalice_legacy.families import apply, area_entries

NEAREST, ENMITY, LOW_DEFENSE, WITHIN_40, HP_LOW, FLAG_OFF = 775, 529, 769, 1024, 3844, 14083
SELF = 4


def section():
    near = ((1, WITHIN_40, 0), (1, NEAREST, 0), (0, 0, 0))
    jump1 = Rule(16663, 100, 0)
    engaged = [Rule(150, 0, 16432, near), Rule(16417, 0, 0, near), Rule(16417, 0, 0)]
    groups = [1, 3] + [0] * 30
    return Bank([Script(list(groups), [jump1] + engaged), Script(list(groups), [jump1] + engaged)]).to_bytes()


def entry(*ops):
    return {'family': 'Test', 'script': 0, 'ops': list(ops)}


class Swap(unittest.TestCase):
    def test_only_the_selector_of_named_rules_changes(self):
        source = section()
        out = Bank.from_bytes(apply(source, [entry({'op': 'swap', 'rules': [1], 'before': [NEAREST, ENMITY], 'after': LOW_DEFENSE})]))
        was = Bank.from_bytes(source)
        rule = out.scripts[0].rules[1]
        self.assertEqual(rule.conditions, ((1, WITHIN_40, 0), (1, LOW_DEFENSE, 0), (0, 0, 0)))
        self.assertEqual((rule.action_object, rule.parameter, rule.flags), (150, 0, 16432))
        self.assertEqual(out.scripts[0].rules[2:], was.scripts[0].rules[2:])
        self.assertEqual(out.scripts[1].to_bytes(), was.scripts[1].to_bytes())

    def test_a_rule_without_the_selector_is_refused(self):
        with self.assertRaises(ValueError):
            apply(section(), [entry({'op': 'swap', 'rules': [3], 'before': [NEAREST], 'after': LOW_DEFENSE})])


class IndexValidation(unittest.TestCase):
    def test_rule_indices_cannot_be_negative_boolean_or_out_of_range(self):
        for kind in ('swap', 'prefer'):
            for index in (-3, True, 4, 1.5):
                with self.subTest(kind=kind, index=index):
                    op = {'op':kind, 'before':[NEAREST], 'after':LOW_DEFENSE}
                    op['rules' if kind == 'swap' else 'rule'] = [index] if kind == 'swap' else index
                    with self.assertRaises(ValueError):
                        apply(section(), [entry(op)])

    def test_reused_groups_must_be_jumpable_nonnegative_integers(self):
        for group in (-31, True, 12, 1.5):
            with self.subTest(group=group):
                with self.assertRaises(ValueError):
                    apply(section(), [entry({'op':'branch', 'group':1, 'reuse':group, 'when':[]})])

    def test_script_indices_cannot_address_the_end_from_a_negative_index(self):
        for index in (-1, True, 2, 0.5):
            with self.subTest(index=index):
                row = entry(); row['script'] = index
                with self.assertRaises(ValueError):
                    apply(section(), [row])


class Prefer(unittest.TestCase):
    def test_the_copy_goes_first_and_the_original_stays(self):
        out = Bank.from_bytes(apply(section(), [entry({'op': 'prefer', 'rule': 1, 'before': [NEAREST], 'after': LOW_DEFENSE})])).scripts[0]
        self.assertEqual(out.group_sizes[:2], [1, 4])
        self.assertEqual(out.rules[1].conditions[1], (1, LOW_DEFENSE, 0))
        self.assertEqual(out.rules[2].conditions[1], (1, NEAREST, 0))
        self.assertEqual(out.rules[1].action_object, 150)


class Branch(unittest.TestCase):
    OP = {'op': 'branch', 'group': 1, 'when': [[SELF, HP_LOW, 0], [SELF, FLAG_OFF, 25]],
          'steps': [[427, 0, 0x4800, [[1, NEAREST, 0]]], [16674, 25, 0x800, [[SELF, FLAG_OFF, 25]]], [16417, 3, 0x800, []]]}

    def test_jump_heads_the_group_and_steps_form_a_new_one(self):
        out = Bank.from_bytes(apply(section(), [entry(self.OP)])).scripts[0]
        self.assertEqual(out.group_sizes[:3], [1, 4, 4])
        self.assertEqual(out.rules[1].action_object, 16664)
        self.assertEqual(out.rules[1].conditions[:2], ((SELF, HP_LOW, 0), (SELF, FLAG_OFF, 25)))
        self.assertEqual(out.rules[2].action_object, 150)
        self.assertEqual([r.action_object for r in out.rules[5:]], [427, 16674, 16417, 16663])

    def test_a_reused_group_gets_only_the_jump(self):
        op = {'op': 'branch', 'group': 1, 'reuse': 0, 'when': [[SELF, HP_LOW, 0]]}
        out = Bank.from_bytes(apply(section(), [entry(op)])).scripts[0]
        self.assertEqual(out.group_sizes[:3], [1, 4, 0])
        self.assertEqual(out.rules[1].action_object, 16662)

    def test_no_jumpable_group_left(self):
        groups = [1] * 12 + [0] * 20
        full = Bank([Script(groups, [Rule(16417, 0, 0)] * 12)]).to_bytes()
        with self.assertRaises(ValueError):
            apply(full, [entry(dict(self.OP, group=0))])

    def test_output_round_trips(self):
        data = apply(section(), [entry(self.OP)])
        self.assertEqual(Bank.from_bytes(data).to_bytes(), data)


class Plan(unittest.TestCase):
    PLAN = json.loads((Path(__file__).resolve().parents[1] / 'patches/families.json').read_text(encoding='utf-8'))

    def test_a_script_cannot_be_named_twice(self):
        row = {'area': 'x', 'script': 0, 'ops': []}
        with self.assertRaises(ValueError):
            area_entries({'families': [{'family': 'F', 'scripts': [row, row]}]})

    def test_shipped_plan_names_each_script_once(self):
        self.assertTrue(area_entries(self.PLAN))

    def test_protected_scripts_stay_out(self):
        rows = {(r['area'], r['script']) for f in self.PLAN['families'] for r in f['scripts']}
        self.assertFalse({r for r in rows if r[0].startswith('tri_') or r[0] == 'grm_g'}, 'Trial and hunt areas')
        self.assertFalse(rows & {('mic_a', 0), ('mic_b', 0)}, 'Battery Mimic machinery')

    def test_offensive_steps_aim_at_a_foe(self):
        """Ram, Gnaw, Lunge, Leech 2, Choco-Comet, Screwtail, fangs, breaths, spells and Attack must never pick an ally."""
        offensive = {150, 427, 428, 429, 415, 426, 406, 340, 377, 369, 367, 296, 370, 371, 385, 60}
        for family in self.PLAN['families']:
            for row in family['scripts']:
                for op in row['ops']:
                    for action, _, _, conditions in op.get('steps', []):
                        if action in offensive:
                            self.assertEqual(conditions[0][0], 1, (family['family'], row['area'], row['script'], action))

    def test_support_steps_never_pick_a_foe(self):
        for family in self.PLAN['families']:
            for row in family['scripts']:
                for op in row['ops']:
                    for action, _, _, conditions in op.get('steps', []):
                        if action in (51, 52, 47, 304, 306, 349, 351, 346, 98, 16578):
                            self.assertNotEqual(conditions[0][0], 1, (family['family'], row['area'], row['script'], action))

    def test_every_one_shot_step_cannot_loop(self):
        """Each action or move is guarded by a flag the next step enables, so a rescan of the group cannot repeat it."""
        for family in self.PLAN['families']:
            for row in family['scripts']:
                for op in row['ops']:
                    steps = op.get('steps', [])
                    for at, (action, parameter, _, conditions) in enumerate(steps):
                        guards = [c[2] for c in conditions if c[0] == SELF and c[1] == FLAG_OFF]
                        if action in (16417, 16674) or not guards and op.get('chance', 100) != 100:
                            continue
                        if action == 16578:   # the augment guards itself
                            continue
                        self.assertTrue(guards, (family['family'], row['area'], row['script'], at))
                        self.assertEqual(steps[at + 1][:2], [16674, guards[0]], (family['family'], row['area'], row['script'], at))


if __name__ == '__main__':
    unittest.main()
