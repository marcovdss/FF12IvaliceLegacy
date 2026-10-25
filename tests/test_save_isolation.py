"""Actual Lua source with synthetic memory only; no running-game access."""
from pathlib import Path
import hashlib
import tempfile
import unittest
from unittest.mock import patch

try:
    from lupa.lua55 import LuaRuntime
except ImportError:
    LuaRuntime = None

from ivalice_legacy import save_isolation

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT/'package'/save_isolation.SCRIPT_PATH
BASE = 0x120000
TARGETS = {
    0xD12200-BASE: ('%sFFXII_%03d\0'.encode('utf-16le'), '%sLEGCY_%03d\0'.encode('utf-16le')),
    0xD113D4-BASE: (b'FFXII_\0', b'LEGCY_\0'),
}
HEADERS = {0:b'MZ',0x3C:b'\x80\x01\0\0',0x180:b'PE\0\0\x64\x86',
           0x188:b'\xd7\x7f\xca\x5d',0x198:b'\x0b\x02',0x1D0:b'\0\x60\xf7\x02'}


class Fixture:
    def __init__(self, base=BASE, states=(False,False), string_reads=False, failure=None, failure_write_number=1):
        self.runtime = LuaRuntime(unpack_returned_tuples=True, encoding=None)
        self.base,self.memory,self.writes,self.logs = base,{},[],[]
        self.reads,self.failure,self.failed,self.string_reads = {},failure,False,string_reads
        self.failure_write_number = failure_write_number
        for rva, data in HEADERS.items(): self.put(base+rva,data)
        for (rva,(old,new)),state in zip(TARGETS.items(),states): self.put(base+rva,new if state else old)
        self.before = dict(self.memory)
        self.runtime.globals()[b'fixtureRead'] = self.read
        self.runtime.globals()[b'fixtureWrite'] = self.write
        self.runtime.execute(b'memory = { readArray = function(a,n) return fixtureRead(a,n) end, writeArray = function(a,v) return fixtureWrite(a,v) end }')
        self.runtime.globals()[b'print'] = lambda value:self.logs.append(value.decode())
        self.runtime.execute(SCRIPT.read_bytes())
        # The early-init body must never race ahead without the module base.
        if self.writes: raise AssertionError('Top-level source wrote memory')

    def put(self,address,data):
        self.memory.update({address+i:value for i,value in enumerate(data)})

    def read(self,address,count):
        self.reads[address] = self.reads.get(address,0)+1
        if self.failure=='read' and address==self.base+next(iter(TARGETS)): raise RuntimeError('read failure')
        if self.failure=='concurrent-before-write' and address==self.base+next(iter(TARGETS)) and self.reads[address]==2:
            self.memory[address]=0x7e
        raw=bytes(self.memory[address+i] for i in range(count))
        return raw if self.string_reads else self.runtime.table_from(list(raw))

    def write(self,address,values):
        data=bytes(values[i] for i in range(1,len(values)+1))
        self.writes.append(address)
        failure=self.failure
        if failure=='stuck-partial' and self.failed:
            return False
        if not self.failed and len(self.writes)==self.failure_write_number and failure in ('false','ignored','throw','partial','foreign','stuck-partial'):
            self.failed=True
            if failure=='false':return False
            if failure=='ignored':return True
            if failure in ('partial','foreign','stuck-partial'):
                self.put(address,data[:len(data)//2])
                if failure=='foreign':self.memory[address]=0x7e
            if failure=='stuck-partial':return False
            raise RuntimeError('synthetic write failure')
        self.put(address,data)
        return True

    def run(self,base=None):
        return self.runtime.globals()[b'init'](self.base if base is None else base)


@unittest.skipUnless(LuaRuntime,'Lupa Lua5.5 is required for actual-source memory tests')
class SaveIsolationLuaTest(unittest.TestCase):
    def test_cold_mixed_and_idempotent_states_at_both_module_bases(self):
        for base in (BASE,0x140000000):
            for strings in (False,True):
                for states in ((False,False),(True,False),(False,True),(True,True)):
                    with self.subTest(base=base,string_reads=strings,states=states):
                        fixture=Fixture(base,states,strings)
                        self.assertTrue(fixture.run())
                        for rva,(_,new) in TARGETS.items():
                            self.assertEqual(bytes(fixture.memory[base+rva+i] for i in range(len(new))),new)
                        before=list(fixture.writes)
                        self.assertTrue(fixture.run())
                        self.assertEqual(fixture.writes,before)
                        self.assertIn('early-init readback verified',fixture.logs[-1])

    def test_wrong_base_build_or_target_makes_no_writes(self):
        for bad in ('base','header','target'):
            with self.subTest(bad=bad):
                fixture=Fixture()
                if bad=='header':fixture.memory[BASE+0x188]=0
                if bad=='target':fixture.memory[BASE+next(iter(TARGETS))]=0
                before=dict(fixture.memory)
                self.assertFalse(fixture.run(BASE+0x1000 if bad=='base' else BASE))
                self.assertEqual(fixture.memory,before)
                self.assertFalse(fixture.writes)
                self.assertIn('FATAL',fixture.logs[-1])

    def test_false_ignored_thrown_and_partial_writes_restore_exact_snapshot(self):
        for failure in ('false','ignored','throw','partial'):
            for states in ((False,False),(True,False),(False,True)):
                with self.subTest(failure=failure,states=states):
                    fixture=Fixture(states=states,failure=failure)
                    self.assertFalse(fixture.run())
                    self.assertEqual(fixture.memory,fixture.before)
                    self.assertIn('initial bytes preserved',fixture.logs[-1])

    def test_read_error_refuses_all_writes(self):
        fixture=Fixture(failure='read')
        self.assertFalse(fixture.run())
        self.assertFalse(fixture.writes)
        self.assertEqual(fixture.memory,fixture.before)

    def test_second_patch_failure_restores_both_attempted_targets(self):
        for failure in ('false','ignored','throw','partial'):
            with self.subTest(failure=failure):
                fixture=Fixture(failure=failure,failure_write_number=2)
                self.assertFalse(fixture.run())
                self.assertEqual(fixture.memory,fixture.before)
                self.assertIn('initial bytes preserved',fixture.logs[-1])

    def test_unrestorable_partial_patch_reports_restart_not_success(self):
        fixture=Fixture(failure='stuck-partial')
        self.assertFalse(fixture.run())
        self.assertNotEqual(fixture.memory,fixture.before)
        self.assertIn('rollback not verified, restart required',fixture.logs[-1])
        self.assertNotIn('early-init readback verified',fixture.logs[-1])

    def test_unknown_concurrent_bytes_are_not_overwritten_by_rollback(self):
        fixture=Fixture(failure='foreign')
        self.assertFalse(fixture.run())
        address=BASE+next(iter(TARGETS))
        self.assertEqual(fixture.memory[address],0x7e)
        self.assertEqual(fixture.writes,[address])
        self.assertIn('rollback not verified',fixture.logs[-1])

    def test_concurrent_change_before_write_refuses_that_write(self):
        fixture=Fixture(failure='concurrent-before-write')
        self.assertFalse(fixture.run())
        self.assertFalse(fixture.writes)
        self.assertEqual(fixture.memory[BASE+next(iter(TARGETS))],0x7e)

    def test_missing_api_is_reported_without_uncaught_error(self):
        fixture=Fixture()
        fixture.runtime.globals()[b'memory']=None
        self.assertFalse(fixture.run())
        self.assertIn('API unavailable',fixture.logs[-1])


class SaveIsolationBuildTest(unittest.TestCase):
    def test_preflight_accepts_only_the_pinned_executable_and_loader(self):
        with tempfile.TemporaryDirectory() as directory:
            game=Path(directory)
            exe=game/'x64/FFXII_TZA.exe';loader=game/'x64/modules/ff12-lua-loader.dll'
            loader.parent.mkdir(parents=True);exe.write_bytes(b'fixture exe');loader.write_bytes(b'fixture loader')
            with patch.object(save_isolation,'EXE_SHA256',hashlib.sha256(exe.read_bytes()).hexdigest()),\
                 patch.object(save_isolation,'LOADER_SHA256',hashlib.sha256(loader.read_bytes()).hexdigest()):
                self.assertEqual(save_isolation.validate_runtime(game)['earlyInitScript'],save_isolation.SCRIPT_PATH)
                loader.write_bytes(b'changed')
                with self.assertRaisesRegex(ValueError,'Unsupported luaLoader'):save_isolation.validate_runtime(game)

    def test_only_early_init_path_is_packaged(self):
        self.assertTrue(SCRIPT.is_file())
        self.assertFalse((ROOT/'package/x64/scripts/IvaliceLegacySaves.lua').exists())
