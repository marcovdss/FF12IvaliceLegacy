"""Review fixes of Ivalice Legacy 0.1.45: a foe's turn ends its Staggered record (hooks 240, 181, 110 and 127, not only 165 and
170) and the record carries the foe's fingerprint; guardWrap checks eligibility before the campaign test; no document reference
in script comments; Resonance and Frenzy wording; the family version; the hybrid "ailing or Broken" triggers; Imbue II and
Runeguard II fire on Faith or their old trigger. Every change carries a control that must not change. Static: the shipped
prototypes in a synthetic battle (tests/runtime_world.py); nothing here touches a game or a save."""
import re
import sys
import unittest
from pathlib import Path

import runtime_world

try:
    from lupa.lua55 import LuaRuntime
except ImportError:  # pragma: no cover
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]


def rd(rel):
    return (ROOT / rel).read_text(encoding='utf-8')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class Review0145Tests(unittest.TestCase):
    def test_every_review_fix_and_its_control(self):
        results = []

        def check(name, cond):
            results.append((name, bool(cond)))

        FAM = rd('prototypes/family_identities.lua')
        PRO = rd('prototypes/proficiency.lua')
        PRE = r'''
        function newFamily() local f = load(FAM, '@family')(); f.reset(); return f end
        reports = {}
        NOW = 1000000
        deps = {active = function() return ACTIVE end, clock = function() return NOW end,
                category = function(u) return u.cat or CAT end, report = function(kind, ...) reports[#reports + 1] = {kind, ...} end}
        ACTIVE, CAT = true, nil
        local n = 0
        function wielder(id, extra)
          n = n + 1
          local u = {type = 0, identifier = id or 1, address = 1000 + n, weapon = 0x1001, temporaryStatusEffects = {}, permanentStatusEffects = {}}
          for k, v in pairs(extra or {}) do u[k] = v end
          return u
        end
        function foeUnit(addr, extra)
          local u = {type = 1, identifier = 9, address = addr or 900, defense = 100, maxHp = 1000, level = 10, temporaryStatusEffects = {}}
          for k, v in pairs(extra or {}) do u[k] = v end
          return u
        end
        function count(kind) local c = 0 for _, r in ipairs(reports) do if r[1] == kind then c = c + 1 end end return c end
        '''


        def sandbox():
            lua = LuaRuntime(unpack_returned_tuples=True)
            lua.globals().FAM = FAM
            lua.execute(PRE)
            return lua



        EXTRA = r'''
        SECOND = {}
        memory.getSymbol = function(name) if name == 'sa_saveMem' then return 0x50000 end end
        memory.u16 = setmetatable({}, {__index = function(_, a) return SECOND[(a - 0x50000) // 4] or 0 end})
        function wear(u, main, second) u.accessory = main; SECOND[u.identifier] = second; return u end
        local W = {}
        function hook(n, f, caster, target) W[n] = W[n] or R.wrap(n, function() end); W[n](f, caster, target, nil, classes, helpers) end
        Family = load(FAMSRC, '@family')()
        Family.reset()
        assert(Family.register(), 'family register failed in the synthetic world')
        function installFamily() rawset(_G, 'IvaliceLegacyFamily', Family); rawset(_G, 'IvaliceLegacyProficiency', R) end
        function foeAt(addr, extra)
          extra = extra or {}
          extra.type, extra.address, extra.hp, extra.maxHp = 1, addr, extra.hp or 9000, extra.maxHp or 9000
          return unit(extra)
        end
        function strike(attacker, foe, status, extra)
          local f = F({initial=foe.address, target={removedHp=50, identifier=9, work=works[foe.address], status=status or {}},
                       caster={status={}}, critical=extra and extra.critical})
          reset(); R.hook165(f, attacker, foe, classes, helpers)
          return f
        end
        function stripped(f, name)
          if f.target.removedStatusEffects[STATUS[name]] == 1 then return true end
          for _, r in ipairs(LOG.removed) do if r.id == STATUS[name] then return true end end
          return false
        end
        function lineId(name)
          for i, l in ipairs(H.LINES) do if l.line == name then return H.FIRST_ID + i - 1 end end
        end
        function stageOf(name, k) for _, l in ipairs(H.LINES) do if l.line == name then return l[k] end end end
        local function listsOp(stage, ...)
          local want = {...}
          for _, fx in ipairs(stage[5]) do for _, op in ipairs(want) do if fx[1] == op then return true end end end
          return false
        end
        function hasOp(name, k, ...) return listsOp(stageOf(name, k), ...) end
        function hasCond(name, k, cond) for _, c in ipairs(stageOf(name, k)[2] or {}) do if c == cond then return true end end return false end
        '''


        def world(rank=12):
            lua = LuaRuntime(unpack_returned_tuples=True)
            over = {'accessory_effects': (ROOT / 'prototypes/accessory_effects.lua').read_text(encoding='utf-8')}
            runtime_world.world(lua, rank=rank, overrides=over,
                                loader=lambda n: (ROOT / 'prototypes' / f'{n}.lua').read_text(encoding='utf-8'))
            lua.globals().FAMSRC = (ROOT / 'prototypes/family_identities.lua').read_text(encoding='utf-8')
            lua.execute(EXTRA)
            return lua



        # ---- M1: a foe's turn always ends its Staggered record, whichever hook it reaches; and the record has a fingerprint
        got = world().execute(r'''
          installFamily()
          PARTY_IDS = {1}
          local hero = party(1, {job1 = P.JOB.knight, weapon = 0x1001})
          local seen = {}
          local decide = P.decide
          P.decide = function(ev, s, rank) if ev == 'guard' then seen[#seen + 1] = s.attackerStaggered end return decide(ev, s, rank) end
          local n = 0
          local function spend()                       -- a foe whose push budget is spent (cap 25% of 30000 = 7500; the push left it at 12500)
            n = n + 1
            local addr = 900 + n
            local foe = foeAt(addr, {charge = 20000, required = 30000, maxHp = 9000, level = 30})
            local w = works[addr]
            R.pushAllowance(addr, w, 9999, 1111, foe)
            assert(R.isStaggered(addr, w, foe) == true, 'setup: not Staggered')
            return foe, w
          end
          local function refilled(foe, w) w.currentChargeTime = 20000; w.lastChargeTime = 20000; return R.isStaggered(foe.address, w, foe) end
          local out = {}
          -- control (the 0.1.44 behaviour): with no foe action, a refill past rec.after leaves the stale record Staggered
          do local foe, w = spend(); w.currentChargeTime = 30000; out[#out + 1] = refilled(foe, w) end                  -- [1] stays true
          -- 240: an evaded attack (skipState 1), a parried one, and one that hit
          for _, outcome in ipairs({5, 1, 0}) do
            local foe, w = spend()
            w.currentChargeTime = 30000                                              -- a real action: the charge is full
            local f = F({outcome = outcome, target = {status = {}}}); f.skipState = outcome > 0 and 1 or 0
            seen = {}
            hook(240, f, foe, hero)
            out[#out + 1] = seen[#seen] == true                                      -- the state was read before the record ended
            out[#out + 1] = refilled(foe, w)
          end
          -- 181: a foe's status or buff spell on a party member and on another foe
          for _, tgt in ipairs({hero, foeAt(990, {})}) do
            local foe, w = spend(); w.currentChargeTime = 30000
            hook(181, F({action = 60}), foe, tgt)
            out[#out + 1] = refilled(foe, w)
          end
          -- 110 and 127: a foe's heal
          for _, n2 in ipairs({110, 127}) do
            local foe, w = spend(); w.currentChargeTime = 30000
            hook(n2, F({action = 60, target = {addedHp = 200}}), foe, foeAt(991, {}))
            out[#out + 1] = refilled(foe, w)
          end
          -- the hooks that already ended it still do (165 and 170)
          do local foe, w = spend(); w.currentChargeTime = 30000; hook(165, F({target = {removedHp = 40, work = w}}), foe, hero); out[#out + 1] = refilled(foe, w) end
          do local foe, w = spend(); w.currentChargeTime = 30000; hook(170, F({target = {removedHp = 40, work = w}}), foe, hero); out[#out + 1] = refilled(foe, w) end
          -- control: a counterattack lands while the charge is still filling (at or past where the push left it): the record stays
          do
            local foe, w = spend(); w.currentChargeTime = 15000
            hook(240, F({outcome = 2, target = {status = {}}}), foe, hero)
            out[#out + 1] = R.isStaggered(foe.address, w, foe)
          end
          -- a party attacker does not end a foe's record (control: only the foe's own turn does)
          do
            local foe, w = spend(); w.currentChargeTime = 30000
            hook(181, F({action = 60}), hero, foe)
            out[#out + 1] = refilled(foe, w)
          end
          -- a foe with no readable battle work: the record is still dropped (address only, as before)
          do
            local foe, w = spend(); works[foe.address] = nil
            hook(240, F({outcome = 5, target = {status = {}}}), foe, hero)
            works[foe.address] = w; w.currentChargeTime = 20000
            out[#out + 1] = R.isStaggered(foe.address, w, foe)
          end
          return table.unpack(out)
        ''')
        check('M1 control (the instrument): without a foe action a refill past rec.after still reads as Staggered (the stale 0.1.44 state)', got[0] is True)
        check('M1: an evaded attack (240, skipState 1) reaches Hex Round with Staggered still set, then ends the record: a refill is not Staggered',
              got[1] is True and got[2] is False)
        check('M1: a parried attack (240) ends the record the same way', got[3] is True and got[4] is False)
        check('M1: an attack that hit (240, no guard event for Hex Round) ends it too', got[5] is False and got[6] is False)
        check('M1: a foe status or buff spell (181), on a party member or on another foe, ends the record', got[7] is False and got[8] is False)
        check('M1: a foe heal (110 and 127) ends the record', got[9] is False and got[10] is False)
        check('M1 control: hooks 165 and 170, which already ended it, still do', got[11] is False and got[12] is False)
        check('M1 control: a counterattack during the charge (not a real action) leaves the record alone', got[13] is True)
        check('M1 control: a party member\'s own 181 does not end a foe\'s record', got[14] is True)
        check('M1: a foe whose work cannot be read still loses its record', got[15] is False)

        got = world().execute(r'''
          installFamily()
          local function spent(addr, maxHp, level)
            local foe = foeAt(addr, {charge = 20000, required = 30000, maxHp = maxHp, level = level})
            local w = works[addr]
            R.pushAllowance(addr, w, 9999, 1111, foe)
            return foe, w
          end
          local foe, w = spent(900, 9000, 30)
          w.currentChargeTime = 20000
          local same = R.isStaggered(900, w, foe)                                  -- control: the same foe is still Staggered
          local viaTables = R.foeStaggered(900, classes, helpers)
          local again = R.pushAllowance(900, w, 5000, 2222, foe)                   -- control: the same foe has no budget left
          -- another foe at the recycled address (new maxHp, level) in a later fight
          local other = foeAt(900, {charge = 20000, required = 30000, maxHp = 4000, level = 12})
          local w2 = works[900]
          local viaTablesOther = R.foeStaggered(900, classes, helpers)           -- the Forge-table route reads the keep itself
          local staggeredOther = R.isStaggered(900, w2, other)
          local fresh = R.pushAllowance(900, w2, 5000, 3333, other)                -- a fresh budget
          -- the same address with only the level changed
          local f3, w3 = spent(910, 9000, 30)
          w3.currentChargeTime = 20000
          local lvl = R.isStaggered(910, w3, foeAt(910, {charge = 20000, required = 30000, maxHp = 9000, level = 31}))
          -- a caller without the keep (the address-only rule, as before)
          local f4, w4 = spent(920, 9000, 30)
          w4.currentChargeTime = 20000
          local noKeep = R.isStaggered(920, w4)
          -- the push helper and the apply route pass the keep: a recycled foe's push is a fresh budget there too
          local f5 = foeAt(930, {charge = 20000, required = 30000, maxHp = 9000, level = 30}); local w5 = works[930]
          R.pushFoe(w5, 930, 0.25, 1111, f5)
          local spentNow = R.isStaggered(930, w5, f5)
          local f5b = foeAt(930, {charge = 20000, required = 30000, maxHp = 5000, level = 9}); local w5b = works[930]
          local moved = select(1, R.pushFoe(w5b, 930, 0.25, 2222, f5b))
          local hero = party(1, {job1 = P.JOB.knight, weapon = 0x1001})
          local f6 = foeAt(940, {charge = 20000, required = 30000, maxHp = 9000, level = 30}); local w6 = works[940]
          R.apply({others = {{op = 'push', unit = 940, fraction = 0.25}}}, nil, hero, classes, helpers)
          local viaApply = R.isStaggered(940, w6, f6)
          local f6b = foeAt(940, {charge = 20000, required = 30000, maxHp = 5000, level = 9}); local w6b = works[940]
          local viaApplyOther = R.isStaggered(940, w6b, f6b)
          return same, viaTables, again, staggeredOther, viaTablesOther, fresh, lvl, noKeep, spentNow, moved, viaApply, viaApplyOther
        ''')
        check('M1 fingerprint control: the same foe (maxHp, level) is still Staggered and has no budget left', got[0] is True and got[1] is True and got[2] == 0)
        check('M1 fingerprint: another foe at a recycled address (other maxHp and level) is not Staggered and gets a fresh budget',
              got[3] is False and got[4] is False and got[5] == 5000)
        check('M1 fingerprint: a changed level alone is another foe', got[6] is False)
        check('M1 control: a caller without the keep keeps the address-only rule', got[7] is True)
        check('M1 fingerprint: R.pushFoe and the apply route record the fingerprint (a push on the recycled foe is a fresh budget there)',
              got[8] is True and got[9] is not None and got[10] is True and got[11] is False)

        # ---- L4: guardWrap checks the cheap eligibility before the campaign test
        got = sandbox().execute(r'''
          local M = newFamily()
          local calls = 0
          local d = {active = function() calls = calls + 1 return true end, clock = function() return NOW end,
                     category = function() return nil end, report = function() end}
          local wrapped = M.guardWrap(function(f) f.outcomeType = 3; f.skipState = 1 end, d)
          local function go(caster, target) wrapped({evadeShield = 60, evadeWeapon = 0, evadeParry = 0, outcomeType = 0, skipState = 0, counterState = 0,
                                                      caster = {statusEffects = {blind = 0}}, target = {}}, caster, target, {}, {}, {}) return calls end
          local foeOnFoe = go(foeUnit(5), foeUnit(6))
          local summonOnFoe = go(wielder(30), foeUnit(7))
          local foeOnSummon = go(foeUnit(5), wielder(31))
          local ineligible = calls
          local partyOnFoe = go(wielder(1), foeUnit(8))
          local foeOnParty = go(foeUnit(5), wielder(2))
          return ineligible, partyOnFoe, foeOnParty
        ''')
        check('L4: a foe\'s attack on a foe, and an Esper summon involved, never reach deps.active()', got[0] == 0)
        check('L4 control: a party attacker on a foe, and a foe on a party member, still reach it (one call each)', got[1] == 1 and got[2] == 2)

        # ---- L5: no lab-document reference in the shipped code comments
        LAB_REF = re.compile(r'reports/|workspace/|\b[A-Z][A-Z0-9-]{3,}\.md\b')
        hits = {}
        for p in sorted((ROOT / 'prototypes').glob('*.lua')) + sorted((ROOT / 'package/x64/scripts').rglob('*.lua')):
            m = LAB_REF.search(p.read_text(encoding='utf-8'))
            if m:
                hits[p.name] = m.group(0)
        check('L5: no prototype or package script carries a lab-document reference (' + str(hits) + ')', hits == {})

        # ---- L6, L7, R7, L1 on the shipped prototypes
        check('L7: family_identities is 0.5.3 or later (0.1.46: 0.5.4) and its ready line prints the version',
              (lambda v: (int(v.group(1)), int(v.group(2))) >= (5, 3))(re.search(r"M\.version = '0\.(\d+)\.(\d+)'", FAM)) and "'Legacy Family: identities ' .. M.version .. ' ready" in FAM)
        check('L6: Resonance I-2 says "faster"', "makes your next action 20% faster, forgiving a stray spell" in PRO)
        got = world().execute(r'''
          local function line(name) for _, l in ipairs(H.LINES) do if l.line == name then return l end end end
          local function texts(name, k) local out = {} for L = 1, 4 do out[L] = H.stageText(line(name)[k], L, H.firstLimit(name)) end return out end
          local frenzy = texts('Frenzy', 1)
          local nils, total = 0, 0
          for _, l in ipairs(H.LINES) do
            for k = 1, 3 do
              for L = 1, 4 do total = total + 1; if H.stageText(l[k], L, H.firstLimit(l.line)) == nil then nils = nils + 1 end end
            end
          end
          local imbue, rune = texts('Imbue', 2), texts('Runeguard', 2)
          local ailing, multi = {}, {}
          for _, l in ipairs(H.LINES) do
            for k = 1, 3 do
              local st = l[k]
              local isAiling, isMulti = false, false
              for _, c in ipairs(st[2] or {}) do if c == 'ailing' then isAiling = true elseif c == 'ailing2' or c == 'ailing3' then isMulti = true end end
              if isAiling then ailing[#ailing + 1] = st[3] elseif isMulti then multi[#multi + 1] = st[3] end
            end
          end
          local function c(name, cs, m) return H.cond(name, {casterStatus = cs}, m or {}, 'magick') end
          return frenzy[3], frenzy[4], nils, total, imbue[1], imbue[4], rune[1], rune[4], #ailing, table.concat(ailing, '|'), table.concat(multi, '|'),
                 c('imbuedOrFaith', {}, {imbue = 0}), c('imbuedOrFaith', {}, {imbue = 3}), c('imbuedOrFaith', {faith = true}, {imbue = 0}),
                 c('lureOrFaith', {}), c('lureOrFaith', {lure = true}), c('lureOrFaith', {faith = true}), c('lureOrFaith', {lure = true, faith = true}),
                 c('imbued', {faith = true}, {imbue = 0}), c('lured', {faith = true}), c('faithful', {lure = true}), c('imbued', {}, {imbue = 1})
        ''')
        check('L6: Frenzy I-3 and I-4 both say "the most hurt ally"', 'the most hurt ally' in got[0] and 'the most hurt ally' in got[1] and 'give an ally' not in got[1])
        check('every hybrid stage text fits the help window at every level', got[2] == 0 and got[3] > 700)
        check('R7: the Imbue II and Runeguard II texts say Faith or the old trigger, at every level shown',
              all('Faith' in x and ' or ' in x for x in (got[4], got[5], got[6], got[7])) and 'and Faith' not in got[4] + got[6])
        ail = got[9].split('|')
        check('L1: the 17 ailing stages that said "a foe with an ailment" say "ailing or Broken"; three "an ailing foe" triggers stay; two-or-more/three-or-more stages keep their text',
              got[8] == 20 and sum('ailing or Broken' in x for x in ail) == 17 and not any('with an ailment' in x for x in ail)
              and sum(x.endswith('an ailing foe') for x in ail) == 3 and 'Broken' not in got[10] and 'two or more ailments' in got[10])
        check('R7: neither trigger keeps Imbue II and Runeguard II quiet; either alone, or both, fires them', got[11] is False and got[14] is False
              and got[12] is True and got[15] is True and got[13] is True and got[16] is True and got[17] is True)
        check('R7 control: the old single conditions keep their meaning', got[18] is False and got[19] is False and got[20] is False and got[21] is True)
        check('L1: the ninja-sword Clan Primer line says who loses the dodge, not "the dodge still applies"',
              'the dodge still applies' not in FAM and "Slowed, Immobilized, Stopped or tired cannot dodge them" in FAM)

        # ---- S5 and the Clan Primer text, as the repository's patches carry them
        import json
        from ivalice_legacy import clan_primer as CP
        gp = json.loads((ROOT / 'patches/gambit_text.json').read_text(encoding='utf-8'))
        fix = [c for c in gp['fieldCorrections'] if c['row'] == 226 and c['offset'] == 8]
        check('S5: patches/gambit_text.json turns row 226 into "Foe: any ailment" (condition 0x221B -> 0x221E, the game\'s own any-ailment code)',
              len(fix) == 1 and (fix[0]['offset'], fix[0]['before'], fix[0]['after']) == (8, 0x221B, 0x221E)
              and [r['name'] for r in gp['records'] if r['row'] == 226] == ['Foe: any ailment'])
        tp = json.loads((ROOT / 'patches/families_tip.json').read_text(encoding='utf-8'))
        bodies = [t['body'] for t in CP.tips(tp)]
        check('tips: the Weapon Families text defines Broken and Staggered, fits the window, and no longer says the dodge still applies',
              'A foe is Broken for 10 seconds' in bodies[0] and "A Staggered foe's charge is pushed" in bodies[0] and 'dodge still applies' not in bodies[1]
              and all(len(CP.visible(b).split('\n')) <= CP.MAX_BODY_LINES and max(map(len, CP.visible(b).split('\n'))) <= 56 for b in bodies))


        self.assertGreaterEqual(len(results), 25)
        self.assertEqual([n for n, ok in results if not ok], [])


if __name__ == '__main__':
    unittest.main()
