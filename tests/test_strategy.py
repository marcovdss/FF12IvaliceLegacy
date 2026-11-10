import unittest

from ivalice_legacy.enemy_ai_bank import Rule, Script
from ivalice_legacy.strategy import enrich, undo


class StrategyTests(unittest.TestCase):
    def original(self):
        return Script([1] + [0] * 31, [Rule(150, conditions=((1, 775, 0), (0, 0, 0), (0, 0, 0)))], b'\0\0')

    def test_changes_to_trigger_during_healing_do_not_repeat_or_trap_branch(self):
        """A healed ally stops satisfying the entry predicate before the flag write."""
        for rescan in (False, True):
            for windup in (None, 26):
                s, _ = enrich(self.original(), 0, 1, 4, [(5, 3844, 0)], [(5, 3844, 0)], 25, windup)
                groups, offset = [], 0
                for size in s.group_sizes:
                    groups.append(s.rules[offset:offset + size]); offset += size
                flags, hp_low, group, pc, previous, casts, pauses = set(), True, 0, 0, None, 0, 0
                reached_original = False
                for _ in range(80):
                    if pc >= len(groups[group]):
                        pc = 0
                    r = groups[group][pc]
                    eligible = all((c != 14083 or p not in flags) and (c != 3844 or hp_low)
                                   for t, c, p in r.conditions)
                    if r.flags & 0x800 and previous == (group, pc):
                        eligible = False
                    if not eligible:
                        pc += 1; continue
                    previous = (group, pc)
                    if r.action_object == 4:
                        casts += 1; hp_low = False
                    elif r.action_object == 16674:
                        flags.add(r.parameter)
                    elif r.action_object == 16417:
                        pauses += 1
                    elif 16662 <= r.action_object <= 16673:
                        group, pc = r.action_object - 16662, 0; continue
                    elif r.action_object == 150:
                        reached_original = True; break
                    pc = 0 if rescan else pc + 1
                self.assertTrue(reached_original, (rescan, windup, flags, group, pc))
                self.assertEqual(casts, 1)
                self.assertIn(25, flags)
                self.assertLessEqual(pauses, 2)

    def test_all_native_groups_rules_and_trailing_bytes_restore_exactly(self):
        original = self.original()
        one, first = enrich(original, 0, 2, 422, [(4, 3844, 0)], [(1, 775, 0)], 25, 26)
        two, second = enrich(one, 0, 1, 60, [(1, 14338, 0)], [(1, 14338, 0)], 27)
        self.assertEqual(undo(undo(two, second), first).to_bytes(), original.to_bytes())
        self.assertEqual(original.to_bytes(), self.original().to_bytes())

    def test_bad_groups_flags_and_extra_condition_never_mutate_input(self):
        original = self.original(); raw = original.to_bytes()
        for kwargs in [dict(target=0), dict(target=12), dict(action=543), dict(flag=True),
                       dict(windup_flag=25), dict(trigger=[(1, 775, 0)] * 3)]:
            args = dict(entry=0, target=1, action=60, trigger=[], target_conditions=[], flag=25)
            args.update(kwargs)
            with self.assertRaises(ValueError):
                enrich(original, **args)
            self.assertEqual(original.to_bytes(), raw)

    def test_undo_rejects_stale_entry(self):
        s, recipe = enrich(self.original(), 0, 1, 60, [], [(1, 14338, 0)], 25)
        s.rules[0] = Rule(150)
        with self.assertRaises(ValueError):
            undo(s, recipe)

    def test_native_phase_transition_keeps_priority_over_new_branch(self):
        native = self.original()
        transition = Rule(16664, 100, conditions=((4, 3844, 0), (0, 0, 0), (0, 0, 0)))
        native.rules.insert(0, transition); native.group_sizes[0] += 1
        native.rules.append(Rule(150)); native.group_sizes[2] = 1
        enriched, recipe = enrich(native, 0, 1, 422, [], [(1, 775, 0)], 25, entry_index=1)
        self.assertEqual(enriched.rules[0], transition)
        self.assertEqual(recipe['entryIndex'], 1)
        self.assertEqual(undo(enriched, recipe).to_bytes(), native.to_bytes())


if __name__ == '__main__':
    unittest.main()
