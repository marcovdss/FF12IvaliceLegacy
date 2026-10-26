"""Clan ledger contracts: validation, grants and per-save sidecar sessions."""
from pathlib import Path
import unittest

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'requires Lupa Lua5.5')
class ClanLedgerTests(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        self.lua.globals().L = self.lua.execute(
            (Path(__file__).resolve().parents[1] / 'prototypes/clan_ledger.lua').read_text())
        self.lua.execute('''
          logs={}
          deps={isLegacySave=function(p) return type(p)=='string' and p:match('LEGCY_%d%d%d$')~=nil end,
                log=function(t) logs[#logs+1]=t end}
        ''')

    def test_class_keys(self):
        self.lua.execute('''
          assert(L.classKey(8,255)=='8' and L.classKey(8,nil)=='8')
          assert(L.classKey(8,4)=='4:8' and L.classKey(4,8)=='4:8')
          assert(L.classKey(8,8)==nil and L.classKey(255,255)==nil and L.classKey(12,255)==nil and L.classKey(8,12)==nil)
        ''')

    def test_grant_owns_and_immutability(self):
        self.lua.execute('''
          local a=L.empty()
          local b=L.grant(a,0,'8','hunter.pursuit')
          assert(not L.owns(a,0,'8','hunter.pursuit') and L.owns(b,0,'8','hunter.pursuit'))
          assert(not L.owns(b,1,'8','hunter.pursuit') and not L.owns(b,0,'4:8','hunter.pursuit'))
          local c=L.grant(b,0,'8','hunter.pursuit'); assert(#c.characters['0']['8']==1)
          assert(not pcall(L.grant,b,6,'8','x') and not pcall(L.grant,b,0,'8:8','x') and not pcall(L.grant,b,0,'8','Bad Id'))
        ''')

    def test_validation_rejects_malformed_ledgers(self):
        self.lua.execute('''
          assert(L.validate({schema=1,characters={}}))
          assert(L.validate({schema=1,characters={['0']={['8']={'a'}}}}))
          assert(L.validate({schema=1,characters={[0]={[8]={'a'}}}}))  -- numeric keys from JSON
          for _,bad in ipairs({
            'x', {}, {schema=2,characters={}}, {schema=1},
            {schema=1,characters={['6']={['8']={'a'}}}},
            {schema=1,characters={['00']={['8']={'a'}}}},
            {schema=1,characters={['0']={['8:4']={'a'}}}},
            {schema=1,characters={['0']={['8:8']={'a'}}}},
            {schema=1,characters={['0']={['8']={'a','a'}}}},
            {schema=1,characters={['0']={['8']={[1]='a',[3]='b'}}}},
            {schema=1,characters={['0']={['8']={'UPPER'}}}},
          }) do assert(L.validate(bad)==nil) end
        ''')

    def test_session_isolates_saves(self):
        self.lua.execute('''
          local s=L.session(deps)
          s.load('C:/saves/LEGCY_003',nil)
          assert(s.grant(0,'8','hunter.pursuit') and s.owns(0,'8','hunter.pursuit'))
          local stored=s.save('C:/saves/LEGCY_003')
          assert(stored.schema==1 and stored.characters['0']['8'][1]=='hunter.pursuit')
          s.load('C:/saves/LEGCY_004',nil)  -- another save without a sidecar
          assert(not s.owns(0,'8','hunter.pursuit'))
          s.load('C:/saves/LEGCY_003',stored)
          assert(s.owns(0,'8','hunter.pursuit'))
          s.load('C:/saves/FFXII_003',stored)  -- not a Legacy save
          assert(not s.owns(0,'8','hunter.pursuit') and s.save('C:/saves/FFXII_003')==nil)
          s.newGame(); assert(not s.owns(0,'8','hunter.pursuit'))
        ''')

    def test_unreadable_sidecar_is_kept_until_replaced(self):
        self.lua.execute('''
          local s=L.session(deps)
          s.load('C:/saves/LEGCY_001',{schema=99})
          assert(#logs==1 and logs[1]:find('unreadable',1,true))
          assert(s.save('C:/saves/LEGCY_001')==nil)  -- nil keeps the stored sidecar
          s.grant(1,'11','chemist.line')
          local stored=s.save('C:/saves/LEGCY_001')
          assert(stored and stored.characters['1']['11'][1]=='chemist.line')
        ''')

    def test_saved_ledger_is_a_copy_and_never_the_empty_delete_marker(self):
        self.lua.execute('''
          local s=L.session(deps)
          s.load('LEGCY_002',nil)
          local first=s.save('LEGCY_002')
          assert(first.schema==1 and next(first)~=nil)  -- {} would delete the sidecar
          first.characters['0']={['8']={'tampered'}}
          assert(not s.owns(0,'8','tampered'))
        ''')


if __name__ == '__main__':
    unittest.main()
