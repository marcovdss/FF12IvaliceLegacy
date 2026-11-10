"""No-game contracts for the 66 hybrid Proficiency lines (prototypes/hybrid_proficiency.lua)."""
import json
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class HybridProficiencyTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute((ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8'))
        g.H = self.lua.execute((ROOT/'prototypes/hybrid_proficiency.lua').read_text(encoding='utf-8'))
        self.lua.execute(r'''
          H.attach(P)
          P.reset()
          function S(job, t)
            local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100, casterMp=50, casterMaxMp=100,
                       selfMaxHp=1000, targetHpPct=80,
                       party={{address=100, job=job, hpPct=100, mp=50, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={poison=true}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}, {address=902, status={}}},
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
          function others(a, op, unit, id)
            local n = 0
            for _, o in ipairs(a and a.others or {}) do
              if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then n = n + 1 end
            end
            return n
          end
        ''')

    def test_pair_ids_follow_hybrid_names(self):
        names = json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))['hybrids']
        self.assertEqual(len(names), 66)
        for i, h in enumerate(names):
            a, b = h['pair']
            self.assertEqual(self.lua.eval(f'H.pairIndex({a}, {b})'), i)
            self.assertEqual(self.lua.eval(f'H.pairIndex({b}, {a})'), i)
            self.assertEqual(self.lua.eval(f'H.id({a}, {b})'), 12 + i)
        self.assertIsNone(self.lua.eval('H.pairIndex(4, 4)'))
        self.assertIsNone(self.lua.eval('H.pairIndex(4, 12)'))
        self.assertIsNone(self.lua.eval('H.pairIndex(4, -1)'))
        self.assertEqual(self.lua.eval('#H.LINES'), 66)

    def test_every_text_fits_and_every_level_reads_differently(self):
        self.lua.execute(r'''
          for id = 12, 77 do
            local g = P.GEAR[id]
            assert(g and g.line, id)
            local first = H.firstLimit(g.line)
            for k = 1, 3 do
              for L = 1, 4 do
                local t = g[k][L]
                assert(type(t) == 'string', id .. ':' .. k .. ':' .. L .. ' does not fit')
                local a, b = t:match('^([^\n]*)\n?(.*)$')
                assert(#a <= first and #b <= H.EXTRA_LIMIT and not b:find('\n'), t)
                assert(not t:find('[;<>{}]'), t)
                if L > 1 then assert(t ~= g[k][L - 1], id .. ':' .. k .. ':' .. L .. ' repeats level ' .. (L - 1)) end
              end
            end
          end
        ''')

    def test_party_buffs_sit_in_at_most_one_stage_and_only_at_stage_three(self):
        self.lua.execute(r'''
          for i, line in ipairs(H.LINES) do
            local stages = 0
            for k = 1, 3 do
              local has = false
              for _, fx in ipairs(line[k][5]) do if fx[1] == 'party' then has = true end end
              if has then stages = stages + 1; assert(k == 3, line.line .. ' party buff outside stage III') end
            end
            assert(stages <= 1, line.line)
          end
        ''')

    def test_every_rule_runs_on_every_event(self):
        # Exercise every stage with conditions forced true where a snapshot can make them so: no Lua error anywhere.
        self.lua.execute(r'''
          local everything = {slow=true, poison=true, oil=true, stop=true, immobilize=true, blind=true, disable=true,
                              disease=true, haste=true, protect=true, shell=true, faith=true, regen=true, bubble=true}
          for id = 12, 77 do
            for _, ev in ipairs({'hit', 'magick', 'heal', 'guard', 'struck', 'ally', 'status', 'aim'}) do
              for _, outcome in ipairs({'parry', 'block', 'dodge', 'counter'}) do
                P.reset()
                local s = S(id, {kill=true, targetStatus=everything, casterStatus={haste=true, lure=true, float=true,
                  invisible=true, hpCritical=true}, casterHpPct=20, targetHpPct=20, overflow=50, item=true,
                  healAmount=200, attacker=901, attackerStatus=everything, attackerMp=30, ally=200, outcome=outcome,
                  dropCritical=true, landed={8}, element=1, now=1})
                P.decide(ev, s, 12)
              end
            end
          end
        ''')

    def test_paladin_heal_lures_and_frees_the_ally(self):
        a = self.lua.execute(r'''
          local s = S(15, {target=200, healAmount=300, overflow=0, targetStatus={slow=true, immobilize=true}})
          return P.decide('heal', s, 2)
        ''')
        self.assertEqual(a.casterAdd[17], 0.5)             # Lure on the Paladin
        removed = list(a.targetRemove.values())
        self.assertIn(15, removed); self.assertIn(14, removed)  # Slow and Immobilize off the ally
        self.assertEqual(a.targetAdd[20], 0.5)             # level 2: Haste on the ally

    def test_ninja_chain_kill_and_invisible_opening(self):
        self.lua.execute(r'''
          local s1 = S(73, {targetStatus={poison=true}})
          assert(P.decide('hit', s1, 1) == nil)                       -- first hit of the chain: nothing yet
          local a = P.decide('hit', S(73, {targetStatus={poison=true}}), 1)
          assert(a.casterAdd[20] == 0.5)                             -- 2 in a row on a foe with an ailment: Haste
          a = P.decide('hit', S(73, {kill=true, targetId=10}), 5)
          assert(a.casterAdd[24] == 0.5)                             -- II: a kill grants Invisible
          assert(others(a, 'mp', 100) == 1)                          -- and 5% MP
          a = P.decide('hit', S(73, {casterStatus={invisible=true}, targetId=11}), 12)
          assert(math.abs(a.damage - 1.40) < 1e-9 and a.chargeTo and a.drainMp)   -- III 4/4 opening hit
        ''')

    def test_seer_ally_at_critical_gets_bubble(self):
        a = self.lua.execute('return P.decide("ally", S(14, {ally=200}), 12)')
        self.assertEqual(self.lua.eval('others')(a, 'add', 200, 28), 1)   # Bubble
        self.assertEqual(self.lua.eval('others')(a, 'add', 200, 25), 1)   # Regen
        self.assertEqual(self.lua.eval('others')(a, 'add', 200, 19), 1)   # Shell

    def test_struck_and_counter_reactions_hit_the_attacker(self):
        self.lua.execute(r'''
          -- Warden II: struck while Lured blinds the attacker
          local a = P.decide('struck', S(42, {casterStatus={lure=true}, attacker=901, attackerStatus={}, attackerMp=0}), 6)
          assert(others(a, 'add', 901, 7) == 1)
          -- Hell Knight II (counter): strips the attacker's Protect and Haste
          a = P.decide('guard', S(26, {outcome='counter', attacker=901, attackerStatus={protect=true, haste=true}}), 5)
          assert(others(a, 'remove', 901, 18) == 1 and others(a, 'remove', 901, 20) == 1)
          -- a parry is not a counter: Hell Knight has no parry stage
          assert(P.decide('guard', S(26, {outcome='parry', attacker=901, attackerStatus={protect=true}}), 5) == nil)
        ''')

    def test_memory_conditions(self):
        self.lua.execute(r'''
          -- Chaplain I: the first hit after a heal pushes charge; the second hit does not
          P.decide('heal', S(13, {target=200, healAmount=100, overflow=0}), 1)
          local a = P.decide('hit', S(13), 1)
          assert(a and a.chargeTo)
          assert(P.decide('hit', S(13), 1) == nil)
          -- Cannoneer I: only the first hit on each foe
          P.reset()
          a = P.decide('hit', S(23, {targetStatus={protect=true}}), 1)
          assert(a and a.targetRemove)
          a = P.decide('hit', S(23, {targetStatus={protect=true}}), 1)
          assert(a == nil or a.targetRemove == nil)
          -- a decided-twice kill does not double count the first-hit memory
          P.reset()
          a = P.decide('hit', S(23, {targetId=50, damage=90, targetHp=100, targetStatus={protect=true}}), 2)
          assert(a and a.targetRemove)
        ''')

    def test_a_spell_on_three_foes_gives_its_cast_rewards_once(self):
        self.lua.execute(r'''
          P.CAST_WINDOW = 200000
          -- Red Mage III: the same element twice; the second cast reaches three foes
          P.decide('magick', S(20, {element=1, now=1000}), 12)
          local first = P.decide('magick', S(20, {element=1, now=500000, targetId=1}), 12)
          assert(first.casterCharge and others(first, 'add') >= 1)          -- quickened, Faith, party Shell
          for i = 2, 3 do
            local again = P.decide('magick', S(20, {element=1, now=500000 + i, targetId=i}), 12)
            assert(again == nil or (again.casterCharge == nil and others(again, 'add') == 0), i)
          end
        ''')

    def test_a_healing_spell_on_the_party_gives_its_cast_rewards_once(self):
        # Area heals reach targets about a second apart: the runtime's cast id, not the clock, makes them one cast.
        self.lua.execute(r'''
          -- Chaplain II at 4/4: healing an ally adds 5% of the heal to their MP (a per-cast effect)
          local function heal(target, castId, now)
            return P.decide('heal', S(13, {target=target, healAmount=1000, overflow=0, castId=castId, action=4,
                                           now=now}), 12)
          end
          assert(others(heal(200, 7, 1000), 'mp') == 1)
          assert(others(heal(300, 7, 1001000), 'mp') == 0)        -- same Cura, next target a second later
          assert(others(heal(100, 7, 2001000), 'mp') == 0)
          assert(others(heal(200, 8, 9000000), 'mp') == 1)        -- the next Cura
          -- items carry no cast id: each use is its own
          assert(others(heal(200, nil, 9000001), 'mp') == 1 and others(heal(300, nil, 9000002), 'mp') == 1)
        ''')

    def test_a_damage_spells_status_rider_gives_its_cast_rewards_once(self):
        # Forge 182 runs per foe for a damage spell's rider: the cast id makes it one cast; weapon hits have no id.
        self.lua.execute(r'''
          local line
          for i, l in ipairs(H.LINES) do if l[1][1] == 'status' then
            for _, fx in ipairs(l[1][5]) do if fx[1] == 'hp' or fx[1] == 'party' or fx[1] == 'self' then line = i end end
          end end
          assert(line, 'a status stage with a per-cast effect')
          local job = H.FIRST_ID + line - 1
          local function land(castId, target)
            return P.decide('status', S(job, {landed={8}, castId=castId, action=26, targetId=target, now=1}), 12)
          end
          local first = land(5, 1)
          assert(first ~= nil)
          local function percast(a)
            local n = 0
            for _, o in ipairs(a and a.others or {}) do if o.op == 'hp' or o.op == 'add' then n = n + 1 end end
            for _ in pairs(a and a.casterAdd or {}) do n = n + 1 end
            return n
          end
          local n1 = percast(first)
          assert(n1 >= 1)
          assert(percast(land(5, 2)) < n1)                    -- same Bio, next foe
          assert(percast(land(6, 1)) == n1)                   -- next Bio
          assert(percast(land(nil, 1)) == n1 and percast(land(nil, 2)) == n1)   -- weapon riders: each hit
        ''')

    def test_status_spells_reach_cast_stages(self):
        self.lua.execute(r'''
          -- Uhlan I (id 27): Slow cast on a foe strips its Haste and Protect
          local a = P.decide('cast', S(27, {landed={15}, targetStatus={haste=true, protect=true}, allyTarget=false,
                                            action=60, now=1}), 1)
          local removed = {}
          for _, id in ipairs(a.targetRemove) do removed[id] = true end
          assert(removed[20] and removed[18])
          -- a Haste cast on an ally does not trigger it
          assert(P.decide('cast', S(27, {landed={20}, allyTarget=true, target=200, action=61, now=2}), 1) == nil)
          -- Tinker I (id 67): Haste on an ally reaches another ally
          a = P.decide('cast', S(67, {landed={20}, allyTarget=true, target=200, action=61, now=3}), 1)
          assert(others(a, 'add', 300, 20) == 1 and others(a, 'add', 200) == 0)
          -- Saboteur I (id 32, 'An ailment you land') hears a debuff spell too
          a = P.decide('cast', S(32, {landed={7}, allyTarget=false, targetStatus={shell=true}, action=62, now=4}), 1)
          assert(a and a.targetRemove)
          -- one Slowga reaching three foes gives Arc Knight (id 51) its MP once
          P.reset()
          local mp = 0
          for i = 1, 3 do
            local b = P.decide('cast', S(51, {landed={15}, allyTarget=false, targetId=i, action=63, now=100 + i}), 4)
            mp = mp + others(b, 'mp', 100)
          end
          assert(mp == 1, mp)
        ''')

    def test_range_conditions(self):
        self.lua.execute(r'''
          -- Ranger I (id 64) needs range; Flintlock I (id 37) needs to be close
          assert(P.decide('hit', S(64, {targetStatus={slow=true}, distance=20}), 1))
          assert(P.decide('hit', S(64, {targetStatus={slow=true}, distance=5, targetId=8}), 1) == nil)
          assert(P.decide('hit', S(64, {targetStatus={slow=true}, targetId=7}), 1) == nil)   -- unknown distance: no
          assert(P.decide('hit', S(37, {casterHpPct=30, distance=5}), 1))
          assert(P.decide('hit', S(37, {casterHpPct=30, distance=20, targetId=6}), 1) == nil)
        ''')

    def test_legendary_mastery_counts_either_parent(self):
        self.assertTrue(self.lua.eval('P.masters(73, P.JOB.hunter)'))
        self.assertTrue(self.lua.eval('P.masters(73, P.JOB.samurai)'))
        self.assertFalse(self.lua.eval('P.masters(73, P.JOB.knight)'))
        self.assertTrue(self.lua.eval('P.masters(P.JOB.knight, P.JOB.knight)'))


if __name__ == '__main__':
    unittest.main()
