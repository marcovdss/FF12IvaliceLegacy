"""Emulates the Proficiency gear-text patch (GEAR_CODE in prototypes/proficiency_runtime.lua) with Keystone + Unicorn.

The game's helpers are replaced by stubs at their real addresses: 0x282EA0 (menu slot -> index) and 0x31C4C0
(index -> party member). The stack holds the return addresses of the real call chain (handler <- 0x247510 <- help
opener <- menu). Checks: the suffix is appended for eligible equipment asked for by the Equipment menu only,
everything else is left alone, the displaced instructions still run, registers the caller relies on are preserved,
the stack is balanced and the caller record is written.
"""
import re
import struct
import unittest
from pathlib import Path

try:
    import keystone
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    keystone = None

ROOT = Path(__file__).resolve().parents[1]
CODE_AT, DATA_AT, STACK_AT = 0x10000000, 0x20000000, 0x30000000
ROOT_PTR, MENU = 0x0209AC30, 0x21000000


def gear_code():
    source = (ROOT/'prototypes/proficiency_runtime.lua').read_text(encoding='utf-8')
    code = re.search(r"local GEAR_CODE = \[\[\n(.*?)\]\]", source, re.S).group(1)
    return code.replace('.dq 0x00', '.quad 0')


@unittest.skipIf(keystone is None, 'requires keystone-engine and unicorn')
class GearHookTest(unittest.TestCase):
    def run_hook(self, item_id, slot=1, member_of_index=3, suffix=b'\x20\x21', text=b'\x30\x31\x32',
                 frame=(0x247549, 0x2932A4, 0x3FF978), on_stack=True):
        ks = keystone.Ks(keystone.KS_ARCH_X86, keystone.KS_MODE_64)
        code, _ = ks.asm(gear_code(), CODE_AT)
        code = bytes(code)
        uc = unicorn.Uc(unicorn.UC_ARCH_X86, unicorn.UC_MODE_64)
        for base, size in ((CODE_AT, 0x10000), (DATA_AT, 0x100000), (STACK_AT, 0x10000), (MENU, 0x10000),
                           (0x02090000, 0x20000), (0x00280000, 0x10000), (0x00310000, 0x10000), (0x00290000, 0x10000)):
            uc.mem_map(base, size)
        uc.mem_write(CODE_AT, code)
        # Symbol slots are the last four quads of the assembled block (bits, table, buffer, probe).
        slots = CODE_AT + len(code) - 32
        bits, table, buffer, suffix_at, text_at, entry, probe = (DATA_AT + o for o in
                                                                (0, 0x1000, 0x2000, 0x3000, 0x4000, 0x5010, 0x7000))
        rsp = STACK_AT + 0x8000
        if on_stack:
            entry = rsp + 0x130                            # the opener's text buffer (opener rsp+0x60)
        uc.mem_write(slots, struct.pack('<QQQQ', bits, table, buffer, probe))
        uc.mem_write(entry - 0x10, struct.pack('<I', 0x2B))
        stages = bytearray(0x400)
        stages[0], stages[2], stages[3] = 1, 2, 3            # weapon, body armour, helmet; others excluded
        uc.mem_write(bits, bytes(stages))
        for stage in range(1, 4):
            stage_at = suffix_at + (stage - 1) * 0x100
            uc.mem_write(table + 8 * (member_of_index * 3 + stage - 1), struct.pack('<Q', stage_at))
            uc.mem_write(stage_at, (suffix if stage == 1 else bytes([0x20, 0x21 + stage - 1])) + b'\0')
        uc.mem_write(suffix_at, suffix + b'\0')
        uc.mem_write(text_at, text + b'\0')
        uc.mem_write(entry - 0x0A, struct.pack('<H', item_id))
        uc.mem_write(ROOT_PTR, struct.pack('<Q', MENU))
        uc.mem_write(MENU + 0xDE0, struct.pack('<H', slot))
        uc.mem_write(0x00282EA0, bytes([0x89, 0xC8, 0xC3]))                     # mov eax,ecx ; ret
        uc.mem_write(0x0031C4C0, bytes([0x8D, 0x41, 0x02, 0xC3]))               # lea eax,[rcx+2] ; ret
        uc.mem_write(0x00292672, b'\xf4')                                       # return target: hlt
        # Handler return, then the opener's return and the menu's return around the opener's text buffer.
        for at, value in zip((rsp + 0x78, entry - 0x68, entry + 0x228), frame):
            uc.mem_write(at, struct.pack('<Q', value))
        panel_at = DATA_AT + 0x8000
        uc.mem_write(panel_at + 0x60, struct.pack('<Q', 0xABCD))
        rdi = DATA_AT + 0x6000
        for reg, value in ((X.UC_X86_REG_RSP, rsp), (X.UC_X86_REG_RBX, text_at), (X.UC_X86_REG_R14, entry),
                           (X.UC_X86_REG_RDI, rdi), (X.UC_X86_REG_RSI, panel_at), (X.UC_X86_REG_R12, 0x5555),
                           (X.UC_X86_REG_R13, 0x6666), (X.UC_X86_REG_RCX, 0x7777)):
            uc.reg_write(reg, value)
        uc.emu_start(CODE_AT, 0x00292672 + 1, count=20000)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RIP), 0x00292673)              # reached the original continuation
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RSP), rsp)                      # stack balanced
        for reg, value in ((X.UC_X86_REG_R12, 0x5555), (X.UC_X86_REG_R13, 0x6666), (X.UC_X86_REG_RSI, panel_at),
                           (X.UC_X86_REG_RDI, rdi), (X.UC_X86_REG_R14, entry)):
            self.assertEqual(uc.reg_read(reg), value)
        self.assertEqual(uc.reg_read(X.UC_X86_REG_RCX), 0)                        # xor ecx,ecx still runs
        shown = uc.reg_read(X.UC_X86_REG_RBX)
        self.assertEqual(struct.unpack('<Q', bytes(uc.mem_read(rdi + 0x18, 8)))[0], shown)
        out = bytes(uc.mem_read(shown, 0x40))
        record = struct.unpack('<QQQII', bytes(uc.mem_read(probe, 0x20)))
        expected = tuple(frame) if on_stack else (frame[0], 0, 0)
        self.assertEqual(record, expected + (item_id, 1))                         # caller record written
        return shown, out[:out.index(b'\0')], buffer, text_at

    def test_eligible_equipment_gets_the_suffix(self):
        shown, text, buffer, _ = self.run_hook(0x1000)
        self.assertEqual((shown, text), (buffer, b'\x30\x31\x32\x20\x21'))

    def test_body_armour_and_helmet_select_their_own_stage(self):
        for item, suffix in ((0x1000, b'\x20\x21'), (0x1002, b'\x20\x22'), (0x1003, b'\x20\x23')):
            shown, text, buffer, _ = self.run_hook(item)
            self.assertEqual((shown, text), (buffer, b'\x30\x31\x32' + suffix))

    def test_other_items_are_left_alone(self):
        for item in (0x1001, 0x1004, 0x0FFF, 0x2000, 0x1400):                            # not eligible, below, above
            shown, text, _, original = self.run_hook(item)
            self.assertEqual((shown, text), (original, b'\x30\x31\x32'))

    def test_equipment_gear_list_also_gets_the_suffix(self):
        shown, text, buffer, _ = self.run_hook(0x1000, frame=(0x247549, 0x2932A4, 0x3FE2DA))
        self.assertEqual((shown, text), (buffer, b'\x30\x31\x32\x20\x21'))

    def test_first_help_of_a_menu_also_gets_the_suffix(self):
        # The first help text of a menu arrives through window creation (opener return 0x293286).
        for menu in (0x3FF978, 0x3FE2DA):
            shown, text, buffer, _ = self.run_hook(0x1000, frame=(0x247549, 0x293286, menu))
            self.assertEqual((shown, text), (buffer, b'\x30\x31\x32\x20\x21'))

    def test_other_menus_keep_the_plain_text(self):
        for caller in (0x563E47, 0x3FF737, 0x2C5E3E, 0):                         # Inventory, clear, Status, none
            shown, text, _, original = self.run_hook(0x1000, frame=(0x247549, 0x2932A4, caller))
            self.assertEqual((shown, text), (original, b'\x30\x31\x32'))

    def test_unexpected_frames_keep_the_plain_text(self):
        for frame in ((0x247549, 0x2932A5, 0x3FF978), (0x247549, 0, 0x3FF978)):
            shown, text, _, original = self.run_hook(0x1000, frame=frame)
            self.assertEqual((shown, text), (original, b'\x30\x31\x32'))
        shown, text, _, original = self.run_hook(0x1000, on_stack=False)          # text not in a stack frame
        self.assertEqual((shown, text), (original, b'\x30\x31\x32'))

    def test_no_character_or_no_suffix(self):
        for kwargs in ({'slot': 0xFFFF}, {'slot': 9}, {'member_of_index': 7}):
            shown, text, _, original = self.run_hook(0x1002, **kwargs)
            self.assertEqual((shown, text), (original, b'\x30\x31\x32'))


if __name__ == '__main__':
    unittest.main()
