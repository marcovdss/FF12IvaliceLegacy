"""Shorter equipment descriptions: gear_layout.lua (DD custom attribute legacyExtras) + the template rewrite."""
import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from ivalice_legacy.description_layout import CUSTOM, NOTES, TEMPLATE_DIR, TEMPLATE_ICONS, WEAPON_ICONS, \
    equipment_template

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'package/x64/scripts/IvaliceLegacy/gear_layout.lua'
# The game folder: its x64/scripts holds Dynamic Description's engine and the deployed Legacy DD config.
GAME = os.environ.get('IVALICE_LEGACY_GAME')

STOCK = '''return [[

{dt:row itemspace:(1) groupspace:(1)}
  {dt:equipment:common content:(hitsFlying) args:(showIcon)}
  {dt:equipment:common content:(element) args:(showIcon)}
  {dt:equipment:common content:(category) args:(showIcon, showLabel)}
  {dt:equipment:common content:(type) args:(showLabel)}
  {dt:equipment:common content:(license) args:(showLabel)}
{/row}

{dt:row itemspace:(1) groupspace:(1) maxitems:(4)}
  {dt:equipment:attributes content:(attackPower, chargeTime, comboOrCriticalChance, evadeWeapon, knockbackChance, magickPower, maxHp, maxMp, onHitRate, range, speed, strength, vitality) args:(showLabel, showPercentage)}
{/row}

{dt:row itemspace:(1) groupspace:(1) maxitems:(10)}
  {dt:equipment:statusEffect content:(hit, equip, immune) args:(showLabel, showIcon)}
{/row}

{dt:row itemspace:(1) groupspace:(1) maxitems:(10)}
  {dt:equipment:elementalAffinity content:(weak, immune, half, absorb, potency) args:(showLabel, showIcon)}
{/row}

{dt:row itemspace:(1) groupspace:(1)}
  {dt:equipment:common content:(notes)}
{/row}

]]
'''


class TemplateTests(unittest.TestCase):
    def test_armour_template_loses_both_group_rows_and_lists_extras_after_the_stats(self):
        text = equipment_template(STOCK)
        rows = text.split('{/row}')
        self.assertEqual(len(rows), 4)  # header, stats, notes, tail
        self.assertTrue(rows[1].endswith(CUSTOM))
        self.assertNotIn('statusEffect', text)
        self.assertNotIn('elementalAffinity', text)
        self.assertIn('content:(notes)', rows[2])

    def test_weapon_template_moves_the_note_before_the_extras(self):
        text = equipment_template(STOCK, weapon=True)
        rows = text.split('{/row}')
        self.assertEqual(len(rows), 3)
        self.assertTrue(rows[1].endswith(NOTES + CUSTOM))

    def test_rewrite_keeps_crlf_templates_crlf(self):
        for weapon in (False, True):
            self.assertEqual(equipment_template(STOCK.replace('\n', '\r\n'), weapon),
                             equipment_template(STOCK, weapon).replace('\n', '\r\n'))

    def test_rewrite_refuses_twice_and_unknown_templates(self):
        with self.assertRaises(ValueError):
            equipment_template(equipment_template(STOCK))
        with self.assertRaises(ValueError):
            equipment_template(STOCK.replace('(hit, equip, immune)', '(equip)'))
        with self.assertRaises(ValueError):
            equipment_template(STOCK.replace('maxitems:(4)', 'maxitems:(5)'))


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class PackTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        g = self.lua.globals()
        g.L = self.lua.execute(MODULE.read_text())
        self.lua.execute('''
          sp = {low = '{lowspace}', wide = '{widespace}', newline = '\\n'}
          function part(n) return '{scale:82}{rgb:90,90,90}' .. string.rep('a', n - 2) .. ':{lowspace}{rgb:255,255,255}x' end
          function group(label, ...) return {_groupLabel = '{scale:82}{rgb:90,90,90}' .. label .. ':', values = {...}} end
          icon = '{scale:63}{vpos:-6}{icon:31}{vpos:0}{scale:82}{rgb:255,255,255}'
        ''')

    def test_width_counts_visible_text_icons_and_spaces(self):
        self.lua.execute('''
          assert(L.width('{scale:82}{rgb:1,2,3}Hit:{lowspace}' .. icon .. 'Sap') == 4 + 1 + 2 + 3)
          assert(L.width('a{widespace}b') == 4)
        ''')

    def test_groups_join_the_last_stats_line_when_they_fit(self):
        self.lua.execute('''
          local stats = {part(16), part(16), part(16), part(16), part(13), part(9)}
          local label, value = L.pack(stats, 4, {group('Hit', icon .. 'Sap')}, sp)
          assert(label == 'Hit' and value == icon .. 'Sap', value)
          label, value = L.pack(stats, 4, {group('Hit', icon .. 'Oil'), group('Amplify', icon .. 'Water')}, sp)
          assert(label == 'Hit' and value == icon .. 'Oil{widespace}{scale:82}{rgb:90,90,90}Amplify:{lowspace}' .. icon .. 'Water')
          assert(L.pack(stats, 4, {}, sp) == nil)
        ''')

    def test_a_full_stats_line_lets_dd_start_the_new_line(self):
        self.lua.execute('''
          local stats = {part(20), part(20), part(20), part(20)}
          local label, value = L.pack(stats, 4, {group('Weak', icon .. 'Wind')}, sp)
          assert(label == 'Weak' and not value:find('\\n'))
        ''')

    def test_first_group_too_wide_starts_its_own_line_and_later_groups_wrap(self):
        self.lua.execute('''
          local stats = {part(30), part(30)}
          local many = {}
          for i = 1, 16 do many[i] = icon .. 'Status' end
          local label, value = L.pack(stats, 4, {group('Immune', table.unpack(many)), group('Absorb', icon .. 'Fire')}, sp)
          assert(label == '\\nImmune', label)
          local lines = {}
          for line in (label .. ':{lowspace}' .. value):gmatch('[^\\n]+') do lines[#lines + 1] = line end
          assert(#lines == 3, #lines)
          for _, line in ipairs(lines) do assert(L.width(line) <= L.LIMIT, line) end
          assert(lines[3]:find('Absorb:'), lines[3])
        ''')

    def test_a_group_that_overflows_moves_to_the_next_line(self):
        self.lua.execute('''
          local stats = {part(18), part(18), part(18)}
          local label, value = L.pack(stats, 4, {group('Weak', icon .. 'Water'), group('Half', icon .. 'Lightning',
            icon .. 'Ice', icon .. 'Earth', icon .. 'Wind', icon .. 'Holy', icon .. 'Dark')}, sp)
          assert(label == 'Weak' and value:find('\\n{scale:82}{rgb:90,90,90}Half:', 1, true), value)
        ''')


def _resolve_copy(src, dst):
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(src.resolve(), dst)


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
@unittest.skipUnless(GAME, 'set IVALICE_LEGACY_GAME to render through the installed Dynamic Description')
class DynamicDescriptionRenderTests(unittest.TestCase):
    """gear_layout.apply and DD's own engine (installed copy) with the deployed Legacy DD config, new templates."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp())
        scripts = Path(GAME) / 'x64/scripts'
        for name in ('cache', 'parser', 'renderer', 'template', 'utils', 'mappings', 'schemas', 'engine', 'dd_api'):
            _resolve_copy(scripts / f'DynamicDescription/{name}.lua', self.root / f'scripts/DynamicDescription/{name}.lua')
        for name in ('tags', 'i18n', 'settings'):
            _resolve_copy(scripts / f'config/DynamicDescription/{name}.lua',
                          self.root / f'scripts/config/DynamicDescription/{name}.lua')
        for name in ('description_fixes', 'description_data'):
            _resolve_copy(scripts / f'IvaliceLegacy/{name}.lua', self.root / f'scripts/IvaliceLegacy/{name}.lua')
        for i in TEMPLATE_ICONS:
            stock = (Path(GAME) / TEMPLATE_DIR / f'{i}.lua').resolve().read_bytes().decode('utf-8')
            if 'legacyExtras' not in stock:
                stock = equipment_template(stock, i in WEAPON_ICONS)
            out = self.root / f'scripts/config/DynamicDescription/templates/equipment/{i}.lua'
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(stock)
        (self.root / 'export').mkdir()
        self.cwd = os.getcwd()
        os.chdir(self.root)

    def tearDown(self):
        os.chdir(self.cwd)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_every_kind_of_gear_renders_shorter(self):
        lua = LuaRuntime(unpack_returned_tuples=True)
        g = lua.globals()
        g.L = lua.execute(MODULE.read_text())
        g.json_encode = lambda t: json.dumps(t)
        lua.execute('''
          memory = {}
          for _, k in ipairs({'u8', 'u32', 's16', 's8', 'u64'}) do memory[k] = {} end
          for _, k in ipairs({'u8', 'u32', 's16', 's8', 'u64'}) do
            setmetatable(memory[k], {__index = function() return 0 end})
          end
          memory.getSymbol = function() return 0 end
          SAVED = nil
          config = {path = 'scripts/config', loadJson = function() return {} end,
                    saveJson = function(path, data) SAVED = data end}
          local m = L.dd()
          local function flags(names)
            local t = {}; for _, s in ipairs(m.mappings.statusEffects) do t[s] = 0 end
            for _, s in ipairs(names) do t[s] = 1 end; return t
          end
          local function bits(list, names)
            local v = 0; for i, s in ipairs(list) do for _, n in ipairs(names) do if s == n then v = v | (1 << (i - 1)) end end end
            return v
          end
          local function attr(at, equip, immune, potency)
            memory.u32[at + 8] = bits(m.mappings.statusEffects, equip or {})
            memory.u32[at + 12] = bits(m.mappings.statusEffects, immune or {})
            memory.u8[at + 20] = bits(m.mappings.elements, potency or {})
            return at
          end
          local none = flags({})
          local rows = {}
          for i = 0, 485 do rows[i] = nil end
          -- 4209 dagger + Sap; 4226 staff + Amplify Wind; 4260 Joyeuse (note); 4373 helm (note);
          -- 4315 shield + Equip x3; 4481 Ribbon (16 immunities + note)
          rows[113] = {category = 12, icon = 12, attackPower = 28, chargeTime = 15, comboOrCriticalChance = 17,
                       evadeWeapon = 7, onHitRate = 10, range = 10, statusEffects = flags({'sap'}), elements = {}, flags = {}}
          rows[130] = {category = 14, icon = 14, attackPower = 18, chargeTime = 34, evadeWeapon = 8, knockbackChance = 10,
                       range = 10, statusEffects = none, elements = {}, flags = {}, attributePointer = attr(0x5000, {}, {}, {'wind'})}
          memory.s8[0x5000 + 5] = 3
          rows[164] = {category = 1, icon = 1, attackPower = 40, chargeTime = 30, comboOrCriticalChance = 10,
                       evadeWeapon = 10, knockbackChance = 10, range = 10, statusEffects = none, elements = {}, flags = {}}
          rows[277] = {category = 19, icon = 22, magickResist = 46, augment = 21, statusEffects = none, elements = {},
                       flags = {}, attributePointer = attr(0x5100)}
          memory.s8[0x5100 + 5] = 4; memory.s8[0x5100 + 4] = 9
          rows[219] = {category = 18, icon = 18, evadeShield = 90, statusEffects = none, elements = {}, flags = {},
                       attributePointer = attr(0x5200, {'poison', 'sap', 'slow'})}
          rows[385] = {category = 21, icon = 27, statusEffects = none, elements = {}, flags = {},
                       attributePointer = attr(0x5300, {}, {'stone', 'petrify', 'stop', 'sleep', 'confuse', 'doom', 'blind',
                         'poison', 'silence', 'sap', 'oil', 'disable', 'immobilize', 'slow', 'disease', 'xZone'})}
          memory.s16[0x5300] = 35; memory.s16[0x5300 + 2] = 25
          bpack = {section13 = rows, section14 = {}}
          COUNT = L.apply()
          -- DD's own render, initialised as DynamicDescription.lua does, with the JSON gear_layout saved.
          local d = L.dd()
          d.renderer.initialize({i18n = d.i18n, tags = d.tags, mappings = d.mappings, schemas = d.schemas,
            utils = d.utils, cache = d.cache, customData = SAVED.customData, settings = d.settings})
          local engine = assert(loadfile('scripts/DynamicDescription/engine.lua', 't', setmetatable({}, {__index = _G})))()
          engine.initialize({parser = d.parser, template = d.template, renderer = d.renderer, utils = d.utils,
            cache = d.cache, settings = d.settings, tags = d.tags, mappings = d.mappings, exportPath = 'export'})
          L.templates(d)
          engine.run()
          local f = assert(io.open('export/us.lua')); RESULT = f:read('a'); f:close()
        ''')
        self.assertEqual(g.COUNT, 4)  # dagger, staff, shield, Ribbon (the helm and Joyeuse have no groups)
        lines = {}
        for line in g.RESULT.splitlines():
            line = line.strip()
            if line.startswith('{4'):
                lines[int(line[1:5])] = line.split('\\n')
        width = g.L.width
        for bid, text in lines.items():
            for row in text:
                self.assertLessEqual(width(row), 84, (bid, row))
        self.assertEqual(len(lines[4209]), 3)
        self.assertIn('Range:', lines[4209][2])
        self.assertIn('Hit:{lowspace}{rgb:255,255,255}{scale:63}{vpos:-6}{icon:31}', lines[4209][2])
        self.assertEqual(len(lines[4226]), 3)
        self.assertIn('Amplify:', lines[4226][2])
        self.assertEqual(len(lines[4260]), 3)
        self.assertIn('Damage scales with Magick Power.', lines[4260][2])
        self.assertEqual(len(lines[4373]), 3)
        self.assertIn('counter', lines[4373][2])
        self.assertEqual(len(lines[4315]), 2)
        self.assertIn('Equip:', lines[4315][1])
        ribbon = lines[4481]
        self.assertEqual(len(ribbon), 5, ribbon)
        self.assertIn('Immune:', ribbon[2])
        self.assertIn('instant-death', ribbon[4])


if __name__ == '__main__':
    unittest.main()
