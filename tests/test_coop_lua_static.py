"""Static checks for the co-op Lua modules that need no Lua runtime: the generated schema view is current, and the page
code never uses the debug op or the async execute."""
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LUA = ROOT / 'package/x64/scripts/IvaliceLegacy/coop'


class CoopLuaStatic(unittest.TestCase):
    def test_schema_data_is_current(self):
        r = subprocess.run([sys.executable, '-I', str(ROOT / 'native/coop/schema/gen_lua_schema.py'), '--check'],
                           capture_output=True, text=True)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)

    def test_no_debug_op_or_async_execute(self):
        for f in list(LUA.glob('*.lua')) + [ROOT / 'package/x64/scripts/IvaliceLegacyCoop.lua']:
            text = f.read_text(encoding='utf-8')
            for bad in ('TEST_SAFE_POINT', '0x7F01', 'executea', 'readPointer'):
                self.assertNotIn(bad, text, f'{f.name}: {bad}')

    def test_package_never_ships_coop_json(self):
        """coop.json is machine-written and seeded at run time (the settings file is created by the store): no copy of it may be packaged."""
        package = ROOT / 'package'
        shipped = [p for p in package.rglob('*') if p.is_file() and p.name.lower().startswith('coop.json')]
        self.assertEqual(shipped, [])

    def test_only_the_page_timer_edits_menu_rows(self):
        for name in ('flows', 'dialogs', 'store', 'views', 'statusview', 'bridge', 'codec'):
            lines = (LUA / f'{name}.lua').read_text(encoding='utf-8').splitlines()
            text = '\n'.join(line.split('--')[0] for line in lines)          # comments may name them; code may not
            for bad in ('modmenu', 'setValue', 'setStatus', 'setActive', 'setVisible'):
                self.assertNotIn(bad, text, f'{name}.lua: {bad}')


if __name__ == '__main__':
    unittest.main()
