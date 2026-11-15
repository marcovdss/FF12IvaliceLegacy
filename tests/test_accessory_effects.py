"""Accessory effects (prototypes/accessory_effects.lua) through the prototype Proficiency runtime on the synthetic
battle (tests/runtime_world.py): each stage fires from the hook that dispatches it, and its control does not."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import runtime_world  # noqa: E402

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def prototype(name):
    return (ROOT / 'prototypes' / f'{name}.lua').read_text(encoding='utf-8').replace(chr(13) + chr(10), chr(10))


EXTRA = r'''
-- second accessory slot (sa_saveMem) and the accessory field the runtime reads
SECOND = {}
memory.getSymbol = function(name) if name == 'sa_saveMem' then return 0x50000 end end
memory.u16 = setmetatable({}, {__index = function(_, a) return SECOND[(a - 0x50000) // 4] or 0 end})
function wear(u, main, second) u.accessory = main; SECOND[u.identifier] = second; return u end
bpack.section14[60] = {category=1, mpOrMistCost=10, elements=flags({fire=true})}    -- a Fire spell
bpack.section14[61] = {category=1, mpOrMistCost=10, elements=flags({ice=true})}     -- an Ice spell
bpack.section13[7] = {category=1, chargeTime=30, offhandCategory=0, elements=flags({fire=true})}  -- a Fire sword
bpack.section13[8] = {category=1, chargeTime=30, offhandCategory=0, elements=flags()}            -- a plain sword
function said(pattern) for _, line in ipairs(printed) do if line:find(pattern, 1, true) then return true end end return false end
function forget() printed = {} end
-- the adapters call the hooks through R.wrap, which runs the accessories after Proficiency
local W = {}
function hook(n, f, caster, target, before) W[n] = W[n] or R.wrap(n, function() end); W[n](f, caster, target, nil, classes, helpers) end
function hit(caster, foe, t)
  t = t or {}
  local f = F({initial=foe.address, critical=t.critical, target={removedHp=t.damage or 100, identifier=9, work=works[foe.address], status=t.status},
               caster={status=t.casterStatus or {}}})
  hook(165, f, caster, foe)
  return f
end
function onHit(caster, foe, ids)   -- Forge 182 after the hit: statuses newly landed
  local f = F({initial=foe.address, target={identifier=9, work=works[foe.address]}})
  local wrapped = R.wrap(182, function(formula) for _, id in ipairs(ids) do formula.target.addedStatusEffects[id] = 1 end end)
  wrapped(f, caster, foe, nil, classes, helpers)
  return f
end
function spell(caster, foe, action, damage)
  local f = F({action=action, initial=foe.address, target={removedHp=damage or 1000, identifier=9, work=works[foe.address]}})
  R.hook222(f, caster, foe)
  R.wrap(170, function() end)(f, caster, foe, nil, classes, helpers)
  return f
end
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class AccessoryEffectsTest(unittest.TestCase):
    def run_lua(self, code):
        lua = LuaRuntime(unpack_returned_tuples=True)
        runtime_world.world(lua, loader=prototype, overrides={'accessory_effects': prototype('accessory_effects')})
        lua.execute(EXTRA)
        return lua.execute(code)

    def test_module_loads_and_copies_no_hybrid_stage(self):
        loaded, copies = self.run_lua('return R.Accessory ~= nil, #R.Accessory.copies(H.LINES)')
        self.assertTrue(loaded)
        self.assertEqual(copies, 0)

    def test_gillie_boots_slow_once_per_oil(self):
        first, again = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local gillie = wear(party(1, {weapon=0x1008}), 4474)
          onHit(party(2, {weapon=0x1008}), foe, {STATUS.oil})
          local a = hit(gillie, foe, {status={oil=true}})
          local b = hit(gillie, foe, {status={oil=true}})
          return a.target.addedStatusEffects[STATUS.slow], b.target.addedStatusEffects[STATUS.slow]
        ''')
        self.assertEqual((first, again), (1, 0))

    def test_cameo_mark_is_spent_by_another_members_dodge_roll(self):
        own, gun, other, spent = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local cameo = wear(party(1, {weapon=0x1008}), 4468)
          local ally = party(2, {weapon=0x1008})
          local function f(id) local x = F(); x.identifier = id; return x end
          hit(cameo, foe, {critical=true})
          return R.spotter34(f(20), cameo, foe, classes, helpers), R.spotter34(f(27), ally, foe, classes, helpers),
                 R.spotter34(f(20), ally, foe, classes, helpers), R.spotter34(f(20), ally, foe, classes, helpers)
        ''')
        self.assertEqual((own, gun, other, spent), (False, False, True, False))

    def test_cameo_marks_on_a_critical_or_a_combo_follow_up_not_on_a_plain_first_hit(self):
        plain, crit, afterFirst, combo, late = self.run_lua('''
          PARTY_IDS = {1, 2}
          local function foe(a) return unit({type=1, address=a, hp=9000, maxHp=9000}) end
          local function f(id) local x = F(); x.identifier = id; return x end
          local cameo = wear(party(1, {weapon=0x1008}), 4468)
          local ally = party(2, {weapon=0x1008})
          local t1 = foe(900); hit(cameo, t1)                            -- control: a plain first hit
          local plain = R.spotter34(f(20), ally, t1, classes, helpers)
          local t2 = foe(901); hit(cameo, t2, {critical=true})
          local crit = R.spotter34(f(20), ally, t2, classes, helpers)
          local t3 = foe(902); NOW = NOW + 5000000; hit(cameo, t3)
          local afterFirst = R.spotter34(f(20), ally, t3, classes, helpers)
          NOW = NOW + 200000; hit(cameo, t3)                             -- the same member again 0.2 s later
          local combo = R.spotter34(f(20), ally, t3, classes, helpers)
          local t4 = foe(903); NOW = NOW + 5000000; hit(cameo, t4); NOW = NOW + 1000000; hit(cameo, t4)
          local late = R.spotter34(f(20), ally, t4, classes, helpers)    -- a second apart: two swings
          return plain, crit, afterFirst, combo, late
        ''')
        self.assertEqual((plain, crit, afterFirst, combo, late), (False, True, False, True, False))

    def test_a_belt_wearer_is_needed_for_the_mark(self):
        mark = self.run_lua('''
          PARTY_IDS = {1, 2}
          local foe = unit({type=1, address=900, hp=9000, maxHp=9000})
          local bare, ally = party(1, {weapon=0x1008}), party(2, {weapon=0x1008})
          hit(bare, foe, {critical=true})
          local x = F(); x.identifier = 20
          return R.spotter34(x, ally, foe, classes, helpers)
        ''')
        self.assertFalse(mark)

    def test_no_stage_grants_haste_or_bravery_or_pushes(self):
        bad = self.run_lua('''
          local n = 0
          for _, stages in pairs(R.Accessory.STAGES) do
            for _, st in ipairs(stages) do
              for _, fx in ipairs(st[3]) do
                if fx[2] == 'haste' or fx[2] == 'bravery' or fx[1] == 'mp' or fx[1] == 'push' then n = n + 1 end
              end
            end
          end
          return n
        ''')
        self.assertEqual(bad, 0)


if __name__ == '__main__':
    unittest.main()
