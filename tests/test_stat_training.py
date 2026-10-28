"""Contracts for the job stat trainings (patches/stat_training.json -> Stat Lores config)."""
import json
from pathlib import Path
import unittest

from ivalice_legacy.stat_training import AUGMENT_BITS, NATIVE, render, steps

ROOT = Path(__file__).resolve().parents[1]
PATCH = json.loads((ROOT/'patches/stat_training.json').read_text(encoding='utf-8'))

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


class StatTrainingTest(unittest.TestCase):
    def test_twelve_jobs_two_unique_steps_each(self):
        s = steps(PATCH)
        self.assertEqual(len(s), 24)
        self.assertEqual(len({x['licenseId'] for x in s}), 24)
        # No two jobs share a stat mix.
        mixes = [frozenset(x['stats']) | frozenset(y['stats']) for x, y in zip(s[::2], s[1::2])]
        self.assertEqual(len(set(mixes)), 12)

    def test_bad_recipes_are_refused(self):
        import copy
        for mutate in [lambda p: p['trainings'].pop(),
                       lambda p: p['trainings'][0]['steps'][0].update(augment='hpLore99'),
                       lambda p: p['trainings'][0]['steps'][0]['stats'].update(luck=1),
                       lambda p: p['trainings'][1]['steps'][0].update(augment=p['trainings'][0]['steps'][0]['augment'])]:
            p = copy.deepcopy(PATCH); mutate(p)
            with self.assertRaises(ValueError):
                steps(p)

    @unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
    def test_config_keeps_native_rows_and_adds_training_rows(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        config = lua.execute(render(PATCH).decode())
        ident = lua.eval("setmetatable({}, {__index=function(_,k) return k end})")
        rows = config(ident, ident)
        got = [(rows[i][1], rows[i][2], rows[i][3]) for i in range(1, len(rows) + 1)]
        trained = {x['augment']: x['stats'] for x in steps(PATCH)}
        expected = []
        for name, _, kind, value in NATIVE:
            expected += list((name, k, v) for k, v in trained[name].items()) if name in trained else [(name, kind, value)]
        self.assertEqual(got, expected)
        self.assertEqual(len(AUGMENT_BITS), 44)


if __name__ == '__main__':
    unittest.main()
