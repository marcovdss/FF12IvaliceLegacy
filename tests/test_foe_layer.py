"""The enemy layer: package/x64/scripts/IvaliceLegacyFoes.lua and IvaliceLegacy/{foe_layer,foe_crown,story_bosses,foe_tint}.lua.

- Lua (Lupa), against a stand-in for Instanced Foes and the game's memory (foe_layer_support.py): the settle pass and
  its ordering against a Dynamic Bosses listener that runs before or after ours, crown detection and the offence
  re-write, the guarded AI spell insert, the story-boss crown, King threat, tint and size ranges, and that Dynamic
  Bosses' settings are honoured (nothing is rolled twice).
- Game executable (when IVALICE_LEGACY_GAME is set): every address the layer reads is checked against the bytes of the
  pinned FFXII_TZA.exe (static; nothing is launched).
"""
import os
import re
import struct
import unittest
from pathlib import Path

import foe_layer_support as S

ROOT = S.ROOT
DYNAMIC = 'scripts/config/DynamicFoes/modMenu.json'
SHIPPED_JSON = ROOT / 'package/x64/scripts/config/DynamicFoes/modMenu.json'
PARTY = {'maxHp': 6000, 'maxMp': 300, 'strength': 30, 'magickPower': 28, 'vitality': 35, 'speed': 20,
         'attackPower': 40, 'defense': 30, 'magickResist': 25, 'level': 30, 'count': 4}
SETTINGS = {'randomizeCommons': 0, 'ignoreBosses': 1, 'ignoreMarks': 1, 'ignoreRareGames': 1, 'ignoreAllies': 1}


import itertools
SEEDS = itertools.count(1)

# The tests do not depend on what the shipped crown_table.lua holds: they run on this fixed table (Firemane's seed entry).
# Pass sources={} to get the shipped file.
FIXED_CROWN_TABLE = (
    "return {schema = 'crown-table/1', entries = {[16876] = {label = 'Firemane', kind = 0, spell = true, statuses = {}, "
    "mirror = {strength = 1, magickPower = 1, attackPower = 1, vitality = 1, speed = 1}, hpTimes = 2, mpFloor = 400, "
    "defenceLow = 1, defenceHigh = 1}}}")


def runtime(settings=SETTINGS, party=PARTY, location=100, towns=(), story=None, register=True, sources=None):
    if sources is None:
        sources = {'crown_table': FIXED_CROWN_TABLE}
    rt = S.lua(sources)
    rt.execute('math.randomseed(%d)' % next(SEEDS))
    g = rt.globals()
    if settings is not None:
        g.files[DYNAMIC] = rt.table_from(settings)
    if party is not None:
        g.foes.partyStats = rt.table_from(party)
    S.set_world(rt, location, towns, story)
    rt.execute("L = loadfile('scripts/IvaliceLegacy/foe_layer.lua', 't', _ENV)()")
    if register:
        rt.execute('L.register()')
    return rt


def settle(rt):
    """Run the settle pass until it has nothing more to do (a half-crowned foe is put back once)."""
    for _ in range(4):
        if rt.eval('#timers') == 0:
            break
        rt.execute('runTimers()')


def new_foe(rt, **kw):
    """A foe built from keyword options (see makeFoe); lists become Lua tables."""
    o = {}
    for key, value in kw.items():
        if isinstance(value, dict):
            value = rt.table_from(value)
        elif isinstance(value, (list, tuple)):
            value = rt.table_from(list(value))
        o[key] = value
    return rt.globals().makeFoe(rt.table_from(o))


def common(rt, **kw):
    kw.setdefault('name', 16447)
    return new_foe(rt, **kw)


def spawn_and_settle(rt, foe, reason=1):
    rt.globals().spawn(foe, reason)
    settle(rt)


def seq(rt, items):
    return rt.table_from(list(items))


def crown(rt, foe, element='fire'):
    """What a crowned common looks like once Dynamic Bosses has crowned it."""
    foe.abilities.statuses.add(seq(rt, ['protect', 'shell', 'bravery', 'faith']))
    if element:
        foe['class'].affinityAbsorb.add(element)


class Layer(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if S.LuaRuntime is None:
            raise unittest.SkipTest('lupa is not installed')

    def test_files_compile_and_loader_registers(self):
        rt = S.lua()
        g = rt.globals()
        g.memory.u32[S.LOCATION] = 100
        S.set_world(rt)
        g.foes.partyStats = rt.table_from(PARTY)
        g.files[DYNAMIC] = rt.table_from(SETTINGS)
        entry = (S.SCRIPTS / 'IvaliceLegacyFoes.lua').read_text(encoding='utf-8')
        rt.execute(entry)
        text = '\n'.join(g.printed.values())
        self.assertIn('Legacy foes: ready', text)
        self.assertEqual(len(g.listeners), 1)
        self.assertEqual(len(g.menus), 1)

    def test_shipped_settings_file(self):
        import json
        data = json.loads(SHIPPED_JSON.read_text(encoding='utf-8'))
        self.assertEqual((data['randomizeCommons'], data['ignoreBosses'], data['ignoreMarks'], data['ignoreRareGames']),
                         (0, 1, 1, 1))
        # the user's other values are kept
        self.assertEqual((data['superChance'], data['superExp'], data['superHp'], data['superInTrials'],
                          data['superLoot'], data['superLp'], data['superReturns'], data['ignoreAllies']),
                         (20, 3, 0, 1, 0, 3, 0, 1))
        self.assertEqual(len(data), 13)

    # ------------------------------------------------------------------ ordering, claims
    def test_listener_order_does_not_matter(self):
        for dynamic_first in (True, False):
            rt = runtime()
            g = rt.globals()
            foe = common(rt, stats={'maxHp': 1000, 'maxMp': 50, 'strength': 20, 'magickPower': 12, 'vitality': 10, 'speed': 10,
                                    'defense': 10, 'magickResist': 10, 'attackPower': 9, 'licensePoints': 10})
            rt.execute('''
              function dynamicListener(foe, reason)
                foe.abilities.statuses.add({'protect', 'shell', 'bravery', 'faith'})
                foe['class'].affinityAbsorb.add('fire')
                foe.defaultStats.strength = 27   -- the crown's own, smaller offence
                foe.defaultStats.magickPower = 25
                foe.defaultStats.attackPower = 31
              end''')
            if dynamic_first:
                rt.execute('table.insert(listeners, 1, dynamicListener)')
            else:
                rt.execute('table.insert(listeners, dynamicListener)')
            g.spawn(foe, 1)
            self.assertEqual(len(g.timers), 1)        # ours queued exactly one settle pass
            settle(rt)
            want = round(30 * 1.25), round(28 * 1.25), round(40 * 1.25)
            stats = foe.defaultStats
            self.assertEqual((stats.strength, stats.magickPower, stats.attackPower), want, dynamic_first)

    def test_one_claim_per_life_and_reasons(self):
        rt = runtime()
        g = rt.globals()
        foe = common(rt, lifeId=5)
        g.spawn(foe, 2)                    # RESPAWN: same unit, nothing new
        self.assertEqual(len(g.timers), 0)
        g.spawn(foe, 1)
        g.spawn(foe, 3)                    # the same life queued twice is handled once
        self.assertEqual(len(g.timers), 1)
        settle(rt)
        self.assertEqual(g.refreshes, 1)
        g.spawn(foe, 3)
        settle(rt)
        self.assertEqual(g.refreshes, 1)   # the life is done
        other = common(rt, lifeId=6)
        g.claimed[6] = True                # already ours in the foe's own user data
        spawn_and_settle(rt, other)
        self.assertEqual(g.refreshes, 1)

    def test_foe_that_left_before_the_pass_is_skipped(self):
        rt = runtime()
        g = rt.globals()
        foe = common(rt)
        g.spawn(foe, 1)
        g.foes.present[foe.lifeId] = False
        settle(rt)
        self.assertEqual(foe.defaultStats.maxHp, 1000)

    def test_trial_mode_is_untouched(self):
        # Marco, 2026-11-17: Trial Mode is not touched yet; an unreadable location fails closed too.
        for location in (1155, 1200, 1311):
            rt = runtime(location=location)
            foe = common(rt)
            spawn_and_settle(rt, foe)
            self.assertEqual((foe.defaultStats.maxHp, foe.unit.sizeX), (1000, 100), location)
            self.assertEqual(rt.eval('L.process')(foe, rt.eval('L.currentContext()')), 'trial')
        rt = runtime()
        rt.execute('memory.s32 = setmetatable({}, {__index = function() return nil end})')
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.maxHp, foe.unit.sizeX), (1000, 100))
        for location in (1154, 1312):
            rt = runtime(location=location)
            self.assertFalse(rt.eval('L.currentContext()').trial, location)

    def test_town_and_ally_are_untouched(self):
        rt = runtime(towns=(100,))
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.maxHp, foe.unit.sizeX), (1000, 100))
        rt = runtime()
        ally = common(rt, isFoe=0)
        spawn_and_settle(rt, ally)
        self.assertEqual(ally.defaultStats.maxHp, 1000)

    # ------------------------------------------------------------------ crowned commons
    def test_crown_detection_needs_all_four(self):
        rt = runtime()
        C = rt.eval("L.moduleLoader('foe_crown')")
        foe = common(rt)
        self.assertFalse(C.crownedByDynamic(foe))
        foe.abilities.statuses.add(seq(rt, ['protect', 'shell', 'bravery']))
        self.assertFalse(C.crownedByDynamic(foe))
        foe.abilities.statuses.add('faith')
        self.assertTrue(C.crownedByDynamic(foe))

    def test_crowned_common_offence_is_rewritten_not_floored(self):
        rt = runtime()
        foe = common(rt, stats={'maxHp': 900, 'maxMp': 50, 'strength': 90, 'magickPower': 5, 'vitality': 10, 'speed': 10,
                                'defense': 10, 'magickResist': 10, 'attackPower': 99, 'licensePoints': 10})
        crown(rt, foe, None)
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertEqual((s.strength, s.magickPower, s.attackPower), (round(30 * 1.25), round(28 * 1.25), 50))
        self.assertEqual(s.maxHp, 900)               # nothing else is rolled on a crowned foe
        self.assertEqual(foe.unit.sizeX, 100)
        self.assertEqual(foe.color.r, 1)             # and it is never tinted

    def test_crowned_offence_caps_at_99(self):
        rt = runtime(party=dict(PARTY, strength=90, magickPower=90, attackPower=90))
        foe = common(rt)
        crown(rt, foe, None)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.strength, foe.defaultStats.attackPower), (99, 99))

    # ------------------------------------------------------------------ Battle Logic
    HOSTILE = (0x0010, 100, 0x0840, [(1, 0x0300, 0)])       # an action at the Foe with a condition, no flag test
    GUARDED = (0x0011, 100, 0x0800, [(4, 0x3703, 31), (1, 0x0300, 0)])  # one-shot rule: Self, AI flag 31 disabled
    FLAG_TEST = (0x0012, 100, 0, [(1, 0x3600, 3)])
    IDLE = (0x4021, 1, 0, [])
    JUMP = (0x4117, 100, 0, [])

    def native(self, rt, groups=None):
        groups = groups or [[self.JUMP, self.IDLE], [self.GUARDED, self.HOSTILE, self.HOSTILE], [self.FLAG_TEST],
                            [self.HOSTILE]]
        raw = S.script(groups)
        (address,) = S.install_bank(rt, [raw])
        return raw, address

    def test_insert_goes_before_unguarded_hostile_rules_only(self):
        rt = runtime()
        raw, address = self.native(rt)
        C = rt.eval("L.moduleLoader('foe_crown')")
        foe = common(rt, links=[0, 0, 0, 0])
        a, bytes_ = C.nativeScript(foe, 1)
        self.assertEqual(a, address)
        built, added = C.insertSpell(bytes_, 23, 30, 4)
        built = bytes(built.values())
        self.assertEqual(added, 2)                   # group 1 and group 3 (group 2 holds only a flag test)
        self.assertEqual(len(built), len(raw) + 2 * 24)
        counts, rules = S.parse_rules(built)
        self.assertEqual(counts[:4], [2, 4, 1, 2])
        group1 = [r for r in rules if r[0] == 1]
        self.assertEqual([r[1] for r in group1], [0x0011, 23, 0x0010, 0x0010])   # the guarded rule keeps its place
        spell = group1[1]
        self.assertEqual((spell[2], spell[3]), (30, 0x0040))     # chance 30; no-consecutive-use cleared, the rest kept
        self.assertEqual(spell[4], S.parse_rules(raw)[1][3][4])                   # the hostile rule's cases, verbatim
        self.assertEqual(sum(1 for r in rules if r[1] == 0x0011), 1)              # the guarded rule is never copied
        self.assertEqual(sum(1 for r in rules if r[1] == 0x0012), 1)
        self.assertNotIn(23, [r[1] for r in rules if r[0] == 2])

    def test_insert_clears_no_consecutive_use(self):
        rt = runtime()
        C = rt.eval("L.moduleLoader('foe_crown')")
        hostile = (0x0010, 100, 0x0800 | 0x0800 << 0 | (0x08 << 8), [(1, 0x0300, 0)])
        raw = S.script([[hostile]])
        built, added = C.insertSpell(rt.table_from(list(raw)), 18, 30, 4)
        self.assertEqual(added, 1)
        rules = S.parse_rules(bytes(built.values()))[1]
        self.assertEqual(rules[0][3] & (0x08 << 8), 0)
        self.assertEqual(rules[1][3] & (0x08 << 8), 0x08 << 8)

    def test_no_hostile_rule_means_no_copy(self):
        rt = runtime()
        C = rt.eval("L.moduleLoader('foe_crown')")
        raw = S.script([[self.JUMP, self.IDLE], [self.GUARDED], [self.FLAG_TEST]])
        built, added = C.insertSpell(rt.table_from(list(raw)), 18, 30, 4)
        self.assertEqual(added, 0)

    def test_native_script_is_refused_when_it_does_not_hold(self):
        rt = runtime()
        raw, address = self.native(rt)
        C = rt.eval("L.moduleLoader('foe_crown')")
        foe = common(rt, links=[5, 0, 0, 0])
        a, why = C.nativeScript(foe, 1)
        self.assertIsNone(a)
        self.assertIn('outside the bank', why)
        rt.globals().memory.u64[S.BANK_PTR] = 0
        foe = common(rt, links=[0, 0, 0, 0])
        a, why = C.nativeScript(foe, 1)
        self.assertIsNone(a)
        self.assertEqual(why, 'no Battle Logic bank')

    def test_crowned_common_ai_is_repointed_with_the_guard(self):
        rt = runtime()
        raw, address = self.native(rt)
        g = rt.globals()
        dynamic_copy = 0x60000000
        foe = common(rt, links=[0, 0, 0, 0], slots=[dynamic_copy, 0, 0, 0], absorb=['fire'])
        crown(rt, foe, None)
        g.memory.u64[foe.buw + S.ACTIVE_SCRIPT] = dynamic_copy       # what the spawn already ran
        spawn_and_settle(rt, foe)
        slot = foe.battleLogicSlots.slots[1]
        self.assertNotIn(slot, (0, dynamic_copy, address))
        built = S.read_bytes(rt, slot, len(raw) + 48)
        counts, rules = S.parse_rules(built)
        self.assertEqual(sum(counts), sum(S.parse_rules(raw)[0]) + 2)
        self.assertEqual(sum(1 for r in rules if r[1] == 23), 2)     # Fira: party level 30 is tier 2
        self.assertEqual(sum(1 for r in rules if r[1] == 0x0011), 1)
        self.assertEqual(g.memory.u64[foe.buw + S.ACTIVE_SCRIPT], slot)   # the running script followed
        self.assertEqual([foe.battleLogicSlots.slots[i] for i in (2, 3, 4)], [slot] * 3)     # one shared copy of the same script

    def test_crowned_without_a_readable_element_goes_back_to_native(self):
        rt = runtime()
        raw, address = self.native(rt)
        g = rt.globals()
        copy = 0x60000000
        foe = common(rt, slots=[copy, 0, 0, 0])
        crown(rt, foe, None)                                           # no single absorbed element
        g.memory.u64[foe.buw + S.ACTIVE_SCRIPT] = copy
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.battleLogicSlots.slots[1], 0)
        self.assertEqual(g.memory.u64[foe.buw + S.ACTIVE_SCRIPT], address)

    def test_a_running_script_that_is_not_the_replaced_one_is_left(self):
        rt = runtime()
        raw, address = self.native(rt)
        g = rt.globals()
        foe = common(rt, slots=[0x60000000, 0, 0, 0], absorb=['ice'])
        crown(rt, foe, None)
        g.memory.u64[foe.buw + S.ACTIVE_SCRIPT] = 0x61000000
        spawn_and_settle(rt, foe)
        self.assertEqual(g.memory.u64[foe.buw + S.ACTIVE_SCRIPT], 0x61000000)

    def test_copies_are_shared_and_the_cap_holds(self):
        rt = runtime()
        raw, address = self.native(rt)
        C = rt.eval("L.moduleLoader('foe_crown')")
        foe = common(rt)
        a, bytes_ = C.nativeScript(foe, 1)
        first = C.scriptWithSpell(a, bytes_, 18)
        second = C.scriptWithSpell(a, bytes_, 18)
        self.assertEqual(first, second)
        self.assertEqual(C.buildCount, 1)
        C.buildCount = C.MAX_BUILDS
        self.assertIsNone(C.scriptWithSpell(a, bytes_, 19))

    # ------------------------------------------------------------------ story bosses
    def story_foe(self, rt, **kw):
        kw.setdefault('name', 16402)
        kw.setdefault('isBoss', 1)
        kw.setdefault('stats', {'maxHp': 20000, 'maxMp': 50, 'strength': 40, 'magickPower': 20, 'vitality': 30, 'speed': 25,
                                'defense': 28, 'magickResist': 22, 'attackPower': 35, 'licensePoints': 10})
        return new_foe(rt, **kw)

    def test_story_boss_crown_fields(self):
        rt = runtime()
        g = rt.globals()
        raw, address = self.native(rt)
        rt.execute('L.rng.uniform = function(low, high) return high end; L.rng.int = function(low, high) return low end')
        foe = self.story_foe(rt, drops=[0xFFFF, 0x3001, 0xFFFF, 0xFFFF, 0xFFFF], steals=[0x2001, 0x2002, 0x2003])
        spawn_and_settle(rt, foe)
        s, a = foe.defaultStats, foe.additiveStats
        self.assertEqual(s.maxHp, 40000)                      # own x 2 x Normal
        self.assertEqual(a.maxHp, 200)
        self.assertEqual(s.defense, 28)                       # a crowned boss does not roll its Defence (D-1)
        self.assertEqual(a.defense, 1)
        self.assertEqual(s.strength, 40)                      # own 40 is above 1.25 x 30 = 38: kept
        self.assertEqual(s.magickPower, round(28 * 1.25))     # raised to 1.25 x party
        self.assertEqual(s.attackPower, 50)
        self.assertEqual((s.vitality, s.speed), (35, 25))      # raised to party Vit, own Speed above party's
        self.assertEqual(s.magickResist, 22)                  # its own
        self.assertEqual(s.maxMp, 400)
        self.assertEqual(s.licensePoints, 30)
        self.assertEqual((foe.keep.currentHp, foe.keep.currentMp), (40000, 400))
        self.assertTrue(foe.abilities.statuses.has(seq(rt, ['bravery', 'faith'])))
        self.assertFalse(foe.abilities.statuses.has('protect'))
        self.assertTrue(foe.abilities.immunities.has(seq(rt, ['ko', 'stone', 'petrify', 'doom'])))
        self.assertEqual(foe.efx, 8)
        self.assertEqual(len(foe.names), 1)
        self.assertTrue(foe.names[1].endswith(' {species}'))
        self.assertEqual(foe.unit.sizeX, 100)                 # a boss keeps its size
        self.assertTrue(foe.abilities.alwaysDrops.has('drop5'))   # the rarest steal took the last empty slot
        self.assertEqual(foe.unit.drops[5], 0x2003)
        self.assertEqual(foe.unit.drops[2], 0x3001)           # nothing it already dropped is lost
        self.assertNotEqual(foe.battleLogicSlots.slots[1], 0)

    def test_story_boss_health_option_and_second_run(self):
        for health, times in ((0, 2), (1, 3), (2, 4)):
            rt = runtime()
            foe = self.story_foe(rt)
            rt.execute('L.settings.storyHp = %d' % health)
            spawn_and_settle(rt, foe)
            self.assertEqual(foe.defaultStats.maxHp, 20000 * times)

    def test_story_boss_element_is_never_a_native_weakness(self):
        for weak in (['fire'], ['fire', 'ice', 'wind'], ELEMENTS_ALL):
            seen = set()
            for _ in range(60):
                rt = runtime()
                foe = self.story_foe(rt, weak=list(weak))
                spawn_and_settle(rt, foe)
                absorbed = list(foe['class'].affinityAbsorb.list().values())
                self.assertLessEqual(len(absorbed), 1)
                seen.update(S.ELEMENTS[i] for i in absorbed)
                if absorbed:
                    self.assertNotIn(S.ELEMENTS[absorbed[0]], weak)
            self.assertFalse(seen & set(weak))

    def test_story_boss_gates(self):
        # Trial Mode, unflagged unit, ally, not listed, setting off, Dynamic Bosses rolling bosses, unreadable settings
        cases = {
            'trial': dict(location=1200),
            'off': dict(setting='L.settings.crownStoryBosses = 0'),
            'dynamic bosses rolls bosses': dict(settings=dict(SETTINGS, ignoreBosses=0)),
            'settings unreadable': dict(settings=None),
        }
        for label, c in cases.items():
            rt = runtime(settings=c.get('settings', SETTINGS), location=c.get('location', 100))
            if c.get('setting'):
                rt.execute(c['setting'])
            foe = self.story_foe(rt)
            spawn_and_settle(rt, foe)
            self.assertTrue(20000 <= foe.defaultStats.maxHp <= 30000, label)     # the plain boss roll, not the crown
            self.assertEqual(foe.efx, -1, label)
        for label, kw in {'not flagged': dict(isBoss=0), 'ally': dict(isFoe=0), 'not listed': dict(name=17000)}.items():
            rt = runtime()
            foe = self.story_foe(rt, **kw)
            spawn_and_settle(rt, foe)
            self.assertEqual(foe.efx, -1, label)
            self.assertEqual(len(foe.names), 0, label)
        rt = runtime()
        foe = self.story_foe(rt, isBoss=0, hunt=1)             # the hunt flag also counts
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.maxHp, 40000)

    def test_unlisted_flagged_story_unit_in_trial_is_not_crowned_but_trial_unknown_fails_closed(self):
        rt = runtime()
        rt.execute('memory.s32 = setmetatable({}, {__index = function() return nil end})')
        foe = self.story_foe(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.efx, len(foe.names)), (-1, 0))

    def test_story_boss_without_a_party_is_left_alone(self):
        rt = runtime(party=None)
        foe = self.story_foe(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.efx, len(foe.names)), (-1, 0))

    # ------------------------------------------------------------------ other foes
    def test_kings_keep_defence_and_get_the_threat_floor(self):
        for _ in range(40):
            rt = runtime()
            foe = new_foe(rt, name=16450, size=120, stats={'maxHp': 10000, 'maxMp': 100, 'strength': 20, 'magickPower': 50,
                          'vitality': 20, 'speed': 5, 'defense': 33, 'magickResist': 20, 'attackPower': 20, 'licensePoints': 5})
            spawn_and_settle(rt, foe)
            s = foe.defaultStats
            self.assertEqual(s.defense, 33)
            self.assertTrue(10000 <= s.maxHp <= 15000)
            self.assertTrue(100 <= s.maxMp <= 150)
            self.assertEqual(s.strength, round(30 * 1.25))            # raised to the floor
            self.assertEqual(s.magickPower, 50)                        # own stat above the floor is untouched
            self.assertEqual(s.attackPower, 50)
            self.assertEqual(s.vitality, max(35, 20))                  # party Vit 35 (a floor of x1); the draw gives 20-30
            self.assertEqual(s.speed, 20)                              # floor x1
            self.assertTrue(round(120 * 0.95) <= foe.unit.sizeX <= round(120 * 1.10))
            self.assertEqual(foe.color.r, 1)                           # Kings are not tinted

    def test_commons_draw_hp_mp_vit_def_and_size(self):
        for name in (16447,):
            lows, highs = [10 ** 9] * 5, [0] * 5
            for _ in range(200):
                rt = runtime()
                foe = new_foe(rt, name=name)
                spawn_and_settle(rt, foe)
                s, a = foe.defaultStats, foe.additiveStats
                self.assertTrue(1000 <= s.maxHp <= 1500)
                self.assertTrue(50 <= s.maxMp <= 75)
                self.assertTrue(10 <= s.vitality <= 15)
                self.assertTrue(10 <= s.defense <= 15)
                self.assertEqual(s.magickResist, 10)                    # no MRes
                self.assertEqual((s.strength, s.attackPower, s.speed), (10, 10, 10))
                self.assertTrue(100 <= a.maxHp <= 150)
                self.assertTrue(10 <= a.maxMp <= 15)
                self.assertTrue(1 <= a.vitality <= 2 and 1 <= a.defense <= 2)
                size = (foe.unit.sizeX, foe.unit.sizeY, foe.unit.sizeZ)
                self.assertEqual(len(set(size)), 1)
                self.assertTrue(95 <= size[0] <= 110, size)
                for i, v in enumerate((s.maxHp, s.maxMp, s.vitality, s.defense, size[0])):
                    lows[i], highs[i] = min(lows[i], v), max(highs[i], v)
            self.assertLess(lows[0], 1100)                              # the whole range is reached
            self.assertGreater(highs[0], 1400)
            self.assertEqual((lows[4], highs[4]), (95, 110))

    def test_d1_marks_bosses_and_kings_roll_hp_and_mp_but_never_vitality_or_defence(self):
        # Decision D-1 (Marco, 2026-11-17): carved marks, bosses and Kings keep the HP and MP roll and size roll, but
        # their Defence and Vitality stay exactly as carved. Commons keep the full roll (test above).
        kinds = {16448: 'mark', 16449: 'boss', 16450: 'rare'}
        for name, label in kinds.items():
            hp, mp, size = set(), set(), set()
            for _ in range(120):
                rt = runtime(settings=dict(SETTINGS))
                foe = new_foe(rt, name=name, stats={'maxHp': 1000, 'maxMp': 50, 'strength': 10, 'magickPower': 10,
                              'vitality': 0, 'speed': 10, 'defense': 12, 'magickResist': 10, 'attackPower': 10,
                              'licensePoints': 10})
                spawn_and_settle(rt, foe)
                s, a = foe.defaultStats, foe.additiveStats
                self.assertTrue(1000 <= s.maxHp <= 1500, label)
                self.assertTrue(50 <= s.maxMp <= 75, label)
                self.assertTrue(100 <= a.maxHp <= 150 and 10 <= a.maxMp <= 15, label)
                self.assertEqual(s.defense, 12, label)
                self.assertEqual(a.defense, 1, label)
                self.assertEqual(a.vitality, 1, label)
                if label != 'rare':
                    self.assertEqual(s.vitality, 0, label)               # a carved Vitality 0 survives
                hp.add(s.maxHp)
                mp.add(s.maxMp)
                size.add(foe.unit.sizeX)
            self.assertGreater(len(hp), 30, label)                       # the roll is really drawn
            self.assertGreater(len(mp), 5, label)
            self.assertGreater(len(size), 5, label)

    def test_d1_commons_still_roll_all_four(self):
        seen = {'vitality': set(), 'defense': set()}
        for _ in range(120):
            rt = runtime()
            foe = common(rt, stats={'maxHp': 1000, 'maxMp': 50, 'strength': 10, 'magickPower': 10, 'vitality': 40, 'speed': 10,
                                    'defense': 40, 'magickResist': 10, 'attackPower': 10, 'licensePoints': 10})
            spawn_and_settle(rt, foe)
            s = foe.defaultStats
            self.assertTrue(40 <= s.vitality <= 60 and 40 <= s.defense <= 60)
            seen['vitality'].add(s.vitality)
            seen['defense'].add(s.defense)
        self.assertGreater(len(seen['vitality']), 5)
        self.assertGreater(len(seen['defense']), 5)

    def test_d1_king_without_defence_roll_keeps_threat_floor_over_a_carved_vitality(self):
        rt = runtime()
        foe = new_foe(rt, name=16450, stats={'maxHp': 10000, 'maxMp': 100, 'strength': 20, 'magickPower': 50, 'vitality': 5,
                      'speed': 5, 'defense': 33, 'magickResist': 20, 'attackPower': 20, 'licensePoints': 5})
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertEqual((s.defense, s.vitality, s.speed), (33, 35, 20))   # Vit and Speed lifted to the party's, not rolled
        self.assertEqual(s.strength, round(30 * 1.25))

    def test_each_stat_has_its_own_draw(self):
        rt = runtime()
        rt.execute('local n = 0; L.rng.uniform = function(low, high) n = n + 1; return low + (high - low) * (n % 5) / 4 end')
        foe = common(rt)
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertEqual(len({s.maxHp / 1000, s.maxMp / 50, s.vitality / 10, s.defense / 10}), 4)

    def test_draws_never_lower_a_stat(self):
        rt = runtime()
        foe = common(rt, stats={'maxHp': 7, 'maxMp': 0, 'strength': 10, 'magickPower': 10, 'vitality': 0, 'speed': 10,
                                'defense': 99, 'magickResist': 10, 'attackPower': 10, 'licensePoints': 10})
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertGreaterEqual(s.maxHp, 7)
        self.assertEqual((s.maxMp, s.vitality, s.defense), (0, 0, 99))

    # ------------------------------------------------------------------ tint
    def test_tint_ranges_and_hue(self):
        rt = runtime()
        T = rt.eval("L.moduleLoader('foe_tint')")
        brights, leans = [], []
        for _ in range(4000):
            r, g, b = T.roll(rt.eval('L.rng.uniform'), rt.eval('L.rng.sign'))
            level = g
            self.assertTrue(0.96 - 1e-9 <= level <= 0.98 + 1e-9 or 1.02 - 1e-9 <= level <= 1.04 + 1e-9, level)
            lean = r / level - 1
            self.assertLessEqual(abs(lean), 0.012 + 1e-9)
            self.assertAlmostEqual(b / level - 1, -lean, places=9)
            # never red, green or purple: no channel departs from the level by more than 1.2%
            self.assertLessEqual(max(abs(r - g), abs(b - g)) / g, 0.0125)
            brights.append(level)
            leans.append(lean)
        self.assertLess(min(brights), 0.965)
        self.assertGreater(max(brights), 1.035)
        self.assertLess(min(leans), -0.01)
        self.assertGreater(max(leans), 0.01)

    def test_tint_only_ordinary_foes_and_never_crowned_or_coloured(self):
        rt = runtime()
        plain = common(rt, lifeId=1)
        mark = new_foe(rt, name=16448, lifeId=2)
        boss = new_foe(rt, name=16449, lifeId=3)
        king = new_foe(rt, name=16450, lifeId=4)
        coloured = common(rt, lifeId=5, color={'r': 1.3, 'g': 1.0, 'b': 1.0, 'a': 1})
        near = common(rt, lifeId=6, color={'r': 1.004, 'g': 0.997, 'b': 1.0, 'a': 1})
        crowned = common(rt, lifeId=7)
        crown(rt, crowned, None)
        for foe in (plain, mark, boss, king, coloured, near, crowned):
            rt.globals().spawn(foe, 1)
        settle(rt)
        self.assertNotEqual((plain.color.r, plain.color.g, plain.color.b), (1, 1, 1))
        self.assertNotEqual(near.color.g, 0.997)                         # within 0.005 still counts as neutral
        for foe in (mark, boss, king, crowned):
            self.assertEqual((foe.color.r, foe.color.g, foe.color.b), (1, 1, 1))
        self.assertEqual(coloured.color.r, 1.3)
        self.assertEqual((coloured.color.g, coloured.color.b), (1.0, 1.0))
        self.assertIsNone(plain.efxColor)
        self.assertEqual(plain.efx, -1)                                  # the effect is never written

    def test_tint_setting_off(self):
        rt = runtime()
        rt.execute('L.settings.foeTint = 0')
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.color.r, 1)
        self.assertGreater(foe.defaultStats.maxHp, 999)                   # the roll still happened

    def test_variation_setting_off_keeps_tint(self):
        rt = runtime()
        rt.execute('L.settings.foeVariation = 0')
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.maxHp, foe.unit.sizeX), (1000, 100))
        self.assertNotEqual(foe.color.r, 1)

    # ------------------------------------------------------------------ Dynamic Bosses' settings are honoured
    def test_nothing_is_rolled_twice(self):
        # what Dynamic Bosses rolls itself, this layer leaves alone
        cases = [
            ('randomizeCommons', 1, 16447),
            ('ignoreMarks', 0, 16448),
            ('ignoreBosses', 0, 16449),
            ('ignoreRareGames', 0, 16450),
        ]
        for key, value, name in cases:
            rt = runtime(settings=dict(SETTINGS, **{key: value}))
            foe = new_foe(rt, name=name)
            spawn_and_settle(rt, foe)
            self.assertEqual((foe.defaultStats.maxHp, foe.unit.sizeX, foe.color.r), (1000, 100, 1), key)
            if key != 'ignoreBosses':          # the other kinds still roll
                other = new_foe(rt, name=16447 if key != 'randomizeCommons' else 16448, lifeId=9)
                spawn_and_settle(rt, other)
                self.assertGreater(other.defaultStats.maxHp, 999, key) if key != 'randomizeCommons' else None
        rt = runtime(settings=None)
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.maxHp, 1000)
        self.assertIn('unreadable', ' '.join(rt.globals().printed.values()))

    def test_allies_and_unlisted_foes_are_never_rolled(self):
        rt = runtime(settings=dict(SETTINGS, ignoreAllies=0))
        ally = new_foe(rt, name=16451)
        unlisted = new_foe(rt, name=20000, lifeId=2)
        for foe in (ally, unlisted):
            rt.globals().spawn(foe, 1)
        settle(rt)
        self.assertEqual((ally.defaultStats.maxHp, unlisted.defaultStats.maxHp), (1000, 1000))

    def test_settings_are_re_read_on_a_map_change(self):
        rt = runtime()
        g = rt.globals()
        foe = common(rt, lifeId=1)
        spawn_and_settle(rt, foe)
        self.assertGreater(foe.defaultStats.maxHp, 999)
        g.files[DYNAMIC] = rt.table_from(dict(SETTINGS, randomizeCommons=1))
        g.events['onMapJump'](200)
        again = common(rt, lifeId=2)
        spawn_and_settle(rt, again)
        self.assertEqual(again.defaultStats.maxHp, 1000)

    def test_missing_kind_list_turns_rolls_off_only(self):
        rt = S.lua(sources={'Foes': 'return nil'})
        g = rt.globals()
        g.files[DYNAMIC] = rt.table_from(SETTINGS)
        g.foes.partyStats = rt.table_from(PARTY)
        S.set_world(rt)
        rt.execute("L = loadfile('scripts/IvaliceLegacy/foe_layer.lua', 't', _ENV)(); L.register()")
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.maxHp, 1000)
        story = self.story_foe(rt, lifeId=2)
        spawn_and_settle(rt, story)
        self.assertEqual(story.defaultStats.maxHp, 40000)
        self.assertIn('kind list', ' '.join(g.printed.values()))

    def test_menu_page_and_save(self):
        rt = runtime()
        g = rt.globals()
        page = g.menus[1]
        ids = [row['id'] for row in page['schema'].values()]
        self.assertEqual(ids, ['crownStoryBosses', 'storyHp', 'foeVariation', 'foeTint', 'kingThreat', 'expShare'])
        self.assertEqual(page['group']['id'], 'IvaliceLegacy')
        page['schema'][1]['callback']('IvaliceLegacyFoes', 'crownStoryBosses', 0)
        self.assertEqual(rt.eval('L.settings.crownStoryBosses'), 0)
        page['save']['callback']('IvaliceLegacyFoes', rt.table_from({'storyHp': 2, 'foeTint': 0}), True)
        self.assertEqual(rt.eval('saved.storyHp'), 2)
        self.assertEqual(rt.eval('saved.foeTint'), 0)

    def test_stored_settings_are_read(self):
        rt = S.lua()
        g = rt.globals()
        g.files['scripts/config/IvaliceLegacy/foes.json'] = rt.table_from({'storyHp': 2, 'foeTint': 0, 'crownStoryBosses': 7})
        g.files[DYNAMIC] = rt.table_from(SETTINGS)
        S.set_world(rt)
        rt.execute("L = loadfile('scripts/IvaliceLegacy/foe_layer.lua', 't', _ENV)(); L.register()")
        self.assertEqual((rt.eval('L.settings.storyHp'), rt.eval('L.settings.foeTint'), rt.eval('L.settings.crownStoryBosses')),
                         (2, 0, 1))                                      # a bad value falls back to the default

    def test_world_readers(self):
        rt = runtime(location=57, towns=(57, 58), story=140)
        self.assertEqual(rt.eval('L.isTown(57)'), True)
        self.assertEqual(rt.eval('L.isTown(59)'), False)
        self.assertEqual(rt.eval('L.isTown(5000)'), False)
        self.assertEqual(rt.eval('L.story()'), 140)
        self.assertEqual((rt.eval('L.isTrial(1154)'), rt.eval('L.isTrial(1155)'), rt.eval('L.isTrial(1311)'),
                          rt.eval('L.isTrial(1312)'), rt.eval('L.isTrial(nil)')), (False, True, True, False, None))
        rt.globals().memory.u8[S.STORY_FLAG] = 0
        self.assertIsNone(rt.eval('L.story()'))
        rt.globals().memory.u32[S.LOCATION] = 0xFFFFFFFF
        self.assertEqual(rt.eval('L.location()'), 0)                     # a negative id reads as 0, as the game does

    # ------------------------------------------------------------------ the per-boss crown table (IDEA-1)
    PARTY_STATS = {'strength': 30, 'magickPower': 28, 'vitality': 35, 'speed': 20, 'attackPower': 40}

    def boss_stats(self, **over):
        stats = {'maxHp': 20000, 'maxMp': 50, 'strength': 10, 'magickPower': 10, 'vitality': 10, 'speed': 10,
                 'defense': 28, 'magickResist': 22, 'attackPower': 10, 'licensePoints': 10}
        stats.update(over)
        return stats

    def table_runtime(self, entries_lua, **kw):
        text = "return {schema = 'crown-table/1', entries = {%s}}" % entries_lua
        return runtime(sources={'crown_table': text}, **kw)

    def test_every_shipped_crown_entry_resolves_without_problems(self):
        rt = runtime(sources={})
        C = rt.eval("L.moduleLoader('foe_crown')")
        count = 0
        for nid, entry in rt.eval('L.crownTable').items():
            crown, problems = C.resolveEntry(entry)
            self.assertEqual(len(problems), 0, (nid, list(problems.values())))
            self.assertTrue(crown.authored)
            self.assertTrue(rt.eval('L.story_bosses.IDS')[nid])
            count += 1
        self.assertGreaterEqual(count, 1)

    def test_shipped_crown_table_has_the_firemane_entry(self):
        rt = runtime(sources={})
        entry = rt.eval('L.crownTable[16876]')
        self.assertEqual((entry.kind, entry.spell, len(entry.statuses)), (0, True, 0))
        self.assertEqual([entry.mirror[k] for k in ('strength', 'magickPower', 'attackPower', 'vitality', 'speed')],
                         [1.25, 1.25, 1.25, 1, 1])
        self.assertEqual((entry.defenceLow, entry.defenceHigh, entry.hpTimes, entry.mpFloor), (1, 1, 2, 400))
        self.assertRegex('\n'.join(rt.globals().printed.values()), r'crown table: \d+ authored boss crown\(s\)')

    def test_authored_crown_firemane(self):
        rt = runtime()
        g = rt.globals()
        self.native(rt)
        rt.execute('L.rng.uniform = function(low, high) return high end; L.rng.int = function(low, high) return low end')
        foe = self.story_foe(rt, name=16876, hunt=1, isBoss=0, stats=self.boss_stats(),
                             steals=[0x2001, 0x2002, 0x2003])
        spawn_and_settle(rt, foe)
        s, a = foe.defaultStats, foe.additiveStats
        self.assertEqual(s.maxHp, 40000)                                   # HP x2 stays
        self.assertEqual((s.strength, s.magickPower, s.attackPower), (30, 28, 40))   # mirror 1.0 of the party average
        self.assertEqual((s.vitality, s.speed), (35, 20))
        self.assertEqual((s.defense, a.defense), (28, 1))                  # no Defence draw
        self.assertEqual(s.maxMp, 400)
        self.assertEqual(s.licensePoints, 30)
        self.assertFalse(foe.abilities.statuses.has('bravery'))            # no permanent Bravery or Faith
        self.assertFalse(foe.abilities.statuses.has('faith'))
        self.assertTrue(foe.abilities.immunities.has(seq(rt, ['ko', 'stone', 'petrify', 'doom'])))
        self.assertEqual([S.ELEMENTS[i] for i in foe['class'].affinityAbsorb.list().values()], ['fire'])
        self.assertEqual((foe.efx, foe.names[1]), (8, 'Pyro {species}'))   # the fixed kind, every run
        self.assertTrue(foe.abilities.alwaysDrops.has('drop5'))
        self.assertNotEqual(foe.battleLogicSlots.slots[1], 0)             # Pyro's tier-3 spell was inserted
        self.assertIn('(authored)', '\n'.join(g.printed.values()))

    def test_authored_kind_is_the_same_on_every_run(self):
        kinds = set()
        for _ in range(25):
            rt = runtime()
            foe = self.story_foe(rt, name=16876, hunt=1, isBoss=0, stats=self.boss_stats())
            spawn_and_settle(rt, foe)
            kinds.add(foe.names[1])
        self.assertEqual(kinds, {'Pyro {species}'})

    def test_boss_without_an_entry_keeps_the_default_crown(self):
        seen, names = set(), set()
        for _ in range(60):
            rt = runtime()
            foe = self.story_foe(rt, stats=self.boss_stats())                # Belias: no entry
            spawn_and_settle(rt, foe)
            s = foe.defaultStats
            self.assertEqual((s.strength, s.magickPower, s.attackPower), (38, 35, 50))   # 1.25 x party
            self.assertEqual((s.defense, foe.additiveStats.defense), (28, 1))            # D-1: no roll here either
            self.assertTrue(foe.abilities.statuses.has(seq(rt, ['bravery', 'faith'])))
            names.add(foe.names[1])
        self.assertGreater(len(names), 3)                                   # a random kind, as today

    def test_default_crown_never_calls_the_defence_roll(self):
        rt = runtime()
        rt.execute('calls = 0; local u = L.rng.uniform; L.rng.uniform = function(a, b) calls = calls + 1; return u(a, b) end')
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertEqual(rt.eval('calls'), 0)

    def test_every_field_of_an_entry_overrides_the_default(self):
        entry = ("[16402] = {kind = 3, spell = false, statuses = {'protect'}, immunities = {'ko'}, "
                 "mirror = {strength = 0.9, magickPower = 0, attackPower = 1.1, vitality = 0, speed = 0}, hpTimes = 3, "
                 "mpFloor = 100, defenceLow = 1.2, defenceHigh = 1.2, lpTimes = 1, loot = 'native'}")
        rt = self.table_runtime(entry)
        self.native(rt)
        foe = self.story_foe(rt, stats=self.boss_stats(vitality=3, speed=3), steals=[0x2001, 0x2002, 0x2003])
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertEqual(s.maxHp, 20000 * 3)
        self.assertEqual(s.maxMp, 100)
        self.assertEqual(s.defense, round(28 * 1.2))
        self.assertEqual(foe.additiveStats.defense, round(1 * 1.2))
        self.assertEqual(s.licensePoints, 10)
        self.assertEqual((s.strength, s.attackPower), (27, 44))
        self.assertEqual(s.magickPower, 10)                                 # 0 = no floor
        self.assertEqual((s.vitality, s.speed), (3, 3))                     # 0 keeps the row's own
        self.assertTrue(foe.abilities.statuses.has('protect'))
        self.assertFalse(foe.abilities.statuses.has('bravery'))
        self.assertTrue(foe.abilities.immunities.has('ko'))
        self.assertFalse(foe.abilities.immunities.has('stone'))
        self.assertEqual([S.ELEMENTS[i] for i in foe['class'].affinityAbsorb.list().values()], ['earth'])
        self.assertEqual(foe.names[1], 'Quake {species}')
        self.assertEqual(foe.battleLogicSlots.slots[1], 0)                  # spell = false
        self.assertFalse(foe.abilities.alwaysDrops.has('drop5'))            # loot = native

    def test_a_defence_range_rolls_once(self):
        rt = self.table_runtime("[16402] = {kind = 8, defenceLow = 1, defenceHigh = 1.5}")
        rt.execute('L.rng.uniform = function(low, high) return high end')
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.defense, round(28 * 1.5))

    def test_kind_false_gives_no_aura_absorb_name_or_spell(self):
        rt = self.table_runtime("[16402] = {kind = false}")
        self.native(rt)
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.efx, len(foe.names), foe.battleLogicSlots.slots[1]), (-1, 0, 0))
        self.assertEqual(list(foe['class'].affinityAbsorb.list().values()), [])
        self.assertEqual(foe.defaultStats.maxHp, 40000)                     # the rest of the crown still applies
        self.assertTrue(foe.abilities.statuses.has(seq(rt, ['bravery', 'faith'])))

    def test_an_authored_kind_never_absorbs_a_native_weakness(self):
        rt = self.table_runtime("[16402] = {kind = 0}")
        foe = self.story_foe(rt, stats=self.boss_stats(), weak=['fire'])
        spawn_and_settle(rt, foe)
        self.assertEqual(list(foe['class'].affinityAbsorb.list().values()), [])
        self.assertEqual((foe.efx, foe.names[1]), (8, 'Pyro {species}'))

    def test_a_damaged_entry_falls_back_field_by_field_and_never_stops_the_crown(self):
        entry = ("[16402] = {kind = 99, spell = 'yes', statuses = 5, immunities = {1}, mirror = {strength = 'x', luck = 1}, "
                 "hpTimes = 0, mpFloor = -1, defenceLow = 2, defenceHigh = 1, lpTimes = 9, loot = 'all', surprise = true}")
        rt = self.table_runtime(entry)
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        s = foe.defaultStats
        self.assertEqual(s.maxHp, 40000)                                    # the default x2
        self.assertEqual(s.strength, 38)                                    # the default mirror
        self.assertEqual(s.licensePoints, 30)
        self.assertTrue(foe.abilities.statuses.has(seq(rt, ['bravery', 'faith'])))
        self.assertEqual(foe.efx, 8)                                        # a random kind was still chosen
        text = '\n'.join(rt.globals().printed.values())
        for fragment in ('unknown field surprise', 'bad kind', 'bad spell', 'bad statuses', 'bad immunities',
                         'bad mirror strength', 'bad mirror luck', 'bad hpTimes', 'bad mpFloor', 'bad lpTimes', 'bad loot',
                         'bad defence range'):
            self.assertIn(fragment, text)

    def test_a_status_name_the_api_does_not_know_costs_only_itself(self):
        rt = self.table_runtime("[16402] = {kind = 8, statuses = {'bravery', 'nonsense'}, immunities = {'ko', 'nope'}}")
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertTrue(foe.abilities.statuses.has('bravery'))
        self.assertTrue(foe.abilities.immunities.has('ko'))
        text = '\n'.join(rt.globals().printed.values())
        self.assertIn('crown statuses not set: nonsense', text)
        self.assertIn('crown immunities not set: nope', text)

    def test_missing_or_broken_crown_table_leaves_every_boss_on_the_default_crown(self):
        for label, source in {'error': "error('boom')", 'not a table': 'return 5', 'no entries': 'return {}',
                              'entries not a table': 'return {entries = 7}'}.items():
            rt = runtime(sources={'crown_table': source})
            self.assertEqual(rt.eval('next(L.crownTable)'), None, label)
            foe = self.story_foe(rt, name=16876, hunt=1, isBoss=0, stats=self.boss_stats())
            spawn_and_settle(rt, foe)
            self.assertEqual(foe.defaultStats.maxHp, 40000, label)
            self.assertEqual(foe.defaultStats.strength, 38, label)          # the default 1.25 mirror, not Firemane's 1.0
            self.assertIn('crown table unavailable', '\n'.join(rt.globals().printed.values()), label)

    def test_crown_table_keys_only_matter_for_listed_story_bosses(self):
        rt = self.table_runtime("[17000] = {kind = 3}")
        foe = self.story_foe(rt, name=17000, stats=self.boss_stats())      # not a listed boss: nothing is crowned
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.efx, len(foe.names)), (-1, 0))

    def test_trial_firemane_is_untouched_even_with_an_entry(self):
        rt = runtime(location=1200)
        foe = self.story_foe(rt, name=16876, hunt=1, isBoss=0, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.maxHp, foe.efx, len(foe.names), foe.defaultStats.strength), (20000, -1, 0, 10))

    # ------------------------------------------------------------------ EXP share
    def exp_stats(self, experience=100):
        return {'maxHp': 1000, 'maxMp': 50, 'strength': 10, 'magickPower': 10, 'vitality': 10, 'speed': 10, 'defense': 10,
                'magickResist': 10, 'attackPower': 10, 'licensePoints': 10, 'experience': experience}

    def exp_additive(self, experience=4):
        return {'maxHp': 100, 'maxMp': 10, 'strength': 0, 'magickPower': 0, 'vitality': 1, 'speed': 0, 'defense': 1,
                'magickResist': 0, 'attackPower': 0, 'licensePoints': 0, 'experience': experience}

    def test_exp_factor_is_eligible_over_native_divisor(self):
        rt = runtime()
        f = rt.eval('L.expFactor')
        self.assertEqual(f(6, 4, 0), 2)                    # four members: the native three-way share
        self.assertEqual(f(6, 3, 0), 2)
        self.assertEqual(f(6, 2, 0), 3)
        self.assertEqual(f(6, 1, 0), 6)
        self.assertEqual(f(6, 3, 1), 1.5)                  # a guest takes a share
        self.assertEqual(f(6, 4, 1), 1.5)
        self.assertEqual(f(3, 3, 0), 1)
        self.assertEqual(f(6, 3, None), 2)
        self.assertEqual(f(9, 1, 0), 9)                    # bounded
        self.assertEqual(f(1, 3, 3), 0.25)
        for bad in ((None, 4, 0), (0, 4, 0), (6, 0, 0), ('6', 4, 0), (10, 4, 0), (40, 1, 0), (6.5, 4, 0), (6, 4, 4),
                    (6, 4, -1), (6, 4, 1.5), (6, 4, 'x'), (float('nan'), 4, 0), (6, float('nan'), 0), (6, 4, float('nan'))):
            self.assertIsNone(f(*bad))

    def test_exp_is_left_alone_while_full_party_exp_share_is_not_detected(self):
        rt = runtime()
        self.assertFalse(rt.eval('L.fpesActive()'))
        self.assertIsNone(rt.eval('L.readEligible()'))
        self.assertFalse(rt.eval('L.expAvailable()'))
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.experience, foe.additiveStats.experience), (100, 4))
        self.assertEqual(rt.eval('L.counts.exp'), 0)

    def exp_runtime(self, eligible=6, **kw):
        rt = runtime(**kw)
        rt.execute('L.eligibleReader = function() return %s end' % eligible)
        return rt

    def test_exp_is_scaled_for_every_hostile_main_game_foe(self):
        for label, kw in {'common': dict(name=16447), 'mark': dict(name=16448), 'boss': dict(name=16449),
                          'king': dict(name=16450), 'unlisted': dict(name=17000)}.items():
            rt = self.exp_runtime()
            foe = new_foe(rt, stats=self.exp_stats(), additive=self.exp_additive(), **kw)
            spawn_and_settle(rt, foe)
            self.assertEqual((foe.defaultStats.experience, foe.additiveStats.experience), (200, 8), label)
        rt = self.exp_runtime()
        foe = self.story_foe(rt, stats=dict(self.boss_stats(), experience=300), additive=self.exp_additive(30))
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.experience, foe.additiveStats.experience), (600, 60))   # story crown too

    def test_exp_follows_the_party_size_and_guests(self):
        for count, guests, want in ((4, None, 200), (2, None, 300), (1, None, 600), (3, 1, 150)):
            rt = self.exp_runtime(party=dict(PARTY, count=count))
            if guests:
                rt.execute('L.guestReader = function() return %d end' % guests)
            foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
            spawn_and_settle(rt, foe)
            self.assertEqual(foe.defaultStats.experience, want, (count, guests))

    def test_exp_is_shared_once_per_foe(self):
        rt = self.exp_runtime()
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        rt.globals().spawn(foe, 1)
        settle(rt)
        self.assertEqual(foe.defaultStats.experience, 200)
        self.assertEqual(rt.eval('L.counts.exp'), 1)

    def test_exp_is_untouched_in_trial_towns_allies_when_off_and_when_unreadable(self):
        cases = {
            'trial': dict(kw=dict(location=1200)),
            'town': dict(kw=dict(towns=(100,))),
            'ally': dict(foe=dict(isFoe=0)),
            'off': dict(setup='L.settings.expShare = 0'),
            'no party': dict(kw=dict(party=None)),
            'reader errors': dict(setup="L.eligibleReader = function() error('x') end"),
            'reader gives text': dict(setup="L.eligibleReader = function() return 'six' end"),
        }
        for label, c in cases.items():
            rt = self.exp_runtime(**c.get('kw', {}))
            if c.get('setup'):
                rt.execute(c['setup'])
            foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive(), **c.get('foe', {}))
            spawn_and_settle(rt, foe)
            self.assertEqual((foe.defaultStats.experience, foe.additiveStats.experience), (100, 4), label)

    def test_exp_is_clamped_to_a_u32_and_a_foe_without_the_field_is_skipped(self):
        rt = self.exp_runtime()
        foe = common(rt, stats=self.exp_stats(4000000000), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 4294967295)
        rt = self.exp_runtime()
        foe = common(rt)                                                    # no experience stat
        spawn_and_settle(rt, foe)
        self.assertIsNone(foe.defaultStats.experience)

    def test_exp_row_in_the_menu_and_the_stored_setting(self):
        rt = runtime()
        page = rt.globals().menus[1]
        row = [r for r in page['schema'].values() if r['id'] == 'expShare'][0]
        self.assertEqual(row['default'], 1)
        page['save']['callback']('IvaliceLegacyFoes', rt.table_from({'expShare': 0}), True)
        self.assertEqual(rt.eval('saved.expShare'), 0)
        rt = S.lua()
        g = rt.globals()
        g.files['scripts/config/IvaliceLegacy/foes.json'] = rt.table_from({'expShare': 0})
        g.files[DYNAMIC] = rt.table_from(SETTINGS)
        S.set_world(rt)
        rt.execute("L = loadfile('scripts/IvaliceLegacy/foe_layer.lua', 't', _ENV)(); L.register()")
        self.assertEqual(rt.eval('L.settings.expShare'), 0)

    # ------------------------------------------------------------------ review fixes
    def test_the_last_bank_script_is_accepted_and_a_short_entry_is_refused(self):
        rt = runtime()
        raws = [S.script([[self.HOSTILE]]), S.script([[self.HOSTILE, self.HOSTILE]])]
        addresses = S.install_bank(rt, raws)
        g = rt.globals()
        C = rt.eval("L.moduleLoader('foe_crown')")
        count = len(raws)
        g.memory.u32[S.BANK_AT + 4 + 4 * count] = 1                    # whatever sits past the offset table is not an offset
        foe = common(rt, links=[0, 1, 0, 0])
        a, bytes_ = C.nativeScript(foe, 2)                              # the last script
        self.assertEqual(a, addresses[1])
        self.assertEqual(len(bytes_), len(raws[1]))
        a, bytes_ = C.nativeScript(foe, 1)                              # a middle script is still checked
        self.assertEqual(a, addresses[0])
        g.memory.u32[S.BANK_AT + 4 + 4 * 1] = addresses[0] + 40         # entry 1 starts inside script 0
        a, why = C.nativeScript(foe, 1)
        self.assertIsNone(a)
        self.assertIn('longer than its bank entry', why)

    def test_a_group_never_grows_past_255_rules(self):
        rt = runtime()
        C = rt.eval("L.moduleLoader('foe_crown')")
        for rules, grows in ((255, False), (254, True)):
            raw = S.script([[self.HOSTILE] * rules])
            built, added = C.insertSpell(rt.table_from(list(raw)), 18, 30, 4)
            self.assertEqual(added, 1 if grows else 0, rules)
            if grows:
                self.assertEqual(built[1], 255)

    def half_crowned(self, rt, statuses, slots=(0, 0, 0, 0), **kw):
        foe = common(rt, slots=list(slots), **kw)
        foe.abilities.statuses.add(seq(rt, statuses))
        return foe

    def test_a_half_written_dynamic_crown_is_put_back_once_then_acted_on(self):
        for label, statuses, slots in (('some statuses', ['protect', 'shell'], (0, 0, 0, 0)),
                                       ('statuses but no AI yet', ['protect', 'shell', 'bravery', 'faith'], (0, 0, 0, 0))):
            rt = runtime()
            g = rt.globals()
            foe = self.half_crowned(rt, statuses, slots)
            g.spawn(foe, 1)
            rt.execute('runTimers()')
            self.assertEqual(foe.defaultStats.maxHp, 1000, label)         # not touched on the first pass
            self.assertEqual(rt.eval('next(claimed)'), None, label)       # not claimed either
            self.assertEqual(len(g.timers), 1, label)                      # a second pass is queued
            rt.execute('runTimers()')
            self.assertEqual(len(g.timers), 0, label)                      # and it is the last one
            if len(statuses) == 4:
                self.assertEqual(foe.defaultStats.strength, round(30 * 1.25), label)   # then acted on as a crowned common
            else:
                self.assertTrue(1000 <= foe.defaultStats.maxHp <= 1500, label)

    def test_a_crown_finished_between_the_passes_is_fixed_as_a_crown(self):
        rt = runtime()
        g = rt.globals()
        raw, address = self.native(rt)
        foe = self.half_crowned(rt, ['protect', 'shell', 'bravery', 'faith'])
        foe['class'].affinityAbsorb.add('fire')
        g.spawn(foe, 1)
        rt.execute('runTimers()')
        self.assertEqual(foe.defaultStats.strength, 10)
        foe.battleLogicSlots.slots[1] = 0x60000000                         # Dynamic Bosses writes its AI
        rt.execute('runTimers()')
        self.assertEqual(foe.defaultStats.strength, round(30 * 1.25))
        self.assertEqual(rt.eval('L.counts.crowned'), 1)

    def test_a_complete_crown_is_handled_in_one_pass_and_a_plain_common_waits_one_pass(self):
        rt = runtime()
        g = rt.globals()
        complete = self.half_crowned(rt, ['protect', 'shell', 'bravery', 'faith'], (0x60000000,) * 4, lifeId=1)
        plain = common(rt, lifeId=2)
        g.spawn(complete, 1)
        g.spawn(plain, 1)
        rt.execute('runTimers()')
        self.assertEqual(complete.defaultStats.strength, round(30 * 1.25))   # done at once
        self.assertEqual(plain.defaultStats.maxHp, 1000)                       # a late Dynamic Bosses crown is awaited
        self.assertEqual((rt.eval('L.counts.drawn'), len(g.timers)), (0, 1))
        rt.execute('runTimers()')
        self.assertEqual((rt.eval('L.counts.drawn'), len(g.timers)), (1, 0))   # then it rolls, and nothing waits again

    def test_a_late_crown_on_a_common_is_seen(self):
        rt = runtime()
        g = rt.globals()
        self.native(rt)
        foe = common(rt)
        foe['class'].affinityAbsorb.add('fire')
        g.spawn(foe, 1)
        rt.execute('runTimers()')                                              # no crown yet: the foe waits
        foe.abilities.statuses.add(seq(rt, ['protect', 'shell', 'bravery', 'faith']))
        for slot in (1, 2, 3, 4):
            foe.battleLogicSlots.slots[slot] = 0x60000000                      # Dynamic Bosses finishes in between
        rt.execute('runTimers()')
        self.assertEqual((foe.defaultStats.strength, rt.eval('L.counts.crowned'), rt.eval('L.counts.drawn')),
                         (round(30 * 1.25), 1, 0))                             # fixed as a crown, never rolled as a plain foe

    def test_a_crown_needs_every_linked_slot_not_just_one(self):
        rt = runtime()
        self.native(rt)                                                         # a bank of one script: link 0 is inside it
        C = rt.eval("L.moduleLoader('foe_crown')")
        four = ['protect', 'shell', 'bravery', 'faith']
        foe = self.half_crowned(rt, four, (0x60000000, 0, 0, 0), links=[0, 0, 0, 0])
        self.assertEqual(C.crownProgress(foe), 'partial')
        foe = self.half_crowned(rt, four, (0x60000000,) * 4, links=[0, 0, 0, 0])
        self.assertEqual(C.crownProgress(foe), 'complete')
        foe = self.half_crowned(rt, four, (0x60000000, 0, 0, 0), links=[0, 7, 9, 0xFFFF])   # the rest point outside the bank
        self.assertEqual(C.crownProgress(foe), 'complete')
        foe = self.half_crowned(rt, four[:3], (0x60000000,) * 4)
        self.assertEqual(C.crownProgress(foe), 'partial')
        self.assertEqual(C.crownProgress(common(rt)), 'absent')

    def test_dynamic_bosses_settings_are_re_read_in_every_pass(self):
        rt = runtime()
        g = rt.globals()
        first = self.story_foe(rt, stats=self.boss_stats(), lifeId=1)
        spawn_and_settle(rt, first)
        self.assertEqual(first.defaultStats.maxHp, 40000)                  # crowned while Dynamic Bosses ignores bosses
        g.files[DYNAMIC] = rt.table_from(dict(SETTINGS, ignoreBosses=0))   # the player changed its file in the menu
        second = self.story_foe(rt, stats=self.boss_stats(), lifeId=2)
        spawn_and_settle(rt, second)
        self.assertEqual((second.efx, len(second.names)), (-1, 0))         # no map change needed to notice
        self.assertTrue(20000 <= second.defaultStats.maxHp <= 30000)
        g.files[DYNAMIC] = rt.table_from(dict(SETTINGS, randomizeCommons=1))
        third = common(rt, lifeId=3)
        spawn_and_settle(rt, third)
        self.assertEqual(third.defaultStats.maxHp, 1000)                   # Dynamic Bosses rolls commons now: not rolled twice

    def test_one_failing_foe_never_drops_the_rest_of_the_pass_and_auto_refresh_is_restored(self):
        for label, setup in {
            'isPresent errors': "local old = foes.isPresent; foes.isPresent = function(id) if id == 2 then error('boom') end return old(id) end",
            'claim errors': "for _, f in ipairs(foeList) do if f.lifeId == 2 then f.claim = function() error('boom') end end end",
            'process errors': "for _, f in ipairs(foeList) do if f.lifeId == 2 then f.unit = nil end end",
        }.items():
            rt = runtime()
            g = rt.globals()
            foes = [common(rt, lifeId=i) for i in (1, 2, 3)]
            rt.execute(setup)
            for foe in foes:
                g.spawn(foe, 1)
            g.foes.autoRefresh = True
            settle(rt)
            self.assertEqual(rt.eval('L.counts.drawn'), 2, label)                # the other two were rolled
            self.assertEqual(foes[1].defaultStats.maxHp, 1000, label)
            self.assertTrue(g.foes.autoRefresh, label)

    def test_an_unreadable_foe_list_restores_auto_refresh(self):
        rt = runtime()
        g = rt.globals()
        foe = common(rt)
        g.spawn(foe, 1)
        rt.execute("foes.list = function() error('no list') end")
        g.foes.autoRefresh = True
        rt.execute('runTimers()')
        self.assertTrue(g.foes.autoRefresh)
        self.assertEqual(foe.defaultStats.maxHp, 1000)
        self.assertIn('foe list could not be read', '\n'.join(g.printed.values()))

    def test_the_foe_list_is_built_once_per_pass(self):
        rt = runtime()
        g = rt.globals()
        foes = [common(rt, lifeId=i) for i in range(1, 6)]
        rt.execute('listCalls = 0; local old = foes.list; foes.list = function() listCalls = listCalls + 1; return old() end')
        for foe in foes:
            g.spawn(foe, 1)
        rt.execute('runTimers()')
        self.assertEqual(rt.eval('listCalls'), 1)

    def test_a_story_boss_is_refreshed_once(self):
        rt = runtime()
        g = rt.globals()
        rt.execute('refreshes = 0')
        foe = self.story_foe(rt, stats=self.boss_stats())
        spawn_and_settle(rt, foe)
        self.assertEqual(g.refreshes, 1)
        self.assertEqual(foe.keep.currentHp, foe.defaultStats.maxHp)

    def test_exp_never_drops_to_zero_and_a_nan_is_left(self):
        rt = runtime()
        rt.execute('L.eligibleReader = function() return 1 end')         # factor 1/3 -> 0.33
        stats = self.exp_stats(1)
        foe = common(rt, stats=stats, additive=self.exp_additive(1))
        spawn_and_settle(rt, foe)
        self.assertEqual((foe.defaultStats.experience, foe.additiveStats.experience), (1, 1))
        zero = common(rt, stats=self.exp_stats(0), additive=self.exp_additive(0), lifeId=2)
        spawn_and_settle(rt, zero)
        self.assertEqual((zero.defaultStats.experience, zero.additiveStats.experience), (0, 0))
        rt.execute('L.eligibleReader = function() return 6 end')
        nan = common(rt, stats=self.exp_stats(100), additive=self.exp_additive(0), lifeId=3)
        rt.execute("for _, f in ipairs(foeList) do if f.lifeId == 3 then f.defaultStats.experience = 0 / 0 end end")
        spawn_and_settle(rt, nan)
        self.assertNotEqual(nan.defaultStats.experience, nan.defaultStats.experience)   # still NaN: untouched

    def test_exp_row_says_it_is_inactive_until_exp_share_is_available(self):
        rt = runtime()
        row = [r for r in rt.eval('L.page()')['schema'].values() if r['id'] == 'expShare'][0]
        self.assertIn('not active', row['label'])
        self.assertIn('no effect now', row['help'])
        rt.execute('L.eligibleReader = function() return 6 end')
        row = [r for r in rt.eval('L.page()')['schema'].values() if r['id'] == 'expShare'][0]
        self.assertEqual(row['label'], 'EXP share')
        self.assertNotIn('no effect', row['help'])

    def test_trial_predicate_hook_replaces_the_id_range(self):
        rt = runtime()
        self.assertEqual(rt.eval('L.isTrial(1200)'), True)
        rt.execute('L.trialPredicate = function(location) return location == 7 end')
        self.assertEqual((rt.eval('L.isTrial(7)'), rt.eval('L.isTrial(1200)')), (True, False))
        rt.execute('L.trialPredicate = function() error("x") end')
        self.assertIsNone(rt.eval('L.isTrial(7)'))                          # unknown fails closed
        rt.execute('L.trialPredicate = function() return 1 end')
        self.assertIsNone(rt.eval('L.isTrial(7)'))
        foe = common(rt)
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.maxHp, 1000)

    def test_a_failed_settings_save_is_logged(self):
        rt = runtime()
        g = rt.globals()
        rt.execute("config.saveJson = function() error('disk full') end")
        rt.execute('L.saveOwnSettings()')
        self.assertIn('settings were not saved', '\n'.join(g.printed.values()))
        rt.execute('config.saveJson = function() return false end')
        rt.execute('L.saveOwnSettings()')
        self.assertEqual(sum('settings were not saved' in v for v in g.printed.values()), 2)

    def test_missing_kind_list_log_tells_the_truth(self):
        rt = runtime(sources={'Foes': 'return nil'})
        text = '\n'.join(rt.globals().printed.values())
        self.assertIn('offence fix of crowned commons are off', text)

    # ------------------------------------------------------------------ the party layout readers
    BLOCK = 0x34000000

    def party_memory(self, rt, slots, statuses=None, fpes=True, block=True, real=True):
        """Fake the party block: slots = nine u16 layout-0 values; statuses = {member id: status word}."""
        g = rt.globals()
        m = g.memory
        if fpes:
            m.u8[0x2F8CE4] = 9
            m.u8[0x2F8C96] = 0xE9
        if block:
            m.u64[0x02EBF190] = self.BLOCK
        for i, value in enumerate(slots):
            m.u16[self.BLOCK + 0x5A48 + 2 * i] = value
            member = value & 0xFF
            if value != 0xFFFF and member <= 0x27 and real:
                record = self.BLOCK + 8 + member * 0x1C8
                m.u8[record + 4] = member                                  # a real record: id at +4, kind 0 at +5
                m.u8[record + 5] = 0
        for member, word in (statuses or {}).items():
            m.u32[self.BLOCK + 8 + member * 0x1C8 + 0x3C] = word

    def read(self, rt):
        eligible, guests, members = rt.eval('L.readParty')()
        self.assertEqual(members, eligible - guests)
        return eligible, guests

    def test_party_reader_counts_eligible_members_and_guests(self):
        E = 0xFFFF
        rt = runtime()
        self.party_memory(rt, [0, 1, 2, 3, 4, 5, E, E, E])
        self.assertEqual(self.read(rt), (6, 0))
        self.assertEqual((rt.eval('L.readEligible()'), rt.eval('L.readGuests()')), (6, 0))
        self.party_memory(rt, [0, 1, 2, 8, E, E, 0x0A, E, E])                # guests 8 and 10, one in a late slot
        self.assertEqual(self.read(rt), (5, 2))
        self.party_memory(rt, [0, 0x1B, 0x27, 0x28, 0x0107, E, E, E, E])      # 0x1A..0x27 never count; 0x28 is out of range
        self.assertEqual(self.read(rt)[0], 2)                                  # member 0 and the id 0x0107 -> low byte 7 (a guest)
        self.assertEqual(self.read(rt)[1], 1)

    def test_party_reader_skips_members_with_a_blocking_status(self):
        E = 0xFFFF
        rt = runtime()
        self.party_memory(rt, [0, 1, 2, 7, E, E, E, E, E], statuses={1: 0x1, 2: 0x80000000, 7: 0x2})
        self.assertEqual(self.read(rt), (1, 0))                                # KO and stone-like words, guest included
        rt.globals().memory.u32[self.BLOCK + 8 + 1 * 0x1C8 + 0x3C] = 0x4      # a bit outside the mask does not block
        rt.globals().memory.u32[self.BLOCK + 8 + 2 * 0x1C8 + 0x3C] = 0
        rt.globals().memory.u32[self.BLOCK + 8 + 2 * 0x1C8 + 0x64] = 0x80000000   # the second status word counts too
        self.assertEqual(self.read(rt), (2, 0))

    def test_party_reader_is_inert_without_fpes_or_a_party(self):
        E = 0xFFFF
        for label, kw in {'no fpes': dict(fpes=False), 'no block': dict(block=False)}.items():
            rt = runtime()
            self.party_memory(rt, [0, 1, 2, E, E, E, E, E, E], **kw)
            self.assertIsNone(rt.eval('L.readParty()'), label)
            self.assertIsNone(rt.eval('L.readEligible()'), label)
        rt = runtime()
        self.party_memory(rt, [0, 1, 2, E, E, E, E, E, E])
        rt.globals().memory.u8[0x2F8CE4] = 8                                   # a different loop bound: not FPES
        self.assertIsNone(rt.eval('L.readParty()'))
        rt.globals().memory.u8[0x2F8CE4] = 9
        rt.globals().memory.u8[0x2F8C96] = 0xE8
        self.assertIsNone(rt.eval('L.readParty()'))

    def test_party_reader_does_not_believe_a_garbage_block(self):
        E = 0xFFFF
        rt = runtime()
        self.party_memory(rt, [0] * 9, real=False)                             # a zeroed block: nine slots of id 0
        eligible, guests, members = rt.eval('L.readParty')()
        self.assertEqual((eligible, guests, members), (1, 0, 1))               # counted once, not nine times
        rt = runtime()
        self.party_memory(rt, [0, 1, 1, 1, 2, 2, E, E, 0])                     # duplicates count once each
        self.assertEqual(self.read(rt), (3, 0))
        rt = runtime()
        self.party_memory(rt, [0, 1, 2, 3, E, E, E, E, E], real=False)         # records that are not real members
        rt.globals().memory.u8[self.BLOCK + 8 + 0 * 0x1C8 + 4] = 5
        self.assertIsNone(rt.eval('L.readParty()'))
        rt = runtime()
        self.party_memory(rt, [0, 1, 2, E, E, E, E, E, E])
        rt.globals().memory.u8[self.BLOCK + 8 + 1 * 0x1C8 + 4] = 9             # a record whose id does not match its slot
        rt.globals().memory.u8[self.BLOCK + 8 + 2 * 0x1C8 + 5] = 1             # and one that is not a playable kind
        self.assertEqual(self.read(rt), (1, 0))
        rt = runtime()
        self.party_memory(rt, [8, 9, 10, E, E, E, E, E, E])                    # guests only: no main member, not believed
        self.assertIsNone(rt.eval('L.readParty()'))

    def test_exp_divisor_comes_from_the_status_filtered_layout(self):
        E = 0xFFFF
        # two main members, one down at spawn: the share is the one who stands (eligible 1 over min(1, 3) = 1)
        rt = runtime(party=dict(PARTY, count=2))
        self.party_memory(rt, [0, 1, E, E, E, E, E, E, E], statuses={1: 1})
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 100)
        # the Instanced Foes party count (1 here) is not what divides: four standing out of five, a divisor of three
        rt = runtime(party=dict(PARTY, count=1))
        self.party_memory(rt, [0, 1, 2, 3, 4, E, E, E, E], statuses={4: 1})
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, round(100 * 4 / 3))

    def test_more_than_three_guests_turn_the_share_off_and_are_logged(self):
        rt = self.exp_runtime()
        rt.execute('L.guestReader = function() return 4 end')
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 100)
        self.assertIn('4 guests', '\n'.join(rt.globals().printed.values()))

    def test_exp_follows_the_real_readers_end_to_end(self):
        E = 0xFFFF
        rt = runtime(party=dict(PARTY, count=4))
        self.party_memory(rt, [0, 1, 2, 3, 4, 5, E, E, E])
        self.assertTrue(rt.eval('L.expAvailable()'))
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 200)                     # six share, a native party of three: x2
        rt = runtime(party=dict(PARTY, count=3))
        self.party_memory(rt, [0, 1, 2, 3, 4, 8, E, E, E])                     # six eligible, one guest
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 150)                     # 6 / (3 + 1)
        rt = runtime()
        self.party_memory(rt, [E] * 9)                                         # an empty layout: eligible 0, no answer
        foe = common(rt, stats=self.exp_stats(), additive=self.exp_additive())
        spawn_and_settle(rt, foe)
        self.assertEqual(foe.defaultStats.experience, 100)

    def test_new_files_have_no_dynamic_bosses_names_or_constants(self):
        text = ''
        for name in ('foe_layer', 'foe_crown', 'story_bosses', 'foe_tint', 'crown_table'):
            text += (S.MODULES / f'{name}.lua').read_text(encoding='utf-8')
        text += (S.SCRIPTS / 'IvaliceLegacyFoes.lua').read_text(encoding='utf-8')
        for forbidden in ('DynamicFoes.lua', 'Scaling', 'ModMenu.lua', 'superHpTimes', 'consts.trialMode', '0x00236760',
                          'withSpell', 'rollFoe', 'rollKing', 'dressKind'):
            self.assertNotIn(forbidden, text)

    def test_line_endings_are_consistent_in_new_files(self):
        for path in [S.MODULES / f'{n}.lua' for n in ('foe_layer', 'foe_crown', 'story_bosses', 'foe_tint', 'crown_table')] + [
                S.SCRIPTS / 'IvaliceLegacyFoes.lua', SHIPPED_JSON]:
            raw = path.read_bytes()
            self.assertNotIn(b'\r\n', raw, path)


ELEMENTS_ALL = list(S.ELEMENTS)


# --------------------------------------------------------------------------------------------------------------------
# The pinned executable: every address and layout the layer relies on (static)
# --------------------------------------------------------------------------------------------------------------------
class ExeFacts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        game = os.environ.get('IVALICE_LEGACY_GAME')
        exe = Path(game) / 'x64/FFXII_TZA.exe' if game else None
        if exe is None or not exe.is_file():
            raise unittest.SkipTest('IVALICE_LEGACY_GAME is not set')
        cls.data = exe.read_bytes()
        d = cls.data
        p = struct.unpack_from('<I', d, 60)[0]
        count = struct.unpack_from('<H', d, p + 6)[0]
        optional = struct.unpack_from('<H', d, p + 20)[0]
        cls.base = struct.unpack_from('<Q', d, p + 48)[0]
        cls.sections = []
        for i in range(count):
            o = p + 24 + optional + 40 * i
            virtual, rva, raw, off = struct.unpack_from('<4I', d, o + 8)
            cls.sections.append((cls.base + rva, raw, off))

    def at(self, va, length):
        for start, size, off in self.sections:
            if start <= va < start + size:
                return self.data[off + va - start: off + va - start + length]
        raise AssertionError(hex(va))

    def test_image_base(self):
        self.assertEqual(self.base, 0x120000)

    def test_story_word_reader(self):
        # 0x264640: cmp ecx,0x100 ; jge ; test byte [flag],1 ; je ; mov rdx,[table] ; test ; je ; movsxd ; movzx eax,word [rdx+rax*2]
        self.assertEqual(self.at(0x264640, 8), bytes.fromhex('81f9000100007d1d'))
        flag = 0x26464F + struct.unpack('<i', self.at(0x26464A, 4))[0]
        self.assertEqual(self.at(0x264648, 2), bytes.fromhex('f605'))
        self.assertEqual(flag, 0x2098E20)
        self.assertEqual(self.at(0x26464E, 1), b'\x01')
        self.assertEqual(self.at(0x264651, 3), bytes.fromhex('488b15'))
        self.assertEqual(0x264658 + struct.unpack('<i', self.at(0x264654, 4))[0], 0x2098E58)
        self.assertEqual(self.at(0x26465D, 8), bytes.fromhex('4863c10fb70442c3'))

    def test_story_word_is_variable_group_3(self):
        # the VM's variable-group dispatcher 0x321190: group - 1 indexes a jump table of 10; entry 2 calls 0x264640
        self.assertEqual(self.at(0x321196, 6), bytes.fromhex('ffc9498bd983'))
        self.assertEqual(self.at(0x3211E4, 8), bytes.fromhex('418bc8e85434f4ff'))
        self.assertEqual(0x3211EC + struct.unpack('<i', self.at(0x3211E8, 4))[0], 0x264640)
        table = 0x120000 + 0x2012AC
        entries = [struct.unpack('<I', self.at(table + 4 * i, 4))[0] + 0x120000 for i in range(3)]
        self.assertEqual(entries[2], 0x3211E4)

    def test_location_getter(self):
        # 0x3148F0: block = 0x2EF2B0() ; eax = [block + 0x1044] ; a negative id reads as 0 (cmovs); the block is the save
        # image + 0x200 and the image is 0x2EF640() = 0x2164280
        self.assertEqual(self.at(0x3148F4, 5), bytes.fromhex('e8b7a9fdff'))
        self.assertEqual(self.at(0x3148F9, 2), bytes.fromhex('33c9'))
        self.assertEqual(self.at(0x3148FB, 6), bytes.fromhex('8b8044100000'))
        self.assertEqual(self.at(0x314901, 5), bytes.fromhex('85c00f48c1'))
        self.assertEqual(self.at(0x2EF2B4, 5), bytes.fromhex('e887030000'))
        self.assertEqual(self.at(0x2EF2B9, 6), bytes.fromhex('480500020000'))
        self.assertEqual(self.at(0x2EF640, 3), bytes.fromhex('488d05'))
        image = 0x2EF647 + struct.unpack('<i', self.at(0x2EF643, 4))[0]
        self.assertEqual(image, 0x2164280)
        self.assertEqual(image + 0x200 + 0x1044, 0x21654C4)

    def test_town_predicate(self):
        # 0x377C20: id = 0x3148F0() ; table = [0x2ADD100] ; if id < [table + 4]: word at table + 8 + 3 * id, bit 15
        self.assertEqual(self.at(0x377C24, 5), bytes.fromhex('e8c7ccf9ff'))
        self.assertEqual(self.at(0x377C29, 3), bytes.fromhex('4c8b05'))
        self.assertEqual(0x377C29 + 7 + struct.unpack('<i', self.at(0x377C2C, 4))[0], 0x2ADD100)
        self.assertEqual(self.at(0x377C35, 4), bytes.fromhex('413b4004'))
        self.assertEqual(self.at(0x377C3B, 3), bytes.fromhex('4863c8'))
        self.assertEqual(self.at(0x377C42, 5), bytes.fromhex('0fb654010 9'.replace(' ', '')))     # movzx edx, [rcx+rax+9]
        self.assertEqual(self.at(0x377C4B, 5), bytes.fromhex('0fb64c0108'))                       # movzx ecx, [rcx+rax+8]
        self.assertEqual(self.at(0x377C50, 9), bytes.fromhex('c1e20803d1c1ea0f8b'[:18]))          # shl 8 ; add ; shr 15

    def test_battle_logic_slot_setter(self):
        # 0x239040(slot 0..3): unit row = [unit + 0xE60]; link = u16 [row + slot*2 + 0x50]; offset = [bank + link*4 + 4];
        # script = offset + [0x1F83530] (0x20E600); stored at [unit + 0x6E0]
        self.assertEqual(self.at(0x239046, 6), bytes.fromhex('4863fa83ff03'))
        self.assertEqual(self.at(0x239060, 3), bytes.fromhex('488b05'))
        self.assertEqual(0x239060 + 7 + struct.unpack('<i', self.at(0x239063, 4))[0], 0x2EBF168)
        self.assertEqual(self.at(0x23906C, 7), bytes.fromhex('488b93600e0000'))
        self.assertEqual(self.at(0x239073, 5), bytes.fromhex('0fb74c7a50'))
        self.assertEqual(self.at(0x239078, 4), bytes.fromhex('8b4c8804'))
        self.assertEqual(self.at(0x23907C, 5)[0], 0xE8)
        self.assertEqual(0x239081 - 5 + 5 + struct.unpack('<i', self.at(0x23907D, 4))[0], 0x20E600)
        self.assertEqual(self.at(0x239081, 7), bytes.fromhex('488983e0060000'))

    def test_offset_to_pointer(self):
        # 0x20E600: eax = ecx ; rax += [0x1F83530]
        self.assertEqual(self.at(0x20E600, 5), bytes.fromhex('8bc1480305'))
        self.assertEqual(0x20E609 + struct.unpack('<i', self.at(0x20E605, 4))[0], 0x1F83530)

    def test_slot_switch_opcode_uses_the_same_bank(self):
        # 0x2FE05B: the AI opcodes 0x4112..0x4115 read the same unit row link, bound it by the bank count, store at +0x6E0
        self.assertEqual(self.at(0x2FE048, 4), bytes.fromhex('418d80ee'))
        self.assertEqual(self.at(0x2FE05B, 5), bytes.fromhex('0fb7544850'))
        self.assertEqual(0x2FE060 + 7 + struct.unpack('<i', self.at(0x2FE063, 4))[0], 0x2EBF168)
        self.assertEqual(self.at(0x2FE06C, 2), bytes.fromhex('0fb7'))
        self.assertEqual(self.at(0x2FE08E, 7), bytes.fromhex('4889bbe0060000'))

    def test_trial_stage_locations_are_exactly_1155_to_1311(self):
        # The location-to-map table at 0x1EE23A0: 1312 slots of three char* (ebp, mot, fpk); a slot's index is the location
        # id and the ebp path names the map. The tri_a01..tri_t05 maps must fill exactly the ids 1155..1311 and no
        # other id may hold a tri_ map or lie inside that range with another map.
        table, slots = 0x1EE23A0, 1312

        def name(va):
            chunk = self.at(va, 80)
            return chunk[:chunk.index(b'\0')].decode('latin1').split('\\')[-1]

        rows = {}
        for i in range(slots):
            (pointer,) = struct.unpack('<Q', self.at(table + 24 * i, 8))
            if pointer:
                rows[i] = name(pointer)
        tri = {i: n for i, n in rows.items() if n.startswith('tri_')}
        self.assertEqual(len(tri), 100)
        self.assertEqual((min(tri), max(tri)), (1155, 1311))
        self.assertEqual({i: n for i, n in rows.items() if 1155 <= i <= 1311 and not n.startswith('tri_')}, {})
        self.assertEqual(tri[1155], 'tri_a01.ebp')
        self.assertEqual(tri[1311], 'tri_t05.ebp')

    def test_trial_stage_maps_are_named_in_the_executable(self):
        paths = re.findall(rb'plan_map\\tri_[a-z]\\area\\tri_[a-z]\.nmb', self.data)
        self.assertTrue(paths)


if __name__ == '__main__':
    unittest.main()
