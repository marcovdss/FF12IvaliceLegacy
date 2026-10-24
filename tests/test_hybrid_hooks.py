from pathlib import Path
import unittest
try:
    from lupa import LuaRuntime
except ImportError:
    LuaRuntime = None


@unittest.skipIf(LuaRuntime is None, 'install lupa for optional Lua transaction tests')
class HookTransactionTest(unittest.TestCase):
    def setUp(self):
        self.lua = LuaRuntime(unpack_returned_tuples=True)
        module = Path(__file__).resolve().parents[1]/'native/hybrid_boards/hooks.lua'
        self.module = self.lua.execute(module.read_text())
        self.mem = {100: [1]*5, 200: [2]*5}
        self.writes = []
        self.failure = None
        def read(address, count):
            return self.lua.table_from(self.mem[address][:count])
        def write(address, data):
            values = [data[i] for i in range(1, len(data)+1)]
            self.writes.append((address, values))
            if self.failure == ('partial', address) and values[0] == 0xe9:
                self.mem[address][:2] = values[:2]
                return False
            if self.failure == ('write', address) and values[0] == 0xe9:
                return False
            self.mem[address] = values
            return True
        self.api = self.lua.table_from({'readArray': read, 'writeArray': write})
        self.plan = self.lua.table_from([
            self.lua.table_from({'address': address,
                                 'original': self.lua.table_from(self.mem[address]),
                                 'replacement': self.lua.table_from([0xe9, 0, 0, 0, 0])})
            for address in (100, 200)])

    def test_complete_install_and_idempotent_rollback(self):
        handle = self.module.install(self.api, self.plan)
        self.assertTrue(handle.active)
        self.assertFalse(handle.patchesRestored)
        self.assertTrue(handle.rollback())
        self.assertTrue(handle.rollback())
        self.assertTrue(handle.patchesRestored)
        self.assertEqual(self.mem, {100: [1]*5, 200: [2]*5})

    def test_mismatch_and_overlap_make_no_writes(self):
        self.mem[200][0] = 3
        result, error = self.module.install(self.api, self.plan)
        self.assertIsNone(result)
        self.assertIn('differ', error)
        self.assertFalse(self.writes)
        self.plan[2].address = 102
        result, error = self.module.install(self.api, self.plan)
        self.assertIsNone(result)
        self.assertIn('overlapping', error)
        self.assertFalse(self.writes)

    def test_failed_second_write_restores_first(self):
        self.failure = ('write', 200)
        result, error, handle = self.module.install(self.api, self.plan)
        self.assertIsNone(result)
        self.assertTrue(handle.patchesRestored)
        self.assertEqual(self.mem, {100: [1]*5, 200: [2]*5})

    def test_partial_write_retains_native_allocations(self):
        self.failure = ('partial', 200)
        result, error, handle = self.module.install(self.api, self.plan)
        self.assertIsNone(result)
        self.assertFalse(handle.patchesRestored)
        self.assertEqual(self.mem[100], [1]*5)
        self.assertEqual(self.mem[200], [0xe9, 0, 2, 2, 2])

    def test_unknown_owner_prevents_unsafe_rollback(self):
        handle = self.module.install(self.api, self.plan)
        self.mem[100] = [9]*5
        self.assertFalse(handle.rollback())
        self.assertFalse(handle.patchesRestored)
        self.assertEqual(self.mem[100], [9]*5)
