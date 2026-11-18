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
            lua.execute('S.change(function(st) st.keepRunning = false end, nil)')
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
            lua.execute('S.change(function(st) st.keepRunning = false end, nil)')
            out = (root / PATH).read_bytes()
            self.assertLess(depth(json.loads(out)), 30)
            self.assertEqual(self.parse(out), (boot.SETTINGS_ON, 0, False))

    def test_mouse_buttons_as_keys_fall_back_for_the_kill_switch_and_the_toggles_only(self):
        """The store mirrors the validator: the kill switch refuses MOUSE 1 and the mouse buttons' virtual-key codes as
        KEY (1, 2, 4-6), a seat toggle refuses the KEY codes; each refused list falls back to its default (the kill
        switch: F8) and nothing else in the file is reset. MOUSE 2, VK_CANCEL (3) and other actions are untouched."""
        bad_kill = [[{'kind': 'KEY', 'code': c}] for c in (1, 2, 4, 5, 6)] + [[{'kind': 'MOUSE', 'code': 1}]]
        for kill in bad_kill + [[{'kind': 'KEY', 'code': 0x79}, 7]]:   # a non-table entry: refused, never indexed
            with self.subTest(kill=kill), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lua, s = self.store(root)
                s.load()
                doc = json.loads((root / PATH).read_text())
                default_kill = doc['killSwitch']
                default_toggle = doc['seats'][2]['bindings']['TOGGLE_CONTROL']
                self.assertEqual(default_kill, [{'kind': 'KEY', 'code': 0x77}], 'the default kill switch is F8')
                doc['killSwitch'] = kill
                doc['seats'][2]['bindings']['TOGGLE_CONTROL'] = [{'kind': 'KEY', 'code': kill[0]['code']}]  # as KEY
                doc['seats'][1]['bindings']['CONFIRM'] = [{'kind': 'KEY', 'code': 0x01}]  # not a toggle: kept
                doc['keepRunning'] = not doc['keepRunning']
                (root / PATH).write_text(json.dumps(doc))
                lua, s = self.store(root)
                s.load()
                out = json.loads((root / PATH).read_text())
                self.assertEqual(s.fileStatus, 'repaired')
                self.assertEqual(out['killSwitch'], default_kill, 'the kill switch falls back to F8')
                code = kill[0]['code']
                want = default_toggle if code in (1, 2, 4, 5, 6) else [{'kind': 'KEY', 'code': code}]
                self.assertEqual(out['seats'][2]['bindings']['TOGGLE_CONTROL'], want)
                self.assertEqual(out['seats'][1]['bindings']['CONFIRM'], [{'kind': 'KEY', 'code': 0x01}])
                self.assertEqual(out['keepRunning'], doc['keepRunning'], 'only the refused fields are reset')
                self.assertEqual(self.parse((root / PATH).read_bytes())[0], boot.SETTINGS_ON)
        for kill, toggle in (([{'kind': 'MOUSE', 'code': 2}], [{'kind': 'KEY', 'code': 3}]),
                             ([{'kind': 'KEY', 'code': 3}], [{'kind': 'MOUSE', 'code': 1}])):
            with self.subTest(kept=(kill, toggle)), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lua, s = self.store(root)
                s.load()
                doc = json.loads((root / PATH).read_text())
                doc['killSwitch'] = kill
                doc['seats'][2]['bindings']['TOGGLE_CONTROL'] = toggle
                (root / PATH).write_text(json.dumps(doc))
                lua, s = self.store(root)
                s.load()
                out = json.loads((root / PATH).read_text())
                self.assertEqual((s.fileStatus, out['killSwitch'], out['seats'][2]['bindings']['TOGGLE_CONTROL']),
                                 ('ok', kill, toggle))

    def test_mouse_bindings_name_the_left_or_right_button_only_as_native_checks(self):
        """A MOUSE binding with any code but 1 or 2 is refused by native's validator (validate.cpp check_binding, on
        the settings push); the store refuses the same one (that list falls back to its default) instead of keeping a
        binding native would reject. MOUSE 1 and 2 stay (instrument)."""
        for code in (0, 3, 4, 5, 255):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lua, s = self.store(root)
                s.load()
                doc = json.loads((root / PATH).read_text())
                default_confirm = doc['seats'][1]['bindings']['CONFIRM']
                doc['seats'][1]['bindings']['CONFIRM'] = [{'kind': 'MOUSE', 'code': code}]
                (root / PATH).write_text(json.dumps(doc))
                lua, s = self.store(root)
                s.load()
                out = json.loads((root / PATH).read_text())
                self.assertEqual((s.fileStatus, out['seats'][1]['bindings']['CONFIRM']), ('repaired', default_confirm))
                self.assertEqual(self.parse((root / PATH).read_bytes())[0], boot.SETTINGS_ON)
        for code in (1, 2):
            with self.subTest(kept=code), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                lua, s = self.store(root)
                s.load()
                doc = json.loads((root / PATH).read_text())
                doc['seats'][1]['bindings']['CONFIRM'] = [{'kind': 'MOUSE', 'code': code}]
                (root / PATH).write_text(json.dumps(doc))
                lua, s = self.store(root)
                s.load()
                out = json.loads((root / PATH).read_text())
                self.assertEqual((s.fileStatus, out['seats'][1]['bindings']['CONFIRM']),
                                 ('ok', [{'kind': 'MOUSE', 'code': code}]))

    # ---- schema 1 -> 2 (0.1.53): "Co-op On" means every subsystem works ------------------------------------------------
    SEED_V1 = {  # what the first test session's seed left in coop.json: every subsystem disarmed, developer and test draw on
        'schema': 1, 'module': {'enabledAtStart': True}, 'fullRoster': False, 'logLevel': 'NORMAL',
        'diagnostics': {'arm': {k: False for k in ('inputIsolation', 'movement', 'party', 'camera', 'firewallMenus',
                                                    'wait', 'render', 'targetsCards')},
                        'developer': True, 'hudTestDraw': True, 'later': {'kept': 1}},
        'keepRunning': True,
    }

    def test_the_seed_file_migrates_to_every_subsystem_armed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / PATH).parent.mkdir(parents=True)
            raw = json.dumps(self.SEED_V1).encode()
            (root / PATH).write_bytes(raw)
            self.assertEqual(self.parse(raw), (boot.SETTINGS_ON, 0, False), 'native still reads the old file')
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.migratedFrom), ('migrated', 1))
            out = json.loads((root / PATH).read_text())
            self.assertEqual(out['schema'], 2)
            self.assertNotIn('arm', out['diagnostics'], 'the old arm switches are gone')
            self.assertNotIn('fullRoster', out)
            self.assertEqual(set(out['diagnostics']['disarm'].values()), {False}, 'no developer disarm')
            self.assertEqual(out['diagnostics']['later'], {'kept': 1}, 'unknown keys survive the migration')
            self.assertIs(out['diagnostics']['developer'], False, 'the seed developer switch is a lab leftover: reset')
            self.assertIs(out['diagnostics']['hudTestDraw'], False, "the seed HUD test pattern is a lab leftover: reset")
            self.assertEqual(out['play'], {'sharedCamera': True, 'guestInteractions': True, 'townView': 'PLAYERS',
                                           'waitPause': 'ALL', 'guestPause': True})
            self.assertEqual({k: out['battleHud']['log'][k] for k in ('compactLines', 'expandedLines', 'fadeSeconds')},
                             {'compactLines': 5, 'expandedLines': 20, 'fadeSeconds': 6})
            self.assertEqual((out['battleHud']['elements']['partyTimers'], out['battleHud']['elements']['guestCursors']),
                             (False, True), 'the Standard preset: no status timers, guest cursors on')
            # (the coop.json.v1 sidecar needs the filesystem plugin, which this harness does not give the store)
            self.assertEqual(self.parse((root / PATH).read_bytes()), (boot.SETTINGS_ON, 0, False),
                             'native reads the migrated file')
            # The record the store pushes asks native for no disarm at all, developer and test draw off.
            lua.globals().S = s
            sd = lua.eval('function(src) return assert(load(src, "@schema_data", "t", _ENV))() end')(
                (COOP / 'schema_data.lua').read_text(encoding='utf-8'))
            codec = lua.eval('function(src, sd) return assert(load(src, "@codec", "t", _ENV))().new(sd) end')(
                (COOP / 'codec.lua').read_text(encoding='utf-8'), sd)
            decode = lua.eval("""function(C) local r = C.decode('ilc_settings', S.encode(S.state, 1))
                                 return r.subsystems_disarm, r.developer, r.shared_camera, r.hud_test_draw end""")
            self.assertEqual(tuple(decode(codec)), (0, 0, 1, 0))
            self.assertEqual((s.state.diagnostics.developer, s.state.diagnostics.hudTestDraw), (False, False))

    def test_the_migration_resets_developer_and_test_draw_once(self):
        """Schema 1 -> 2 sets diagnostics.developer and hudTestDraw to false, whatever the old file held and whether or not
        it held a diagnostics block with them. From schema 2 on they are the player's own switches and survive a start."""
        for diagnostics in ({'developer': True, 'hudTestDraw': True}, {'developer': True}, {'hudTestDraw': True},
                            {'developer': False, 'hudTestDraw': False}, {}):
            with self.subTest(diagnostics=diagnostics), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / PATH).parent.mkdir(parents=True)
                (root / PATH).write_text(json.dumps({'schema': 1, 'diagnostics': diagnostics}))
                lua, s = self.store(root)
                s.load()
                self.assertEqual((s.fileStatus, s.migratedFrom), ('migrated', 1))
                out = json.loads((root / PATH).read_text())
                self.assertEqual(out['schema'], 2)
                self.assertEqual((s.state.diagnostics.developer, s.state.diagnostics.hudTestDraw), (False, False))
                self.assertIs(out['diagnostics'].get('developer', False), False)
                self.assertIs(out['diagnostics'].get('hudTestDraw', False), False)
        with tempfile.TemporaryDirectory() as tmp:                      # a schema-2 file keeps what the player set
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            lua.globals().S = s
            lua.execute('S.change(function(st) st.diagnostics.developer = true; st.diagnostics.hudTestDraw = true end, nil)')
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.state.diagnostics.developer, s.state.diagnostics.hudTestDraw),
                             ('ok', True, True), 'no second reset')

    def test_a_developer_disarm_round_trips_and_a_newer_file_is_read_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            lua.globals().S = s
            lua.execute("S.change(function(st) st.diagnostics.disarm.render = true; st.play.townView = 'PARTY' end, nil)")
            out = json.loads((root / PATH).read_text())
            self.assertIs(out['diagnostics']['disarm']['render'], True)
            self.assertEqual(out['play']['townView'], 'PARTY')
            lua, s = self.store(root)
            s.load()
            self.assertEqual(s.fileStatus, 'ok')
            self.assertIs(s.state.diagnostics.disarm.render, True)
            doc = json.loads((root / PATH).read_text())
            doc['schema'] = 3
            (root / PATH).write_text(json.dumps(doc))
            self.assertEqual(self.parse((root / PATH).read_bytes())[:2], (boot.SETTINGS_INVALID, boot.INVALID_NEWER))
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.readOnly), ('readonly', True))

    def test_bad_play_and_log_values_fall_back_one_by_one(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lua, s = self.store(root)
            s.load()
            doc = json.loads((root / PATH).read_text())
            doc['play'] = {'sharedCamera': 'no', 'guestInteractions': False, 'townView': 'EVERYONE', 'waitPause': 'ANY',
                           'guestPause': 0}
            doc['battleHud']['log'].update({'compactLines': 9, 'expandedLines': 12, 'fadeSeconds': 2})
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            self.assertEqual((s.fileStatus, s.repairs), ('repaired', 5))
            p, l = s.state.play, s.state.battleHud.log
            self.assertEqual((p.sharedCamera, p.guestInteractions, p.townView, p.waitPause, p.guestPause),
                             (True, False, 'PLAYERS', 'ANY', True))
            self.assertEqual((l.compactLines, l.expandedLines, l.fadeSeconds), (5, 12, 6))
            # compact above expanded is refused by the store as by native
            doc['battleHud']['log'].update({'compactLines': 8, 'expandedLines': 6, 'fadeSeconds': 15})
            (root / PATH).write_text(json.dumps(doc))
            lua, s = self.store(root)
            s.load()
            l = s.state.battleHud.log
            self.assertEqual((l.compactLines, l.expandedLines, l.fadeSeconds), (5, 6, 15))


if __name__ == '__main__':
    unittest.main()
