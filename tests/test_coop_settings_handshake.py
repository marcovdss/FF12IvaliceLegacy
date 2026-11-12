"""The settings file handshake: the documents the Lua store really writes, read by the native module's own start-up parser.

The store (Lua, run under lupa) is the only writer of coop.json. At start-up the module reads one value from it
(module.enabledAtStart) with its own parser, and refuses documents it cannot trust. This feeds the store's real output
(new file, seeded Off next to Together, a repaired file, a file with unknown nested keys, a file with a very deep unknown
subtree) through that parser in the host-test DLL, so a change to either side that breaks the other fails here.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

try:
    from lupa import LuaRuntime
except ImportError:                                  # pragma: no cover
    LuaRuntime = None

from coop_native_support import REQUIRE_NATIVE
import test_coop_boot as boot
from test_coop_patch_engine import NATIVE

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


def depth(v) -> int:
    if isinstance(v, dict):
        return 1 + max((depth(x) for x in v.values()), default=0)
    if isinstance(v, list):
        return 1 + max((depth(x) for x in v), default=0)
    return 0


@unittest.skipUnless(LuaRuntime is not None and (NATIVE or REQUIRE_NATIVE), 'requires lupa and the pinned MSYS2 g++')
class SettingsHandshake(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dll = boot.fresh_test_dll()

    def parse(self, raw: bytes):
        out = boot.Switch()
        self.dll.ILCT_BootParseSwitch(raw, len(raw), 1, boot.ctypes.byref(out))
        return out.outcome, out.detail, out.defaulted

    def store(self, root: Path, together=False, schema=None):
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
        opts = lua.table()
        opts.codec, opts.config, opts.path = codec, cfg, PATH
        if together:
            opts.together = lua.eval('function() return true end')
        store = load((COOP / 'store.lua').read_text(encoding='utf-8'), 'store').create(opts)
        return lua, store

    def test_a_new_file_reads_as_on(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua, s = self.store(Path(tmp))
            s.load()
            self.assertEqual(self.parse((Path(tmp) / PATH).read_bytes()), (boot.SETTINGS_ON, 0, False))

    def test_the_seed_next_to_together_reads_as_off(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua, s = self.store(Path(tmp), together=True)
            s.load()
            self.assertEqual(self.parse((Path(tmp) / PATH).read_bytes()), (boot.SETTINGS_OFF, 0, False))

    def test_a_change_of_the_module_switch_is_what_native_reads(self):
        with tempfile.TemporaryDirectory() as tmp:
            lua, s = self.store(Path(tmp))
            s.load()
            lua.globals().S = s
            lua.execute('S.change(function(st) st.module.enabledAtStart = false end, nil)')
            self.assertEqual(self.parse((Path(tmp) / PATH).read_bytes())[0], boot.SETTINGS_OFF)
            lua.execute('S.change(function(st) st.module.enabledAtStart = true end, nil)')
            self.assertEqual(self.parse((Path(tmp) / PATH).read_bytes())[0], boot.SETTINGS_ON)

    def test_a_repaired_file_is_valid_for_native(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            doc = json.loads((root / PATH).read_text())
            doc['keyboardSeat'] = 'x'
            doc['module']['enabledAtStart'] = 'yes'              # wrong type: native refuses this document
            raw = json.dumps(doc).encode()
            self.assertEqual(self.parse(raw), (boot.SETTINGS_INVALID, boot.INVALID_VALUE, False))
            (root / PATH).write_bytes(raw)
            lua, s = self.store(root)
            s.load()
            self.assertEqual(s.fileStatus, 'repaired')
            self.assertEqual(self.parse((root / PATH).read_bytes()), (boot.SETTINGS_ON, 0, False))

    def test_unknown_nested_keys_are_kept_and_stay_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            doc = json.loads((root / PATH).read_text())
            doc['top'] = {'a': [1, {'b': {'enabledAtStart': False}}]}
            doc['seats'][1]['later'] = {'x': 1}
            doc['module']['extra'] = [1, 2]
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            lua.globals().S = s
            lua.execute('S.change(function(st) st.fullRoster = false end, nil)')
            out = (root / PATH).read_bytes()
            kept = json.loads(out)
            self.assertEqual(kept['top'], doc['top'])
            self.assertEqual(kept['seats'][1]['later'], {'x': 1})
            self.assertEqual(self.parse(out), (boot.SETTINGS_ON, 0, False))

    def test_a_very_deep_unknown_subtree_is_capped_below_native_limit(self):
        deep = cur = {}
        for _ in range(60):
            cur['n'] = {}
            cur = cur['n']
        # native refuses a document nested this deep ...
        probe = {'schema': 1, 'module': {'enabledAtStart': True}, 'deep': deep}
        self.assertEqual(self.parse(json.dumps(probe).encode())[:2], (boot.SETTINGS_INVALID, boot.INVALID_SYNTAX))
        # ... so the store never writes one back
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            doc = json.loads((root / PATH).read_text())
            doc['deep'] = deep
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            lua.globals().S = s
            lua.execute('S.change(function(st) st.fullRoster = false end, nil)')
            out = (root / PATH).read_bytes()
            self.assertLess(depth(json.loads(out)), 30)
            self.assertEqual(self.parse(out), (boot.SETTINGS_ON, 0, False))


if __name__ == '__main__':
    unittest.main()
