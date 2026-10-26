"""Clan rank reader: the game's rank rule on synthetic memory, and the diagnostic log line."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

ROOT = Path(__file__).resolve().parents[1]
# Steam build's rank table at 0xD105D0: (points, marks, den) for ranks 1..12.
TABLE = [(0, 0, 0), (700, 2, 0), (8000, 4, 0), (20000, 8, 0), (30000, 10, 0), (40000, 12, 0),
         (100000, 14, 0), (200000, 16, 0), (250000, 24, 0), (300000, 28, 0), (500000, 32, 0),
         (1000000, 44, 30)]


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ClanRankTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().R = self.lua.execute((ROOT / 'prototypes/clan_rank.lua').read_text())
        self.lua.execute('''
          bytes={}
          function put32(a,v) for i=0,3 do bytes[a+i]=(v>>(8*i))&0xFF end end
          function get32(a) local v=0 for i=3,0,-1 do v=(v<<8)|(bytes[a+i] or 0) end return v end
          function setup(rows, points, marks, den)
            bytes={}
            for r,row in ipairs(rows) do
              local a=R.RANK_TABLE+8*(r-1)
              put32(a,row[1]); bytes[a+4]=row[2]; bytes[a+5]=row[3]
            end
            put32(R.POINTS, points)
            for i=0,den-1 do local a=R.DEN_BITS+i//8; bytes[a]=(bytes[a] or 0)|(1<<(i%8)) end
            mem={u8=function(a) return bytes[a] or 0 end, u32=get32, marks=function() return marks end}
          end
        ''')
        self.lua.globals().rows = self.lua.table_from([self.lua.table_from(r) for r in TABLE])

    def read(self, points, marks, den=0, rows=None):
        if rows is not None:
            self.lua.globals().rows = self.lua.table_from([self.lua.table_from(r) for r in rows])
        self.lua.globals().setup(self.lua.globals().rows, points, marks, den)
        result = self.lua.eval('{R.read(mem)}')
        clan, why = result[1], result[2]
        return (dict(clan) if clan else None), why

    def test_addresses_match_static_findings(self):
        self.assertEqual(self.lua.eval('R.POINTS'), 0x2164280 + 0xBA68)
        self.assertEqual(self.lua.eval('R.DEN_BITS'), 0x2164280 + 0x8200 + 0x3844)

    def test_both_points_and_marks_are_required(self):
        self.assertEqual(self.read(0, 0)[0]['rank'], 1)
        self.assertEqual(self.read(700, 2)[0]['rank'], 2)
        self.assertEqual(self.read(699, 2)[0]['rank'], 1)
        self.assertEqual(self.read(700, 1)[0]['rank'], 1)
        self.assertEqual(self.read(9_999_999, 3)[0]['rank'], 2)
        self.assertEqual(self.read(700, 40)[0]['rank'], 2)

    def test_every_threshold_is_exact(self):
        for rank, (points, marks, den) in enumerate(TABLE, 1):
            self.assertEqual(self.read(points, marks, den)[0]['rank'], rank)
            if rank > 1:
                self.assertEqual(self.read(points, marks - 1, den)[0]['rank'], rank - 1)

    def test_top_rank_needs_all_den_bits(self):
        self.assertEqual(self.read(1_000_000, 45, 29)[0]['rank'], 11)
        clan = self.read(1_000_000, 45, 30)[0]
        self.assertEqual((clan['rank'], clan['den'], clan['marks']), (12, 30, 45))

    def test_other_executable_is_refused(self):
        changed = list(TABLE)
        changed[1] = (700, 3, 0)
        self.assertEqual(self.read(700, 2, rows=changed), (None, 'rank table'))

    def test_bad_marks_are_refused(self):
        self.lua.globals().setup(self.lua.globals().rows, 0, -1, 0)
        self.assertEqual(self.lua.eval('select(2, R.read(mem))'), 'marks')


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class DiagnosticClanLineTests(unittest.TestCase):
    def load_line(self, with_reader=True):
        lua = LuaRuntime(unpack_returned_tuples=True)
        lua.globals().ROOT = str(ROOT).replace('\\', '/')
        lua.globals().WITH_READER = with_reader
        lua.execute('''
          lines={}
          print=function(t) lines[#lines+1]=t end
          local files={
            ['scripts/TheInsurgentsForge/functions/171.lua']='return function() end',
            ['scripts/IvaliceLegacy/clan_diagnostic.lua']=[[return {new=function()
              return {reset=function() end, load=function() return true end,
                      wrap=function(_, f) return f end} end}]],
          }
          local realLoadfile=loadfile
          loadfile=function(path, mode, env)
            if path=='scripts/IvaliceLegacy/clan_rank.lua' then
              if not WITH_READER then return nil end
              return realLoadfile(ROOT..'/prototypes/clan_rank.lua', mode, env)
            end
            return load(assert(files[path], path), path, mode, env)
          end
          handlers={}
          event={registerEventSync=function() return true end,
                 registerLoadHandler=function(_, f) handlers.load=f return true end}
          arg={s32='s32'}
          local rows={{0,0,0},{700,2,0},{8000,4,0},{20000,8,0},{30000,10,0},{40000,12,0},
            {100000,14,0},{200000,16,0},{250000,24,0},{300000,28,0},{500000,32,0},{1000000,44,30}}
          local u8, u32 = {}, {}
          for r,row in ipairs(rows) do
            local a=0xD105D0+8*(r-1); u32[a]=row[1]; u8[a+4]=row[2]; u8[a+5]=row[3]
          end
          u32[0x2164280+0xBA68]=8500
          memory={u8=setmetatable({},{__index=function(_,a) return u8[a] or 0 end}),
                  u32=setmetatable({},{__index=function(_,a) return u32[a] or 0 end}),
                  u64={}, readArray=function() return nil end,
                  execute=function(address, ret) assert(address==0x316480 and ret=='s32') return 5 end}
        ''')
        lua.execute((ROOT / 'prototypes/forge_diagnostic_171.lua').read_text())
        lua.execute("handlers.load('C:/saves/LEGCY_001')")
        return lua.eval('lines[#lines]')

    def test_line_reports_rank_marks_and_points(self):
        self.assertEqual(self.load_line(),
                         'Legacy diagnostic: clanRank=3 marks=5 clanPoints=8500 den=0 bonuses=off')

    def test_missing_reader_only_changes_the_line(self):
        self.assertEqual(self.load_line(False), 'Legacy diagnostic: clan=missing bonuses=off')


if __name__ == '__main__':
    unittest.main()
