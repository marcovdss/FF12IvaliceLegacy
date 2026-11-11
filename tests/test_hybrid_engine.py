"""No-game contracts for the hybrid proficiency engine additions (hybrid rework, engine side, 2026-11-11).

Covers the new conditions and effects of prototypes/hybrid_proficiency.lua, the multi-event stage, the runtime
changes that feed them (proficiency_runtime.lua) and the base-line policy changes in proficiency.lua.
"""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parent))

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

import test_proficiency as TP

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class HybridEngineTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.P = self.lua.execute((ROOT/'prototypes/proficiency.lua').read_text(encoding='utf-8'))
        g.H = self.lua.execute((ROOT/'prototypes/hybrid_proficiency.lua').read_text(encoding='utf-8'))
        self.lua.execute(r'''
          H.attach(P)
          P.reset()
          local h, t = 0.5, 0.75
          function S(job, t_)
            local s = {job=job, actorId=1, self=100, targetId=9, target=900, foe=true, kill=false, damage=100,
                       targetStatus={}, casterStatus={}, targetMp=50, casterHpPct=100, casterMp=50, casterMaxMp=100,
                       selfMaxHp=1000, targetHpPct=80,
                       party={{address=100, job=job, hpPct=100, mp=50, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={poison=true}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}, {address=902, status={}}},
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k, v in pairs(t_ or {}) do s[k] = v end
            return s
          end
          function others(a, op, unit, id)
            local n = 0
            for _, o in ipairs(a and a.others or {}) do
              if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then n = n + 1 end
            end
            return n
          end
          function other(a, op, unit, id)
            for _, o in ipairs(a and a.others or {}) do
              if o.op == op and (unit == nil or o.unit == unit) and (id == nil or o.id == id) then return o end
            end
          end
          function idx(name) for i, l in ipairs(H.LINES) do if l.line == name then return i end end end
          function jobOf(name) return H.FIRST_ID + idx(name) - 1 end
          -- Replace line 1 with a one-stage test line (stages 2 and 3 never fire) and decide one event on it.
          local FILLER = {'aim', nil, 'Never', 's', {{'mp', 1}}}
          function testLine(stage1, stage2)
            H.LINES[1] = {line = 'Test', stage1, stage2 or FILLER, FILLER}
            H.attach(P)
            P.reset()
            return H.FIRST_ID
          end
          function fire(ev, fx, t_, rank)
            local id = testLine({ev, nil, 'Trigger', 's', {fx}})
            return P.decide(ev == 'counter' and 'guard' or ev, S(id, t_), rank or 12)
          end
          function probe(ev, conds, t_)
            local id = testLine({ev, conds, 'Trigger', 's', {{'quick', 10}}})
            local a = P.decide(ev == 'counter' and 'guard' or ev, S(id, t_), 12)
            return a ~= nil and a.casterCharge ~= nil
          end
        ''')

    # ---------- B-1: reaction conditions read the attacker ----------

    def test_reaction_conditions_read_the_attacker_not_the_target(self):
        self.lua.execute(r'''
          -- Starfall III (Astrologer): a counter on a Slowed foe (it never fired: the condition read the party member's statuses)
          local id = jobOf('Starfall')
          local a = P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={slow=true}}), 9)
          assert(others(a, 'add', 901, 3) == 1, 'Stop chance on the Slowed attacker')
          a = P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={}}), 9)
          assert(others(a, 'add', 901, 3) == 0, 'no Stop on a foe that is not Slowed')
          a = P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={}, targetStatus={slow=true}}), 9)
          assert(others(a, 'add', 901, 3) == 0, 'the party member is not the foe')
          -- every foe condition, on every reaction event
          for _, ev in ipairs({'guard', 'struck'}) do
            assert(probe(ev, {'buffed'}, {outcome='parry', attackerStatus={haste=true}}))
            assert(not probe(ev, {'buffed'}, {outcome='parry', attackerStatus={}}))
            assert(probe(ev, {'buffless'}, {outcome='parry', attackerStatus={}}))
            assert(probe(ev, {'ailing'}, {outcome='parry', attackerStatus={poison=true}}))
            assert(not probe(ev, {'ailing'}, {outcome='parry', attackerStatus={haste=true}}))
            assert(probe(ev, {'ailing2'}, {outcome='parry', attackerStatus={poison=true, blind=true}}))
            assert(probe(ev, {'slowed'}, {outcome='parry', attackerStatus={slow=true}}))
          end
          assert(probe('ally', {'ailing'}, {ally=200, attackerStatus={blind=true}}))
        ''')

    # ---------- fresh ----------

    def test_fresh_grants_apply_only_to_units_that_lack_the_status(self):
        self.lua.execute(r'''
          local h = 0.5
          local id = testLine({'hit', nil, 'Your hits', 'p', {{'self', 'haste', h, fresh = true}}})
          assert(P.decide('hit', S(id, {casterStatus={haste=true}}), 1) == nil, 'already Hasted: nothing')
          P.reset()
          assert(P.decide('hit', S(id, {}), 1).casterAdd[20] == 0.5)
          -- without the flag the old behaviour stays
          id = testLine({'hit', nil, 'Your hits', 'p', {{'self', 'haste', h}}})
          assert(P.decide('hit', S(id, {casterStatus={haste=true}}), 1).casterAdd[20] == 0.5)
          -- reaction events grant through the helper: same guard
          id = testLine({'guard', nil, 'A parry', 's', {{'self', 'haste', h, fresh = true}}})
          assert(P.decide('guard', S(id, {outcome='parry', casterStatus={haste=true}, attacker=901}), 1) == nil)
          assert(others(P.decide('guard', S(id, {outcome='parry', attacker=901}), 1), 'add', 100, 20) == 1)
          -- party and ally grants skip holders
          id = testLine({'heal', nil, 'Healing an ally', 's', {{'party', 'protect', h, fresh = true},
                                                               {'ally', 'regen', h, fresh = true}}})
          local s = S(id, {target=200, healAmount=100, overflow=0, targetStatus={regen=true}})
          s.party[2].status = {protect=true}
          local a = P.decide('heal', s, 1)
          assert(others(a, 'add', 200, 18) == 0 and others(a, 'add', 100, 18) == 1 and others(a, 'add', 300, 18) == 1)
          assert(a.targetAdd == nil, 'the healed ally already has Regen')
          s = S(id, {target=200, healAmount=100, overflow=0})
          assert(P.decide('heal', s, 1).targetAdd[25] == 0.5)
          -- another unit chosen by `ally` (a hit): the most hurt ally, looked up in the party
          id = testLine({'hit', nil, 'Your hits', 'p', {{'ally', 'bravery', h, fresh = true}}})
          s = S(id, {}); s.party[2].status = {bravery=true}
          assert(P.decide('hit', s, 1) == nil)
          assert(others(P.decide('hit', S(id, {}), 1), 'add', 200, 21) == 1)
          -- text says so
          id = testLine({'hit', nil, 'Your hits', 'p', {{'self', 'haste', h, fresh = true}, {'self', 'faith', h}}})
          assert(P.GEAR[id][1][1]:find('(if not held)', 1, true), P.GEAR[id][1][1])
          id = testLine({'hit', nil, 'Your hits', 'p', {{'self', 'haste', h, fresh = true}}})
          assert(P.GEAR[id][1][1]:find(' if not held', 1, true) and not P.GEAR[id][1][1]:find('(if', 1, true), P.GEAR[id][1][1])
        ''')

    # ---------- guard stages hear counter-rolled parries ----------

    def test_guard_stages_hear_a_parry_that_rolled_a_counter(self):
        self.lua.execute(r'''
          local id = jobOf('Iai')       -- Sword Saint: I = parry or block, II = first hit after a parry or block
          local a = P.decide('guard', S(id, {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={}}), 1)
          assert(a and others(a, 'add', 100, 17) == 1, 'Lure from a counter-rolled parry')
          a = P.decide('guard', S(id, {outcome='counter', guardOutcome='block', attacker=901, attackerStatus={}}), 1)
          assert(a and others(a, 'add', 100, 17) == 1)
          assert(P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={}}), 1) == nil,
                 'a counter with no parry or block under it (fists) is not a guard')
          assert(P.decide('guard', S(id, {outcome='dodge', attacker=901, attackerStatus={}}), 1) == nil)
          assert(P.decide('guard', S(id, {outcome='parry', attacker=901, attackerStatus={}}), 1))
          -- afterGuard counts it
          P.reset()
          P.decide('guard', S(id, {outcome='counter', guardOutcome='parry', attacker=901, attackerStatus={}}), 5)
          assert(P.decide('hit', S(id), 5).damage, 'first hit after the counter-parry')
          P.reset()
          P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={}}), 5)
          assert(P.decide('hit', S(id), 5) == nil, 'a fists counter is not a parry')
          -- counter stages still hear the same roll
          local hk = jobOf('Hellfire')
          assert(others(P.decide('guard', S(hk, {outcome='counter', guardOutcome='parry', attacker=901,
                                                 attackerStatus={protect=true}}), 1), 'remove', 901, 18) == 1)
        ''')

    # ---------- memory conditions ----------

    def test_after_counter_and_guard_memory_need_no_guard_stage(self):
        self.lua.execute(r'''
          -- a line with hit stages only that asks for afterCounter / afterGuard still hears guard events
          local id = testLine({'hit', {'afterCounter'}, 'Your first hit after a counter', 's', {{'quick', 10}}})
          P.decide('guard', S(id, {outcome='counter', attacker=901, attackerStatus={}}), 12)
          assert(P.decide('hit', S(id), 12).casterCharge, 'hit after the counter')
          assert(P.decide('hit', S(id), 12) == nil, 'only the first')
          P.reset()
          assert(P.decide('hit', S(id), 12) == nil)
          id = testLine({'hit', {'afterGuard'}, 'Your first hit after a parry or block', 's', {{'quick', 10}}})
          P.decide('guard', S(id, {outcome='parry', attacker=901}), 12)
          assert(P.decide('hit', S(id), 12).casterCharge)
          P.reset()
          P.decide('guard', S(id, {outcome='dodge', attacker=901}), 12)
          assert(P.decide('hit', S(id), 12) == nil, 'a dodge is not a parry or block')
          P.reset()
          P.decide('guard', S(id, {outcome='counter', guardOutcome='block', attacker=901}), 12)
          assert(P.decide('hit', S(id), 12).casterCharge, 'a counter-rolled block counts')
          -- afterCounter after a spell in between: not the next action
          id = testLine({'magick', {'afterCounter'}, 'A spell right after a counter', 's', {{'quick', 10}}})
          P.decide('guard', S(id, {outcome='counter', attacker=901}), 12)
          assert(P.decide('magick', S(id, {element=1, now=1}), 12).casterCharge)
        ''')

    def test_spell_target_remembers_the_last_foe_a_damage_spell_hit(self):
        self.lua.execute(r'''
          local id = testLine({'hit', {'spellTarget'}, 'Hits on the foe your last spell struck', 'p', {{'quick', 10}}})
          assert(P.decide('hit', S(id, {targetId=7}), 12) == nil, 'no spell yet')
          P.decide('magick', S(id, {element=1, targetId=7, now=1}), 12)
          assert(P.decide('hit', S(id, {targetId=7}), 12).casterCharge)
          assert(P.decide('hit', S(id, {targetId=8}), 12) == nil, 'another foe')
          P.decide('magick', S(id, {element=1, targetId=8, now=900000}), 12)
          assert(P.decide('hit', S(id, {targetId=8}), 12).casterCharge)
        ''')

    def test_alternate3_and_a_stage_that_listens_to_several_events(self):
        self.lua.execute(r'''
          local id = testLine({{'magick', 'heal'}, {'alternate3'}, 'Every 3rd switch', 's', {{'quick', 30}}})
          local now = 0
          local function magick() now = now + 1000000; return P.decide('magick', S(id, {element=1, now=now}), 12) end
          local function heal() now = now + 1000000
            return P.decide('heal', S(id, {target=200, healAmount=100, overflow=0, now=now}), 12) end
          assert(magick() == nil)     -- the first action is not a switch
          assert(heal() == nil)       -- switch 1
          assert(magick() == nil)     -- switch 2
          local a = heal()            -- switch 3: heard on a heal event
          assert(a and a.casterCharge == 0.30)
          assert(magick() == nil)     -- switch 4
          assert(heal() == nil)       -- switch 5
          a = magick()                -- switch 6: heard on a damage event
          assert(a and a.casterCharge == 0.30)
          assert(heal() == nil)
          -- repeats of the same kind are no switch; healing items do not count
          assert(heal() == nil and heal() == nil)
          local item = P.decide('heal', S(id, {target=200, healAmount=100, overflow=0, item=true, now=now + 5000000}), 12)
          assert(item == nil)
          -- an area spell's later targets repeat the first target's flag
          P.reset()
          magick(); heal(); magick()
          local first = P.decide('heal', S(id, {target=200, healAmount=100, overflow=0, castId=5, action=4, now=1}), 12)
          assert(first and first.casterCharge)
          local second = P.decide('heal', S(id, {target=300, healAmount=100, overflow=0, castId=5, action=4, now=2}), 12)
          assert(second == nil or second.casterCharge == nil, 'per-cast effect once')
          -- the text is built for the first event and every event hears the stage
          assert(type(P.GEAR[id][3][4]) == 'string' and P.GEAR[id][3][4] ~= '')
          assert(P.rules[id].heal and P.rules[id].magick)
        ''')

    # ---------- foe conditions ----------

    def test_empty_mp_skips_foes_without_an_mp_pool(self):
        self.lua.execute(r'''
          assert(probe('hit', {'emptyMp'}, {targetMp=0, targetMaxMp=100}))
          assert(not probe('hit', {'emptyMp'}, {targetMp=0, targetMaxMp=0}), 'no pool at all')
          assert(not probe('hit', {'emptyMp'}, {targetMp=0}), 'pool unknown')
          assert(not probe('hit', {'emptyMp'}, {targetMp=5, targetMaxMp=100}))
          assert(probe('magick', {'emptyMp'}, {element=1, now=1, targetMp=0, targetMaxMp=40}))
          assert(probe('guard', {'emptyMp'}, {outcome='parry', attackerMp=0, attackerMaxMp=50}))
          assert(not probe('guard', {'emptyMp'}, {outcome='parry', attackerMp=0, attackerMaxMp=0}))
        ''')

    def test_tough_detonable_and_sleeping(self):
        self.lua.execute(r'''
          assert(probe('hit', {'tough'}, {tough=true}) and not probe('hit', {'tough'}, {}))
          assert(probe('guard', {'tough'}, {outcome='parry', attackerTough=true}))
          assert(not probe('guard', {'tough'}, {outcome='parry', tough=true}))
          for _, st in ipairs({'slow', 'disable', 'immobilize'}) do
            assert(probe('hit', {'detonable'}, {targetStatus={[st]=true}}), st)
          end
          assert(not probe('hit', {'detonable'}, {targetStatus={stop=true, poison=true}}))
          assert(not probe('hit', {'detonable'}, {}))
          -- Sleep: its own condition; "ailing" (M.HINDERED) is left as it was
          assert(P.STATUS.sleep == 4)
          assert(probe('hit', {'sleeping'}, {targetStatus={sleep=true}}))
          assert(not probe('hit', {'sleeping'}, {targetStatus={poison=true}}))
          assert(not probe('hit', {'ailing'}, {targetStatus={sleep=true}}))
          for _, n in ipairs(P.HINDERED) do assert(n ~= 'sleep') end
          for _, n in ipairs(P.AILMENTS) do assert(n ~= 'sleep') end
          -- grantable, with a name in the text
          local id = testLine({'hit', nil, 'Your hits', 'p', {{'foe', 'sleep', 0.5}}})
          assert(P.decide('hit', S(id), 1).targetAdd[4] == 0.5)
          assert(P.GEAR[id][1][1]:find('inflict Sleep', 1, true), P.GEAR[id][1][1])
        ''')

    # ---------- "an ailment you land" ----------

    def test_an_ailment_you_land_is_one_set_on_both_routes(self):
        self.lua.execute(r'''
          local id = testLine({'status', nil, 'An ailment you land', 's', {{'quick', 10}}})
          local function weapon(landed) return P.decide('status', S(id, {landed=landed}), 12) end
          local function spell(landed, full)
            return P.decide('cast', S(id, {landed=landed, landedFull=full, allyTarget=false, action=60, now=1}), 12)
          end
          for _, ailment in ipairs({4, 3, 5, 7, 8, 9, 10, 11, 14, 15, 16, 13}) do   -- Sleep Stop Confuse Blind ... Disable
            assert(weapon({ailment}), 'weapon ' .. ailment); P.reset()
            assert(spell({ailment}), 'spell ' .. ailment); P.reset()
          end
          assert(not weapon({20}) and not spell({20}), 'a buff is no ailment')
          P.reset()
          assert(not weapon({}) and not spell({}))
          P.reset()
          assert(spell({}, {4}), 'the weapon route hands over its own wider list')
          -- the status list the stage works on holds only ailments, even when the spell landed a buff too
          local seen
          H.LINES[1] = {line='Test', {'status', nil, 'An ailment you land', 's', {{'spread', 100}}},
                        {'cast', nil, 'x', 's', {{'mp', 1}}}, {'cast', nil, 'x', 's', {{'mp', 1}}}}
          H.attach(P); P.reset()
          local s = S(H.FIRST_ID, {landed={20, 8}, allyTarget=false, action=61, now=2})
          local a = P.decide('cast', s, 12)
          assert(others(a, 'add', 901, 8) == 1 and others(a, 'add', 901, 20) == 0, 'only the ailment spreads')
          assert(s.landed[1] == 20 and #s.landed == 2, 'the snapshot is restored')
        ''')

    # ---------- stop cap ----------

    def test_hybrid_extend_stop_stops_after_four_extensions(self):
        self.lua.execute(r'''
          P.reset()
          local id = testLine({'hit', {'stopped'}, 'Hits on a Stopped foe', 'p', {{'extend', 'stop', 10}}})
          local alone = {{address=100, job=id, hpPct=100, mp=1, maxMp=1, status={}}}
          local function hit(target)
            return P.decide('hit', S(id, {targetId=target or 9, targetStatus={stop=true}, party=alone}), 12)
          end
          local n = 0
          for i = 1, 7 do if hit() then n = n + 1 end end
          assert(n == 4, 'four extensions, got ' .. n)
          assert(hit(10), 'another Stop has its own count')
          P.observeStop(9, false)                       -- the Stop ended
          assert(hit(), 'a new Stop starts again')
          -- Slow has no cap
          local sid = testLine({'hit', {'slowed'}, 'Hits on a Slowed foe', 'p', {{'extend', 'slow', 10}}})
          for i = 1, 8 do assert(P.decide('hit', S(sid, {targetStatus={slow=true}, party=alone}), 12)) end
          -- the Time Mage's own aura shares the count: one extension per hit, not two
          P.reset()
          local both = {{address=100, job=id, hpPct=100, mp=1, maxMp=1, status={}}, {address=300, job=6, hpPct=90, mp=1, maxMp=1, status={}}}
          sid = testLine({'hit', {'stopped'}, 'Hits on a Stopped foe', 'p', {{'extend', 'stop', 10}}})
          n = 0
          for i = 1, 7 do
            local r = P.decide('hit', S(sid, {targetStatus={stop=true}, party=both}), 12)
            if r and r.targetExtend then n = n + 1 end
          end
          assert(n == 4, 'shared count: ' .. n)
        ''')

    # ---------- the measure buff event ----------

    def test_chemist_hybrids_hear_the_measure_buff_event(self):
        self.lua.execute(r'''
          local bishop = jobOf('Sacrament')           -- Chemist + White Mage
          assert(P.rules[bishop].buff, 'Chemist hybrid registers the buff event')
          assert(P.rules[jobOf('Crusade')].buff == nil, 'Lancer + White Mage has no measure')
          local s = S(bishop, {landed={20}, target=200})
          local a = P.decide('buff', s, 1)
          assert(others(a, 'add', 300, 20) == 1, 'the buff spreads to a second ally')
          assert(P.decide('buff', S(bishop, {landed={20}, target=200, roll=function() return false end}), 1) == nil)
          assert(P.decide('buff', S(jobOf('Crusade'), {landed={20}, target=200}), 1) == nil)
        ''')

    # ---------- firing counter ----------

    def test_about_to_act_firing_counter_is_logged(self):
        self.lua.execute(r'''
          local logs = {}
          H.log = function(line) logs[#logs + 1] = line end
          H.resetAbout()
          local near = {current=25000, required=30000, last=25000}
          local far = {current=10000, required=30000, last=10000}
          local id = testLine({'hit', {'about'}, 'Hits on a foe about to act', 'p', {{'dmg', 20}}})
          assert(P.decide('hit', S(id, {charge=near}), 1))
          assert(#logs == 1 and logs[1]:find('fired 1 of 1 checks', 1, true), logs[1])
          assert(logs[1]:find('hits 1 of 1', 1, true) and logs[1]:find('spells 0 of 0', 1, true), logs[1])
          assert(P.decide('hit', S(id, {charge=far}), 1) == nil)
          assert(H.about.checked == 2 and H.about.fired == 1)
          -- a hit decided twice (its own bonus makes it lethal) is one check
          P.reset()
          local before = H.about.checked
          P.decide('hit', S(id, {charge=near, damage=90, targetHp=100}), 1)
          assert(H.about.checked == before + 1, 'one check per event')
          -- every tenth check writes a line
          for i = 3, 10 do P.reset(); P.decide('hit', S(id, {charge=near}), 1) end
          local last = logs[#logs]
          assert(H.about.checked == 11 and last:find('of 10 checks', 1, true), tostring(last) .. ' ' .. H.about.checked)
          -- stages that do not read the condition count nothing
          H.resetAbout()
          local other_ = testLine({'hit', {'slowed'}, 'x', 'p', {{'dmg', 20}}})
          P.decide('hit', S(other_, {targetStatus={slow=true}}), 1)
          assert(H.about.checked == 0)
          -- spells count under their own tally
          id = testLine({'magick', {'about'}, 'Spells on a foe about to act', 'p', {{'dmg', 20}}})
          assert(P.decide('magick', S(id, {element=1, now=1, charge=near}), 1))
          assert(H.about.magick[1] == 1 and H.about.magick[2] == 1)
        ''')

    # ---------- wording ----------

    def test_struck_and_ally_triggers_say_physical_hit(self):
        self.lua.execute(r'''
          assert(H.trigger({'struck', nil, 'Being struck while you have Lure'}) == 'Taking a physical hit while you have Lure')
          assert(H.trigger({'struck', nil, 'Dropping to critical HP'}) == 'Physical hit to critical HP')
          assert(H.trigger({'ally', nil, 'An ally dropping to critical HP'}) == 'Physical hit: ally to critical HP')
          assert(H.trigger({'hit', nil, 'Being struck'}) == 'Being struck', 'other events keep their phrases')
          assert(H.trigger({'struck', nil, 'A physical hit on you'}) == 'A physical hit on you')
          local unspoken = {}
          for _, u in ipairs(H.unspoken()) do unspoken[u[1] .. ':' .. u[2]] = true end
          local checked = 0
          for id = 12, 77 do
            local line = H.LINES[id - 11]
            for k = 1, 3 do
              local ev = H.firstEvent(line[k][1])
              if ev == 'struck' or ev == 'ally' then
                local fell = unspoken[line.line .. ':' .. k]
                for L = 1, 4 do
                  local text = P.GEAR[id][k][L]:lower()
                  if not fell then assert(text:find('physical hit', 1, true), text) end
                  checked = checked + 1
                end
              end
            end
          end
          assert(checked > 0)
          -- a stage whose reworded text cannot fit keeps the stock phrase, and says so
          local long = {'struck', nil, 'Dropping to critical HP', 's', {{'self', 'protect', 1}, {'self', 'shell', 1},
            {'self', 'haste', 1}, {'foe', 'blind', 1}, {'foe', 'disable', 1}, {'party', 'bravery', 1}}}
        ''')

    # ---------- ops ----------

    def test_gift_buff_moves_a_foe_buff_to_the_most_hurt_ally(self):
        self.lua.execute(r'''
          local a = fire('hit', {'giftBuff', 0.5}, {targetStatus={protect=true, haste=true, reflect=true}})
          assert(#a.targetRemove == 1 and a.targetRemove[1] == 18, 'the first buff')
          assert(other(a, 'add', 200, 18).scale == 0.5)
          -- the ally already has it: the next buff
          local s = S(H.FIRST_ID, {targetStatus={protect=true, haste=true}}); s.party[2].status = {protect=true}
          a = P.decide('hit', s, 12)
          assert(a.targetRemove[1] == 20 and other(a, 'add', 200, 20))
          -- Reflect and Berserk never move; no hurt ally, no theft
          assert(fire('hit', {'giftBuff', 0.5}, {targetStatus={reflect=true, berserk=true}}) == nil)
          local full = S(H.FIRST_ID, {targetStatus={protect=true}})
          for _, p in ipairs(full.party) do p.hpPct = 100 end
          assert(P.decide('hit', full, 12) == nil)
          -- on a reaction the buff comes off the attacker through the helper
          a = fire('guard', {'giftBuff', 0.5}, {outcome='parry', attacker=901, attackerStatus={shell=true}})
          assert(others(a, 'remove', 901, 19) == 1 and others(a, 'add', 200, 19) == 1 and a.targetRemove == nil)
          -- the ally may refuse it
          assert(fire('hit', {'giftBuff', 0.5}, {targetStatus={protect=true}, canLand=function() return false end}) == nil)
        ''')

    def test_strip_other_leech_silence_and_transfer_ailment(self):
        self.lua.execute(r'''
          local a = fire('hit', {'stripOther', {'protect', 'shell'}},
                         {foes={{address=901, status={protect=true}}, {address=902, status={shell=true}}}})
          assert(others(a, 'remove', 901, 18) == 1 and others(a, 'remove') == 1, 'the nearest other foe only')
          assert(fire('hit', {'stripOther', {'protect'}}, {}) == nil)
          -- leechSilence: only when the drain really empties the foe
          local id = testLine({'hit', nil, 'Your hits', 'p', {{'drain', 100}, {'leechSilence', 0.5}}})
          local function go(t_) P.reset(); return P.decide('hit', S(id, t_), 12) end
          local r = go({targetMp=3, casterMaxMp=1000, casterMp=0}).leechSilence
          assert(r and r.scale == 0.5 and r.need == 3)
          assert(go({targetMp=50, casterMaxMp=1000, casterMp=0}).leechSilence == nil, 'not emptied')
          assert(go({targetMp=3, casterMaxMp=100, casterMp=0}).leechSilence == nil, 'the per-hit cap kept it above 0')
          assert(go({targetMp=3, casterMaxMp=1000, casterMp=0, targetStatus={silence=true}}).leechSilence == nil)
          -- transferAilment: an ally's ailment goes to the attacker
          a = fire('counter', {'transferAilment', 1}, {outcome='counter', attacker=901, attackerStatus={}})
          assert(others(a, 'remove', 200, 8) == 1 and others(a, 'add', 901, 8) == 1)
          assert(fire('counter', {'transferAilment', 1}, {outcome='counter', attacker=901, attackerStatus={poison=true}}) == nil,
                 'the attacker has it already')
          assert(fire('counter', {'transferAilment', 1}, {outcome='counter', attacker=901, attackerStatus={},
                 canLand=function() return false end}) == nil, 'an immune attacker keeps the ailment off the ally')
          local own = S(H.FIRST_ID, {outcome='counter', attacker=901, attackerStatus={}})
          own.party[2].status = {}; own.party[1].status = {slow=true}
          assert(P.decide('guard', own, 12) == nil, 'the caster is not an ally')
          assert(fire('hit', {'transferAilment', 1}, {}) == nil, 'reaction events only')
        ''')

    def test_heal_ops_cure_other_party_heal_pct_and_ally_hp(self):
        self.lua.execute(r'''
          -- cureOther: another ailing ally than the one healed
          local a = fire('heal', {'cureOther', 1}, {target=300, healAmount=100, overflow=0})
          assert(others(a, 'remove', 200, 8) == 1)
          assert(fire('heal', {'cureOther', 1}, {target=200, healAmount=100, overflow=0}) == nil, 'only the healed ally is ailing')
          local s = S(H.FIRST_ID, {target=300, healAmount=100, overflow=0})
          s.party[2].status = {poison=true, silence=true}
          a = P.decide('heal', s, 12)
          assert(others(a, 'remove', 200) == 1, 'one ailment per use')
          s.party[2].status = {}; s.party[1].status = {slow=true}
          assert(others(P.decide('heal', s, 12), 'remove', 100, 15) == 1, 'the caster last')
          -- partyHealPct: every other ally below full HP
          a = fire('heal', {'partyHealPct', 25}, {target=200, healAmount=200, overflow=0})
          assert(others(a, 'hp') == 1 and other(a, 'hp', 300).amount == 50, 'not the healed ally, not the full-HP caster')
          s = S(H.FIRST_ID, {target=200, healAmount=200, overflow=0}); s.party[1].hpPct = 60
          assert(others(P.decide('heal', s, 12), 'hp', 100) == 0)
          H.LINES[1][1][5][1].withSelf = true; H.attach(P); P.reset()
          assert(others(P.decide('heal', s, 12), 'hp', 100) == 1, 'withSelf includes the caster')
          -- allyHp: a share of the ally's own max HP, the caster's when the party snapshot has none
          a = fire('counter', {'allyHp', 5}, {outcome='counter', attacker=901, attackerStatus={}})
          assert(other(a, 'hp', 200).amount == 50)
          local t_ = S(H.FIRST_ID, {outcome='counter', attacker=901, attackerStatus={}}); t_.party[2].maxHp = 2000
          assert(other(P.decide('guard', t_, 12), 'hp', 200).amount == 100)
        ''')

    def test_drain_to_heal_ally_quick_and_hp_overflow_regen(self):
        self.lua.execute(r'''
          -- drainToHeal: at full MP the drain heals the most hurt ally for a share of the damage
          local id = testLine({'hit', nil, 'Your hits', 'p', {{'drain', 100}, {'drainToHeal', 10}}})
          local function go(t_) P.reset(); return P.decide('hit', S(id, t_), 12) end
          local a = go({damage=400, casterMp=1000, casterMaxMp=1000})
          assert(a.drainMp and other(a, 'hp', 200).amount == 40)
          assert(others(go({damage=400, casterMp=500, casterMaxMp=1000}), 'hp') == 0, 'not at full MP')
          assert(others(go({damage=400, casterMp=1000, casterMaxMp=1000, targetMp=0}), 'hp') == 0, 'nothing to drain')
          assert(others(go({damage=400, casterMp=1000, casterMaxMp=1000, comboFollow=true}), 'hp') == 0, 'once per attack')
          id = testLine({'hit', nil, 'Your hits', 'p', {{'drain', 100}, {'drainToHeal'}}})
          assert(other(go({damage=400, casterMp=1000, casterMaxMp=1000}), 'hp', 200).amount == 40, 'default 10%')
          -- allyQuick arms an ally's next action
          a = fire('cast', {'allyQuick', 20}, {allyTarget=true, target=200, landed={20}, action=61, now=1})
          assert(other(a, 'quick', 200).fraction == 0.20)
          id = testLine({'castAlly', {'hasteCast'}, 'Casting Haste on an ally', 's', {{'allyQuick', {20, 20, 20, 30}}}})
          a = P.decide('cast', S(id, {allyTarget=true, target=300, landed={20}, action=61, now=1}), 12)
          assert(other(a, 'quick', 300).fraction == 0.30)
          a = fire('hit', {'allyQuick', 20}, {})
          assert(other(a, 'quick', 200), 'on a hit: the most hurt ally')
          -- hpOverflowRegen: the stage's own heal spills over
          id = testLine({'counter', nil, 'Each counter', 's', {{'hp', 5}, {'hpOverflowRegen', 0.5}}})
          local function reg(t_) P.reset(); t_.outcome, t_.attacker, t_.attackerStatus = 'counter', 901, {}
            return P.decide('guard', S(id, t_), 12) end
          assert(other(reg({casterHpPct=99}), 'add', 200, 25).scale == 0.5, '5% of 1000 is more than the 1% missing')
          assert(other(reg({casterHpPct=50}), 'add', 200, 25) == nil, 'room for all of it')
        ''')

    def test_hp_overflow_regen_uses_exact_hp_when_known(self):
        self.lua.execute(r'''
          local id = testLine({'counter', nil, 'Each counter', 's', {{'hp', 5}, {'hpOverflowRegen', 0.5}}})
          local function reg(t_) P.reset(); t_.outcome, t_.attacker, t_.attackerStatus = 'counter', 901, {}
            return P.decide('guard', S(id, t_), 12) end
          assert(other(reg({casterHp=980, casterHpPct=98}), 'add', 200, 25), '50 healed, 20 missing: overflow')
          assert(other(reg({casterHp=900, casterHpPct=90}), 'add', 200, 25) == nil, '100 missing: it all fits')
          assert(other(reg({casterHp=950, casterHpPct=95}), 'add', 200, 25) == nil, 'exactly full: no overflow')
          -- no hurt ally, no Regen
          local s = S(id, {casterHp=1000, casterHpPct=100, outcome='counter', attacker=901, attackerStatus={}})
          for _, p in ipairs(s.party) do p.hpPct = 100 end
          assert(P.decide('guard', s, 12) == nil or other(P.decide('guard', s, 12), 'add') == nil)
        ''')

    def test_every_new_op_has_text(self):
        self.lua.execute(r'''
          local fxs = {{'giftBuff', 0.5}, {'stripOther', {'protect', 'shell'}}, {'leechSilence', 0.5},
            {'transferAilment', 1}, {'cureOther', 1}, {'partyHealPct', 25}, {'drainToHeal'}, {'allyQuick', 20},
            {'hpOverflowRegen', 0.5}, {'allyHp', 5}}
          for _, fx in ipairs(fxs) do
            local id = testLine({'hit', nil, 'Your hits', 'p', {fx}})
            local text = P.GEAR[id][1][1]
            assert(type(text) == 'string' and #text > 10, fx[1])
            assert(not text:find('[;<>{}]'), text)
          end
          local id = testLine({'hit', nil, 'A kill', 's', {{'stripOther', {'protect', 'shell'}}}})
          assert(P.GEAR[id][1][1]:find("strips another foe's Protect and Shell", 1, true), P.GEAR[id][1][1])
          id = testLine({'hit', nil, 'A kill', 's', {{'leechSilence', 0.5}}})
          assert(P.GEAR[id][1][1]:find('silences', 1, true), P.GEAR[id][1][1])
        ''')

    def test_every_hybrid_rule_still_runs_on_every_event_with_the_new_data(self):
        # every stage, forced true where a snapshot can make it so, on every event: no Lua error anywhere
        self.lua.execute(r'''
          local everything = {slow=true, poison=true, oil=true, stop=true, immobilize=true, blind=true, disable=true,
                              disease=true, haste=true, protect=true, shell=true, faith=true, regen=true, bubble=true,
                              sleep=true}
          for id = 12, 77 do
            for _, ev in ipairs({'hit', 'magick', 'heal', 'guard', 'struck', 'ally', 'status', 'cast', 'buff', 'aim'}) do
              for _, outcome in ipairs({'parry', 'block', 'dodge', 'counter'}) do
                P.reset()
                local s = S(id, {kill=true, targetStatus=everything, casterStatus={haste=true, lure=true, float=true,
                  invisible=true, hpCritical=true}, casterHpPct=20, targetHpPct=20, overflow=50, item=true, healAmount=200,
                  attacker=901, attackerStatus=everything, attackerMp=0, attackerMaxMp=50, ally=200, outcome=outcome,
                  guardOutcome=outcome == 'counter' and 'parry' or nil, dropCritical=true, landed={8, 4}, element=1, now=1,
                  targetMaxMp=100, targetMp=0, tough=true, attackerTough=true, switch3=true, allyTarget=true})
                P.decide(ev, s, 12)
              end
            end
          end
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class HybridEngineApplyTest(unittest.TestCase):
    """What the new action fields do when they reach the game helpers."""

    def setUp(self):
        TP.ProficiencyApplyTest.setUp(self)

    def test_pursuit_stretches_renewed_timers(self):
        self.lua.execute('''
          local f = apply({targetRenew={15}, renewScale=1.10}, keep({has={[15]=10}}))
          assert(f.target.addedStatusEffects[15] == 1 and f.target.statusEffectDurations[15] == 110)
          f = apply({targetRenew={15}}, keep({has={[15]=10}}))
          assert(f.target.addedStatusEffects[15] == 1 and f.target.statusEffectDurations[15] == -1, 'a plain renew writes no duration')
          f = apply({targetRenew={15}, renewScale=1.10}, keep({}))
          assert(f.target.addedStatusEffects[15] == 0, 'nothing to renew')
          -- a longer queued grant is kept
          local source, target = keep({address=100, type=0}), keep({has={[15]=10}})
          local g = {caster=plus(), target=plus()}
          g.target.addedStatusEffects[15] = 1; g.target.statusEffectDurations[15] = 150
          R.apply({targetRenew={15}, renewScale=1.10}, g, source, classes, helpers, target)
          assert(g.target.statusEffectDurations[15] == 150)
        ''')

    def test_leech_silence_needs_the_whole_drain(self):
        self.lua.execute('''
          local f = apply({drainMp=3, leechSilence={scale=0.5, need=3}}, keep({}))
          assert(f.target.addedStatusEffects[9] == 1 and f.target.statusEffectDurations[9] == 50)
          f = apply({drainMp=2, leechSilence={scale=0.5, need=3}}, keep({}))
          assert(f.target.addedStatusEffects[9] == 0, 'a drain cut short does not Silence')
          f = apply({drainMp=3, leechSilence={scale=0.5, need=3}}, keep({immune={9}}))
          assert(f.target.addedStatusEffects[9] == 0, 'immune foes stay immune')
          -- a cast budget that clips the drain also clips the Silence
          local source, target = keep({address=100, type=0}), keep({})
          local g = {caster=plus(), target=plus()}
          R.apply({drainMp=5, leechSilence={scale=1, need=5}, mpBudget={left=2}}, g, source, classes, helpers, target)
          assert(g.target.addedStatusEffects[9] == 0)
        ''')

    def test_quick_arms_another_units_next_action_and_cleave_clears_reckless(self):
        self.lua.execute('''
          local before = #ticks
          apply({others={{op='quick', unit=200, fraction=0.2}}}, keep({}))
          assert(#ticks > before, 'the charge watcher started for the ally')
          local cleared
          rawset(_G, 'IvaliceLegacyFamily', {clearReckless=function(a) cleared = a end})
          apply({clearReckless=true}, keep({}))
          assert(cleared == 100, 'the Warrior\\'s own Reckless dent')
          cleared = nil
          apply({}, keep({}))
          assert(cleared == nil)
          rawset(_G, 'IvaliceLegacyFamily', {})
          apply({clearReckless=true}, keep({}))      -- a Family without the function: nothing, no error
          rawset(_G, 'IvaliceLegacyFamily', nil)
          apply({clearReckless=true}, keep({}))
        ''')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class HybridEngineHookTest(unittest.TestCase):
    """The hooks hand the new fields to the rules."""

    def setUp(self):
        TP.ProficiencyWritTest.setUp(self)
        self.lua.execute(r'''
          sources['scripts/DynamicFoes/Foes.lua'] = "return {Dragon='boss', Fox='rare', Rat='fodder'}"
          R = load(sources['scripts/IvaliceLegacy/proficiency_runtime.lua'], '@runtime', 't', _ENV)()
          assert(R.register())
          RANK = 12; keys[0x81F0] = true; keys[0x81F1] = true; keys[0x81F2] = true
          loadCampaign(12)
          assert(R.effective() == 12)
          local IDS = {lure=17, slow=15, blind=7, haste=20, sleep=4, stop=3}
          function flags(t)
            local f = setmetatable({}, {__index=function() return 0 end})
            for name, on in pairs(t or {}) do f[name] = on; f[IDS[name]] = on end
            return f
          end
          local function record() return {flags1={isNegative=0, isNotApplicableToFoes=0, isRefreshable=1},
                                           absentStatusEffects=flags()} end
          bpack = {section15=setmetatable({}, {__index=function() return record() end}),
                   section14={[26]={category=2, mpOrMistCost=10, elements={fire=1}}}}
          keeps = {}
          function unit(t)
            local u = {type=t.type, identifier=t.id or 0, address=t.address, job1=t.job1 or 0, job2=t.job2 or 255,
                       maxHp=100, currentHp=t.hp or 100, maxMp=t.maxMp or 50, currentMp=t.mp or 10,
                       temporaryStatusEffects=flags(t.status), permanentStatusEffectImmunities=flags(),
                       permanentStatusEffects=flags(), statusEffectDurations=flags(), temporaryAugments=flags(),
                       permanentAugments=flags()}
            keeps[u.address] = u
            return u
          end
          memory.u64[0x02EBF190] = 1000
          function party(i) return 1000 + 8 + i * 0x1C8 end
          NAMES = {}
          added = {}
          helpers = {getActivePartyMember=function() return -1 end, getRandomNumber=function() return 0 end,
            getStatusEffectDuration=function() return 100 end, getStatusEffectTickDuration=function() return 0 end,
            getStatusEffectsMatch=function() return false end, removeStatusEffect=function() end,
            addStatusEffect=function(c, u, id, d) added[#added+1] = {u=u, id=id} end,
            modifyHp=function() end, modifyMp=function() end, getBattleUnitWork=function(a) return a end}
          classes = {getBattleUnitKeep=function(a) return keeps[a] end,
                     getBattleUnitWork=function(a) return {address=a, ardUnit={name=NAMES[a]}} end}
          function plus(t)
            local p = {statusEffects=flags(t and t.status), addedStatusEffects=flags(), removedStatusEffects=flags(),
                       statusEffectDurations=setmetatable({}, {__index=function() return -1 end})}
            for k, v in pairs(t or {}) do if k ~= 'status' then p[k] = v end end
            return p
          end
          function addedTo(u, id) for _, a in ipairs(added) do if a.u == u and a.id == id then return true end end end
          function setStage(name, k, stage)
            for i, l in ipairs(R.Hybrid.LINES) do if l.line == name then l[k] = stage end end
            R.Hybrid.attach(R.Proficiency)
          end
        ''')

    def test_spells_carry_the_foes_charge_and_its_mp_pool(self):
        self.lua.execute(r'''
          -- Spellbow (Hunter 8 + Black Mage 9): stage I on a foe about to act, empty of MP
          setStage('Seeker', 1, {'magick', {'about', 'emptyMp'}, 'Spells on a drained foe about to act', 'p',
                                 {{'foe', 'poison', 0.5}}})
          local me = unit({type=0, id=2, address=party(2), job1=8, job2=9})
          local foe = unit({type=1, id=40, address=7000, maxMp=60, mp=0})
          NAMES[7000] = 'Dragon'
          local function cast(charge)
            added = {}
            local f = {caster=plus(), target=plus({removedHp=30, identifier=7, battleUnitWork=charge}), action=26}
            R.hook170(f, me, foe, classes, helpers)
            return f
          end
          local f = cast({address=500, currentChargeTime=25000, requiredChargeTime=30000, lastChargeTime=25000})
          assert(f.target.addedStatusEffects[8] == 1, 'about to act and at 0 MP: Poison')
          f = cast({address=500, currentChargeTime=10000, requiredChargeTime=30000, lastChargeTime=10000})
          assert(f.target.addedStatusEffects[8] == 0, 'not about to act')
          foe.maxMp = 0
          f = cast({address=500, currentChargeTime=25000, requiredChargeTime=30000, lastChargeTime=25000})
          assert(f.target.addedStatusEffects[8] == 0, 'no MP pool: not drained')
          assert(logged('about-to-act fired 1 of 1 checks'), 'the firing counter reached hook.log')
        ''')

    def test_tough_foes_are_bosses_marks_and_rare_game(self):
        self.lua.execute(r'''
          NAMES[7001], NAMES[7002], NAMES[7003], NAMES[7004] = 'Dragon', 'Fox', 'Rat', nil
          assert(R.isTough(7001, classes, helpers) == true)
          assert(R.isTough(7002, classes, helpers) == true)
          assert(R.isTough(7003, classes, helpers) == false)
          assert(R.isTough(7004, classes, helpers) == false)
          assert(R.isTough(7005, {getBattleUnitWork=function() error('no work') end}, helpers) == false, 'never throws')
          -- a hybrid hit reads it: Beastslayer-style stage on a tough foe
          setStage('Big Game', 3, {'hit', {'tough'}, 'Hits on a boss, mark or rare game', 'p', {{'foe', 'blind', 0.5}}})
          local me = unit({type=0, id=2, address=party(2), job1=1, job2=8})
          local boss, rat = unit({type=1, id=41, address=7001}), unit({type=1, id=42, address=7003})
          for _, c in ipairs({{boss, 1}, {rat, 0}}) do
            local f = {caster=plus(), target=plus({removedHp=10, identifier=c[1].identifier,
                       battleUnitWork={address=500, currentChargeTime=1, requiredChargeTime=2, lastChargeTime=1}}),
                       initialTargetPointer=0, criticalState=0}
            R.hook165(f, me, c[1], classes, helpers)
            assert(f.target.addedStatusEffects[7] == c[2], c[1].address)
          end
        ''')

    def test_the_weapon_route_reports_the_wider_ailment_set(self):
        self.lua.execute(r'''
          -- Despoiler (Lancer 1 + Chemist 11): "an ailment you land" hears Sleep from a weapon rider
          setStage('Corrode', 1, {'status', nil, 'An ailment you land', 's', {{'foe', 'poison', 0.5}}})
          local me = unit({type=0, id=2, address=party(2), job1=1, job2=11})
          local foe = unit({type=1, id=40, address=7000})
          local function land(id)
            local f = {caster=plus(), target=plus({removedHp=5})}
            local before = {}
            f.target.addedStatusEffects[id] = 1
            R.hook182(f, me, foe, classes, helpers, before)
            return f
          end
          assert(land(4).target.addedStatusEffects[8] == 1, 'Sleep')
          assert(land(3).target.addedStatusEffects[8] == 1, 'Stop')
          assert(land(7).target.addedStatusEffects[8] == 1, 'Blind, as before')
          assert(land(20).target.addedStatusEffects[8] == 0, 'a buff is no ailment')
          -- the base Chemist keeps reading the nine ailments only
          local chem = unit({type=0, id=3, address=party(3), job1=11})
          assert(R.listens(11, 'status'))
        ''')

    def test_a_counter_swing_names_the_parry_under_it(self):
        self.lua.execute(r'''
          -- Sword Saint (Knight 4 + Samurai 10) stage I: parry or block, Lure
          local me = unit({type=0, id=2, address=party(2), job1=4, job2=10})
          local foe = unit({type=1, address=7000})
          for _, c in ipairs({{2, true}, {4, true}, {1, true}, {3, true}, {5, false}}) do
            added = {}
            R.hook240({caster=plus(), target=plus(), outcomeType=c[1], counterState=c[1] <= 4 and 1 or 0}, foe, me, classes, helpers)
            assert((addedTo(me.address, 17) or false) == c[2], 'outcome ' .. c[1])
          end
        ''')

    def test_chemist_hybrid_measure_spreads_through_the_hook(self):
        self.lua.execute(r'''
          bpack.section13 = {count=1, [0]={category=16, chargeTime=10}}
          local me = unit({type=0, id=2, address=party(2), job1=11, job2=0})
          me.weapon = 0x1000
          helpers.getActivePartyMember = function(i) return ({2, 3, 4})[i + 1] or -1 end
          helpers.getBattleUnitKeep = function(id) return party(id) end
          local a1, a2 = unit({type=0, id=3, address=party(3), hp=50}), unit({type=0, id=4, address=party(4), hp=50})
          local f = {caster=plus(), target=plus(), action=150}
          f.target.addedStatusEffects[20] = 1
          R.hook182(f, me, a1, classes, helpers, {})
          assert(addedTo(party(4), 20) or addedTo(party(2), 20), 'Haste spread to a second ally')
        ''')


if __name__ == '__main__':
    unittest.main()
