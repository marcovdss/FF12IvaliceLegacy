"""The co-op Mod Menu page built with the real Lua modules under lupa, on the enforcing Mod Menu double
(coop_menu_double.py). Shared by the page tests; nothing here touches the game, Vortex or a save."""
from __future__ import annotations

from pathlib import Path

from coop_menu_double import MENU_DOUBLE

try:
    from lupa import LuaRuntime
except ImportError:  # the tests skip themselves
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'

HARNESS = MENU_DOUBLE + r"""
local S, C, V, B, ST, D, F, page, vision = ...
local calls = {}
local mm = newMenu(calls)
local P = page.create({modmenu = mm, store = ST, bridge = B, codec = C, views = V, flows = F, dialogs = D,
                       statusview = S, vision = vision})
assert(P.start())
return P, calls, mm
"""


class PageEnv:
    """Everything a page test needs. ``config`` is the loader's config table (loadJson/saveJson): ``saved`` holds the
    files it wrote; ``seed`` seeds coop.json before the store loads (a Lua table or None).
    ``files`` (a dict of path -> text, optional) is the filesystem the store reads and writes plain text through (the Safety
    rows read and rewrite other mods' settings files there); ``self.files`` is the live Lua table."""

    def __init__(self, seed=None, files=None):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.load = self.lua.eval('function(src, name) return assert(load(src, "@" .. name, "t", _ENV))() end')
        schema = self.load((LUA / 'schema_data.lua').read_text(encoding='utf-8'), 'schema_data')
        self.C = self.load((LUA / 'codec.lua').read_text(encoding='utf-8'), 'codec').new(schema)
        self.S = self.module('statusview')
        self.views = self.module('views').new(self.C)
        self.bridge = self.module('bridge').create(
            self.lua.table_from({'codec': self.C, 'after': self.lua.eval('function() end')}))
        self.saved = self.lua.table()
        if seed is not None:
            self.saved['scripts/config/IvaliceLegacy/coop.json'] = seed
        self.config = self.lua.eval('''function(saved) return {
            loadJson = function(p) return saved[p] end,
            saveJson = function(p, d) saved[p] = d; return true end} end''')(self.saved)
        opts = {'codec': self.C, 'config': self.config}
        self.files = self.lua.table()
        for path, text in (files or {}).items():
            self.files[path] = text
        if files is not None:
            opts['fs'] = self.lua.eval('''function(files) return {
                error = {NOT_FOUND = 1},
                readFile = function(p) if files[p] == nil then return nil, nil, 1 end return files[p] end,
                writeFile = function(p, d) files[p] = d; return true end,
                exists = function(p) return files[p] ~= nil end} end''')(self.files)
        self.store = self.module('store').create(self.lua.table_from(opts))
        self.store.load()
        self.notes = self.lua.table()          # every dialog text the page asked for (the dialog service is absent)
        self.dialogs = self.module('dialogs').create(self.lua.table_from(
            {'log': self.lua.eval('function(notes) return function(t) notes[#notes + 1] = t end end')(self.notes)}))
        self.flows = self.module('flows').create(self.lua.table_from(
            {'store': self.store, 'bridge': self.bridge, 'codec': self.C, 'views': self.views,
             'dialogs': self.dialogs, 'seed': 1}))
        self.page_mod = self.module('page')
        self.vision_doc = self.lua.eval('''{noMinimap = {state = true}, noOverheadHpBars = {state = true},
            noOverheadStatusEffects = {state = false}, noCombatLog = {state = false}}''')
        holder = self.lua.eval('function(doc) local h = {doc = doc}; return h, function() return h.doc end end')
        self.holder, vision = holder(self.vision_doc)
        run = self.lua.eval('function(src, ...) return assert(load(src, "@harness", "t", _ENV))(...) end')
        self.P, self.calls, self.menu = run(HARNESS, self.S, self.C, self.views, self.bridge, self.store, self.dialogs,
                                            self.flows, self.page_mod, vision)

    def lt(self, obj):
        """A Python dict/list as a Lua table, recursively (lupa hands dicts to Lua as foreign objects)."""
        if isinstance(obj, dict):
            return self.lua.table_from({k: self.lt(v) for k, v in obj.items()})
        if isinstance(obj, (list, tuple)):
            return self.lua.table_from([self.lt(v) for v in obj])
        return obj

    def last_note(self):
        n = len(self.notes)
        return self.notes[n].removeprefix('dialog: ') if n else None

    def module(self, name):
        return self.load((LUA / f'{name}.lua').read_text(encoding='utf-8'), name)

    # ---- the registered tree -----------------------------------------------------------------------------------------
    def table(self):
        return self.calls.tables[1]

    def tree(self, rows=None):
        """The registered rows as a nested list: ``(id, type)`` or ``(id, type, [children])``."""
        rows = self.table().schema if rows is None else rows
        out = []
        for k in range(1, len(rows) + 1):
            r = rows[k]
            out.append((r.id, r.type, self.tree(r.sub)) if r.sub is not None else (r.id, r.type))
        return out

    def row(self, wanted, rows=None):
        rows = self.table().schema if rows is None else rows
        for k in range(1, len(rows) + 1):
            r = rows[k]
            if r.id == wanted:
                return r
            if r.sub is not None:
                found = self.row(wanted, r.sub)
                if found is not None:
                    return found
        return None

    def sub_ids(self, wanted):
        return [r[0] for r in self.tree(self.row(wanted).sub)]

    def options(self, wanted):
        row = self.row(wanted)
        return [row.options[k] for k in range(1, len(row.options) + 1)]

    def go_ready(self, **native):
        """Make the bridge answer ``ready`` and give the page a native status (a Lua table of the given fields)."""
        fields = {'state': 6, 'stateName': 'READY', 'reason': 17, 'reasonDetail': 0, 'bootFlags': 0, 'coopSwitch': 0,
                  'appliedSubsystems': 0xFF, 'acceptedGeneration': 1, 'appliedGeneration': 1, 'hudReport': 0}
        fields.update(native)
        self.bridge.status.state = 'ready'
        self.bridge.is_ready = self.lua.eval('function() return true end')
        self.P.native = self.lua.table_from(fields)
