"""The 66 reworked hybrid Proficiency lines: every stage attaches and fires, and the line data keeps the rework's rules.

No game needed: it loads prototypes/proficiency.lua and prototypes/hybrid_proficiency.lua under Lupa.
"""
import json
from pathlib import Path
import re
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]

# Jobs that cast: a hybrid with none of these as a parent never gives itself Faith.
MAGES = {'White Mage', 'Green Mage', 'Black Mage', 'Time Mage', 'Mystic'}
PROTECT_OK = {'Knight', 'White Mage', 'Mystic'}
SPELL_EVENTS = {'magick', 'cast', 'castFoe', 'castAlly'}
INFREQUENT = {'kill', 'dropCritical'}      # a Haste from these may be given again while held
GRANTS = ('self', 'foe', 'ally', 'party', 'other', 'otherAlly')
ROMAN = ('I', 'II', 'III')

DUMP = r'''
  local function ser(x)
    local t = type(x)
    if t == 'table' then
      local named, n = false, 0
      for k in pairs(x) do if type(k) ~= 'number' then named = true end n = n + 1 end
      local out = {}
      if named or n ~= #x then
        for k, v in pairs(x) do out[#out + 1] = string.format('%q:%s', tostring(k), ser(v)) end
        return '{' .. table.concat(out, ',') .. '}'
      end
      for i = 1, #x do out[i] = ser(x[i]) end
      return '[' .. table.concat(out, ',') .. ']'
    elseif t == 'string' then return string.format('%q', x):gsub('\\\n', '\\n')
    elseif t == 'nil' then return 'null' end
    return tostring(x)
  end
  return ser
'''


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class HybridLinesTest(unittest.TestCase):
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
                       targetStatus={}, casterStatus={}, targetMp=50, targetMaxMp=100, casterHpPct=100, casterMp=50,
                       casterMaxMp=100, selfMaxHp=1000, targetHpPct=80,
                       party={{address=100, job=job, hpPct=100, mp=50, maxMp=100, status={}},
                              {address=200, job=0, hpPct=40, mp=5, maxMp=100, status={poison=true}},
                              {address=300, job=6, hpPct=90, mp=80, maxMp=100, status={}}},
                       foes={{address=901, status={}}, {address=902, status={}}},
                       charge={current=20000, required=30000, last=20000}, roll=function() return true end}
            for k, v in pairs(t or {}) do s[k] = v end
            return s
          end
        ''')

    # ---------- reading the data ----------

    def lines(self):
        """H.LINES as plain data: [{'line', 'stages': [{'event', 'conds', 'trigger', 'number', 'effects'}]}]."""
        dump = self.lua.execute(DUMP)
        raw = json.loads(dump(self.lua.eval('H.LINES')))
        out = []
        for ln in raw:
            stages = []
            for k in range(1, 4):
                st = ln[str(k)] if isinstance(ln, dict) else ln[k - 1]
                ev, conds, trigger, number, fx = (st.get(str(i)) if isinstance(st, dict) else st[i - 1] for i in range(1, 6))
                effects = []
                for e in fx:
                    if isinstance(e, dict):
                        args = [e[str(i)] for i in range(2, 9) if str(i) in e]
                        effects.append({'op': e['1'], 'args': args, 'f': int(e.get('f', 1)), 'fresh': e.get('fresh') is True})
                    else:
                        effects.append({'op': e[0], 'args': e[1:], 'f': 1, 'fresh': False})
                stages.append({'event': ev if isinstance(ev, list) else [ev], 'conds': conds or [], 'trigger': trigger,
                               'number': number, 'effects': effects})
            out.append({'line': ln['line'], 'stages': stages})
        return out

    def names(self):
        return json.loads((ROOT/'patches/hybrid_names.json').read_text(encoding='utf-8'))['hybrids']

    # ---------- every stage attaches and fires ----------

    def test_every_stage_of_every_line_fires_on_its_own_event(self):
        # Each real stage is dropped into slot 1 beside two stages that never fire, given a snapshot that satisfies its
        # conditions (priming the memory conditions with the events they remember), and decided at 4/4: it must act.
        failures, fired = self.lua.execute(r'''
          local ORIG = {}
          for i, l in ipairs(H.LINES) do ORIG[i] = l end
          local FILLER = {'aim', nil, 'Never', 's', {{'mp', 1}}}
          local ALL_BUFFS = {protect=true, shell=true, haste=true, bravery=true, faith=true, regen=true, reflect=true}
          local AILMENTS = {poison=true, blind=true, silence=true, sap=true, disease=true, slow=true, disable=true}
          local failures, fired, serial = {}, 0, 100
          local EVENT = {hit='hit', magick='magick', heal='heal', guard='guard', counter='guard', struck='struck',
                         ally='ally', status='status', cast='cast', castFoe='cast', castAlly='cast'}

          local function snapshot(stage, ev)
            serial = serial + 1
            local ts, cs = {}, {}
            local t = {targetId=serial, kill=false}
            for _, c in ipairs(stage[2] or {}) do
              if c == 'buffed' then for k in pairs(ALL_BUFFS) do ts[k] = true end
              elseif c == 'ailing' or c == 'allyAiling' then ts.poison = true
              elseif c == 'ailing2' then ts.poison, ts.blind = true, true
              elseif c == 'ailing3' then ts.poison, ts.blind, ts.silence = true, true, true
              elseif c == 'slowed' or c == 'held' or c == 'detonable' then ts.slow = true
              elseif c == 'stopped' then ts.stop = true
              elseif c == 'immobilized' then ts.immobilize = true
              elseif c == 'oiled' then ts.oil = true
              elseif c == 'diseased' then ts.disease = true
              elseif c == 'blinded' then ts.blind = true
              elseif c == 'disabled' then ts.disable = true
              elseif c == 'sleeping' then ts.sleep = true
              elseif c == 'foeFaith' then ts.faith = true
              elseif c == 'allyWarded' then ts.bubble = true
              elseif c == 'allyHasted' then ts.haste = true
              elseif c == 'tough' then t.tough, t.attackerTough = true, true
              elseif c == 'emptyMp' then t.targetMp, t.attackerMp, t.attackerMaxMp = 0, 0, 100
              elseif c == 'wounded' or c == 'allyCritical' then t.targetHpPct = 20
              elseif c == 'about' then t.charge = {current=25000, required=30000, last=25000}
              elseif c == 'hasted' then cs.haste = true
              elseif c == 'lured' then cs.lure = true
              elseif c == 'floating' then cs.float = true
              elseif c == 'invisible' then cs.invisible = true
              elseif c == 'low50' then t.casterHpPct = 40
              elseif c == 'critical' then t.casterHpPct = 20
              elseif c == 'dropCritical' then t.dropCritical = true
              elseif c == 'fullMp' then t.casterMp = 100
              elseif c == 'overflow' then t.overflow = 50
              elseif c == 'item' then t.item = true
              elseif c == 'selfTarget' then t.target = 100
              elseif c == 'kill' then t.kill = true
              elseif c == 'far' then t.distance = 20
              elseif c == 'near' then t.distance = 5
              elseif c == 'slowCast' then t.landed = {15}
              elseif c == 'hasteCast' then t.landed = {20}
              elseif c == 'slowOrHaste' then t.landed = {15}
              end
            end
            t.targetStatus, t.attackerStatus, t.casterStatus = ts, ts, cs
            t.attacker = 901
            t.element, t.now = 1, 9000000000
            if ev == 'heal' then t.foe, t.target, t.healAmount, t.overflow = false, t.target == 100 and 100 or 200, 300, t.overflow or 0 end
            if ev == 'guard' then t.outcome = 'parry' end
            if ev == 'counter' then t.outcome, t.guardOutcome = 'counter', 'parry' end
            if ev == 'ally' then t.ally = 200 end
            if ev == 'status' then t.landed = t.landed or {8} end
            if ev == 'cast' or ev == 'castFoe' then t.landed, t.allyTarget, t.action, t.now = t.landed or {15}, false, 60 + serial, t.now end
            if ev == 'castAlly' then t.landed, t.allyTarget, t.target, t.action, t.now = t.landed or {20}, true, 200, 60 + serial, t.now end
            return t
          end

          -- Events that make a memory condition true, played before the stage's own event.
          local function prime(id, stage, ev, t)
            local clock = 0
            local function play(e, extra)
              clock = clock + 1000000000     -- far apart: no two primes count as one cast
              local s = S(id, {targetId=t.targetId, element=1, foe=true, target=900, healAmount=300, overflow=0, attacker=901,
                               outcome=e == 'guard' and 'parry' or nil, now=clock})
              if e == 'heal' then s.foe, s.target = false, 200 end
              if e == 'counterRoll' then e, s.outcome, s.guardOutcome = 'guard', 'counter', 'parry' end
              for k, v in pairs(extra or {}) do s[k] = v end
              P.decide(e, s, 4)
            end
            for _, c in ipairs(stage[2] or {}) do
              if c == 'afterHeal' then play('heal')
              elseif c == 'afterMagick' or c == 'imbued' then play('magick')
              elseif c == 'afterHit' then play('hit', {targetId=t.targetId + 5000})
              elseif c == 'afterGuard' then play('guard')
              elseif c == 'afterCounter' then play('counterRoll')
              elseif c == 'chain2' then play('hit')
              elseif c == 'chain3' then play('hit'); play('hit')
              elseif c == 'spellTarget' then play('magick')
              elseif c == 'sameElement' then play('magick')
              elseif c == 'newElement' then play('magick'); t.element = 2
              elseif c == 'alternate3' then
                if ev == 'magick' then play('heal'); play('magick', {targetId=t.targetId + 1}); play('heal')
                else play('magick'); play('heal'); play('magick', {targetId=t.targetId + 1}) end
              end
            end
          end

          for i = 1, #ORIG do
            for k = 1, 3 do
              local stage = ORIG[i][k]
              local events = type(stage[1]) == 'table' and stage[1] or {stage[1]}
              for _, ev in ipairs(events) do
                H.LINES[1] = {line = 'Test', stage, FILLER, FILLER}
                H.attach(P)
                P.reset()
                local id = H.FIRST_ID
                local t = snapshot(stage, ev)
                prime(id, stage, ev, t)
                local a = P.decide(EVENT[ev], S(id, t), 4)
                if a == nil then failures[#failures + 1] = ORIG[i].line .. ' ' .. k .. ' ' .. ev else fired = fired + 1 end
              end
            end
          end
          return table.concat(failures, '; '), fired
        ''')
        self.assertEqual(failures, '', 'stages that did not act at 4/4 with their conditions met')
        self.assertEqual(fired, 199)     # 198 stages, and Doublecast III listens to two events

    def test_loading_attaches_66_lines_of_3_stages_with_distinct_names(self):
        lines = self.lines()
        self.assertEqual(len(lines), 66)
        self.assertEqual(len({ln['line'] for ln in lines}), 66)
        for ln, h in zip(lines, self.names()):
            self.assertEqual(len(ln['stages']), 3, h['name'])
        self.assertEqual(self.lua.eval('H.COUNT'), 66)
        for id in range(12, 78):
            self.assertTrue(self.lua.eval(f'P.GEAR[{id}] ~= nil and P.GEAR[{id}].line ~= nil'), id)

    # ---------- the rework's rules (decisions 10-13, Shell) ----------

    def test_no_stage_grants_libra(self):
        for ln in self.lines():
            for st in ln['stages']:
                for fx in st['effects']:
                    if fx['op'] in GRANTS:
                        self.assertNotEqual(fx['args'][0], 'libra', ln['line'])

    def test_protect_is_a_knight_white_mage_or_mystic_signature(self):
        for ln, h in zip(self.lines(), self.names()):
            parents = set(h['parents'])
            stages, party = set(), False
            for k, st in enumerate(ln['stages']):
                for fx in st['effects']:
                    if fx['op'] in GRANTS and fx['args'][0] == 'protect':
                        stages.add(k)
                        party = party or fx['op'] == 'party'
            if stages:
                self.assertTrue(parents & PROTECT_OK, f"{ln['line']}: Protect without a Knight, White Mage or Mystic parent")
            self.assertLessEqual(len(stages), 1, f"{ln['line']}: Protect in more than one stage")
            if party:
                self.assertIn('Knight', parents, f"{ln['line']}: party Protect needs a Knight")

    def test_shell_in_one_stage_and_party_shell_only_at_stage_three(self):
        for ln in self.lines():
            stages = set()
            for k, st in enumerate(ln['stages']):
                for fx in st['effects']:
                    if fx['op'] in GRANTS and fx['args'][0] == 'shell':
                        stages.add(k)
                        if fx['op'] == 'party':
                            self.assertEqual(k, 2, f"{ln['line']}: party Shell before stage III")
            self.assertLessEqual(len(stages), 1, f"{ln['line']}: Shell in more than one stage")

    def test_at_most_one_party_wide_effect_and_it_sits_at_stage_three(self):
        for ln in self.lines():
            wide = [(k, fx['op']) for k, st in enumerate(ln['stages']) for fx in st['effects']
                    if fx['op'] in ('party', 'partyHealPct')]
            self.assertLessEqual(len(wide), 1, ln['line'])
            for k, _ in wide:
                self.assertEqual(k, 2, f"{ln['line']}: party-wide effect before stage III")

    def test_no_bravery_on_spell_stages_and_no_self_faith_without_a_caster_parent(self):
        for ln, h in zip(self.lines(), self.names()):
            for k, st in enumerate(ln['stages']):
                for fx in st['effects']:
                    if fx['op'] == 'self' and fx['args'][0] == 'bravery':
                        self.assertFalse(set(st['event']) & SPELL_EVENTS, f"{ln['line']} {ROMAN[k]}: Bravery on a spell stage")
                    if fx['op'] == 'self' and fx['args'][0] == 'faith':
                        self.assertTrue(set(h['parents']) & MAGES, f"{ln['line']} {ROMAN[k]}: self Faith on a non-caster")

    def test_a_frequent_self_haste_only_applies_when_not_held(self):
        for ln in self.lines():
            for k, st in enumerate(ln['stages']):
                if set(st['conds']) & INFREQUENT:
                    continue
                for fx in st['effects']:
                    if fx['op'] == 'self' and fx['args'][0] == 'haste':
                        self.assertTrue(fx['fresh'], f"{ln['line']} {ROMAN[k]}: self Haste without `fresh`")

    def test_no_two_stages_share_a_trigger_and_first_effect(self):
        seen = {}
        for ln in self.lines():
            for k, st in enumerate(ln['stages']):
                key = (tuple(sorted(st['event'])), tuple(sorted(st['conds'])), st['effects'][0]['op'],
                       json.dumps(st['effects'][0]['args']))
                self.assertNotIn(key, seen, f"{ln['line']} {ROMAN[k]} repeats {seen.get(key)}")
                seen[key] = f"{ln['line']} {ROMAN[k]}"

    def test_texts_name_protect_and_shell_only_where_the_stage_uses_them_and_never_libra(self):
        lines = self.lines()
        for i, ln in enumerate(lines):
            for k, st in enumerate(ln['stages']):
                for L in range(1, 5):
                    text = self.lua.eval(f'P.GEAR[{12 + i}][{k + 1}][{L}]')
                    self.assertIsInstance(text, str, f"{ln['line']} {ROMAN[k]} {L}/4")
                    self.assertNotIn('libra', text.lower(), f"{ln['line']} {ROMAN[k]} {L}/4")
                    used = set()
                    for fx in st['effects']:
                        if fx['f'] <= L:
                            used.update(a for a in re.findall(r"[a-z]+", json.dumps(fx['args'])))
                    for status in ('protect', 'shell'):
                        self.assertEqual(status.capitalize() in text, status in used,
                                         f"{ln['line']} {ROMAN[k]} {L}/4: {text!r}")

    def test_stage_texts_that_carry_their_own_wording_still_name_every_effect(self):
        # Three levels in two stages are worded by hand because their four effects do not fit the help window.
        stages = self.lua.execute(r'''
          local found = {}
          for i, line in ipairs(H.LINES) do
            for k = 1, 3 do
              if line[k].text then
                for L, text in pairs(line[k].text) do found[#found + 1] = {i, k, L, text} end
              end
            end
          end
          return found
        ''')
        entries = [tuple(row.values()) for row in stages.values()]
        self.assertGreaterEqual(len(entries), 3)
        lines = self.lines()
        NAME = {'haste': 'Haste', 'shell': 'Shell', 'slow': 'Slow', 'poison': 'Poison', 'protect': 'Protect'}
        for i, k, L, text in entries:
            st = lines[i - 1]['stages'][k - 1]
            self.assertLessEqual(len(text), 160)
            for fx in st['effects']:
                if fx['f'] > L:
                    continue
                if fx['op'] in GRANTS and fx['args'][0] in NAME:
                    self.assertIn(NAME[fx['args'][0]].lower(), text.lower(), text)
                if fx['op'] == 'stopChance':
                    self.assertIn('Stop', text)
                if fx['op'] == 'otherCopy':
                    self.assertIn('ailment', text)
                if fx['op'] == 'allyHeal':
                    self.assertIn('heal', text)
                if fx['op'] == 'push':
                    self.assertIn('push', text)

    # ---------- names ----------

    def test_two_more_renames_by_pair(self):
        by_pair = {tuple(h['pair']): h['name'] for h in self.names()}
        self.assertEqual(by_pair[(9, 11)], 'Detonator')      # Chemist + Black Mage, was Grenadier
        self.assertEqual(by_pair[(1, 3)], 'Magebreaker')     # Lancer + Green Mage, was Breaker
        self.assertNotIn('Grenadier', by_pair.values())
        self.assertNotIn('Breaker', by_pair.values())
        boards = json.loads((ROOT/'patches/hybrid_boards.json').read_text(encoding='utf-8'))['boards']
        board_names = {tuple(b['pair']): b['name'] for b in boards}
        self.assertEqual(board_names, by_pair)
        source = (ROOT/'prototypes/hybrid_proficiency.lua').read_text(encoding='utf-8')
        self.assertIn('Detonator (Chemist + Black Mage)', source)
        self.assertIn('Magebreaker (Lancer + Green Mage)', source)
        self.assertNotIn('Grenadier', source)


if __name__ == '__main__':
    unittest.main()
