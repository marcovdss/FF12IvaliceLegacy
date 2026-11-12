"""Description updates preserve shared metadata and fail without partial edits."""
from pathlib import Path
import unittest

try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None

SCRIPTS = Path(__file__).resolve().parents[1] / 'package/x64/scripts/IvaliceLegacy'


@unittest.skipIf(LuaRuntime is None, 'requires lupa')
class DescriptionFixesTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().fixes = self.lua.execute((SCRIPTS / 'description_fixes.lua').read_text())
        self.lua.globals().corrections = self.lua.execute((SCRIPTS / 'description_data.lua').read_text())

    def test_all_accessories_and_false_defense_claim(self):
        self.lua.execute('''
          local base = {database={equipment={},items={[45]={notes='Water damage.'}}}}
          local existing = {name='Orrachea Armlet',notes='Adds defense.',license='Accessories 1',custom=17}
          base.database.equipment[4443] = existing
          local returned = fixes.decorate(base, corrections)
          assert(returned == base and base.database.equipment[4443] == existing)
          assert(existing.name == 'Cachusha' and existing.notes == '')
          assert(existing.license == 'Accessories 1' and existing.custom == 17)
          assert(base.database.items[45].notes == 'Water damage.')
          local count = 0
          for id, row in pairs(base.database.equipment) do
            assert(type(row.notes) == 'string' and #row.notes <= 90)
            if id >= 4436 and id <= 4483 then count = count + 1 end
          end
          assert(count == 48)
          assert(base.database.equipment[4480].notes == '')
          assert(base.database.equipment[4442].notes == '')
          assert(base.database.equipment[4448].notes == 'Returns 5% of damage taken to the attacker.')
          assert(base.database.equipment[4453].notes == 'Blocks instant-death effects.')
          assert(base.database.equipment[4471].name == 'Battle Harness')
          assert(base.database.equipment[4471].notes == 'Chance to counterattack after a parry or block.')
          assert(base.database.equipment[4483].notes == 'Reduces maximum MP to 0.')
          assert(base.database.equipment[4458].name == 'Zephyr Collar')
          assert(base.database.equipment[4458].notes == 'Physical attacks deal 20% more damage.')
          for _, row in pairs(base.database.equipment) do
            assert(not row.notes:find('Libra information', 1, true))
          end
        ''')

    def test_guest_gear_identities_and_false_license(self):
        self.lua.execute('''
          local mythril = {name='Mythril Sword',license='Smallswords',notes=''}
          local base = {database={items={},equipment={[4256]=mythril,[4262]={name='Nightmare',license='',notes=''}}}}
          fixes.decorate(base, corrections)
          local equipment = base.database.equipment
          assert(mythril.license == '' and equipment[4256] == mythril)
          assert(equipment[4260].notes == 'Damage scales with Magick Power.')
          assert(equipment[4262].name == 'Nightmare')
          assert(equipment[4265].name == 'Nightmare II')
          assert(equipment[4564].name == "Lamont's Sword")
          assert(equipment[4565].name == "Lamont's Guard")
          assert(equipment[4581].name == "Reddas's Guard")
          local ok = pcall(fixes.decorate, base, {equipment={[4256]={notes='',license=7}}})
          assert(not ok and mythril.license == '')
        ''')

    def test_invalid_batch_does_not_partially_change_shared_rows(self):
        self.lua.execute('''
          local base = {database={equipment={[1]={notes='Original'}}}}
          local ok = pcall(fixes.decorate, base, {equipment={[1]={notes='Changed'},[2]={notes=' '}}})
          assert(not ok and base.database.equipment[1].notes == 'Original')
          assert(base.database.equipment[2] == nil)
          ok = pcall(fixes.decorate, base, {equipment={[1]={notes='Changed',name=42}}})
          assert(not ok and base.database.equipment[1].notes == 'Original')
        ''')


if __name__ == '__main__':
    unittest.main()
