"""Host tests for input isolation (native/coop/src/isolation, src/input/pad_mirror). No game run, nothing deployed.

* handler bodies, called natively on synthetic device objects (vtables at the image twin's addresses): fail-open
  (no record, an epoch more than one away, subsystem bit off, wrong device class -> untouched; a record one epoch
  either side still acts); the pad policy (P1's own XInput device untouched, every other pad neutral with its
  packet number cleared); flee from the movement subsystem's word (which it sets only with GATE_WORLD and
  GATE_MOVEMENT open and MOVEMENT armed) onto P1's flee byte, rewritten from the real RT so it never sticks; P1 on
  SDL (host election, never a disconnected XInput object, the substituted block, the host's packet number
  cleared); release after isolation stops and the release primitives for a disarm; the keyboard filter (shared
  keys rebuilt from the raw sample through the game's key map) and its budget of 240 updates, refilled every beat;
  IN-06 (unfocused pad block keeps the focus flag at 0) and IN-07 (focus loss reads as active); IN-06a mismatch
  leaves IN-06 unhooked and reported; the record composition (guards, keyboard owner, release beats, a torn P1
  mirror read keeps the last source, no fourth seat); the co-op thread moves the liveness epoch right after the
  session step;
* unicorn differential with the game's own code (the hooked image twin, our DLL and the islands): device objects
  built by the game's constructors, polled by the game's XInput poll and the DirectInput/Steam finalize windows,
  through IN-01/02/03 into our handlers and the game's finalizer: disarmed = stock for every device; armed, P1's
  device equals stock while every other pad equals a neutral reference; flee reaches P1's device; a session epoch
  change while a guest holds a button gives no press edge;
* the substitution checkpoint restated (P1 on an SDL-only pad): P1's logical state substituted by our C++ through
  the hooked finalize calls gives a connected idle XInput object, an XInput object carrying another packet, a
  DirectInput object and a Steam object
  exactly the state the game's own XInput path produces from a real packet (platform bytes, stick and button
  nodes with edges, get-button and get-axis), and every instrument control fails.

Needs the pinned MSYS2 g++ (ILC_REQUIRE_NATIVE=1), IVALICE_LEGACY_GAME (exe read-only), unicorn and capstone.
"""
from __future__ import annotations

import ctypes
import random
import struct
import unittest

import test_coop_game_thread as gt
import test_coop_patch_engine as pe
from coop_native_support import SCHEMA as SC
from test_coop_patch_engine import GAME, NATIVE, OK, TWIN_BASE, TWIN_SIZE, Twin, checked

try:
    import unicorn
    from unicorn import x86_const as X
except ImportError:
    unicorn = None

# game facts (VAs, pinned base 0x120000)
VT_XI, VT_DI, VT_ST, VT_KB = 0x1CB2678, 0x1CB2638, 0x1CB26B8, 0x1CB2430
KEYMAP, FOCUS_FLAG, PAD_RESUME = 0x1F3DAB0, 0x2EA5213, 0x7A2243
SHARED_KEYS = (0x0C, 0x19, 0x25, 0x26, 0x27, 0x33, 0x56)
BLOCK, BLOCK_SIZE = 0x414, 0x3B

ISO_GUARD, ISO_SUBST, ISO_KB, ISO_FLEE, ISO_BG, ISO_RELEASE, ISO_NATIVE = 1, 2, 4, 8, 0x10, 0x20, 0x40
RUN_ORIGINAL, RESUME = 0, 1
SRC_NONE, SRC_XINPUT, SRC_SDL = 0, 1, 2
CONNECTED = 0x01
INPUT_ISOLATION = 0x01
EPOCH = 7
FLEE_ACTION = 6
KB_BUDGET = 240
STATS = ['passed', 'substituted', 'masked', 'released', 'stock', 'wrong_class', 'probe_failed', 'kb_filtered',
         'kb_expired', 'background', 'focus_kept', 'host_changes']


class IsoRecord(ctypes.Structure):
    _fields_ = [('epoch', ctypes.c_uint32), ('flags', ctypes.c_uint32), ('generation', ctypes.c_uint64),
                ('p1_slot', ctypes.c_uint8), ('kbm_seat', ctypes.c_uint8), ('guest_seats', ctypes.c_uint8),
                ('reserved0', ctypes.c_uint8), ('p1_vid', ctypes.c_uint16), ('p1_pid', ctypes.c_uint16),
                ('reserved1', ctypes.c_uint32)]


class Gamepad(ctypes.Structure):
    _fields_ = [('buttons', ctypes.c_uint16), ('lt', ctypes.c_uint8), ('rt', ctypes.c_uint8),
                ('lx', ctypes.c_int16), ('ly', ctypes.c_int16), ('rx', ctypes.c_int16), ('ry', ctypes.c_int16)]


class PadView(ctypes.Structure):
    _fields_ = [('seq', ctypes.c_uint32), ('generation', ctypes.c_uint32), ('source', ctypes.c_uint8),
                ('xinput_slot', ctypes.c_uint8), ('flags', ctypes.c_uint8), ('reserved', ctypes.c_uint8),
                ('act_held', ctypes.c_uint32), ('pad', Gamepad), ('reserved2', ctypes.c_uint8 * 4)]


class Status(ctypes.Structure):
    _fields_ = [('in06_armed', ctypes.c_uint32), ('published', ctypes.c_uint32), ('host', ctypes.c_uint64)]


class Ctx(ctypes.Structure):
    _fields_ = [('xmm', ctypes.c_uint64 * 12), ('resume', ctypes.c_uint64), ('reserved', ctypes.c_uint64)] + \
               [(n, ctypes.c_uint64) for n in ('rax', 'rcx', 'rdx', 'rbx', 'rsp', 'rbp', 'rsi', 'rdi', 'r8', 'r9',
                                               'r10', 'r11', 'r12', 'r13', 'r14', 'r15', 'rflags', 'index')]


assert ctypes.sizeof(IsoRecord) == 32 and ctypes.sizeof(PadView) == 32 and ctypes.sizeof(Ctx) == 0x100


def tdiv128(v: int) -> int:
    q = abs(v) // 128
    return q if v >= 0 else -q


XI_BUTTONS = [(0x4000, 0x43C), (0x1000, 0x43D), (0x2000, 0x43E), (0x8000, 0x43F), (0x0100, 0x440), (0x0200, 0x441),
              (0x0020, 0x444), (0x0010, 0x445), (0x0040, 0x446), (0x0080, 0x447), (0x0001, 0x448), (0x0008, 0x449),
              (0x0002, 0x44A), (0x0004, 0x44B)]


def reference_block(pad: tuple, flee: bool = False) -> bytes:
    """Our test's own statement of the game's XInput interpretation (0x7A228C..0x7A23BF), for cross-checking."""
    buttons, lt, rt, lx, ly, rx, ry = pad
    b = bytearray(BLOCK_SIZE)
    for off, v in ((0x414, tdiv128(lx)), (0x418, tdiv128(rx)), (0x420, -tdiv128(ly)), (0x424, -tdiv128(ry))):
        struct.pack_into('<i', b, off - BLOCK, v)
    for bit, off in XI_BUTTONS:
        b[off - BLOCK] = 1 if buttons & bit else 0
    b[0x442 - BLOCK] = 1 if lt else 0
    b[0x443 - BLOCK] = 1 if rt or flee else 0
    return bytes(b)


class Iso:
    """The test DLL's isolation exports, on the installed image twin."""
    _instance = None

    @classmethod
    def get(cls) -> 'Iso':
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def __init__(self):
        self.w = gt.world()
        self.twin = self.w.twin
        d = self.d = self.twin.dll
        d.ILCT_IsoPublish.argtypes = [ctypes.POINTER(IsoRecord)]
        d.ILCT_IsoBudget.restype = ctypes.c_int32
        d.ILCT_IsoSetBudget.argtypes = [ctypes.c_int32]
        d.ILCT_IsoVariant.argtypes = [ctypes.c_uint32]
        d.ILCT_IsoBlock.argtypes = [ctypes.POINTER(Gamepad), ctypes.c_uint32, ctypes.c_int32, ctypes.c_char_p]
        d.ILCT_IsoCall.argtypes = [ctypes.c_uint32, ctypes.POINTER(Ctx), ctypes.c_uint32]
        d.ILCT_IsoCall.restype = ctypes.c_int32
        d.ILCT_IsoStats.argtypes = [ctypes.POINTER(ctypes.c_uint64), ctypes.c_uint32]
        d.ILCT_IsoStatus.argtypes = [ctypes.POINTER(Status)]
        d.ILCT_IsoMirror.argtypes = [ctypes.c_uint32, ctypes.POINTER(PadView)]
        d.ILCT_IsoConfigure.argtypes = [ctypes.c_char_p]
        d.ILCT_IsoCompose.argtypes = [ctypes.c_uint32, ctypes.c_uint64, ctypes.c_char_p, ctypes.POINTER(IsoRecord)]
        d.ILCT_IsoFakeFlee.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        d.ILCT_IsoMirrorHold.argtypes = [ctypes.c_uint32, ctypes.c_int32]
        d.ILCT_IsoSlotAddress.restype = ctypes.c_uint64
        self.rows = {sid: self.twin.index[sid] for sid in ('IN-01', 'IN-02', 'IN-03', 'IN-05', 'IN-06', 'IN-07')}

    def setup(self):
        """Install on the twin, LIVE, armed, INPUT_ISOLATION open; isolation state reset."""
        self.w.setup()
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_IsoReset()
        for sid in ('IN-01', 'IN-02', 'IN-03', 'IN-05', 'IN-06', 'IN-07'):
            self.d.ILCT_PatchSetPassthrough(self.rows[sid], 0)
        assert self.d.ILCT_IsoAttach() == 1
        for seat in (1, 2, 3, 4):
            self.mirror(seat, SRC_NONE)

    def publish(self, flags: int, epoch: int = EPOCH, p1_slot: int = 0xFF, vid: int = 0, pid: int = 0,
                guests: int = 0b10, kbm: int = 0):
        r = IsoRecord(epoch, flags, 1, p1_slot, kbm, guests, 0, vid, pid, 0)
        self.d.ILCT_IsoPublish(ctypes.byref(r))

    def mirror(self, seat: int, source: int, slot: int = 0xFF, pad: tuple = (0, 0, 0, 0, 0, 0, 0), held: int = 0,
               connected: bool = True, seq: int = 1):
        v = PadView(seq, 1, source, slot, CONNECTED if connected and source else 0, 0, held, Gamepad(*pad))
        self.d.ILCT_IsoMirror(seat, ctypes.byref(v))

    def stats(self) -> dict:
        out = (ctypes.c_uint64 * len(STATS))()
        self.d.ILCT_IsoStats(out, len(STATS))
        return dict(zip(STATS, out))

    def block(self, pad: tuple, extra: int = 0, flee: bool = False) -> bytes:
        out = ctypes.create_string_buffer(BLOCK_SIZE)
        self.d.ILCT_IsoBlock(ctypes.byref(Gamepad(*pad)), extra, int(flee), out)
        return out.raw

    def call(self, which: int, ctx: Ctx, epoch: int = EPOCH) -> int:
        return self.d.ILCT_IsoCall(which, ctypes.byref(ctx), epoch)


class Device:
    """A synthetic device object in this process: vtable, platform block, XInput index and packet number."""

    def __init__(self, vtable: int, slot: int = 0, product: int = 0, size: int = 0x470):
        self.buf = ctypes.create_string_buffer(size + 16)
        self.addr = (ctypes.addressof(self.buf) + 15) & ~15
        self.poke(0, struct.pack('<Q', vtable))
        self.poke(0x1E4, struct.pack('<I', product))
        if vtable == VT_XI:
            self.poke(0x458, struct.pack('<I', slot))
        self.junk = bytes(random.Random(vtable ^ slot).randrange(1, 255) for _ in range(BLOCK_SIZE))
        self.reset()

    def reset(self):
        self.poke(BLOCK, self.junk)
        self.poke(0x45C, struct.pack('<I', 0xABCD))

    def poke(self, offset: int, data: bytes):
        ctypes.memmove(self.addr + offset, data, len(data))

    def peek(self, offset: int, n: int) -> bytes:
        return ctypes.string_at(self.addr + offset, n)

    def ctx(self) -> Ctx:
        c = Ctx()
        c.rcx = self.addr
        return c


@unittest.skipUnless(NATIVE and GAME, 'requires the pinned MSYS2 g++ and IVALICE_LEGACY_GAME (exe read-only)')
class IsolationBodyTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.iso = Iso.get()

    def setUp(self):
        self.iso.setup()
        self.d = self.iso.d
        self.xi0, self.xi1, self.xi2 = Device(VT_XI, 0), Device(VT_XI, 1), Device(VT_XI, 2)
        self.di = Device(VT_DI, product=0x0CE6054C)  # DualSense: VID 0x054C, PID 0x0CE6
        self.st = Device(VT_ST)
        self.pads = [(1, self.xi0), (1, self.xi1), (1, self.xi2), (2, self.di), (3, self.st)]

    def tearDown(self):
        self.iso.twin.reset()

    def visit_all(self):
        return [self.iso.call(which, dev.ctx()) for which, dev in self.pads]

    def neutral(self, dev: Device) -> bool:
        ok = dev.peek(BLOCK, BLOCK_SIZE) == bytes(BLOCK_SIZE)
        return ok and (dev.peek(0, 8) != struct.pack('<Q', VT_XI) or dev.peek(0x45C, 4) == bytes(4))

    def untouched(self, dev: Device) -> bool:
        return dev.peek(BLOCK, BLOCK_SIZE) == dev.junk and dev.peek(0x45C, 4) == struct.pack('<I', 0xABCD)

    def test_fail_open_without_a_record_for_this_epoch_or_with_the_bit_off(self):
        self.d.ILCT_IsoWithdraw()
        self.assertEqual(self.visit_all(), [RUN_ORIGINAL] * 5)
        self.assertTrue(all(self.untouched(d) for _, d in self.pads), 'no record: everything stock')
        self.iso.publish(ISO_GUARD | ISO_NATIVE, p1_slot=0)
        self.assertEqual([self.iso.call(w, d.ctx(), epoch=EPOCH + 2) for w, d in self.pads], [RUN_ORIGINAL] * 5)
        self.assertTrue(all(self.untouched(d) for _, d in self.pads), 'a record two epochs old acts on nothing')
        self.d.ILCT_PatchApplySubsystems(0xFF & ~INPUT_ISOLATION)
        self.visit_all()
        self.assertTrue(all(self.untouched(d) for _, d in self.pads), 'INPUT_ISOLATION off: stock')
        self.d.ILCT_PatchApplySubsystems(0xFF)
        self.d.ILCT_PatchFault(INPUT_ISOLATION)
        self.visit_all()
        self.assertTrue(all(self.untouched(d) for _, d in self.pads), 'faulted subsystem: stock')

    def test_wrong_device_class_is_left_alone(self):
        self.iso.publish(ISO_GUARD | ISO_NATIVE | ISO_KB, p1_slot=0, kbm=2)
        before = self.iso.stats()['wrong_class']
        for which, dev in ((1, self.di), (2, self.xi1), (3, self.di), (5, self.xi1)):
            dev.reset()
            self.assertEqual(self.iso.call(which, dev.ctx()), RUN_ORIGINAL)
            self.assertTrue(self.untouched(dev), f'hook {which} wrote a device of another class')
        self.assertEqual(self.iso.stats()['wrong_class'] - before, 4)

    def test_guard_masks_every_pad_but_p1s_own_and_p1_is_never_affected(self):
        self.iso.publish(ISO_GUARD | ISO_NATIVE | ISO_BG, p1_slot=0)
        self.assertEqual(self.visit_all(), [RUN_ORIGINAL] * 5, 'the finalizer must always run')
        self.assertTrue(self.untouched(self.xi0), "P1's own XInput device must be untouched")
        for name, dev in (('guest XInput', self.xi1), ('unowned XInput', self.xi2), ('DirectInput', self.di),
                          ('Steam', self.st)):
            self.assertTrue(self.neutral(dev), f'{name} not neutral')
        s = self.iso.stats()
        self.assertEqual((s['passed'], s['masked']), (1, 4))
        # P1 on another slot: that slot passes, slot 0 is masked.
        for _, dev in self.pads:
            dev.reset()
        self.iso.publish(ISO_GUARD | ISO_NATIVE, p1_slot=2)
        self.visit_all()
        self.assertTrue(self.untouched(self.xi2) and self.neutral(self.xi0) and self.neutral(self.xi1))

    def test_no_guard_and_no_substitution_is_stock(self):
        self.iso.publish(ISO_NATIVE, p1_slot=0, guests=0)
        self.visit_all()
        self.assertTrue(all(self.untouched(d) for _, d in self.pads))

    def test_guest_flee_word_from_movement_reaches_p1s_flee_byte(self):
        """The movement subsystem's epoch-tagged flee word (bit s = seat s asks) is OR-ed into +0x443 of P1's own
        device and of the substitution host; a word made for another epoch, or no word, changes nothing."""
        d = self.d
        try:
            self.iso.publish(ISO_GUARD | ISO_NATIVE, p1_slot=0)
            for word_epoch, seats, expected in ((EPOCH, 0b10, b'\1'), (EPOCH, 0b100, b'\1'), (EPOCH - 1, 0b10, b'\0'),
                                                (EPOCH, 0, b'\0')):
                d.ILCT_IsoFakeFlee(word_epoch, seats)
                self.xi1.reset()
                self.iso.call(1, self.xi0.ctx())
                self.iso.call(1, self.xi1.ctx())
                self.assertEqual(self.xi0.peek(0x443, 1), expected, (word_epoch, seats))
                self.assertTrue(self.neutral(self.xi1), 'a masked guest pad never carries flee')
            pad = (0, 0, 0, 0, 0, 0, 0)
            self.iso.mirror(1, SRC_SDL, pad=pad)
            self.iso.publish(ISO_SUBST, guests=0)
            d.ILCT_IsoFakeFlee(EPOCH, 0b10)
            self.iso.call(1, self.xi2.ctx())
            self.assertEqual(self.xi2.peek(BLOCK, BLOCK_SIZE), reference_block(pad, flee=True), 'host carries flee')
            d.ILCT_IsoFakeFlee(0, 0)  # the real source: no movement record in this test -> no flee
            self.xi2.reset()
            self.iso.call(1, self.xi2.ctx())
            self.assertEqual(self.xi2.peek(BLOCK, BLOCK_SIZE), reference_block(pad))
        finally:
            d.ILCT_IsoFakeFlee(0, 0)

    def test_flee_is_mirrored_onto_p1s_device_only(self):
        self.iso.publish(ISO_GUARD | ISO_NATIVE | ISO_FLEE, p1_slot=0)
        self.xi0.poke(0x443, b'\0')
        self.visit_all()
        self.assertEqual(self.xi0.peek(0x443, 1), b'\1')
        self.assertEqual(self.xi1.peek(0x443, 1), b'\0', 'a masked pad must not carry flee')
        self.xi0.poke(0x443, b'\0')
        self.iso.publish(ISO_GUARD | ISO_NATIVE, p1_slot=0)
        self.iso.call(1, self.xi0.ctx())
        self.assertEqual(self.xi0.peek(0x443, 1), b'\0', 'no flee request: P1 RT byte untouched')

    def test_p1_on_sdl_feeds_one_host_and_masks_the_rest(self):
        pad = (0x5A0F, 0, 200, 12345, -32768, -129, 255)
        self.iso.mirror(1, SRC_SDL, pad=pad)
        self.iso.publish(ISO_SUBST | ISO_BG, vid=0x054C, pid=0x0CE6, guests=0)
        for _ in range(3):  # three device rounds
            for _, dev in self.pads:
                dev.reset()
            self.visit_all()
        self.assertEqual(self.di.peek(BLOCK, BLOCK_SIZE), reference_block(pad), "P1's own DualSense object is host")
        for dev in (self.xi0, self.xi1, self.xi2, self.st):
            self.assertTrue(self.neutral(dev))
        status = Status()
        self.d.ILCT_IsoStatus(ctypes.byref(status))
        self.assertEqual(status.host, self.di.addr)
        # Without a product match the first XInput object is host; the host is re-elected when it disappears.
        self.d.ILCT_IsoReset()
        self.iso.publish(ISO_SUBST, guests=0)
        self.visit_all()
        self.assertEqual(self.xi0.peek(BLOCK, BLOCK_SIZE), reference_block(pad))
        self.pads = [p for p in self.pads if p[1] is not self.xi0]
        for _ in range(3):
            for _, dev in self.pads:
                dev.reset()
            self.visit_all()
        self.assertEqual(self.xi1.peek(BLOCK, BLOCK_SIZE), reference_block(pad), 'host not re-elected')
        self.assertTrue(self.neutral(self.xi2) and self.neutral(self.di))
        # A disconnected SDL pad feeds neutral.
        self.iso.mirror(1, SRC_SDL, pad=pad, connected=False)
        self.visit_all()
        self.assertEqual(self.xi1.peek(BLOCK, BLOCK_SIZE), bytes(BLOCK_SIZE))

    def test_release_neutralizes_the_old_host_once_then_stock(self):
        pad = (0x1000, 0, 0, 30000, 0, 0, 0)
        self.iso.mirror(1, SRC_SDL, pad=pad)
        self.iso.publish(ISO_SUBST, guests=0)
        self.visit_all()
        self.assertEqual(self.xi0.peek(BLOCK, BLOCK_SIZE), reference_block(pad))
        for _, dev in self.pads:
            dev.reset()
        self.iso.publish(ISO_RELEASE, guests=0)
        self.visit_all()
        self.assertTrue(self.neutral(self.xi0), 'the old host keeps the substituted state')
        self.assertTrue(all(self.untouched(d) for _, d in self.pads[1:]), 'release touches only the old host')
        self.xi0.reset()
        self.visit_all()
        self.assertTrue(self.untouched(self.xi0), 'release is one write')
        self.assertEqual(self.iso.stats()['released'], 1)

    def test_platform_block_matches_the_reference_rule(self):
        rng = random.Random(0x7A228C)
        for _ in range(500):
            pad = (rng.getrandbits(16) & 0xF3FF, rng.choice([0, 1, 255]), rng.choice([0, 1, 255]),
                   *(rng.choice([0, 1, -1, 127, -128, 128, -129, 32767, -32768, rng.randint(-32768, 32767)])
                     for _ in range(4)))
            self.assertEqual(self.iso.block(pad), reference_block(pad), pad)
        self.assertEqual(self.iso.block((0,) * 7, flee=True), reference_block((0,) * 7, flee=True))
        # Presses since the last read are OR-ed in (bit n-1 = ilc_pad_control n: A=1, DPAD_LEFT=13, RT=16).
        tapped = self.iso.block((0,) * 7, extra=(1 << 0) | (1 << 12) | (1 << 15))
        self.assertEqual(tapped, reference_block((0x1000 | 0x0004, 0, 1, 0, 0, 0, 0)))

    def test_fail_open_accepts_one_epoch_either_side(self):
        """Isolation reads no world memory: at an epoch change the record and the liveness epoch may be one apart
        for a beat; that must not run the pads stock. Two apart (a stale record) is stock."""
        for record_epoch, acts in ((EPOCH, True), (EPOCH - 1, True), (EPOCH + 1, True), (EPOCH - 2, False),
                                   (EPOCH + 2, False)):
            with self.subTest(record_epoch=record_epoch):
                self.xi1.reset()
                self.iso.publish(ISO_GUARD | ISO_NATIVE, epoch=record_epoch, p1_slot=0)
                self.iso.call(1, self.xi1.ctx(), epoch=EPOCH)
                self.assertEqual(self.neutral(self.xi1), acts)
                self.assertEqual(self.untouched(self.xi1), not acts)

    def test_flee_byte_follows_the_real_rt_after_the_request_ends(self):
        """Flee pressed, then released, with no new P1 packet: P1's flee byte must drop back to the real RT."""
        d = self.d
        try:
            self.iso.publish(ISO_GUARD | ISO_NATIVE, p1_slot=0)
            self.xi0.poke(0x443, b'\0')
            self.xi0.poke(0x463, b'\0')  # the last packet's RT: released
            d.ILCT_IsoFakeFlee(EPOCH, 0b10)
            self.iso.call(1, self.xi0.ctx())
            self.assertEqual(self.xi0.peek(0x443, 1), b'\1', 'flee pressed')
            d.ILCT_IsoFakeFlee(EPOCH, 0)
            self.iso.call(1, self.xi0.ctx())
            self.assertEqual(self.xi0.peek(0x443, 1), b'\0', 'flee released: the byte must not stick')
            self.xi0.poke(0x443, b'\1')  # P1's own RT, interpreted by the game from a later packet
            self.iso.call(1, self.xi0.ctx())
            self.assertEqual(self.xi0.peek(0x443, 1), b'\1', 'no flee activity: P1 untouched')
            # P1 holds RT itself while a guest's flee ends: the byte stays 1.
            self.xi0.poke(0x463, b'\xC8')
            d.ILCT_IsoFakeFlee(EPOCH, 0b10)
            self.iso.call(1, self.xi0.ctx())
            d.ILCT_IsoFakeFlee(EPOCH, 0)
            self.iso.call(1, self.xi0.ctx())
            self.assertEqual(self.xi0.peek(0x443, 1), b'\1', "P1's real RT is kept")
        finally:
            d.ILCT_IsoFakeFlee(0, 0)

    def test_a_disconnected_xinput_object_is_never_the_host(self):
        pad = (0x1000, 0, 0, 1000, 0, 0, 0)
        self.iso.mirror(1, SRC_SDL, pad=pad)
        self.iso.publish(ISO_SUBST, guests=0)
        gone = self.xi0.ctx()
        gone.rdi = 0x48F  # XInputGetState: ERROR_DEVICE_NOT_CONNECTED
        for _ in range(3):
            for _, dev in self.pads:
                dev.reset()
            self.iso.call(1, gone)
            for which, dev in self.pads[1:]:
                self.iso.call(which, dev.ctx())
        self.assertTrue(self.neutral(self.xi0), 'a disconnected XInput object must be masked, not fed')
        self.assertEqual(self.xi1.peek(BLOCK, BLOCK_SIZE), reference_block(pad), 'the connected one is host')
        self.assertEqual(self.xi1.peek(0x45C, 4), bytes(4), "the host's packet number is cleared every tick")

    def test_release_primitives_for_a_disarm(self):
        d = self.d
        pad = (0x1000, 0, 0, 1000, 0, 0, 0)
        d.ILCT_IsoConfigure(self.settings(guests=(1,)))
        self.iso.mirror(1, SRC_SDL, pad=pad)
        record = self.compose()
        self.assertTrue(record.flags & ISO_SUBST)
        d.ILCT_IsoPublish(ctypes.byref(record))
        self.visit_all()
        self.assertEqual(self.xi0.peek(BLOCK, BLOCK_SIZE), reference_block(pad))
        d.ILCT_IsoBeginRelease()
        self.assertEqual(d.ILCT_IsoReleaseSettled(), 0, 'nothing released yet')
        record = self.compose()
        self.assertEqual(record.flags, ISO_RELEASE, 'a release record masks and substitutes nothing')
        d.ILCT_IsoPublish(ctypes.byref(record))
        self.assertEqual(d.ILCT_IsoReleaseSettled(), 0, 'the host has not been visited yet')
        for _, dev in self.pads:
            dev.reset()
        self.visit_all()
        self.assertTrue(self.neutral(self.xi0), 'the host gets the neutral block (and packet 0) once')
        self.assertTrue(all(self.untouched(dev) for _, dev in self.pads[1:]), 'every other pad is stock')
        self.assertEqual(d.ILCT_IsoReleaseSettled(), 1)
        d.ILCT_IsoEndRelease()
        self.assertEqual(d.ILCT_IsoReleaseSettled(), 0)
        self.assertTrue(self.compose().flags & ISO_SUBST, 'end_release returns to normal')

    def test_a_torn_p1_mirror_read_keeps_the_last_source(self):
        d = self.d
        d.ILCT_IsoConfigure(self.settings(guests=(1,)))
        self.iso.mirror(1, SRC_XINPUT, slot=2)
        self.assertEqual(self.compose().p1_slot, 2)
        d.ILCT_IsoMirrorHold(1, 1)  # the writer is "inside" for every retry
        try:
            r = self.compose()
            self.assertEqual((r.p1_slot, bool(r.flags & ISO_NATIVE)), (2, True), 'policy flipped on a torn read')
        finally:
            d.ILCT_IsoMirrorHold(1, 0)

    def test_the_liveness_epoch_moves_right_after_the_session_step(self):
        boot = (pe.COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        loop = boot[boot.index('session::step(now);'):]
        calls = [line.strip() for line in loop.splitlines() if line.strip() and not line.strip().startswith('//')]
        self.assertEqual(calls[1], 'patch::heartbeat(session::epoch());', 'the heartbeat must follow the session step')
        self.assertLess(loop.index('patch::heartbeat'), loop.index('isolation::publish'))
        self.assertLess(loop.index('patch::heartbeat'), loop.index('movement::step'))
        self.assertEqual(boot.count('patch::heartbeat(session::epoch());'), 2, 'arm + loop, nothing else')

    # ---- keyboard ----
    def keyboard(self, raw_down: set[int]):
        kb = Device(VT_KB)  # 0x470 bytes: the helper also writes the pad fields at +0x414
        for off in (0x30, 0x130, 0x231):
            kb.poke(off, b'\1' * 256)  # what the stock rebuild left: every key down
        stack = ctypes.create_string_buffer(0x200)
        raw = bytearray(256)
        for dik in raw_down:
            raw[dik] = 0x80
        ctypes.memmove(ctypes.addressof(stack) + 0x28, bytes(raw), 256)
        c = kb.ctx()
        c.rsp = ctypes.addressof(stack)
        return kb, c, stack

    def test_keyboard_filter_keeps_only_the_shared_keys(self):
        keymap = ctypes.string_at(KEYMAP, 256)
        down = {keymap[k] for k in (0x0C, 0x25)} | {0x11, 0x1E, 0x01}  # M, F1 plus W, A, Esc (DIK codes)
        kb, c, _stack = self.keyboard(down)
        self.iso.publish(ISO_GUARD | ISO_KB, kbm=2)
        self.assertEqual(self.iso.call(5, c), RUN_ORIGINAL)
        self.assertEqual(kb.peek(0x30, 256), bytes(256))
        self.assertEqual(kb.peek(0x130, 256), bytes(256))
        keys = kb.peek(0x231, 256)
        expected = bytearray(256)
        for k in SHARED_KEYS:
            expected[k] = 1 if keymap[k] in down else 0
        self.assertEqual(keys, bytes(expected))
        self.assertEqual((keys[0x0C], keys[0x25]), (1, 1))
        # Not filtering (P1 owns the keyboard): untouched.
        kb2, c2, _s2 = self.keyboard(down)
        self.iso.publish(ISO_GUARD, kbm=1)
        self.iso.call(5, c2)
        self.assertEqual(kb2.peek(0x231, 256), b'\1' * 256)

    def test_keyboard_budget_refills_to_240_per_beat(self):
        self.iso.publish(ISO_GUARD | ISO_KB, kbm=2)
        self.d.ILCT_IsoSetBudget(KB_BUDGET)
        for n in range(KB_BUDGET):
            kb, c, _s = self.keyboard(set())
            self.iso.call(5, c)
            self.assertEqual(kb.peek(0x231, 1), b'\0', f'update {n + 1} was not filtered')
        self.assertEqual(self.d.ILCT_IsoBudget(), 0)
        kb, c, _s = self.keyboard(set())
        self.iso.call(5, c)
        self.assertEqual(kb.peek(0x231, 256), b'\1' * 256, 'update 241 without a refill must be stock')
        s = self.iso.stats()
        self.assertEqual((s['kb_filtered'], s['kb_expired']), (KB_BUDGET, 1))
        self.d.ILCT_IsoPublishNow()  # one co-op beat (no session here: the record is withdrawn, the lease refilled)
        self.assertEqual(self.d.ILCT_IsoBudget(), KB_BUDGET)
        self.iso.publish(ISO_GUARD | ISO_KB, kbm=2)
        kb, c, _s = self.keyboard(set())
        self.iso.call(5, c)
        self.assertEqual(kb.peek(0x231, 1), b'\0', 'filtering resumes after the refill')
        self.assertEqual(self.d.ILCT_IsoBudget(), KB_BUDGET - 1)

    # ---- focus ----
    def test_in06_runs_the_pad_block_unfocused_and_keeps_the_flag_honest(self):
        self.iso.twin.poke(FOCUS_FLAG, b'\1')
        self.iso.publish(ISO_GUARD | ISO_BG)
        c = Ctx()
        c.rflags = 0x202  # ZF clear: the window is not in the foreground
        self.assertEqual(self.iso.call(6, c), RESUME)
        self.assertEqual(c.resume, PAD_RESUME)
        self.assertEqual(ctypes.string_at(FOCUS_FLAG, 1), b'\0')
        c = Ctx()
        c.rflags = 0x242  # ZF set: focused, the stock path writes the flag itself
        self.assertEqual(self.iso.call(6, c), RUN_ORIGINAL)
        self.iso.publish(ISO_GUARD)
        c = Ctx()
        c.rflags = 0x202
        self.assertEqual(self.iso.call(6, c), RUN_ORIGINAL, 'no background flag: stock (the block is skipped)')

    def test_in07_turns_focus_loss_into_active(self):
        event = ctypes.create_string_buffer(struct.pack('<II', 4, 0), 8)
        c = Ctx()
        c.rdx = ctypes.addressof(event)
        self.iso.publish(ISO_GUARD | ISO_BG)
        self.assertEqual(self.iso.call(7, c), RUN_ORIGINAL, 'the dispatch must still run')
        self.assertEqual(struct.unpack('<II', event.raw), (4, 1))
        event2 = ctypes.create_string_buffer(struct.pack('<II', 4, 0), 8)
        c.rdx = ctypes.addressof(event2)
        self.iso.publish(ISO_GUARD)
        self.iso.call(7, c)
        self.assertEqual(struct.unpack('<II', event2.raw), (4, 0))

    def test_in06a_mismatch_leaves_in06_unhooked_and_reported(self):
        status = Status()
        self.d.ILCT_IsoStatus(ctypes.byref(status))
        self.assertEqual(status.in06_armed, 1)
        twin = self.iso.twin
        twin.reset()
        gate = twin.index['IN-06a']
        twin.poke(TWIN_BASE + twin.rows[gate]['rva'], b'\xE9')  # as a mod that rewrites the approach would
        report = twin.call('ILCT_PatchInstall')
        self.assertEqual((report.result, report.gated_off, report.hooked), (OK, 1, 60))
        self.d.ILCT_IsoStatus(ctypes.byref(status))
        self.assertEqual(status.in06_armed, 0)
        site = TWIN_BASE + twin.rows[self.iso.rows['IN-06']]['rva']
        self.assertEqual(ctypes.string_at(site, 6), twin.rows[self.iso.rows['IN-06']]['expected'])

    # ---- the record ----
    def settings(self, p1=None, guests=None, kbm=0, seat4=False) -> bytes:
        record = bytearray(SC.defaults())
        for seat in range(4):
            record[SC.locate('ilc_settings', ['seats', seat, 'device', 'kind'])[0]] = 0
        if p1 == 'sdl':
            record[SC.locate('ilc_settings', ['seats', 0, 'device', 'kind'])[0]] = SC.e('device_kind', 'SDL')
            guid = SC.locate('ilc_settings', ['seats', 0, 'device', 'sdl', 'guid'])[0]
            record[guid:guid + 16] = bytes([3, 0, 0, 0, 0x4C, 0x05, 0, 0, 0xE6, 0x0C, 0, 0, 0, 0, 0, 0])
        for seat in guests or ():
            record[SC.locate('ilc_settings', ['seats', seat, 'device', 'kind'])[0]] = SC.e('device_kind', 'XINPUT')
        if seat4:
            record[SC.locate('ilc_settings', ['seats', 3, 'device', 'kind'])[0]] = SC.e('device_kind', 'XINPUT')
        record[SC.locate('ilc_settings', ['kbm_seat'])[0]] = kbm
        return bytes(record)

    def compose(self, session: bytes | None = None, epoch: int = EPOCH) -> IsoRecord:
        out = IsoRecord()
        self.d.ILCT_IsoCompose(epoch, 3, session, ctypes.byref(out))
        return out

    def session(self, world: bool, manual: tuple = (), epoch: int = EPOCH) -> bytes:
        size = self.d.ILCT_IsoSessionRecordSize()
        rec = bytearray(size)
        struct.pack_into('<IIQ', rec, 0, epoch, 0x1 if world else 0, 3)
        for seat in manual:
            struct.pack_into('<H', rec, 32 + 32 * seat, 0x0003)  # IN_SESSION | MANUAL
        return bytes(rec)

    def test_record_composition(self):
        d = self.d
        d.ILCT_IsoConfigure(self.settings())
        self.iso.mirror(1, SRC_XINPUT, slot=1)
        r = self.compose()
        self.assertEqual((r.flags & ~ISO_RELEASE, r.p1_slot), (ISO_NATIVE, 1), 'P1 alone on XInput: nothing to isolate')
        d.ILCT_IsoConfigure(self.settings(guests=(1,)))
        r = self.compose()
        self.assertEqual(r.flags, ISO_GUARD | ISO_NATIVE | ISO_KB | ISO_BG, 'a guest pad: guard, keyboard filtered')
        self.assertEqual((r.guest_seats, r.epoch, r.generation), (0b10, EPOCH, 3))
        d.ILCT_IsoConfigure(self.settings(guests=(1,), kbm=1))
        self.assertFalse(self.compose().flags & ISO_KB, 'P1 owns the keyboard: no filter')
        d.ILCT_IsoConfigure(self.settings(kbm=3))
        r = self.compose()
        self.assertEqual((r.guest_seats, bool(r.flags & ISO_KB)), (0b100, True), 'a guest keyboard alone guards')
        d.ILCT_IsoConfigure(self.settings(seat4=True))
        self.assertEqual(self.compose().guest_seats, 0, 'the fourth seat never takes part')
        # P1 on SDL: substitution with the pad's vendor and product.
        d.ILCT_IsoConfigure(self.settings(p1='sdl'))
        self.iso.mirror(1, SRC_SDL)
        r = self.compose()
        self.assertEqual((r.flags & (ISO_SUBST | ISO_NATIVE | ISO_GUARD), r.p1_vid, r.p1_pid),
                         (ISO_SUBST, 0x054C, 0x0CE6))
        # Flee is the movement subsystem's word, never decided by the record.
        d.ILCT_IsoConfigure(self.settings(guests=(1, 2)))
        self.iso.mirror(1, SRC_XINPUT, slot=0)
        self.iso.mirror(3, SRC_XINPUT, slot=2, held=1 << FLEE_ACTION)
        self.assertFalse(self.compose(self.session(True, manual=(2,))).flags & ISO_FLEE)
        # Release: up for ten beats after isolation stops, then gone.
        d.ILCT_IsoConfigure(self.settings())
        self.iso.mirror(1, SRC_XINPUT, slot=0)
        flags = [self.compose().flags & ISO_RELEASE for _ in range(12)]
        self.assertEqual(flags, [ISO_RELEASE] * 10 + [0, 0])


# ---- unicorn: the game's own device code through the hooked finalize calls ----------------------------------------
XI_CTOR, DI_CTOR, PAD_BASE_CTOR = 0x792CB0, 0x792AB0, 0x792940
NODE_CTOR, BIND, MGR_BIND_PADS = 0x792D90, 0x799F40, 0x79AF20
XI_POLL, GET_BUTTON, GET_AXIS = 0x7A2140, 0x79D670, 0x79D730
IN02, IN02_END, IN03, IN03_END = 0x7A1C9F, 0x7A1CA7, 0x7A2113, 0x7A211B
DEVLIST, HWND_GLOBAL = 0x1F401E8, 0x2EA5248
NO_LOG = (0x5CDAA0, 0x79CEC0, 0x862C70)  # logger, pad-index helper, XInput DLL loader: made to return 0
STICK_NODES = (0xB0, 0xF8, 0x140, 0x188)
EXE_STUBS, HEAP, HEAP_SIZE = 0x60_0000_0000, 0x70_0000_0000, 0x400000
SENTINEL = EXE_STUBS + 0xFFF0
FAKE_HWND = 0x1234
NOT_CONNECTED = 0x48F


class IsoEmulator(gt.TickEmulator):
    """The hooked twin, our DLL and the islands (TickEmulator), plus the game's imports answered by stubs, a heap
    for device objects, and an executor that runs game functions to a sentinel."""

    budget = 2_000_000

    def __init__(self, twin):
        super().__init__(twin)
        uc = self.uc
        f = twin.file
        lfanew = struct.unpack_from('<I', f, 0x3C)[0]
        imp_rva = struct.unpack_from('<I', f, lfanew + 24 + 112 + 8)[0]
        uc.mem_map(EXE_STUBS, 0x10000)
        uc.mem_write(EXE_STUBS, b'\xC3' * 0x10000)
        self.exe_stub = {}
        k = 0
        desc = imp_rva
        image = ctypes.string_at(TWIN_BASE, TWIN_SIZE)
        while True:
            oft, _, _, name_rva, ft = struct.unpack_from('<5I', image, desc)
            if name_rva == 0:
                break
            j = 0
            while True:
                entry = struct.unpack_from('<Q', image, oft + 8 * j)[0]
                if entry == 0:
                    break
                name = b'' if entry >> 63 else ctypes.string_at(TWIN_BASE + (entry & 0x7FFFFFFF) + 2)
                stub = EXE_STUBS + 16 * k
                self.exe_stub[stub] = name.decode()
                uc.mem_write(TWIN_BASE + ft + 8 * j, struct.pack('<Q', stub))
                k += 1
                j += 1
            desc += 20
        uc.mem_write(SENTINEL, b'\xF4')
        uc.hook_add(unicorn.UC_HOOK_CODE, self._exe_import, begin=EXE_STUBS, end=EXE_STUBS + 0xFFFF)
        for a in NO_LOG:
            uc.mem_write(a, b'\x31\xC0\xC3')
        uc.mem_write(HWND_GLOBAL, struct.pack('<Q', FAKE_HWND))
        uc.mem_write(DEVLIST, struct.pack('<QQ', DEVLIST, DEVLIST))
        uc.mem_map(HEAP, HEAP_SIZE)
        self.heap = HEAP
        self.xinput = {}
        self.exe_unknown = set()
        self.foreground = FAKE_HWND

    def _exe_import(self, uc, address, size, _):
        name = self.exe_stub.get(address)
        if name is None:
            return
        rcx, rdx, r8 = (uc.reg_read(r) for r in (X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8))
        rax = 0
        if name == 'memset':
            uc.mem_write(rcx, bytes([rdx & 0xFF]) * r8)
            rax = rcx
        elif name == 'XInputGetState':
            rc, st = self.xinput.get(rcx & 0xFFFFFFFF, (NOT_CONNECTED, b'\0' * 16))
            uc.mem_write(rdx, st)
            rax = rc
        elif name == 'XInputSetState':
            rax = 0
        elif name == 'GetForegroundWindow':
            rax = self.foreground
        elif name == 'GetVersionExA':
            uc.mem_write(rcx + 4, struct.pack('<III', 10, 0, 19041))
            uc.mem_write(rcx + 16, struct.pack('<I', 2))
            rax = 1
        else:
            self.exe_unknown.add(name)
        uc.reg_write(X.UC_X86_REG_RAX, rax)

    def _trap(self, uc, address, size, _):
        name = self.trap.get(address)
        if name == 'VirtualQuery':  # the probe's range check: every address here is committed read-write memory
            rcx, rdx = uc.reg_read(X.UC_X86_REG_RCX), uc.reg_read(X.UC_X86_REG_RDX)
            base = rcx & ~0xFFF
            uc.mem_write(rdx, struct.pack('<QQIHHQIIII', base, base, 4, 0, 0, 0x100000, 0x1000, 4, 0x20000, 0))
            uc.reg_write(X.UC_X86_REG_RAX, 48)
            self.imports.append(name)
            return
        if name == 'AddVectoredExceptionHandler':
            uc.reg_write(X.UC_X86_REG_RAX, 1)
            self.imports.append(name)
            return
        super()._trap(uc, address, size, _)

    def alloc(self, n: int) -> int:
        self.heap = (self.heap + 15) & ~15
        p = self.heap
        self.heap += n
        self.uc.mem_write(p, b'\0' * n)
        return p

    def call(self, fn: int, *args, rbx: int | None = None, until: int | None = None, start: int | None = None):
        uc = self.uc
        self.allowed = lambda x: True
        self.steps = 0
        self.undo, self.written, self.calls = [], set(), []  # run() bookkeeping; executor writes are kept
        rsp = pe.STACK + pe.STACK_SIZE - 0x1000
        if until is None:
            rsp -= 8
            uc.mem_write(rsp, struct.pack('<Q', SENTINEL))
        uc.reg_write(X.UC_X86_REG_RSP, rsp)
        uc.reg_write(X.UC_X86_REG_RFLAGS, 0x202)
        for reg, v in zip((X.UC_X86_REG_RCX, X.UC_X86_REG_RDX, X.UC_X86_REG_R8, X.UC_X86_REG_R9), args):
            uc.reg_write(reg, v)
        if rbx is not None:
            uc.reg_write(X.UC_X86_REG_RBX, rbx)
        try:
            uc.emu_start(start if start is not None else fn, until or SENTINEL, count=self.budget)
        except unicorn.UcError as error:
            last = f'0x{self.last[0]:X}' if self.last else '?'
            raise AssertionError(f'0x{fn:X}: {error} at RIP 0x{uc.reg_read(X.UC_X86_REG_RIP):X} '
                                 f'(last instruction {last})') from None
        if uc.reg_read(X.UC_X86_REG_RIP) != (until or SENTINEL):
            raise AssertionError(f'0x{fn:X} did not finish (RIP 0x{uc.reg_read(X.UC_X86_REG_RIP):X})')
        return uc.reg_read(X.UC_X86_REG_RAX)

    def bytes_at(self, a: int, n: int) -> bytes:
        return bytes(self.uc.mem_read(a, n))


def xstate(packet: int, pad: tuple) -> bytes:
    buttons, lt, rt, lx, ly, rx, ry = pad
    return struct.pack('<IHBBhhhh', packet, buttons, lt, rt, lx, ly, rx, ry)


STICK_VALUES = [0, 1, -1, 127, -127, 128, -128, 129, -129, 255, -255, 256, -256, 1000, -1000, 7849, -7849, 8689,
                -8689, 16384, -16384, 32767, -32768, -32767]


def rand_pad(rng: random.Random, prev=None) -> tuple:
    if prev is not None and rng.random() < 0.3:
        return prev

    def stick():
        return rng.choice(STICK_VALUES) if rng.random() < 0.6 else rng.randint(-32768, 32767)
    buttons = rng.getrandbits(16) & 0xF3FF if rng.random() < 0.8 else 0
    return (buttons, rng.choice([0, 0, 1, 30, 255]), rng.choice([0, 0, 1, 30, 255]), stick(), stick(), stick(), stick())


@unittest.skipUnless(NATIVE and GAME and unicorn is not None and pe.capstone is not None,
                     'requires the pinned MSYS2 g++, IVALICE_LEGACY_GAME, unicorn and capstone')
class IsolationEmulationTest(unittest.TestCase):
    """The game's own device code, through IN-01/02/03 into our handlers."""

    @classmethod
    def setUpClass(cls):
        cls.iso = Iso.get()
        cls.iso.setup()
        cls.twin = cls.iso.twin
        cls.emu = IsoEmulator(cls.twin)
        cls.arm(cls, None)  # module data, clock and gates in the emulator: everything closed
        cls.di_inst = cls.emu.alloc(0x244)
        cls.emu.uc.mem_write(cls.di_inst, struct.pack('<I', 0x244))
        cls.packet = 100

    @classmethod
    def tearDownClass(cls):
        cls.twin.reset()

    # ---- devices ----
    def make(self, kind: str, slot: int = 0, product: int = 0) -> dict:
        emu = self.emu
        if kind == 'XI':
            dev = emu.alloc(0x470)
            emu.call(XI_CTOR, dev, self.di_inst, slot)
        elif kind == 'DI':
            dev = emu.alloc(0x458)
            emu.call(DI_CTOR, dev, self.di_inst, emu.alloc(0x100))
            emu.uc.mem_write(dev + 0x1E4, struct.pack('<I', product))
        else:
            dev = emu.alloc(0x458)
            emu.call(PAD_BASE_CTOR, dev, 0)
            emu.uc.mem_write(dev, struct.pack('<Q', VT_ST))
        return {'kind': kind, 'dev': dev, 'slot': slot, 'nodes': []}

    def fresh_list(self):
        """Empty the game's device list: the constructors link each new group into it, and the stock binding pass
        binds every pad on it (an older group bound twice would corrupt its node lists)."""
        self.emu.uc.mem_write(DEVLIST, struct.pack('<QQ', DEVLIST, DEVLIST))

    def bind(self, devices: list):
        emu = self.emu
        emu.call(MGR_BIND_PADS, emu.alloc(0x400))  # stock stick binding for every pad in the device list
        for d in devices:
            if d['nodes']:
                continue
            for bid in range(0x0B, 0x1C):
                n = emu.alloc(0x48)
                emu.call(NODE_CTOR, n)
                self.assertEqual(emu.call(BIND, d['dev'], n, bid), 0)
                d['nodes'].append(n)

    def snapshot(self, d: dict) -> dict:
        emu, dev = self.emu, d['dev']
        snap = {'fields': emu.bytes_at(dev + BLOCK, BLOCK_SIZE),
                'sticks': [emu.bytes_at(dev + off + 0x28, 8) for off in STICK_NODES],
                'buttons': [emu.bytes_at(n + 0x30, 3) for n in d['nodes']]}
        snap['get_button'] = [emu.call(GET_BUTTON, dev, bid) & 0xFF for bid in range(0x0B, 0x1C)]
        axes = []
        for aid in range(1, 0x13):
            emu.call(GET_AXIS, dev, aid)
            axes.append(emu.uc.reg_read(X.UC_X86_REG_XMM0) & 0xFFFFFFFF)
        snap['get_axis'] = axes
        return snap

    def poll(self, d: dict, packet_pad=None, raw: bytes | None = None):
        """One device tick: XInput -> the game's full poll (XInputGetState answers `packet_pad`, None = not
        connected); DirectInput/Steam -> `raw` as the class translation's platform fields, then the finalize window."""
        emu = self.emu
        if d['kind'] == 'XI':
            if packet_pad is None:
                emu.xinput[d['slot']] = (NOT_CONNECTED, b'\0' * 16)
            else:
                self.__class__.packet += 1
                emu.xinput[d['slot']] = (0, xstate(self.packet, packet_pad))
            emu.call(XI_POLL, d['dev'])
        else:
            emu.uc.mem_write(d['dev'] + BLOCK, raw)
            start, end = (IN02, IN02_END) if d['kind'] == 'DI' else (IN03, IN03_END)
            emu.call(start, rbx=d['dev'], until=end)

    # ---- arming in the emulator ----
    def arm(self, record: IsoRecord | None, live_epoch: int = EPOCH):
        """Publish (or withdraw) the record in-process, copy our module's data into the emulator, open the three
        finalize gates with a fresh liveness record."""
        d = self.iso.d
        if record is None:
            d.ILCT_IsoWithdraw()
        else:
            d.ILCT_IsoPublish(ctypes.byref(record))
        emu = self.emu
        emu.refresh()
        emu.tick_clock(gt.NOW + 5)
        for sid in ('IN-01', 'IN-02', 'IN-03'):
            emu.force_gate(self.iso.rows[sid], record is not None)
        for sid in ('IN-05', 'IN-06', 'IN-07'):  # not under test here: the focused pad path stays stock
            emu.force_gate(self.iso.rows[sid], False)
        emu.uc.mem_write(self.twin.addresses.liveness, struct.pack('<IIQI', 1, live_epoch, gt.NOW, 0))

    def stock_sites(self, original: bool):
        for sid in ('IN-01', 'IN-02', 'IN-03'):
            r = self.twin.rows[self.iso.rows[sid]]
            site = TWIN_BASE + r['rva']
            data = checked(r)[:r['hook_len']] if original else ctypes.string_at(site, r['hook_len'])
            self.emu.write_code(site, data)

    def frames(self, hooked: list, stock: list, neutral: list, record, frames=6, seed=1, mirror=None):
        """Each frame: the same inputs to the hooked set (through our handlers) and to the stock set (original
        bytes); the neutral set gets zero inputs on stock code. Returns per-frame snapshots."""
        rng = random.Random(seed)
        out = []
        prev = {}
        for t in range(frames):
            inputs = {}
            for k, d in enumerate(hooked):
                if d['kind'] == 'XI':
                    inputs[k] = rand_pad(rng, prev.get(k))
                    prev[k] = inputs[k]
                else:
                    inputs[k] = bytes(rng.getrandbits(8) & 0x81 if 0x43C - BLOCK <= i < 0x44C - BLOCK else
                                      rng.randrange(-255, 256) & 0xFF for i in range(BLOCK_SIZE))
            self.stock_sites(True)
            for k, d in enumerate(stock):
                self.poll(d, inputs[k], inputs[k] if d['kind'] != 'XI' else None)
            for d in neutral:
                self.poll(d, (0,) * 7, bytes(BLOCK_SIZE))
            self.stock_sites(False)
            self.arm(record)
            for k, d in enumerate(hooked):
                self.poll(d, inputs[k], inputs[k] if d['kind'] != 'XI' else None)
            self.assertEqual(self.emu.unexpected, [], 'our handler called an unexpected import')
            out.append(([self.snapshot(d) for d in hooked], [self.snapshot(d) for d in stock],
                        [self.snapshot(d) for d in neutral]))
        self.assertEqual(self.emu.exe_unknown, set())
        return out

    def sets(self):
        layout = [('XI', 0, 0), ('XI', 1, 0), ('XI', 2, 0), ('DI', 0, 0x0CE6054C), ('ST', 0, 0)]
        self.fresh_list()
        hooked = [self.make(k, s, p) for k, s, p in layout]
        stock = [self.make(k, s, p) for k, s, p in layout]
        neutral = [self.make(k, s, p) for k, s, p in layout]
        self.bind(hooked + stock + neutral)
        return hooked, stock, neutral

    def test_disarmed_equals_stock_for_every_device(self):
        hooked, stock, neutral = self.sets()
        for t, (h, s, _n) in enumerate(self.frames(hooked, stock, neutral, None, seed=0xD15)):
            for k in range(len(h)):
                self.assertEqual(h[k], s[k], f'frame {t} device {k}: disarmed must be stock')

    def test_armed_masks_every_pad_but_p1s_and_p1_is_never_affected(self):
        hooked, stock, neutral = self.sets()
        record = IsoRecord(EPOCH, ISO_GUARD | ISO_NATIVE | ISO_BG, 1, 0, 0, 0b10, 0, 0, 0, 0)
        names = ['P1 XInput 0', 'guest XInput 1', 'unowned XInput 2', 'DirectInput', 'Steam']
        for t, (h, s, n) in enumerate(self.frames(hooked, stock, neutral, record, seed=0xA11)):
            self.assertEqual(h[0], s[0], f'frame {t}: P1 must equal stock')
            for k in range(1, 5):
                self.assertEqual(h[k], n[k], f'frame {t}: {names[k]} must equal the neutral reference')

    def test_flee_request_reaches_p1s_device(self):
        """A guest's flee request (the movement subsystem's word for this epoch) reaches the game as P1's RT."""
        hooked, stock, neutral = self.sets()
        record = IsoRecord(EPOCH, ISO_GUARD | ISO_NATIVE, 1, 0, 0, 0b10, 0, 0, 0, 0)
        self.iso.d.ILCT_IsoFakeFlee(EPOCH, 0b10)
        try:
            for t, (h, _s, n) in enumerate(self.frames(hooked, stock, neutral, record, frames=3, seed=0xF1EE)):
                self.assertEqual(h[0]['fields'][0x443 - BLOCK], 1)
                self.assertEqual(h[0]['get_button'][0x443 - 0x43C], 1, 'flee (RT) reads as held')
                self.assertEqual(h[1], n[1], 'the guest pad stays neutral')
        finally:
            self.iso.d.ILCT_IsoFakeFlee(0, 0)

    def test_epoch_change_gives_no_press_edge_on_a_held_guest_button(self):
        """A guest holds A on XInput slot 1 through a session epoch change: the beat in which the liveness epoch has
        moved and the isolation record has not (and the reverse) must stay masked, so no press edge reaches the game.
        Control: a record two epochs old runs the pad stock and the held A arrives as a fresh press."""
        self.fresh_list()
        guest = self.make('XI', 1)
        self.bind([guest])
        held_a = (0x1000, 0, 0, 0, 0, 0, 0)
        a_node = 0x43D - 0x43C  # button id 0x0C
        flags = ISO_GUARD | ISO_NATIVE | ISO_BG
        for live, rec in ((EPOCH, EPOCH), (EPOCH + 1, EPOCH), (EPOCH + 1, EPOCH + 1), (EPOCH + 2, EPOCH + 3),
                          (EPOCH + 3, EPOCH + 3)):
            with self.subTest(live=live, record=rec):
                self.arm(IsoRecord(rec, flags, 1, 0, 0, 0b10, 0, 0, 0, 0), live_epoch=live)
                self.poll(guest, held_a)
                snap = self.snapshot(guest)
                self.assertEqual(snap['buttons'][a_node], b'\0\0\0', 'A held, pressed edge or released edge leaked')
                self.assertEqual(snap['fields'], bytes(BLOCK_SIZE))
        self.arm(IsoRecord(EPOCH + 1, flags, 1, 0, 0, 0b10, 0, 0, 0, 0), live_epoch=EPOCH + 3)
        self.poll(guest, held_a)
        self.assertEqual(self.snapshot(guest)['buttons'][a_node][:2], b'\1\1',
                         'instrument: a stale record must run stock (A arrives as a fresh press)')

    def test_a_disconnected_xinput_object_is_masked_not_fed(self):
        self.fresh_list()
        gone = self.make('XI', 3)
        neutral = self.make('XI', 0)
        self.bind([gone, neutral])
        self.iso.d.ILCT_IsoReset()
        self.iso.mirror(1, SRC_SDL, pad=(0x1000, 0, 255, 20000, -20000, 0, 0))
        for _ in range(3):
            self.arm(IsoRecord(EPOCH, ISO_SUBST | ISO_BG, 1, 0xFF, 0, 0, 0, 0, 0, 0))
            self.poll(gone, None)
            self.stock_sites(True)
            self.poll(neutral, (0,) * 7)
            self.stock_sites(False)
            self.assertEqual(self.snapshot(gone)['fields'], bytes(BLOCK_SIZE))
        s = self.snapshot(gone)
        n = self.snapshot(neutral)
        self.assertEqual((s['sticks'], s['buttons'], s['get_button'], s['get_axis']),
                         (n['sticks'], n['buttons'], n['get_button'], n['get_axis']))

    # ---- the substitution checkpoint (P1 on an SDL-only pad), restated ----
    def c2_run(self, seqs: int, frames: int, seed: int, variant: int = 0, which=('B1', 'B2', 'C', 'D')) -> dict:
        """P1 on SDL: our C++ substitutes P1's state at the hooked finalize call of each host kind; the reference is
        a real XInput packet through the game's full poll on stock bytes."""
        iso, emu = self.iso, self.emu
        iso.d.ILCT_IsoVariant(variant)
        try:
            self.fresh_list()
            ref = self.make('XI', 0)
            hosts = {'B1': self.make('XI', 1), 'B2': self.make('XI', 2), 'C': self.make('DI', 0, 0x0CE6054C),
                     'D': self.make('ST')}
            self.bind([ref] + list(hosts.values()))
            rng = random.Random(seed)
            stats = {'frames': 0, 'compares': 0, 'mismatch': 0, 'block_vs_ref': 0, 'first': None}
            for q in range(seqs):
                prev = None
                for t in range(frames):
                    pad = rand_pad(rng, prev)
                    prev = pad
                    self.stock_sites(True)
                    self.poll(ref, pad)
                    want = self.snapshot(ref)
                    if want['fields'] != reference_block(pad):
                        stats['block_vs_ref'] += 1
                    self.stock_sites(False)
                    iso.mirror(1, SRC_SDL, pad=pad, seq=1 + q * frames + t)
                    for name in which:
                        d = hosts[name]
                        iso.d.ILCT_IsoReset()          # one host kind at a time: elect this device
                        iso.d.ILCT_IsoVariant(variant)
                        self.arm(IsoRecord(EPOCH, ISO_SUBST | ISO_BG, 1, 0xFF, 0, 0, 0, 0x054C, 0x0CE6, 0))
                        if name == 'B1':
                            self.poll(d, (0,) * 7)  # connected, idle (a disconnected object is never host)
                        elif name == 'B2':
                            self.poll(d, rand_pad(rng))
                        else:
                            self.poll(d, None, bytes(rng.getrandbits(8) for _ in range(BLOCK_SIZE)))
                        got = self.snapshot(d)
                        stats['compares'] += 1
                        if got != want:
                            stats['mismatch'] += 1
                            if stats['first'] is None:
                                stats['first'] = (q, t, name, {k: (want[k], got[k]) for k in want if want[k] != got[k]})
                    stats['frames'] += 1
            return stats
        finally:
            iso.d.ILCT_IsoVariant(0)

    def test_c2_substitution_through_the_hooked_finalize_calls(self):
        claim = self.c2_run(60, 8, 0xB18)
        print(f"\nsubstitution restated: frames={claim['frames']} compares={claim['compares']} mismatches={claim['mismatch']} "
              f"f(S)!=game interpretation={claim['block_vs_ref']}")
        self.assertEqual((claim['mismatch'], claim['block_vs_ref']), (0, 0), claim['first'])
        self.assertEqual(claim['compares'], 60 * 8 * 4)
        controls = {'no substitution': (4, ('B1', 'C', 'D')), 'Y not negated': (1, ('B1', 'B2', 'C', 'D')),
                    '>>7 instead of division': (2, ('B1', 'B2', 'C', 'D')), 'extra fields not cleared': (3, ('C', 'D'))}
        for label, (variant, which) in controls.items():
            with self.subTest(control=label):
                r = self.c2_run(6, 6, 0xB18 + variant, variant, which)
                print(f'control {label}: compares={r["compares"]} mismatches={r["mismatch"]} (must be > 0)')
                self.assertGreater(r['mismatch'], 0, f'instrument control "{label}" did not fail')


if __name__ == '__main__':
    unittest.main()
