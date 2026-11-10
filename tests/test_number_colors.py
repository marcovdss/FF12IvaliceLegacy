"""Battle number colours: the 0.2.0 diagnostic (palette dump + one deliberate recolour)."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]

SETUP = r'''
store = {}
memory = {u32 = setmetatable({}, {__index = function(_, a) return store[a] or 0 end,
                                  __newindex = function(_, a, v) store[a] = v end})}
function native()
  store = {}
  for i, v in ipairs(N.NATIVE) do store[N.TYPE_TABLE + 4 * (i - 1)] = v end
  for s = 0, 8 do store[N.STYLE_COLOUR + 4 * s] = N.NATIVE_STYLE_COLOUR[s] end
end
-- A loaded palette: count and pointer set; the colour dwords' contents are irrelevant to the module (they are grey).
function palette()
  local base = 0x7000000000
  store[N.PALETTE_COUNT] = 0x40
  store[N.PALETTE_PTR] = base & 0xFFFFFFFF; store[N.PALETTE_PTR + 4] = base >> 32
  store[base] = 0x7F7F7F
end
function styles() local t = {} for i = 0, 7 do t[#t + 1] = memory.u32[N.TYPE_TABLE + 4 * i] end return t end
lines = {}
function emit(line) lines[#lines + 1] = line end
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class NumberColourTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().N = self.lua.execute((ROOT / 'prototypes/number_colors.lua').read_text())
        self.lua.execute(SETUP)

    def test_only_mp_recovery_moves_to_the_foe_damage_style(self):
        self.lua.execute(r'''
          native(); palette()
          local s, text = N.plan(memory); assert(s, text)
          N.apply(memory, s)
          local t = styles()
          -- side A: HP damage, MP damage and HP heal stay native; MP heal -> foe damage style
          assert(t[1] == 2 and t[2] == 4 and t[3] == 1 and t[4] == 6, table.concat(t, ','))
          -- side B: only MP heal moves
          assert(t[5] == 6 and t[6] == 8 and t[7] == 5 and t[8] == 6, table.concat(t, ','))
          assert(text:find('foe-damage', 1, true))
          local again, why = N.plan(memory); assert(not again and why:find('already applied', 1, true))
        ''')

    def test_refuses_when_the_structure_is_not_native(self):
        self.lua.execute(r'''
          native()
          local s, why = N.plan(memory); assert(not s and why == 'palette not loaded yet')
          palette()
          store[N.TYPE_TABLE] = 9
          s, why = N.plan(memory); assert(not s and why:find('not the native', 1, true))
          native(); store[N.STYLE_COLOUR] = 7
          s, why = N.plan(memory); assert(not s and why:find('style colour table differs', 1, true))
        ''')

    def test_dump_emits_the_palette_style_and_type_rows(self):
        self.lua.execute(r'''
          -- With no palette loaded, dump refuses silently; the "skipped" line is register's, not dump's.
          assert(N.dump(memory, emit) == false and #lines == 0)
          lines = {}
          native(); palette()
          assert(N.dump(memory, emit) == true)
          assert(lines[1]:find('palette dump 00=', 1, true))
          local typeRow, count
          for _, l in ipairs(lines) do
            if l:find('type->style', 1, true) then typeRow = l end
          end
          assert(typeRow and typeRow:find('entries=64', 1, true) and typeRow:find('0=2', 1, true))
        ''')

    def test_register_dumps_once_then_applies_when_the_palette_arrives(self):
        self.lua.execute(r'''
          native(); queued = {}
          event = {executeAfterMs = function(ms, fn) queued[#queued + 1] = fn end,
                   registerEventSync = function() return true end, registerEventAsync = function() return true end}
          N.register(emit)
          assert(#queued == 1 and #lines == 1 and lines[1]:find('skipped', 1, true))
          palette()
          table.remove(queued, 1)()
          assert(styles()[4] == 6 and styles()[1] == 2)
          local applied, dumpRows = false, 0
          for _, l in ipairs(lines) do
            if l:find('applied', 1, true) then applied = true end
            if l:find('palette dump', 1, true) then dumpRows = dumpRows + 1 end
          end
          assert(applied and dumpRows > 0)
        ''')


if __name__ == '__main__':
    unittest.main()
