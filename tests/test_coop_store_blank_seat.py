"""Player 4 with no controls at all (0.1.54): the store's 1 -> 2 migration and its one-time repair on load.

Schema 1 wrote the unused fourth seat with every binding list empty, and the first migration kept it, so a migrated file
had a Player 4 with no join key and no battle-menu key. Pure Lua under lupa; no native module, no game files."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:                                  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'package' / 'x64' / 'scripts' / 'IvaliceLegacy' / 'coop'
PATH = 'scripts/config/IvaliceLegacy/coop.json'


def to_py(v):
    if type(v).__name__ != '_LuaTable':
        return v
    items = list(v.items())
    keys = [k for k, _ in items]
    if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
        return [to_py(x) for _, x in sorted(items)]
    return {k: to_py(x) for k, x in items}


def to_lua(lua, v):
    if isinstance(v, dict):
        t = lua.table()
        for k, x in v.items():
            t[k] = to_lua(lua, x)
        return t
    if isinstance(v, list):
        t = lua.table()
        for i, x in enumerate(v, 1):
            t[i] = to_lua(lua, x)
        return t
    return v


@unittest.skipIf(LuaRuntime is None, 'lupa is not installed')
class BlankSeatTest(unittest.TestCase):
    def store(self, root: Path):
        lua = LuaRuntime(unpack_returned_tuples=True)
        load = lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        sd = load((COOP / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        codec = load((COOP / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(sd)
        cfg = lua.table()

        def load_json(path):
            try:
                return to_lua(lua, json.loads((root / path).read_text()))
            except Exception:
                return None

        def save_json(path, tbl):
            p = root / path
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(to_py(tbl), sort_keys=True))
            return True
        wrap = lua.eval('function(f) return function(...) return f(...) end end')
        cfg.loadJson, cfg.saveJson = wrap(load_json), wrap(save_json)
        logs = []
        opts = lua.table()
        opts.codec, opts.config, opts.path = codec, cfg, PATH
        opts.log = wrap(lambda text: logs.append(text))
        s = load((COOP / 'store.lua').read_text(encoding='utf-8'), 'store').create(opts)
        s.test_logs = logs
        return lua, s

    # ---- Player 4 with no controls at all (0.1.54): schema 1 wrote the unused fourth seat with 20 empty lists ----------------
    ACTIONS = ('TOGGLE_CONTROL', 'BATTLE_MENU', 'CONFIRM', 'CANCEL')

    def empty_seat(self, **extra):
        return {'character': 'AUTO', 'device': {'kind': 'NONE', 'slot': 0},
                'bindings': {a: [] for a in self.ACTIONS}, **extra}

    def default_binds(self, s, seat):
        out = {}
        for a in ('TOGGLE_CONTROL', 'BATTLE_MENU'):
            out[a] = to_py(s.defaults().seats[seat].bindings[a])
        return out

    def test_an_empty_fourth_seat_gets_its_defaults_in_the_migration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PATH).parent.mkdir(parents=True)
            doc = {'schema': 1, 'seats': [{'character': 'BASCH'}, {'character': 'BASCH'}, {'character': 'BALTHIER'},
                                          self.empty_seat()]}
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.migratedFrom), ('migrated', 1))
            want = self.default_binds(s, 4)
            self.assertTrue(want['TOGGLE_CONTROL'] and want['BATTLE_MENU'], 'the defaults give seat 4 a join key and a menu key')
            self.assertEqual({a: to_py(s.state.seats[4].bindings[a]) for a in want}, want)
            self.assertIsNone(s.seatRepaired, 'the migration did it, so no second report')
            out = json.loads((root / PATH).read_text())
            self.assertTrue(out['seats'][3]['bindings']['TOGGLE_CONTROL'])

    def test_a_schema_2_file_with_an_empty_seat_is_repaired_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            keep = to_py(s.state.seats[2].bindings.BATTLE_MENU)
            doc = json.loads((root / PATH).read_text())
            doc['seats'][3]['bindings'] = {a: [] for a in self.ACTIONS}      # Marco's file after the 1 -> 2 migration
            doc['seats'][3]['device'] = {'kind': 'NONE', 'slot': 0}
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            self.assertTrue(any('Player 4' in t or 'player 4' in t for t in s.test_logs), 'logged once')
            want = self.default_binds(s, 4)
            self.assertEqual({a: to_py(s.state.seats[4].bindings[a]) for a in want}, want)
            self.assertEqual(to_py(s.state.seats[2].bindings.BATTLE_MENU), keep, 'other seats are untouched')
            self.assertEqual(to_py(s.seatRepaired), [4])
            self.assertIn('Player 4 had no controls', s.status_text())
            self.assertTrue(json.loads((root / PATH).read_text())['seats'][3]['bindings']['BATTLE_MENU'], 'saved')
            lua, s = self.store(root)                                              # the next start finds nothing to repair
            s.load()
            self.assertIsNone(s.seatRepaired)
            self.assertEqual(s.fileStatus, 'ok')

    def test_a_seat_with_a_device_or_any_binding_is_not_repaired(self):
        for seat, join_keys in ((self.empty_seat(device={'kind': 'XINPUT', 'slot': 1}), 0),
                                ({**self.empty_seat(), 'bindings': {'BATTLE_MENU': [{'kind': 'KEY', 'code': 70}]}}, 1)):
            with tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lua, s = self.store(root)
                s.load()
                doc = json.loads((root / PATH).read_text())
                doc['seats'][3] = seat
                (root / PATH).write_text(json.dumps(doc))
                lua, s = self.store(root)
                s.load()
                self.assertIsNone(s.seatRepaired)
                self.assertEqual(len(to_py(s.state.seats[4].bindings.TOGGLE_CONTROL)), join_keys)


if __name__ == '__main__':
    unittest.main()
