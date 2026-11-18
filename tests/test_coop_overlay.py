"""Host tests for our own co-op overlay (native/coop/src/overlay/) and Player 1 on the game's own battle frontends
(frontend::set_p1_native). No game run, nothing deployed; the exe is only read (IVALICE_LEGACY_GAME, a copy) for the
image twin the OV-01 handler tests run on.

* static: the overlay adds no import (no d3d11/dxgi/d3dcompiler import: every D3D call is a COM call on the game's
  device); only gpu.cpp includes d3d11.h; the render-thread code never posts a log line; the generated inputs
  (shaders.inc, the built-in themes' font.inc / theme.json) belong to the committed HLSL and the themes lock; the boot wires attach, settings, the beat
  and the stop; HD-01 feeds the heartbeat; the old canvas layer and its test draw stay off;
* the font (the default theme, legacy-bold): the atlas decodes to the digest the converter recorded, every ASCII glyph
  of the body and title faces and every icon sits inside the atlas, measure() and fit() (with "...") agree with the
  glyph advances and the kerning;
* the batch: every primitive, the fade multiplier, the capacity (refused, never overwritten), the authoring space
  at 16:9, 16:10, 21:9 and 4:3 (uniform scale, centred), vertex expansion (clip space, glass in pixels, snapping);
* the scene: the test card stays inside the authoring space; the seats' windows from a menu view, one uniform window
  per seat in a fixed slot along the bottom (P1 leftmost): Player 1's window is drawn only when P1's menu is ours and
  reported left out otherwise, every window lies in its own slot whatever the party list does, a window fades in and
  rises 12 units into its slot over 150 ms; a full batch drops whole windows and reports them;
* the slots (SlotTest): four identical windows in seat order, on screen with their tabs and gloves, clear of the stock
  and the reduced party list for 1..4 rows, of Target Info (with the boss gauge) and of the log zone; the rows fit
  Player 1's root page; long lists scroll with the cursor in view;
* Player 1's arming (P1ArmRuleTest, P1ArmLayerTest, real stamps): P1's menu moves onto the overlay only after a run of
  presented frames, never under an open stock frontend, never taking the arming beat's presses; it goes back to the
  vanilla menu at once when the run breaks twice or goes stale (any screen hiding the HUD), the presenter dies, a frame
  is refused, P1's window is left out or P1's vanilla battle menu comes up (holds of 5, 10, 20 s, then vanilla for the
  rest of the launch);
* the GPU side on a WARP device (Windows' software D3D11): the test card's pixels land in the corners and the screen's
  middle stays untouched; every pipeline state the draw binds is put back; the fault drill (a fault right after the
  bind) restores it through the guarded restore; a swap chain whose device is not the renderer's, a null renderer
  field, a back buffer below 640x360 or in an odd format draw nothing; an empty batch never touches the back buffer;
* OV-01 (the handler on the image twin, with the WARP renderer): nothing without a fresh HUD heartbeat; the test card
  only with the switch; the presenter stamped on every frame where the guests' menus are presentable (an empty view
  included) and never with a stale or foreign view or snapshot, co-op off, FIREWALL_MENUS off; Player 1's window
  reported left out while P1 is native and drawn otherwise; every stamp extends the presenter's run, every other frame
  breaks it and a refused frame is reported; a fault latches RENDER, withdraws the stamp and logs once; the heartbeat
  from HD-01's arguments;
* the frontend with Player 1 on the game's own frontends: P1's A and UP/DOWN open nothing, the gate never arms, a
  guest's menu leaves P1's directions alone, P1's own battle menu does not suspend the guests' menus (a shop still
  does), the Wait rule counts only the guests, a fallen P1 gets no co-op KO selector.
"""
from __future__ import annotations

import ctypes
import hashlib
import math
import os
import re
import struct
import unittest
import zlib
from pathlib import Path

import test_coop_frontend as tfe
import test_coop_hud as th
import test_coop_hud_driver as thd
import test_coop_hud_menus as thm
from coop_native_support import native_test, quiet_faulthandler
from test_coop_input_service import fresh_test_dll
from test_coop_patch_engine import GAME

ROOT = Path(__file__).resolve().parents[1]
COOP = ROOT / 'native' / 'coop'
OV_SRC = COOP / 'src' / 'overlay'
PREVIEW = os.environ.get('ILC_OVERLAY_PREVIEW')   # a folder: the WARP tests also write their frames there as PNG

u8, u16, u32, u64, i16, i32, f32 = (ctypes.c_uint8, ctypes.c_uint16, ctypes.c_uint32, ctypes.c_uint64, ctypes.c_int16,
                                     ctypes.c_int32, ctypes.c_float)
P = ctypes.POINTER


def S(name, fields):
    return type(name, (ctypes.Structure,), {'_fields_': fields})


Quad = S('Quad', [('x0', f32), ('y0', f32), ('x1', f32), ('y1', f32), ('u0', f32), ('v0', f32), ('u1', f32),
                  ('v1', f32), ('c', u32 * 4), ('c1', u32), ('p0', f32), ('p1', f32), ('p2', f32), ('mode', u8),
                  ('snap', u8), ('shine', u8), ('bias', u8), ('skew', f32)])
Vertex = S('Vertex', [('x', f32), ('y', f32), ('u', f32), ('v', f32), ('p', f32 * 4), ('c0', u32), ('c1', u32),
                      ('mode', f32), ('px', f32)])
Glyph = S('Glyph', [('x', u16), ('y', u16), ('w', u16), ('h', u16), ('bx', i16), ('by', i16), ('advance64', i32)])
Space = S('Space', [('w', u32), ('h', u32), ('scale', f32), ('ox', f32), ('oy', f32)])
TextStyle = S('TextStyle', [('core', u32), ('edge', u32), ('outline', f32), ('glow', f32), ('shadow', u32),
                            ('shadow_dx', f32), ('shadow_dy', f32), ('shadow_soft', f32), ('tracking', f32),
                            ('gap', f32)])
CardInfo = S('CardInfo', [('bb_w', u32), ('bb_h', u32), ('format', u32), ('frame_us', u32), ('draw_us', u32),
                          ('frames', u64), ('hud_alpha_pct', u32), ('menus_live', u8), ('reserved', u8 * 3),
                          ('seat_colour', u32 * 4)])
MenusOut = S('MenusOut', [('drawn', u32), ('dropped', u32), ('open', u32)])
FrameInfo = S('FrameInfo', [('w', u32), ('h', u32), ('format', u32), ('samples', u32), ('quads', u32)])
GpuStatus = S('GpuStatus', [('device', u64), ('ready', u32), ('failed_object', u32), ('failed_hr', i32),
                            ('last_w', u32), ('last_h', u32), ('last_format', u32)])
COUNTERS = ('reached', 'declined', 'hidden', 'idle', 'drawn', 'ready', 'failures', 'busy', 'faults', 'stamps', 'windows',
            'dropped_frames', 'no_view', 'no_snapshot', 'quads_last', 'cost_us_last', 'cost_us_max', 'cost_us_total',
            'frame_us', 'hud_ms', 'hud_alpha', 'last_result', 'heartbeats', 'bb_w', 'bb_h', 'bb_format', 'events',
            'tick_heartbeats', 'hud_frames', 'hud_refused', 'hud_quads_party', 'hud_quads_target', 'hud_quads_log')
Counters = S('Counters', [(n, u64) for n in COUNTERS])
OvStatus = S('OvStatus', [('attached', u8), ('row_hooked', u8), ('test_card', u8), ('dead', u8)])

QUAD_CAP = 6144   # phase 7: room for the HUD elements, four windows and the card
QM_SOLID, QM_SDF, QM_GLASS, QM_GLASS_CHAMFER, QM_HATCH = 0, 1, 2, 3, 4
FACE_BODY, FACE_TITLE, FACE_NUMERALS, FACE_ICONS = 0, 1, 2, 3
ICONS = 22   # the default theme's icon set (glove, chevrons, diamond, dot, badge, ring, six statuses x 2)
THEMES_SRC = OV_SRC / 'themes'
DRAWN, READY, NO_RENDERER, MISMATCH, CREATE_FAILED, DEVICE_REMOVED, NO_BACK_BUFFER, BAD_BACK_BUFFER = range(8)
R8G8B8A8_UNORM, B8G8R8A8_UNORM, R8_UNORM, R32G32B32A32_FLOAT = 28, 87, 61, 2
EV_FIRST_FRAME, EV_CREATE_FAILED, EV_MISMATCH, EV_BAD_BACK_BUFFER, EV_FAULT = 0x1, 0x2, 0x4, 0x8, 0x10
RUN_ORIGINAL = 0
FIREWALL_MENUS, RENDER = 0x10, 0x40
EPOCH, NOW = thd.EPOCH, thd.NOW
STYLE = TextStyle(0xFFE4E4E4, 0xE6220E0A, 1.4, 0.0, 0x96000000, 1.0, 2.0, 1.0, 0.0, 0.0)

SIG = {
    'ILCT_OvSizes': ([P(u32), u32], u32), 'ILCT_OvFontDigest': ([], ctypes.c_char_p),
    'ILCT_OvFontAtlas': ([ctypes.c_void_p, u32], i32), 'ILCT_OvFontGlyph': ([u32, u32, P(Glyph)], i32),
    'ILCT_OvFontMetrics': ([u32, P(f32), P(f32)], None), 'ILCT_OvMeasure': ([u32, f32, ctypes.c_char_p, u32], f32),
    'ILCT_OvFit': ([u32, f32, ctypes.c_char_p, u32, f32, P(i32), P(f32)], u32),
    'ILCT_OvSpace': ([u32, u32, P(Space)], None), 'ILCT_OvExpand': ([P(Quad), P(Space), P(Vertex)], u32),
    'ILCT_OvText': ([u32, f32, f32, f32, ctypes.c_char_p, u32, P(TextStyle), f32, u32, P(Quad), u32, P(f32)], u32),
    'ILCT_OvIcon': ([u32, f32, f32, f32, P(TextStyle), P(Quad), u32], u32),
    'ILCT_OvPrim': ([u32, f32, f32, f32, f32, u32, u32, u32, f32, f32, P(Quad), u32], u32),
    'ILCT_OvFill': ([u32, P(u32), P(u32)], None), 'ILCT_OvMix': ([u32, u32, f32], u32),
    'ILCT_OvFromPs2': ([u32], u32), 'ILCT_OvWithAlpha': ([u32, f32], u32),
    'ILCT_OvScene': ([u32, ctypes.c_void_p, ctypes.c_void_p, u64, P(CardInfo), P(Quad), u32, P(MenusOut)], u32),
    'ILCT_OvSceneFull': ([u32, ctypes.c_void_p, ctypes.c_void_p, u64, P(Quad), u32, P(MenusOut)], u32),
    'ILCT_OvEase': ([u64, u64], f32), 'ILCT_OvRenderLive': ([], i32),
    'ILCT_OvStates': ([P(ctypes.c_char_p), u32, P(ctypes.c_char_p), u32, P(u32)], u32),
    'ILCT_OvFormatOk': ([u32], i32), 'ILCT_OvGpuStatus': ([P(GpuStatus)], None),
    'ILCT_OvWarpInit': ([u32, u32, u32], u64), 'ILCT_OvWarpFree': ([], None), 'ILCT_OvWarpClear': ([u32], None),
    'ILCT_OvWarpFrame': ([u32, ctypes.c_void_p, ctypes.c_void_p, u64, P(CardInfo), P(FrameInfo), P(MenusOut)], i32),
    'ILCT_OvWarpRead': ([ctypes.c_void_p, u32], u32), 'ILCT_OvWarpRoundTrip': ([u32, P(CardInfo), P(i32)], u32),
    'ILCT_OvWarpFaultDrill': ([P(CardInfo), P(i32)], u32), 'ILCT_OvWarpMismatch': ([i32], i32),
    'ILCT_OvWarpRendererField': ([u32, u64], None), 'ILCT_OvWarpRenderer': ([], u64),
    'ILCT_OvWarpGetBufferCalls': ([], u32),
    'ILCT_OvWarpGlass': ([u32, f32, f32, f32, f32, f32, f32, f32, u32, P(FrameInfo)], i32),
    'ILCT_OvReset': ([], None), 'ILCT_OvAttach': ([u64], i32), 'ILCT_OvSetTestCard': ([i32], None),
    'ILCT_OvSetHeartbeat': ([u64, u32], None), 'ILCT_OvInjectFault': ([u32], None),
    'ILCT_OvCall': ([ctypes.c_void_p, u32], i32), 'ILCT_OvNoteHud': ([u64, u64], None),
    'ILCT_OvUiTick': ([], None), 'ILCT_TiGbForcePins': ([i32], None),
    'ILCT_OvStats': ([P(Counters)], None), 'ILCT_OvStatus': ([P(OvStatus)], None),
    'ILCT_OvConfigure': ([ctypes.c_char_p], None), 'ILCT_OvLastDropped': ([], u32),
    'ILCT_OvLastBatch': ([P(Quad), u32], u32), 'ILCT_OvLogCapture': ([i32], None), 'ILCT_OvStep': ([u64], None),
    'ILCT_OvLogText': ([], ctypes.c_char_p),
    'ILCT_FeSetP1Native': ([i32], None), 'ILCT_FeP1Native': ([], i32), 'ILCT_SesP1Native': ([], i32),
    'ILCT_OvSlot': ([u32, ctypes.c_void_p, ctypes.c_void_p], i32), 'ILCT_OvSlotConstants': ([P(f32)], None),
    'ILCT_OvSceneCardLast': ([u32, ctypes.c_void_p, ctypes.c_void_p, u64, P(CardInfo), P(Quad), u32, P(MenusOut)],
                             u32),
    'ILCT_FeSetP1Arming': ([i32], None), 'ILCT_FeP1Arming': ([], i32), 'ILCT_FeP1Arm': ([ctypes.c_void_p], None),
    'ILCT_FeP1ArmStep': ([ctypes.c_void_p, ctypes.c_void_p, u64], u32), 'ILCT_FePresenterRunFrame': ([u64], None),
    'ILCT_FePresenterBreakRun': ([], None), 'ILCT_FePresenterFailed': ([u64], None),
    'ILCT_FePresenterRunView': ([ctypes.c_void_p], None), 'ILCT_FeP1Givebacks': ([], u64),
    'ILCT_FePresenterRunReady': ([u64], None), 'ILCT_FeSetOverlayLive': ([i32], None),
    'ILCT_OvForceLive': ([i32], None), 'ILCT_OvSetPresented': ([u64], None), 'ILCT_OvLive': ([u64], i32),
    'ILCT_OvFieldOpen': ([], i32), 'ILCT_OvLogReplaces': ([u64], i32),
    'ILCT_IxTakeMenuOpens': ([u32], u32), 'ILCT_IxNoteMenuOpen': ([u32, u32], None), 'ILCT_IxMenuOpensCount': ([], u64),
    'ILCT_IxPromptStatus': ([ctypes.c_void_p], None), 'ILCT_IxPromptAttach': ([u64], i32),
}
PromptStatus = S('PromptStatus', [('attached', u8), ('marker_pins', u8), ('label_pins', u8), ('gate_open', u8),
                                  ('row', u32), ('first_mismatch', u64)])
Rect = S('Rect', [('x', f32), ('y', f32), ('w', f32), ('h', f32)])
Slot = S('Slot', [('frame', Rect), ('tab', Rect), ('header', Rect), ('rows', Rect), ('footer', Rect), ('visible', u32),
                  ('start', u32)])
P1ArmIn = S('P1ArmIn', [('wanted', u8), ('live', u8), ('layer', u8), ('native_up', u8), ('gave_back', u8),
                        ('vanilla_race', u8), ('drawn_ok', u8), ('reserved0', u8), ('live_since', u64),
                        ('live_last', u64), ('failed_ms', u64), ('reserved1', u64)])
P1Arm = S('P1Arm', [('armed', u8), ('last_event', u8), ('latched', u8), ('reserved', u8 * 5), ('armed_ms', u64), ('hold_until', u64),
                    ('arms', u32), ('disarms', u32), ('holds', u32), ('decay_base_s', u32)])
RunView = S('RunView', [('frames', u32), ('missed', u32), ('since_ms', u64), ('last_ms', u64), ('failed_ms', u64),
                        ('gpu_drawn_ms', u64)])
PA_NONE, PA_ARMED, PA_DISARMED, PA_HELD, PA_LATCHED = range(5)
P1_ARM_FRAMES, P1_ARM_MS, P1_HOLD_MS, RUN_GAP_MS, P1_HOLDS_MAX, P1_RUN_FRESH_MS = 15, 250, 5000, 100, 4, 100
P1_RACE_HOLD_MS, P1_DECAY_MS, LIVE_MS = 2000, 600000, 250
SCENE_P1 = 4   # ILCT_OvScene kind bit: Player 1's window is ours


def rect_box(r) -> tuple:
    return (r.x, r.y, r.x + r.w, r.y + r.h)


def inside(inner, outer) -> bool:
    return outer[0] <= inner[0] and outer[1] <= inner[1] and inner[2] <= outer[2] and inner[3] <= outer[3]


def disjoint(a, b) -> bool:
    return a[2] <= b[0] or b[2] <= a[0] or a[3] <= b[1] or b[3] <= a[1]


def declare(d):
    for name, (args, res) in SIG.items():
        fn = getattr(d, name)
        fn.argtypes = args
        fn.restype = res
    return d


def rgba(r, g, b, a=255) -> int:
    return r | g << 8 | b << 16 | a << 24


def card(w=1920, h=1080) -> CardInfo:
    c = CardInfo()
    c.bb_w, c.bb_h, c.format, c.frame_us, c.draw_us, c.frames, c.hud_alpha_pct = w, h, R8G8B8A8_UNORM, 16667, 42, 7, 100
    return c


def party_snapshot(seats_by_row=(1, 2, 3, 0), epoch=EPOCH, sampled=NOW) -> thd.Snapshot:
    """Four party rows; row k is held by seat seats_by_row[k] (1-based, 0 = a gambit ally)."""
    s = thd.snapshot(epoch=epoch, sampled=sampled)
    s.member_count = len(seats_by_row)
    for k, seat in enumerate(seats_by_row):
        u = s.members[k]
        u.member, u.seat, u.flags, u.hp, u.hp_max = k, seat, 0x3, 100, 100
        u.handle = 0x100001 + k
    return s


def view_with(seats: dict, **kw) -> thm.MenuView:
    return thm.menu_view(seats, **kw)


def quads(d, kind, view=None, snap=None, now=NOW, ci=None, cap=QUAD_CAP):
    out = (Quad * cap)()
    mo = MenusOut()
    n = d.ILCT_OvScene(kind, ctypes.addressof(view) if view is not None else None,
                       ctypes.addressof(snap) if snap is not None else None, now,
                       ctypes.byref(ci) if ci is not None else None, out, cap, ctypes.byref(mo))
    return list(out[:min(n, cap)]), mo


def bounds(qs):
    return (min(q.x0 for q in qs), min(q.y0 for q in qs), max(q.x1 for q in qs), max(q.y1 for q in qs))


GLYPH_PAD = 14.0   # an SDF cell's transparent margin (spread + 1 atlas pixels) at the biggest mark (the glove), and its keyline


def visible_bounds(qs):
    """Bounds with each SDF quad's transparent cell margin taken off (what can show on screen)."""
    out = []
    for q in qs:
        pad = min(GLYPH_PAD, (q.x1 - q.x0) * 0.2) if q.mode == QM_SDF else 0.0
        out.append((q.x0 + pad, q.y0 + pad, q.x1 - pad, q.y1 - pad))
    return (min(b[0] for b in out), min(b[1] for b in out), max(b[2] for b in out), max(b[3] for b in out))


class Win:
    """A themed window body: the whole-window rectangle its glass bands share, and the top band's colours."""
    def __init__(self, r, c):
        self.x0, self.y0, self.x1, self.y1 = r
        self.c = c


def glass_rects(qs, w=None, h=None):
    """Distinct whole-window rectangles of the glass quads (a band, a keyline or a plain glass), optionally of a size."""
    out = {}
    for q in qs:
        if q.mode not in (QM_GLASS, QM_GLASS_CHAMFER):
            continue
        r = (q.u0, q.v0, q.u1, q.v1) if q.u1 > q.u0 else (q.x0, q.y0, q.x1, q.y1)
        if w is not None and abs((r[2] - r[0]) - w) > 0.01:
            continue
        if h is not None and abs((r[3] - r[1]) - h) > 0.01:
            continue
        if r not in out or q.y0 < out[r].first_y:
            out[r] = Win(r, list(q.c))
            out[r].first_y = q.y0
    return list(out.values())


def write_png(path: Path, w: int, h: int, rgba_bytes: bytes):
    raw = b''.join(b'\0' + rgba_bytes[y * w * 4:(y + 1) * w * 4] for y in range(h))

    def chunk(tag, data):
        return struct.pack('>I', len(data)) + tag + data + struct.pack('>I', zlib.crc32(tag + data) & 0xFFFFFFFF)

    path.write_bytes(b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', w, h, 8, 6, 0, 0, 0)) +
                     chunk(b'IDAT', zlib.compress(raw, 9)) + chunk(b'IEND', b''))


# ---- static -------------------------------------------------------------------------------------------------------------
class StaticTest(unittest.TestCase):
    def test_only_gpu_cpp_talks_to_d3d(self):
        for path in OV_SRC.glob('*.[ch]*'):
            text = path.read_text(encoding='utf-8')
            if path.name == 'gpu.cpp':
                self.assertIn('#include <d3d11.h>', text)
                continue
            if path.suffix in ('.cpp', '.h'):
                self.assertNotIn('d3d11.h', text, path.name)

    def test_no_new_import_and_no_runtime_compiler(self):
        text = (OV_SRC / 'gpu.cpp').read_text(encoding='utf-8')
        for name in ('D3D11CreateDevice', 'D3DCompile', 'CreateDXGIFactory', 'LoadLibrary', 'GetProcAddress'):
            self.assertNotIn(name, text, f'{name}: the overlay uses the game\'s device, nothing else')
        allow = (COOP / 'pe-allowlist.json').read_text(encoding='utf-8').lower()
        for dll in ('d3d11.dll', 'dxgi.dll', 'd3dcompiler'):
            self.assertNotIn(f'"{dll}', allow)

    def test_the_render_thread_never_logs(self):
        text = (OV_SRC / 'overlay.cpp').read_text(encoding='utf-8')
        hook = re.search(r'int32_t frame_hook\(.*?\n}\n', text, re.S).group(0)
        heart = re.search(r'void note_hud\(.*?\n}\n', text, re.S).group(0)
        for body in (hook, heart):
            self.assertNotIn('log::post', body)
            self.assertNotIn('log::drain', body)
        for name in ('gpu.cpp', 'batch.cpp', 'scene.cpp', 'font.cpp'):
            self.assertNotIn('log::', (OV_SRC / name).read_text(encoding='utf-8'), name)

    def test_generated_inputs_are_current(self):
        hlsl = (OV_SRC / 'overlay.hlsl').read_bytes().replace(b'\r\n', b'\n')
        self.assertIn(f'// hlsl-sha256: {hashlib.sha256(hlsl).hexdigest()}',
                      (OV_SRC / 'shaders.inc').read_text(encoding='utf-8'))
        import json
        lock = json.loads((THEMES_SRC / 'themes.lock.json').read_text(encoding='utf-8'))
        self.assertEqual(lock['order'], ['legacy-bold', 'legacy-humanist', 'legacy-crisp'], 'C first: the default')
        for theme_id in lock['order']:
            for name, want in lock['themes'][theme_id]['files'].items():
                data = (THEMES_SRC / theme_id / name).read_bytes().replace(b'\r\n', b'\n')
                self.assertEqual(hashlib.sha256(data).hexdigest(), want, f'{theme_id}/{name}')

    def test_the_stand_in_fonts_are_gone(self):
        # Our own fonts only (Marco, 2026-11-16): the notices name SDL and MinHook (with HDE) and nothing else.
        self.assertFalse((COOP / 'third_party' / 'fonts').exists())
        self.assertFalse((OV_SRC / 'font.inc').exists())
        notices = (COOP / 'THIRD_PARTY_NOTICES.txt').read_text(encoding='utf-8')
        for needle in ('Simple DirectMedia Layer', 'MinHook'):
            self.assertIn(needle, notices)
        for gone in ('Alegreya', 'Marcellus', 'SIL OPEN FONT LICENSE'):
            self.assertNotIn(gone, notices)

    def test_the_boot_and_the_heartbeat_are_wired(self):
        boot = (COOP / 'src' / 'boot' / 'boot.cpp').read_text(encoding='utf-8')
        for call in ('overlay::attach(g_host.image_base)', 'overlay::configure_from_settings(applied_now)',
                     'overlay::configure_from_settings(applied)', 'overlay::step(now)', 'overlay::stop()'):
            self.assertIn(call, boot)
        replay = (COOP / 'src' / 'hud' / 'replay.cpp').read_text(encoding='utf-8')
        body = re.search(r'int32_t h0_body\(patch::ctx\* c, uint32_t epoch\)\n\{(.*?)\n}\n', replay, re.S).group(1)
        self.assertLess(body.index('overlay::note_hud(c->rcx, c->rdx)'), body.index('armed()'),
                        'the heartbeat counts before the replay path declines')

    def test_p1s_battle_frontend_is_sampled_masked(self):
        world = (COOP / 'src' / 'session' / 'world.cpp').read_text(encoding='utf-8')
        self.assertIn('command_actor & FRONTEND_ID_MASK', world)
        for name in ('FRONTEND_BATTLE', 'FRONTEND_LEADER', 'FRONTEND_TREE_ACTIVE'):
            self.assertIn(name, world)
        facts = (COOP / 'src' / 'session' / 'game_facts.h').read_text(encoding='utf-8')
        fw = (COOP / 'src' / 'firewall' / 'firewall_facts.h').read_text(encoding='utf-8')
        for name in ('FRONTEND_ID_MASK', 'FRONTEND_BATTLE', 'FRONTEND_LEADER', 'FRONTEND_TREE_ACTIVE'):
            a = re.search(rf'constexpr uint32_t {name} = (0x[0-9A-F]+|\d+);', facts).group(1)
            b = re.search(rf'constexpr uint32_t {name} = (0x[0-9A-F]+|\d+);', fw).group(1)
            self.assertEqual(int(a, 0), int(b, 0), f'{name}: the session and the firewall agree')
        policy = (COOP / 'src' / 'session' / 'policy.cpp').read_text(encoding='utf-8')
        self.assertIn('const bool cutscene = p1_structural && !w.control_enabled && !w.menu_open', policy,
                      'cutscene detection keeps the raw flag')
        self.assertIn('w.menu_open || w.modal_active', (COOP / 'src' / 'session' / 'session.cpp').read_text(
            encoding='utf-8'), 'the phase keeps the raw flag (no guest Party request into P1\'s battle menu)')

    def test_pads_keep_playing_unfocused(self):
        focus = (COOP / 'src' / 'focus' / 'focus.cpp').read_text(encoding='utf-8')
        body = re.search(r'int32_t in06\(patch::ctx\* c, uint32_t epoch\)\n\{(.*?)\n}\n', focus, re.S).group(1)
        self.assertIn('c->resume = game(XI_FOCUSED_PATH);', body)
        self.assertNotIn('write_game', body, 'IN-06 passes the real pad: nothing written')
        self.assertIn('constexpr uint64_t XI_FOCUSED_PATH = 0x7A223C;', focus)

    def test_the_old_layer_and_its_test_draw_stay_off(self):
        self.assertIn('driver::set_layer_enabled(false);',
                      (COOP / 'src' / 'hud' / 'battle_hud.cpp').read_text(encoding='utf-8'))
        self.assertIn('const bool on = false;', (COOP / 'src' / 'hud' / 'canvas.cpp').read_text(encoding='utf-8'))

    def test_the_site_rows(self):
        inc = (COOP / 'src' / 'patch' / 'sites.inc').read_text(encoding='utf-8')
        ov = re.search(r'X\("OV-01", 0x4E7588u, 5, ILC_SITE_KIND_CALL, ILC_SITE_POLICY_PATCH, ILC_SITE_SUBSYS_RENDER, '
                       r'0x([0-9A-F]{2}), ILC_SITE_NO_GATE, "\\xE8\\x13\\xBF\\x01\\x00"', inc)
        self.assertIsNotNone(ov)
        self.assertEqual(int(ov.group(1), 16), 0x08, 'OPTIONAL, not INTERIOR')
        rows = re.findall(r'X\("([^"]+)"', inc)
        ov_index = rows.index('OV-01')
        for gate in ('OV-V1', 'OV-V2', 'OV-V3'):
            m = re.search(rf'X\("{gate}", 0x[0-9A-F]+u, \d+, ILC_SITE_KIND_VERIFY, ILC_SITE_POLICY_GATE, '
                          rf'ILC_SITE_SUBSYS_RENDER, 0x00, (\d+),', inc)
            self.assertIsNotNone(m, gate)
            self.assertEqual(int(m.group(1)), ov_index, gate)


# ---- pure: font, batch, scene -----------------------------------------------------------------------------------------
@native_test
class PureCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())
        sizes = (u32 * 32)()
        n = cls.d.ILCT_OvSizes(sizes, 32)
        cls.sizes = list(sizes[:n])
        cls.atlas_w, cls.atlas_h, cls.em, cls.spread = cls.sizes[3:7]


class LayoutsTest(PureCase):
    def test_struct_layouts(self):
        want = [ctypes.sizeof(Quad), ctypes.sizeof(Vertex), QUAD_CAP]
        self.assertEqual(self.sizes[:3], want)
        self.assertEqual(self.sizes[8], ctypes.sizeof(Counters))
        self.assertEqual(self.sizes[9:16], [ctypes.sizeof(TextStyle), ctypes.sizeof(Space), ctypes.sizeof(CardInfo),
                                            ctypes.sizeof(MenusOut), ctypes.sizeof(Glyph), ctypes.sizeof(GpuStatus),
                                            ctypes.sizeof(OvStatus)])


class FontTest(PureCase):
    def test_the_atlas_decodes_to_the_recorded_digest(self):
        size = self.atlas_w * self.atlas_h
        buf = (u8 * size)()
        self.assertEqual(self.d.ILCT_OvFontAtlas(buf, size), 1)
        self.assertEqual(self.d.ILCT_OvFontAtlas(buf, size - 1), 0, 'a short buffer is refused')
        inc = (THEMES_SRC / 'legacy-bold' / 'font.inc').read_text(encoding='utf-8')
        self.assertIn(f'// atlas-digest: {self.d.ILCT_OvFontDigest().decode()}', inc)
        self.assertGreater(sum(1 for b in bytes(buf) if b > 128), 10000, 'glyph interiors are there')

    def test_every_glyph_and_icon_sits_in_the_atlas(self):
        g = Glyph()
        for face in (FACE_BODY, FACE_TITLE):
            for code in range(32, 127):
                self.assertEqual(self.d.ILCT_OvFontGlyph(face, code, ctypes.byref(g)), 1, (face, chr(code)))
                self.assertGreater(g.advance64, 0, (face, chr(code)))
                if code == 32:
                    self.assertEqual((g.w, g.h), (0, 0), 'the space draws nothing')
                    continue
                self.assertTrue(g.w > 2 * self.spread and g.h > 2 * self.spread, (face, chr(code)))
                self.assertLessEqual(g.x + g.w, self.atlas_w)
                self.assertLessEqual(g.y + g.h, self.atlas_h)
            self.assertEqual(self.d.ILCT_OvFontGlyph(face, 0x4E00, ctypes.byref(g)), 0, 'outside the charset: none')
        self.assertEqual(self.d.ILCT_OvFontGlyph(FACE_BODY, 0xE9, ctypes.byref(g)), 1, 'accented Latin-1 letters exist')
        for icon in range(ICONS):
            self.assertEqual(self.d.ILCT_OvFontGlyph(FACE_ICONS, icon, ctypes.byref(g)), 1)
            self.assertEqual((g.w, g.h), (self.em + 2 * (self.spread + 1),) * 2)
        self.assertEqual(self.d.ILCT_OvFontGlyph(FACE_ICONS, ICONS, ctypes.byref(g)), 0)

    def test_measure_is_the_sum_of_advances(self):
        g = Glyph()
        self.d.ILCT_FtKern.argtypes, self.d.ILCT_FtKern.restype = [u32, u32, u32], i32
        for face in (FACE_BODY, FACE_TITLE):
            text = b'Curaxo'
            total = 0
            for k, ch in enumerate(text):
                self.d.ILCT_OvFontGlyph(face, ch, ctypes.byref(g))
                total += g.advance64
                if k + 1 < len(text):
                    total += self.d.ILCT_FtKern(face, ch, text[k + 1])
            want = total / 64 * 24 / self.em
            self.assertAlmostEqual(self.d.ILCT_OvMeasure(face, 24.0, text, 99), want, places=3)
            self.assertAlmostEqual(self.d.ILCT_OvMeasure(face, 48.0, text, 99), 2 * want, places=3)
            self.assertAlmostEqual(self.d.ILCT_OvMeasure(face, 24.0, text, 3), self.d.ILCT_OvMeasure(face, 24.0, b'Cur', 9),
                                   places=4, msg='n stops early')

    def test_fit_cuts_with_dots(self):
        cut, width = i32(), f32()
        text = b'Phoenix Down of Many Names'
        full = self.d.ILCT_OvMeasure(FACE_BODY, 23.0, text, 99)
        self.assertEqual(self.d.ILCT_OvFit(FACE_BODY, 23.0, text, 99, full + 1, ctypes.byref(cut), ctypes.byref(width)),
                         len(text))
        self.assertEqual(cut.value, 0)
        keep = self.d.ILCT_OvFit(FACE_BODY, 23.0, text, 99, full / 2, ctypes.byref(cut), ctypes.byref(width))
        self.assertEqual(cut.value, 1)
        self.assertLess(keep, len(text))
        self.assertLessEqual(width.value, full / 2 + 1e-3)
        # The width drawn: the kept letters, the kerning to the first '.', the dots (= measure of prefix + "...").
        drawn = text[:keep] + b'...'
        self.assertAlmostEqual(width.value, self.d.ILCT_OvMeasure(FACE_BODY, 23.0, drawn, len(drawn)), places=3)


class BatchTest(PureCase):
    def prim(self, kind, a=0xFF0000FF, b=0xFF00FF00, c=0xFFFF0000, param=6.0, alpha=1.0, rect=(10, 20, 100, 30)):
        out = (Quad * 8)()
        n = self.d.ILCT_OvPrim(kind, *rect, a, b, c, param, alpha, out, 8)
        return list(out[:n])

    def test_primitives(self):
        q, = self.prim(0)
        self.assertEqual((q.mode, q.snap, q.x0, q.y0, q.x1, q.y1, list(q.c)), (QM_SOLID, 1, 10, 20, 110, 50, [0xFF0000FF] * 4))
        q, = self.prim(1)
        self.assertEqual(list(q.c), [0xFF0000FF, 0xFF0000FF, 0xFF00FF00, 0xFF00FF00], 'top, top, bottom, bottom')
        q, = self.prim(2)
        self.assertEqual(list(q.c), [0xFF0000FF, 0xFF00FF00, 0xFF0000FF, 0xFF00FF00], 'left, right, left, right')
        fade = self.prim(3, param=20.0)
        self.assertEqual(len(fade), 3)
        self.assertEqual((fade[0].c[0] >> 24, fade[0].c[1] >> 24, fade[2].c[1] >> 24), (0, 0xFF, 0), 'ends fade out')
        self.assertEqual((fade[0].x1, fade[2].x0), (30.0, 90.0))
        q, = self.prim(4, param=6.0)
        self.assertEqual((q.mode, q.p0, q.c1, list(q.c)), (QM_GLASS, 6.0, 0xFFFF0000,
                                                           [0xFF0000FF, 0xFF0000FF, 0xFF00FF00, 0xFF00FF00]))
        self.assertEqual(self.prim(0, rect=(0, 0, 0, 10)), [], 'an empty rectangle draws nothing')
        self.assertEqual(self.prim(0, a=0x00FFFFFF), [], 'a clear colour draws nothing')

    def test_the_fade_multiplier(self):
        q, = self.prim(4, alpha=0.5)
        self.assertEqual([c >> 24 for c in q.c] + [q.c1 >> 24], [128] * 5)
        self.assertEqual(self.d.ILCT_OvWithAlpha(0xC8112233, 0.5), 0x64112233)
        self.assertEqual(self.d.ILCT_OvMix(0xFF000000, 0xFFFFFFFF, 0.5), 0xFF808080)
        self.assertEqual(self.d.ILCT_OvFromPs2(0x80408020), 0xFF80FF40, '0x80 = 1.0 per channel, alpha full')

    def test_capacity_refuses_and_never_overwrites(self):
        kept, refused = u32(), u32()
        self.d.ILCT_OvFill(QUAD_CAP + 7, ctypes.byref(kept), ctypes.byref(refused))
        self.assertEqual((kept.value, refused.value), (QUAD_CAP, 7))

    def test_text_and_icons(self):
        out = (Quad * 64)()
        width = f32()
        n = self.d.ILCT_OvText(FACE_BODY, 100.0, 200.0, 24.0, b'Fire', 99, ctypes.byref(STYLE), 0.0, 0, out, 64,
                               ctypes.byref(width))
        self.assertEqual(n, 8, 'four glyphs and their shadows')
        self.assertTrue(all(q.mode == QM_SDF for q in out[:n]))
        shadow, main = out[:4], out[4:8]
        self.assertTrue(all(abs((m.x0 - s.x0) - (-1.0)) < 1e-4 and abs((m.y0 - s.y0) - (-2.0)) < 1e-4
                            for s, m in zip(shadow, main)), 'shadow at (+1, +2)')
        self.assertEqual(main[0].c[0], STYLE.core)
        self.assertAlmostEqual(width.value, self.d.ILCT_OvMeasure(FACE_BODY, 24.0, b'Fire', 99), places=3)
        n = self.d.ILCT_OvText(FACE_BODY, 500.0, 0.0, 24.0, b'Fire', 99, ctypes.byref(STYLE), 0.0, 1, out, 64,
                               ctypes.byref(width))
        pad = (self.spread + 1) * 24.0 / self.em   # a cell's distance padding past the glyph's ink
        self.assertLessEqual(max(q.x1 for q in out[4:n]), 500.0 + pad + 0.5, 'right-aligned on x')
        self.assertGreater(max(q.x1 for q in out[4:n]), 500.0 - 6.0, 'ending at x, not short of it')
        n = self.d.ILCT_OvIcon(0, 10.0, 10.0, 32.0, ctypes.byref(STYLE), out, 64)
        self.assertEqual(n, 2)
        q = out[1]
        k = 32.0 / self.em
        self.assertAlmostEqual(q.x0, 10.0 - (self.spread + 1) * k, places=4)
        self.assertAlmostEqual(q.x1 - q.x0, (self.em + 2 * (self.spread + 1)) * k, places=4)

    def test_the_authoring_space(self):
        s = Space()
        for (w, h), (scale, ox, oy) in {(1920, 1080): (1.0, 0, 0), (3840, 2160): (2.0, 0, 0),
                                        (1280, 720): (2 / 3, 0, 0), (1920, 1200): (1.0, 0, 60),
                                        (2560, 1080): (1.0, 320, 0), (1024, 768): (1024 / 1920, 0, (768 - 576) / 2)}.items():
            self.d.ILCT_OvSpace(w, h, ctypes.byref(s))
            self.assertAlmostEqual(s.scale, scale, places=5, msg=(w, h))
            self.assertAlmostEqual(s.ox, ox, places=3, msg=(w, h))
            self.assertAlmostEqual(s.oy, oy, places=3, msg=(w, h))

    def test_expansion(self):
        s = Space()
        self.d.ILCT_OvSpace(2560, 1440, ctypes.byref(s))
        q, = self.prim(4, param=6.0, rect=(0, 0, 1920, 1080))
        v = (Vertex * 6)()
        self.assertEqual(self.d.ILCT_OvExpand(ctypes.byref(q), ctypes.byref(s), v), 6)
        self.assertEqual([(v[k].x, v[k].y) for k in (0, 1, 2, 4)], [(-1, 1), (1, 1), (-1, -1), (1, -1)])
        self.assertEqual((v[0].u, v[0].v, v[4].u, v[4].v), (-1280, -720, 1280, 720), 'glass: pixels from the centre')
        self.assertEqual((v[0].p[0], v[0].p[1], v[0].p[2], v[0].mode, v[0].px), (1280, 720, 8.0, QM_GLASS, s.scale))
        self.assertEqual((v[1].x, v[1].y), (v[3].x, v[3].y))
        self.assertEqual((v[2].x, v[2].y), (v[5].x, v[5].y))
        self.d.ILCT_OvSpace(1366, 768, ctypes.byref(s))
        q, = self.prim(0, rect=(10.3, 20.2, 0.5, 0.4))
        self.d.ILCT_OvExpand(ctypes.byref(q), ctypes.byref(s), v)
        x0, x1 = (v[0].x + 1) / 2 * 1366, (v[1].x + 1) / 2 * 1366
        self.assertAlmostEqual(x0, round(x0), places=3, msg='snapped to a pixel')
        self.assertGreaterEqual(round(x1 - x0), 1, 'never thinner than a pixel')

    def test_every_mode_survives_its_packed_fraction(self):
        """Review ★2: the pixel shader selects by floor(mode); every mode with every packed sheen and rim bias (the glass
        modes carry (shine * 32 + bias) / 1024 in the fraction, up to 1023/1024) floors back to that mode."""
        s = Space()
        self.d.ILCT_OvSpace(1920, 1080, ctypes.byref(s))
        v = (Vertex * 6)()
        for mode in (QM_SOLID, QM_SDF, QM_GLASS, QM_GLASS_CHAMFER, QM_HATCH):
            for shine in range(32):
                for bias in (0, 1, 15, 30, 31):
                    q = Quad(x0=10, y0=10, x1=200, y1=100, u0=10, v0=10, u1=200, v1=100, mode=mode, shine=shine,
                             bias=bias, p0=4.0, p1=8.0, p2=1.0)
                    self.d.ILCT_OvExpand(ctypes.byref(q), ctypes.byref(s), v)
                    got = v[0].mode
                    self.assertEqual(math.floor(got + 0.0001), mode, (mode, shine, bias, got))
                    if mode in (QM_GLASS, QM_GLASS_CHAMFER):
                        self.assertEqual(round((got - mode) * 1024), shine * 32 + bias)
                    else:
                        self.assertEqual(got, float(mode), 'only the glass modes pack a fraction')
        q = Quad(x0=0, y0=0, x1=100, y1=100, u0=0, v0=0, u1=100, v1=100, mode=QM_GLASS_CHAMFER, shine=31, bias=31,
                 p1=10.0)
        self.d.ILCT_OvExpand(ctypes.byref(q), ctypes.byref(s), v)
        self.assertGreater(v[0].mode, 3.5, 'the probe: chamfer + full sheen is above the old 3.5 hatch threshold')
        self.assertEqual(math.floor(v[0].mode), QM_GLASS_CHAMFER)

    def test_the_shader_selects_modes_by_floor(self):
        """Review ★2, the source: no threshold on m.x itself selects a mode; HATCH only when floor(mode) == 4."""
        hlsl = (OV_SRC / 'overlay.hlsl').read_text(encoding='utf-8')
        body = hlsl[hlsl.index('float4 ps_main('):]
        code = '\n'.join(line.split('//')[0] for line in body.splitlines())
        self.assertIn('float base = floor(i.m.x + 0.0001);', code)
        self.assertNotRegex(code, r'if \(i\.m\.x\s*[<>]', 'no mode test on the raw m.x')
        self.assertIn('if (base > 3.5) {', code)
        self.assertIn('if (base > 4.5) {', code)
        self.assertLess(code.index('float base = floor('), code.index('if (base < 0.5)'))


class SceneTest(PureCase):
    def test_the_test_card_stays_on_screen(self):
        qs, mo = quads(self.d, 1, ci=card())
        self.assertGreater(len(qs), 200)
        x0, y0, x1, y1 = bounds(qs)
        self.assertGreaterEqual(x0, -0.5)
        self.assertGreaterEqual(y0, -0.5)
        self.assertLessEqual(x1, 1920.5)
        self.assertLessEqual(y1, 1080.5)
        glass = [q for q in qs if q.mode == QM_GLASS]
        corners = {(q.x0 < 960, q.y0 < 540) for q in glass}
        self.assertEqual(len(glass), 4)
        self.assertEqual(corners, {(True, True), (False, True), (True, False), (False, False)}, 'a card per corner')
        self.assertEqual((mo.drawn, mo.dropped), (0, 0))

    def slot(self, seat) -> Slot:
        sl = Slot()
        self.assertEqual(self.d.ILCT_OvSlot(seat, ctypes.addressof(thm.seat_view(member=seat)), ctypes.addressof(sl)), 1)
        return sl

    @staticmethod
    def windows(qs):
        """The window bodies: the slot-sized glass (its bands share the window's rectangle; the keyline is bigger)."""
        return glass_rects(qs, 275.0, 230.0)

    def test_player_1s_window_only_when_ours(self):
        v = view_with({0: thm.seat_view(member=0), 1: thm.seat_view(member=1, values=True, toggle=True),
                       2: thm.seat_view(kind=thm.WK_TARGET, member=2, rows=3)})
        snap = party_snapshot()
        qs, mo = quads(self.d, 2, view=v, snap=snap)
        self.assertEqual((mo.drawn, mo.dropped, mo.open), (2, 0b001, 0b111), 'P1 native: reported left out')
        bodies = self.windows(qs)
        self.assertEqual(len(bodies), 2, 'P2 and P3 only')
        self.assertEqual(len(glass_rects(qs, h=32.0)), 2, 'each window with its tab (the theme\'s 32-unit tab)')
        p1_slot = rect_box(self.slot(0).frame)
        for g in bodies:
            self.assertTrue(disjoint((g.x0, g.y0, g.x1, g.y1), p1_slot), 'P1\'s slot stays empty')
        qs, mo = quads(self.d, 2 | SCENE_P1, view=v, snap=snap)
        self.assertEqual((mo.drawn, mo.dropped, mo.open), (3, 0, 0b111), 'P1\'s menu is ours: drawn')
        bodies = self.windows(qs)
        self.assertEqual(len(bodies), 3)
        self.assertEqual(min(g.x0 for g in bodies), self.slot(0).frame.x, 'P1 leftmost, in slot 0')

    def test_every_window_sits_in_its_own_slot(self):
        """Fixed slots: the same place and size whoever is open, whatever the party list (reduced or stock, any row)."""
        for open_seats in ((0,), (3,), (1, 3), (0, 1, 2, 3)):
            for reduced in (True, False):
                for rows in ((1, 2, 3, 0), (0, 3, 2, 1)):
                    v = view_with({s: thm.seat_view(member=s) for s in open_seats}, reduced=reduced)
                    qs, mo = quads(self.d, 2 | SCENE_P1, view=v, snap=party_snapshot(seats_by_row=rows))
                    with self.subTest(open=open_seats, reduced=reduced, rows=rows):
                        self.assertEqual((mo.drawn, mo.dropped), (len(open_seats), 0))
                        bodies = sorted(self.windows(qs), key=lambda g: g.x0)
                        for seat, g in zip(open_seats, bodies):
                            f = self.slot(seat).frame
                            self.assertEqual((g.x0, g.y0, g.x1 - g.x0, g.y1 - g.y0), (f.x, f.y, f.w, f.h))

    def test_every_page_kind_for_four_seats(self):
        for kind in (thm.WK_LIST, thm.WK_TARGET, thm.WK_SELECTOR):
            for rows in (1, 3, 8):
                v = view_with({s: thm.seat_view(kind=kind, member=s, rows=rows, values=True, total=rows + 4, first=2)
                               for s in (0, 1, 2, 3)})
                qs, mo = quads(self.d, 2 | SCENE_P1, view=v, snap=party_snapshot())
                with self.subTest(kind=kind, rows=rows):
                    self.assertEqual((mo.drawn, mo.dropped), (4, 0))
                    x0, y0, x1, y1 = visible_bounds(qs)
                    self.assertTrue(x0 >= 0 and y0 >= 0 and x1 <= 1920 and y1 <= 1080, (x0, y0, x1, y1))
                    sdf = [q for q in qs if q.mode == QM_SDF]
                    self.assertGreater(len(sdf), 40, 'tabs, titles, rows, help')
                    bodies = sorted(self.windows(qs), key=lambda g: g.x0)
                    self.assertEqual(len({(round(g.x1 - g.x0), round(g.y1 - g.y0)) for g in bodies}), 1, 'uniform')
                    for a, b in zip(bodies, bodies[1:]):
                        self.assertTrue(disjoint((a.x0, a.y0, a.x1, a.y1), (b.x0, b.y0, b.x1, b.y1)))

    def test_the_seat_shows_in_the_tab_the_focused_row_and_the_glove(self):
        """The default theme (Bold Modern): the tab's numbered block, the focused row's bar (theme focus_row) and the
        glove's seat-colour layer carry the seat; nothing else is in the seat colour."""
        v = view_with({1: thm.seat_view(member=1, rows=5, cursor=2)})
        qs, _ = quads(self.d, 2 | SCENE_P1, view=v, snap=party_snapshot())
        seat = self.d.ILCT_OvFromPs2(v.seats[1].colour) & 0xFFFFFF
        f = self.slot(1).frame
        solids = [q for q in qs if q.mode == QM_SOLID and (q.c[0] & 0xFFFFFF) == seat]
        block = [q for q in solids if q.y1 <= f.y + 2.0]
        self.assertEqual(len(block), 1, 'the tab\'s numbered block')
        lines = [q for q in solids if q.y0 > f.y + 30.0]
        self.assertEqual(len(lines), 2, 'the focused row bar\'s two edge lines')
        cursor_top = f.y + 40.0 + 2 * 30.0
        self.assertTrue(all(cursor_top - 1 <= q.y0 <= cursor_top + 30 for q in lines), 'on the cursor\'s row')
        gloves = [q for q in qs if q.mode == QM_SDF and (q.c[0] & 0xFFFFFF) == seat]
        self.assertGreaterEqual(len(gloves), 1, 'the glove has a seat-colour layer')
        x0 = min(q.x0 for q in gloves) + GLYPH_PAD
        self.assertLess(x0, f.x, 'it pokes out of the window\'s left edge')
        self.assertGreaterEqual(x0, f.x - 16.0, 'but not into the slot to its left')

    def test_the_opening(self):
        self.assertEqual(self.d.ILCT_OvEase(1000, 1000), 0.0)
        self.assertAlmostEqual(self.d.ILCT_OvEase(1000, 1075), 0.5, places=4)
        self.assertEqual(self.d.ILCT_OvEase(1000, 1150), 1.0)
        self.assertEqual(self.d.ILCT_OvEase(0, 5), 1.0, 'never opened: in place')
        v = view_with({1: thm.seat_view(member=1, opened=NOW)})
        start, _ = quads(self.d, 2, view=v, snap=party_snapshot(), now=NOW)
        mid, _ = quads(self.d, 2, view=v, snap=party_snapshot(), now=NOW + 75)
        end, _ = quads(self.d, 2, view=v, snap=party_snapshot(), now=NOW + 150)
        g0, g1, g2 = (self.windows(qs)[0] for qs in (start, mid, end))
        self.assertAlmostEqual(g0.y0 - g2.y0, 12.0, places=3, msg='starts 12 units low and rises into its slot')
        self.assertAlmostEqual(g1.y0 - g2.y0, 6.0, places=3)
        self.assertEqual((g0.x0, g1.x0), (g2.x0, g2.x0), 'no slide from the party row')
        self.assertEqual(g0.c[0] >> 24, 0, 'and fades in')
        self.assertEqual((g2.x0, g2.y0), (self.slot(1).frame.x, self.slot(1).frame.y))

    def test_the_windows_claim_the_batch_before_the_test_card(self):
        """As the module's frame job: the seat windows are composed first (a full batch cuts the card, never a window,
        P1's least of all), then the card is moved underneath them."""
        v = view_with({s: thm.seat_view(member=s, rows=8, values=True) for s in (0, 1, 2, 3)})
        snap, ci = party_snapshot(), card()
        both, mo = quads(self.d, 3 | SCENE_P1, view=v, snap=snap, ci=ci)
        card_only, _ = quads(self.d, 1, ci=ci)
        windows_only, _ = quads(self.d, 2 | SCENE_P1, view=v, snap=snap)
        key = lambda q: (q.x0, q.y0, q.x1, q.y1, q.mode, q.c[0])
        self.assertEqual(len(both), len(card_only) + len(windows_only))
        self.assertEqual([key(q) for q in both[:len(card_only)]], [key(q) for q in card_only], 'the card underneath')
        self.assertEqual([key(q) for q in both[len(card_only):]], [key(q) for q in windows_only], 'the windows on top')
        out = (Quad * QUAD_CAP)()
        mo = MenusOut()
        prefill = QUAD_CAP - len(windows_only) - 40        # room for the windows, not for the card
        n = self.d.ILCT_OvSceneCardLast(prefill, ctypes.addressof(v), ctypes.addressof(snap), NOW, ctypes.byref(ci), out,
                                        QUAD_CAP, ctypes.byref(mo))
        self.assertEqual((mo.drawn, mo.dropped, n), (4, 0, QUAD_CAP), 'every window kept its room; the card was cut')
        self.assertEqual(len(self.windows(out[:n])), 4)

    def test_a_full_batch_drops_whole_windows(self):
        v = view_with({s: thm.seat_view(member=s, rows=8, values=True) for s in (0, 1, 2, 3)})
        snap = party_snapshot()
        alone, mo = quads(self.d, 2 | SCENE_P1, view=v, snap=snap)
        self.assertEqual((mo.drawn, mo.dropped), (4, 0))
        per_window = len(alone) // 4
        qs, mo = quads(self.d, 3 | SCENE_P1, view=v, snap=snap, ci=card())
        self.assertEqual(mo.dropped, 0, 'the card and four full windows fit the batch')
        self.assertLess(len(qs), QUAD_CAP * 3 // 4, 'with room to spare')
        out = (Quad * QUAD_CAP)()
        mo = MenusOut()
        prefill = QUAD_CAP - per_window - per_window // 2   # room for one window and a half
        n = self.d.ILCT_OvSceneFull(prefill, ctypes.addressof(v), ctypes.addressof(snap), NOW, out, QUAD_CAP,
                                    ctypes.byref(mo))
        self.assertEqual(mo.drawn, 1)
        self.assertEqual(bin(mo.dropped).count('1'), 3, 'three windows left out and reported')
        self.assertEqual(mo.dropped & 1, 0, 'Player 1 first: the one window drawn is P1\'s')
        bodies = self.windows(out[prefill:n])
        self.assertEqual(len(bodies), 1, 'never half a window: the one drawn is whole, the rest rolled back')
        self.assertLessEqual(n, QUAD_CAP)

    def test_state_lists(self):
        saved = (ctypes.c_char_p * 32)()
        bound = (ctypes.c_char_p * 32)()
        nb = u32()
        ns = self.d.ILCT_OvStates(saved, 32, bound, 32, ctypes.byref(nb))
        saved_names = {saved[k] for k in range(ns)}
        bound_names = {bound[k] for k in range(nb.value)}
        self.assertTrue(bound_names <= saved_names, bound_names - saved_names)
        for fmt, ok in ((R8G8B8A8_UNORM, 1), (B8G8R8A8_UNORM, 1), (R8_UNORM, 0), (R32G32B32A32_FLOAT, 0)):
            self.assertEqual(self.d.ILCT_OvFormatOk(fmt), ok, fmt)


class ConfigureTest(PureCase):
    """The Diagnostics test switch (hud_test_draw) shows the card only with Developer options, and never from the native
    defaults record (generation 0)."""

    def status(self) -> OvStatus:
        s = OvStatus()
        self.d.ILCT_OvStatus(ctypes.byref(s))
        return s

    def test_the_card_follows_developer_and_the_switch(self):
        import json
        schema = json.loads((COOP / 'schema' / 'bridge-abi-v1.json').read_text(encoding='utf-8'))
        fields = {f['name']: f for f in schema['structs']['ilc_settings']['fields']}
        base = bytearray.fromhex(schema['defaults']['hex'])
        for generation, developer, switch, want in ((0, 1, 1, 0), (3, 1, 1, 1), (3, 0, 1, 0), (3, 1, 0, 0)):
            rec = bytearray(base)
            struct.pack_into('<I', rec, 12, generation)
            rec[fields['developer']['offset']] = developer
            rec[fields['hud_test_draw']['offset']] = switch
            self.d.ILCT_OvConfigure(bytes(rec))
            with self.subTest(generation=generation, developer=developer, switch=switch):
                self.assertEqual(self.status().test_card, want)


# ---- the seats' fixed slots -----------------------------------------------------------------------------------------------
class SlotTest(PureCase):
    """scene::slot_layout against the rest of the screen, in authoring units. Each slot's footprint runs from its tab's
    top to its window's bottom and from the glove (12 units out of the left edge, plus a 3-unit bob) to its right edge.
    The other corners come from the same DLL (menus_draw's party list and couch zones); Target Info grows by the boss
    gauge's 54 units (hud/layout.h BOSS_SHIFT)."""
    BOSS_SHIFT = 54
    GLOVE_OUT = 12

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        d = cls.d
        d.ILCT_HmPartyList.argtypes, d.ILCT_HmPartyList.restype = [u32, i32, P(thm.Box)], None
        d.ILCT_HmZones.argtypes, d.ILCT_HmZones.restype = [P(thm.Box), P(thm.Box)], None
        d.ILCT_OvMeasure.argtypes, d.ILCT_OvMeasure.restype = SIG['ILCT_OvMeasure']

    def slot(self, seat, sv=None) -> Slot:
        sl = Slot()
        self.d.ILCT_OvSlot(seat, ctypes.addressof(sv if sv is not None else thm.seat_view(member=seat)),
                           ctypes.addressof(sl))
        return sl

    def footprint(self, seat) -> tuple:
        s = self.slot(seat)
        return (s.frame.x - self.GLOVE_OUT, s.tab.y, s.frame.x + s.frame.w, s.frame.y + s.frame.h)

    def test_the_constants(self):
        k = (f32 * 8)()
        self.d.ILCT_OvSlotConstants(k)
        self.assertEqual(list(k), [24.0, 275.0, 16.0, 230.0, 1056.0, 32.0, 30.0, 5.0])

    def test_four_identical_slots_in_seat_order(self):
        slots = [self.slot(s) for s in range(4)]
        self.assertEqual({(s.frame.w, s.frame.h, s.frame.y) for s in slots}, {(275.0, 230.0, 826.0)}, 'uniform')
        self.assertEqual([s.frame.x for s in slots], [24.0, 315.0, 606.0, 897.0], 'P1 leftmost, then P2, P3, P4')
        for a, b in zip(slots, slots[1:]):
            self.assertEqual(b.frame.x - (a.frame.x + a.frame.w), 16.0)
            self.assertGreaterEqual(b.frame.x - self.GLOVE_OUT, a.frame.x + a.frame.w, 'a glove never enters the '
                                    'window to its left')
        sl = Slot()
        self.assertEqual(self.d.ILCT_OvSlot(4, ctypes.addressof(thm.seat_view()), ctypes.addressof(sl)), 0)
        self.assertEqual(sl.frame.w, 0.0)

    def test_the_parts_of_a_window(self):
        for seat in range(4):
            s = self.slot(seat)
            f = rect_box(s.frame)
            with self.subTest(seat=seat):
                self.assertEqual(s.tab.y + s.tab.h, s.frame.y + 2.0, 'the tab tucks 2 units under the window\'s rim')
                self.assertLessEqual(s.tab.x + s.tab.w, s.frame.x + s.frame.w)
                for part in (s.header, s.rows, s.footer):
                    self.assertTrue(inside(rect_box(part), f), part)
                self.assertLessEqual(s.header.y + s.header.h, s.rows.y)
                self.assertLessEqual(s.rows.y + s.rows.h, s.footer.y)
                self.assertEqual(s.rows.h, 5 * 30.0)
                fp = self.footprint(seat)
                self.assertTrue(0 <= fp[0] and 0 <= fp[1] and fp[2] <= 1920 and fp[3] <= 1080, fp)

    def test_clear_of_the_party_list_target_info_and_the_log(self):
        ti, log = thm.Box(), thm.Box()
        self.d.ILCT_HmZones(ctypes.byref(ti), ctypes.byref(log))
        zones = {'Target Info': (ti.x, ti.y, ti.x + ti.w, ti.y + ti.h + self.BOSS_SHIFT),
                 'log': (log.x, log.y, log.x + log.w, log.y + log.h)}
        clearance = {}
        for reduced in (0, 1):
            for rows in (1, 2, 3, 4):
                b = thm.Box()
                self.d.ILCT_HmPartyList(rows, reduced, ctypes.byref(b))
                zones[f'party list reduced={reduced} rows={rows}'] = (b.x, b.y, b.x + b.w, b.y + b.h)
                clearance[reduced] = b.x - (self.slot(3).frame.x + self.slot(3).frame.w)
        for seat in range(4):
            for name, z in zones.items():
                with self.subTest(seat=seat, zone=name):
                    self.assertTrue(disjoint(self.footprint(seat), z), (self.footprint(seat), z))
        self.assertEqual(clearance, {0: 12.0, 1: 209.0}, 'units between P4\'s window and the stock / reduced list')

    def test_the_rows_hold_player_1s_root_page(self):
        s = self.slot(0)
        m = lambda text, em: self.d.ILCT_OvMeasure(FACE_BODY, em, text.encode(), len(text))
        # The default theme's sizes (Bold Modern: rows 21, values 20; the converter's width check: 201 of 213 units).
        self.assertGreater(s.rows.w, m('Magicks & Technicks', 21.0) + 8, 'the longest root entry fits uncut')
        self.assertGreater(s.rows.w, m('Gambits', 21.0) + 10 + m('OFF', 20.0) + 8)
        self.assertGreater(s.footer.w, m('Attack with weapon in hand.', 17.0))
        root = thm.seat_view(member=0, rows=5, cursor=4)
        self.assertEqual((self.slot(0, root).visible, self.slot(0, root).start), (5, 0), 'all five rows, no scroll')

    def test_long_lists_scroll_with_the_cursor_in_view(self):
        for count in (6, 8):
            for cursor in range(count):
                sl = self.slot(1, thm.seat_view(member=1, rows=count, cursor=cursor))
                with self.subTest(count=count, cursor=cursor):
                    self.assertEqual(sl.visible, 5)
                    self.assertTrue(sl.start <= cursor < sl.start + 5, (sl.start, cursor))
                    self.assertLessEqual(sl.start, count - 5)
        self.assertEqual(self.slot(1, thm.seat_view(member=1, rows=1)).visible, 1)
        self.assertEqual(self.slot(1, thm.seat_view(member=1, rows=0)).visible, 1, 'an empty page keeps its window')
        self.assertEqual(self.slot(1, thm.seat_view(member=1, rows=8, cursor=0xFF)).start, 0)


# ---- Player 1's arming: the pure rule (0.1.60 item 7: it follows overlay_live with 250 ms of hysteresis) ------------------
class P1ArmRuleTest(PureCase):
    T = 100000

    def step(self, s: P1Arm, now=None, **kw) -> int:
        now = self.T if now is None else now
        i = P1ArmIn(wanted=1, live=1, layer=1, drawn_ok=1, live_since=now - P1_ARM_MS, live_last=now)
        for k, v in kw.items():
            setattr(i, k, v)
        return self.d.ILCT_FeP1ArmStep(ctypes.addressof(s), ctypes.addressof(i), now)

    def armed(self) -> P1Arm:
        s = P1Arm()
        self.assertEqual(self.step(s), PA_ARMED)
        self.assertEqual((s.armed, s.arms, s.armed_ms), (1, 1, self.T))
        return s

    def test_arms_after_250_ms_live(self):
        self.armed()

    def test_never_before_a_live_streak_or_under_a_stock_frontend(self):
        T = self.T
        cases = {
            'live too short': dict(live_since=T - P1_ARM_MS + 1),
            'not live': dict(live_since=0, live_last=0),
            'live, but not within 100 ms': dict(live_last=T - 101),
            'stock frontend or pause up': dict(native_up=1),
            'refused frame 250 ms ago': dict(failed_ms=T - 250),
            'presenter not live': dict(live=0),
            'layer idle': dict(layer=0),
            'not wanted': dict(wanted=0),
            'no frame really drawn since the last refusal (empty READY frames only)': dict(drawn_ok=0),
        }
        for name, kw in cases.items():
            s = P1Arm()
            with self.subTest(name):
                self.assertEqual(self.step(s, **kw), PA_NONE)
                self.assertEqual(s.armed, 0)
        s = P1Arm()
        self.assertEqual(self.step(s, failed_ms=T - 251), PA_ARMED, 'a refused frame older than 250 ms is history')

    def test_disarms(self):
        T = self.T
        for name, kw in {'presenter not live': dict(live=0), 'layer idle': dict(layer=0), 'not wanted': dict(wanted=0),
                         'refused frame now': dict(failed_ms=T),
                         'overlay_live false for over 250 ms': dict(live_since=0, live_last=T - 251),
                         'never live': dict(live_since=0, live_last=0)}.items():
            s = self.armed()
            with self.subTest(name):
                self.assertEqual(self.step(s, **kw), PA_DISARMED)
                self.assertEqual((s.armed, s.disarms, s.armed_ms), (0, 1, 0))

    def test_a_gap_within_the_hysteresis_keeps_p1_armed(self):
        """0.1.60 bug 2: a native focus panel or Target Info request silenced HD-01 for a moment, two frames went
        unpresented and P1 disarmed, bringing the vanilla HUD back. Now only overlay_live counts, with 250 ms of
        hysteresis: missed frames and a stale run alone change nothing."""
        T = self.T
        s = self.armed()
        for dt in (16, 100, 250):
            self.assertEqual(self.step(s, now=T + dt, live_last=T), PA_NONE, dt)
            self.assertEqual(s.armed, 1)
        self.assertEqual(self.step(s, now=T + 251, live_last=T, live_since=0), PA_DISARMED)
        self.assertEqual(self.step(s, now=T + 300, live_since=T + 300, live_last=T + 300), PA_NONE,
                         're-arms only after 250 ms live again')
        self.assertEqual(self.step(s, now=T + 550, live_since=T + 300, live_last=T + 550), PA_ARMED)

    def test_a_stock_frontend_over_a_presented_hud_keeps_p1_armed_a_race_does_not(self):
        s = self.armed()
        self.assertEqual(self.step(s, native_up=1), PA_NONE, 'a dialog with the HUD still presented')
        self.assertEqual(self.step(s, native_up=1, vanilla_race=1), PA_HELD, 'a race: vanilla wins for seat 0')
        self.assertEqual((s.armed, s.holds, s.hold_until), (0, 0, self.T + P1_RACE_HOLD_MS),
                         'one short fixed hold, never an escalation')
        self.assertEqual(self.step(s, now=self.T + 1, vanilla_race=1), PA_NONE, 'once disarmed it is P1\'s own menu')
        self.assertEqual(s.holds, 0)
        self.assertEqual(self.step(s, now=self.T + P1_RACE_HOLD_MS), PA_ARMED)

    def test_races_never_latch(self):
        s = self.armed()
        now = self.T
        for _ in range(10):
            self.assertEqual(self.step(s, now=now, vanilla_race=1), PA_HELD)
            now += P1_RACE_HOLD_MS
            self.assertEqual(self.step(s, now=now), PA_ARMED)
        self.assertEqual((s.latched, s.holds), (0, 0))

    def test_give_backs_decay_after_ten_minutes_armed(self):
        s = self.armed()
        T = self.T
        self.assertEqual(self.step(s, gave_back=1), PA_HELD)
        now = T + P1_HOLD_MS
        self.assertEqual(self.step(s, now=now), PA_ARMED)
        self.assertEqual(self.step(s, now=now + P1_DECAY_MS - 1000), PA_NONE)
        self.assertEqual(s.holds, 1, 'not yet ten minutes armed')
        self.assertEqual(self.step(s, now=now + P1_DECAY_MS), PA_NONE)
        self.assertEqual((s.holds, s.armed), (0, 1), 'one give-back forgotten after ten minutes armed')
        self.assertEqual(self.step(s, now=now + P1_DECAY_MS + 1000, gave_back=1), PA_HELD)
        self.assertEqual((s.holds, s.hold_until), (1, now + P1_DECAY_MS + 1000 + P1_HOLD_MS), 'the first hold again')

    def test_a_latch_never_decays(self):
        s = self.armed()
        now = self.T
        for _ in range(P1_HOLDS_MAX):
            self.step(s, now=now, gave_back=1)
            now += 20000
            self.step(s, now=now)
        self.assertEqual(s.latched, 1)
        self.assertEqual(self.step(s, now=now + 10 * P1_DECAY_MS), PA_NONE)
        self.assertEqual((s.latched, s.armed), (1, 0))

    def test_a_give_back_holds_p1_on_vanilla(self):
        s = self.armed()
        self.assertEqual(self.step(s, gave_back=1), PA_HELD)
        self.assertEqual((s.armed, s.holds, s.hold_until), (0, 1, self.T + P1_HOLD_MS))
        self.assertEqual(self.step(s, now=self.T + P1_HOLD_MS - 1), PA_NONE, 'held: a full run does not arm')
        self.assertEqual(self.step(s, now=self.T + P1_HOLD_MS), PA_ARMED)

    def test_holds_escalate_and_latch_vanilla(self):
        s = self.armed()
        now = self.T
        for k, hold in enumerate((5000, 10000, 20000), start=1):
            with self.subTest(give_back=k):
                self.assertEqual(self.step(s, now=now, gave_back=1), PA_HELD)
                self.assertEqual((s.armed, s.holds, s.hold_until), (0, k, now + hold))
                self.assertEqual(self.step(s, now=now + hold - 1), PA_NONE)
                now += hold
                self.assertEqual(self.step(s, now=now), PA_ARMED)
        self.assertEqual(self.step(s, now=now, gave_back=1), PA_LATCHED, f'the {P1_HOLDS_MAX}th give-back')
        self.assertEqual((s.armed, s.latched, s.holds), (0, 1, P1_HOLDS_MAX))
        for later in (60000, 3600000):
            self.assertEqual(self.step(s, now=now + later), PA_NONE, 'vanilla for the rest of the launch')
        self.assertEqual(s.armed, 0)


# ---- the GPU side on WARP -------------------------------------------------------------------------------------------------
@native_test
class WarpTest(unittest.TestCase):
    W, H = 1280, 720

    @classmethod
    def setUpClass(cls):
        cls.d = declare(fresh_test_dll())
        cls.renderer = cls.d.ILCT_OvWarpInit(cls.W, cls.H, R8G8B8A8_UNORM)
        if cls.renderer == 0:
            raise unittest.SkipTest('no D3D11 WARP device on this machine')

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_OvWarpFree()

    def setUp(self):
        self.d.ILCT_OvWarpMismatch(0)
        self.d.ILCT_OvWarpClear(0)

    def pixels(self) -> bytes:
        buf = (u8 * (self.W * self.H * 4))()
        self.assertEqual(self.d.ILCT_OvWarpRead(buf, len(buf)), len(buf))
        return bytes(buf)

    def frame(self, kind=1, view=None, snap=None, now=NOW, ci=None):
        info, mo = FrameInfo(), MenusOut()
        r = self.d.ILCT_OvWarpFrame(kind, ctypes.addressof(view) if view is not None else None,
                                    ctypes.addressof(snap) if snap is not None else None, now,
                                    ctypes.byref(ci if ci is not None else card(self.W, self.H)), ctypes.byref(info),
                                    ctypes.byref(mo))
        return r, info, mo

    def alpha_at(self, px: bytes, x0, y0, x1, y1) -> int:
        """Pixels with any alpha written in the box (authoring units mapped to this back buffer)."""
        s = self.W / 1920
        n = 0
        for y in range(int(y0 * s), int(y1 * s)):
            row = y * self.W * 4
            for x in range(int(x0 * s), int(x1 * s)):
                n += px[row + x * 4 + 3] != 0
        return n

    def test_the_test_card_is_drawn_where_it_belongs(self):
        r, info, _ = self.frame()
        self.assertEqual(r, DRAWN)
        self.assertEqual((info.w, info.h, info.format), (self.W, self.H, R8G8B8A8_UNORM))
        self.assertGreater(info.quads, 200)
        px = self.pixels()
        for box in ((36, 36, 376, 128), (1544, 36, 1884, 128), (36, 952, 376, 1044), (1544, 952, 1884, 1044)):
            self.assertGreater(self.alpha_at(px, *box), 5000, box)
        self.assertEqual(self.alpha_at(px, 700, 450, 1200, 700), 0, 'the middle of the screen stays the game\'s')
        if PREVIEW:
            write_png(Path(PREVIEW) / 'overlay-test-card-1280x720.png', self.W, self.H, px)

    def test_seat_windows_are_drawn_in_their_slots(self):
        v = view_with({0: thm.seat_view(member=0, toggle=True, title='Vaan', help_text='Attack with weapon in hand.'),
                       1: thm.seat_view(member=1, values=True, toggle=True, title='P2 - Magicks',
                                        help_text='Deals fire damage.'),
                       3: thm.seat_view(kind=thm.WK_TARGET, member=3, rows=3, title='P4 - Attack')})
        r, info, mo = self.frame(kind=2 | SCENE_P1, view=v, snap=party_snapshot())
        self.assertEqual((r, mo.drawn, mo.dropped), (DRAWN, 3, 0))
        px = self.pixels()
        for x0 in (24, 315, 897):   # slots 1, 2 and 4
            self.assertGreater(self.alpha_at(px, x0, 826, x0 + 275, 1056), 15000, x0)
        self.assertEqual(self.alpha_at(px, 606 + 20, 800, 881, 1056), 0, 'P3\'s menu is closed: its slot stays empty')
        self.assertEqual(self.alpha_at(px, 0, 0, 600, 500), 0, 'nothing in Target Info\'s corner')
        self.assertEqual(self.alpha_at(px, 1184, 700, 1920, 1080), 0, 'nothing over the party list')
        if PREVIEW:
            write_png(Path(PREVIEW) / 'overlay-seat-windows-1280x720.png', self.W, self.H, px)

    def test_chamfered_window_with_full_sheen_keeps_its_cut_corners(self):
        """Review ★2 on the GPU: a chamfer window (mode 3) with the diagonal sheen at 31 (mode 3 + 0.97..) once fell
        into the shader's `m.x >= 3.5` HATCH branch and drew as a hatched rectangle, corners filled. Selected by floor,
        its top-left and bottom-right cuts stay empty at every sheen; a rounded-off (radius 0) GLASS window is the
        control: its corners are filled."""
        x, y, w, h, cut = 600.0, 300.0, 420.0, 300.0, 72.0
        corners = ((x + 4, y + 4, x + 24, y + 24), (x + w - 24, y + h - 24, x + w - 4, y + h - 4))
        centre = (x + w / 2 - 30, y + h / 2 - 30, x + w / 2 + 30, y + h / 2 + 30)
        for chamfer, shine in ((1, 0.0), (1, 0.484), (1, 0.6), (1, 1.0), (0, 1.0)):
            with self.subTest(chamfer=chamfer, shine=shine):
                self.d.ILCT_OvWarpClear(0)
                info = FrameInfo()
                r = self.d.ILCT_OvWarpGlass(chamfer, shine, 1.0, x, y, w, h, cut, 0xF0402010, ctypes.byref(info))
                self.assertEqual((r, info.quads), (DRAWN, 1))
                px = self.pixels()
                self.assertGreater(self.alpha_at(px, *centre), 1000, 'the window body is drawn')
                for box in corners:
                    if chamfer:
                        self.assertEqual(self.alpha_at(px, *box), 0, f'cut corner {box} stays empty')
                    else:
                        self.assertGreater(self.alpha_at(px, *box), 100, f'control: square corner {box} filled')

    def test_every_pipeline_state_is_put_back(self):
        result = i32()
        bad = self.d.ILCT_OvWarpRoundTrip(1, ctypes.byref(card(self.W, self.H)), ctypes.byref(result))
        self.assertEqual(result.value, DRAWN)
        self.assertEqual(bad, 0, f'slots not restored: {bad:#x}')

    def test_the_fault_drill_restores_the_state(self):
        faulted = i32()
        with quiet_faulthandler():
            bad = self.d.ILCT_OvWarpFaultDrill(ctypes.byref(card(self.W, self.H)), ctypes.byref(faulted))
        self.assertEqual(faulted.value, 1, 'guard::FAULTED')
        self.assertEqual(bad & 0x40000000, 0, 'the fault left our state bound (the drill is real)')
        self.assertEqual(bad & ~0x40000000, 0, f'slots not restored after the fault: {bad:#x}')
        r, _, _ = self.frame()
        self.assertEqual(r, DRAWN, 'the GPU side still draws afterwards (the module latches RENDER itself)')

    def test_a_swap_chain_of_another_device_draws_nothing(self):
        if self.d.ILCT_OvWarpMismatch(1) == 0:
            self.skipTest('no second WARP device')
        r, _, _ = self.frame()
        self.assertEqual(r, MISMATCH)
        self.assertEqual(self.alpha_at(self.pixels(), 0, 0, 1920, 1080), 0)
        self.d.ILCT_OvWarpMismatch(0)
        self.assertEqual(self.frame()[0], DRAWN)

    def test_a_null_renderer_field_draws_nothing(self):
        saved = struct.unpack('<Q', ctypes.string_at(self.renderer + 0x118, 8))[0]
        try:
            self.d.ILCT_OvWarpRendererField(0x118, 0)
            self.assertEqual(self.frame()[0], NO_RENDERER)
        finally:
            self.d.ILCT_OvWarpRendererField(0x118, saved)

    def test_an_empty_batch_never_touches_the_back_buffer(self):
        before = self.d.ILCT_OvWarpGetBufferCalls()
        r, _, _ = self.frame(kind=2, view=view_with({}), snap=party_snapshot())
        self.assertEqual(r, READY)
        self.assertEqual(self.d.ILCT_OvWarpGetBufferCalls(), before)


@native_test
class WarpBackBufferTest(unittest.TestCase):
    def test_small_or_odd_back_buffers_draw_nothing(self):
        d = declare(fresh_test_dll())
        try:
            for (w, h, fmt), want in {(639, 480, R8G8B8A8_UNORM): BAD_BACK_BUFFER,
                                      (1280, 359, R8G8B8A8_UNORM): BAD_BACK_BUFFER,
                                      (1280, 720, R32G32B32A32_FLOAT): BAD_BACK_BUFFER,
                                      (1280, 720, B8G8R8A8_UNORM): DRAWN}.items():
                if d.ILCT_OvWarpInit(w, h, fmt) == 0:
                    self.skipTest('no D3D11 WARP device on this machine')
                info, mo = FrameInfo(), MenusOut()
                r = d.ILCT_OvWarpFrame(1, None, None, NOW, ctypes.byref(card(w, h)), ctypes.byref(info), ctypes.byref(mo))
                with self.subTest(w=w, h=h, fmt=fmt):
                    self.assertEqual(r, want)
        finally:
            d.ILCT_OvWarpFree()


# ---- OV-01 on the twin --------------------------------------------------------------------------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class HookTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(thd.declare(cls.hw.d))
        tfe.declare(cls.d)
        cls.renderer = cls.d.ILCT_OvWarpInit(1280, 720, R8G8B8A8_UNORM)
        if cls.renderer == 0:
            raise unittest.SkipTest('no D3D11 WARP device on this machine')
        cls.row = next(i for i, sid in enumerate(re.findall(r'X\("([^"]+)"', (COOP / 'src' / 'patch' / 'sites.inc')
                                                             .read_text(encoding='utf-8'))) if sid == 'OV-01')

    @classmethod
    def tearDownClass(cls):
        cls.d.ILCT_OvWarpFree()

    def setUp(self):
        d = self.d
        self.hw.setup()
        d.ILCT_CtlReset()
        d.ILCT_FePresenterReset()
        d.ILCT_FePresenter(-1)
        d.ILCT_OvReset()
        d.ILCT_FeSetP1Native(0)   # P1's menu ours unless a test says otherwise
        self.assertEqual(d.ILCT_OvAttach(th.TWIN_BASE), 1)
        d.ILCT_OvSetHeartbeat(NOW, 0x80)
        self.publish()

    def tearDown(self):
        d = self.d
        d.ILCT_OvInjectFault(0)
        d.ILCT_OvReset()
        d.ILCT_OvLogCapture(0)
        d.ILCT_FePresenterReset()
        d.ILCT_CtlReset()
        thd.withdraw_menu_view(d)
        self.hw.twin.reset()

    def publish(self, seats=None, epoch=EPOCH, built=NOW, snap_epoch=None, sampled=NOW):
        d = self.d
        d.ILCT_HudSnapPublish(ctypes.byref(party_snapshot(epoch=snap_epoch if snap_epoch is not None else epoch,
                                                          sampled=sampled)))
        d.ILCT_HmPublish.argtypes, d.ILCT_HmPublish.restype = [P(thm.MenuView)], None
        d.ILCT_HmPublish(ctypes.byref(view_with(seats or {}, epoch=epoch, built=built)))

    def call(self, epoch=EPOCH) -> int:
        c = th.HookCtx()
        c.rcx = self.renderer
        c.index = self.row
        with quiet_faulthandler():
            return self.d.ILCT_OvCall(ctypes.byref(c), epoch)

    def stats(self) -> Counters:
        c = Counters()
        self.d.ILCT_OvStats(ctypes.byref(c))
        return c

    def drawn_ms(self) -> int:
        return self.d.ILCT_FePresenterDrawn()

    def test_an_empty_layer_is_stamped_without_drawing(self):
        self.assertEqual(self.call(), RUN_ORIGINAL)
        c = self.stats()
        self.assertEqual((c.ready, c.drawn, c.stamps), (1, 0, 1))
        self.assertEqual(self.drawn_ms(), NOW)
        self.assertEqual(self.d.ILCT_OvLastDropped(), 0)

    def test_guest_windows_draw_and_stamp_player_1_left_out_while_native(self):
        self.d.ILCT_FeSetP1Native(1)
        try:
            self.publish({0: thm.seat_view(member=0), 1: thm.seat_view(member=1)})
            self.call()
        finally:
            self.d.ILCT_FeSetP1Native(0)
        c = self.stats()
        self.assertEqual((c.drawn, c.stamps, c.windows, c.dropped_frames), (1, 1, 1, 0))
        self.assertEqual(self.d.ILCT_OvLastDropped(), 0b1, 'Player 1\'s window is the game\'s: reported left out')
        self.assertEqual(self.d.ILCT_FeSeatShown(0), 0)
        self.assertEqual(self.d.ILCT_FeSeatShown(1), 1)

    def test_player_1s_window_is_drawn_when_p1_is_not_native(self):
        self.assertEqual(self.d.ILCT_FeP1Native(), 0)
        self.publish({0: thm.seat_view(member=0), 1: thm.seat_view(member=1)})
        self.call()
        c = self.stats()
        self.assertEqual((c.drawn, c.stamps, c.windows), (1, 1, 2))
        self.assertEqual(self.d.ILCT_OvLastDropped(), 0, 'seat 0 drawn by us')
        self.assertEqual((self.d.ILCT_FeSeatShown(0), self.d.ILCT_FeSeatShown(1)), (1, 1))

    def run_view(self) -> RunView:
        r = RunView()
        self.d.ILCT_FePresenterRunView(ctypes.addressof(r))
        return r

    def test_stamps_extend_the_run_and_every_other_frame_breaks_it(self):
        d = self.d
        for k in range(3):
            self.call()
        r = self.run_view()
        self.assertEqual((r.frames, r.missed, r.since_ms, r.last_ms, r.failed_ms), (3, 0, NOW, NOW, 0))
        d.ILCT_OvSetHeartbeat(0, 0)                          # the HUD hidden
        self.call()
        self.call()
        r = self.run_view()
        self.assertEqual((r.frames, r.missed), (0, 2), 'hidden frames break the run and count as missed')
        d.ILCT_OvSetHeartbeat(NOW, 0x80)
        self.call()
        self.assertEqual((self.run_view().frames, self.run_view().missed), (1, 0))
        thd.withdraw_menu_view(d)                            # nothing presentable
        self.call()
        self.assertEqual((self.run_view().frames, self.run_view().missed), (0, 1), 'an idle frame breaks it too')

    def test_a_refused_frame_is_reported(self):
        d = self.d
        self.call()
        field = ctypes.c_uint64.from_address(self.renderer + 0x118)
        swap = field.value
        field.value = 0                                      # the swap chain gone
        try:
            self.call()
        finally:
            field.value = swap
        r = self.run_view()
        self.assertEqual((r.frames, r.failed_ms), (0, NOW), 'refused while the menus were presentable')
        self.assertEqual(self.stats().stamps, 1, 'and not stamped')
        self.publish({1: thm.seat_view(member=1)})           # something to draw: the drill faults inside the bind
        self.call()
        self.assertEqual(self.run_view().frames, 1)
        d.ILCT_OvInjectFault(1)
        self.call()
        self.assertEqual((self.drawn_ms(), self.run_view().frames), (0, 0), 'a fault withdraws the stamp and the run')

    def test_nothing_without_a_fresh_hud_heartbeat(self):
        self.d.ILCT_OvSetTestCard(1)
        for ms in (0, NOW - 101):
            self.d.ILCT_OvSetHeartbeat(ms, 0x80)
            self.call()
        c = self.stats()
        self.assertEqual((c.hidden, c.drawn, c.ready, c.stamps), (2, 0, 0, 0))
        self.assertEqual(self.drawn_ms(), 0)
        self.d.ILCT_OvSetHeartbeat(NOW - 100, 0x80)
        self.call()
        self.assertEqual(self.stats().drawn, 1, '100 ms old still counts')

    def test_every_reason_not_to_stamp(self):
        d = self.d
        cases = {
            'menu view of another epoch': lambda: self.publish(epoch=EPOCH + 1, snap_epoch=EPOCH),
            'stale menu view': lambda: self.publish(built=NOW - 251),
            'menu view withdrawn': lambda: thd.withdraw_menu_view(d),
            'snapshot of another epoch': lambda: self.publish(snap_epoch=EPOCH + 1),
            'stale snapshot': lambda: self.publish(sampled=NOW - 251),
            'co-op off': lambda: (d.ILCT_CtlKill(1), d.ILCT_CtlStep(NOW)),
            'FIREWALL_MENUS disarmed': lambda: d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS),
            'HUD hidden': lambda: d.ILCT_OvSetHeartbeat(0, 0),
        }
        for name, prepare in cases.items():
            with self.subTest(name):
                self.setUp()
                prepare()
                self.call()
                c = self.stats()
                self.assertEqual((c.stamps, self.drawn_ms()), (0, 0), name)
                self.tearDown()

    def test_the_test_card_needs_its_switch(self):
        d = self.d
        d.ILCT_PatchApplySubsystems(0xFF & ~FIREWALL_MENUS)   # no guests' menus: only the card could draw
        for name in ('ILCT_TiReset', 'ILCT_BlReset', 'ILCT_RowsReset', 'ILCT_FeedReset', 'ILCT_StReset'):
            getattr(d, name).argtypes, getattr(d, name).restype = [], None
            getattr(d, name)()                                  # no HUD element has anything to show either
        self.call()
        c = self.stats()
        # The HUD elements may still be composed (a fresh snapshot) and find nothing: an empty READY frame, never drawn.
        self.assertEqual((c.idle + c.ready, c.drawn), (1, 0))
        d.ILCT_OvSetTestCard(1)
        self.call()
        c = self.stats()
        self.assertEqual(c.drawn, 1)
        self.assertGreater(c.quads_last, 200)
        self.assertEqual(c.stamps, 0, 'the card alone presents no menus')

    def test_a_fault_latches_render_and_withdraws_the_stamp(self):
        d = self.d
        d.ILCT_OvLogCapture(1)
        self.publish({1: thm.seat_view(member=1)})
        self.call()
        self.assertEqual(self.drawn_ms(), NOW)
        d.ILCT_OvInjectFault(1)
        self.call()
        c = self.stats()
        self.assertEqual(c.faults, 1)
        self.assertTrue(c.events & EV_FAULT)
        self.assertEqual(self.drawn_ms(), 0, 'the stamp is withdrawn at once')
        self.assertEqual(d.ILCT_OvRenderLive(), 0, 'RENDER latched faulted')
        st = OvStatus()
        d.ILCT_OvStatus(ctypes.byref(st))
        self.assertEqual(st.dead, 1)
        before = self.stats().declined
        d.ILCT_OvInjectFault(0)
        self.call()
        self.assertEqual(self.stats().declined, before + 1, 'dead: stock from then on')
        d.ILCT_OvStep(NOW)
        log = d.ILCT_OvLogText().decode()
        self.assertEqual(log.count('overlay: fault while drawing'), 1, log)
        d.ILCT_OvStep(NOW + 20)
        self.assertEqual(d.ILCT_OvLogText().decode().count('overlay: fault while drawing'), 1, 'logged once')

    def test_the_first_frame_is_logged_once(self):
        d = self.d
        d.ILCT_OvLogCapture(1)
        d.ILCT_OvSetTestCard(1)
        self.call()
        self.call()
        d.ILCT_OvStep(NOW)
        log = d.ILCT_OvLogText().decode()
        self.assertEqual(log.count('overlay: first frame drawn, back buffer 1280x720 R8G8B8A8_UNORM'), 1, log)

    # ---- Target Info up, combat log faded (HD-01 silent): the UI tick keeps the HUD shown ----
    UI_MANAGER, PAUSE_A, PAUSE_B, COMMAND_ACTOR, COMMAND_FLAG = 0x209BE80, 0x209AD28, 0x209AD30, 0x2092730, 0x2092734
    HUD, TI_OWNER_WINDOW = 0x8FA0, 0x8FA0 + 0xAC0 + 0x570

    def target_info_up(self, ti=True, hud_flags=0x82F, frontend=0, pause=0, hud_alpha=0x80, ti_flags=0):
        """The UI manager with the HUD window (handler 0x2be490, drawn) and, with `ti`, a TARGET INFO window in its
        owner (handler 0x2c0870); the UI ready and drawn; the TI-V pins forced ok. The twin sits at the pinned base, so
        a VA is its own address."""
        a = ctypes.addressof
        self.mgr = ctypes.create_string_buffer(0xA200)
        self.ti = ctypes.create_string_buffer(0x300)
        struct.pack_into('<Q', self.mgr, self.HUD, 0x2BE490)
        struct.pack_into('<I', self.mgr, self.HUD + 0x40, hud_flags)
        struct.pack_into('<B', self.mgr, self.HUD + 0xA0, hud_alpha)      # the HUD window's own alpha
        struct.pack_into('<H', self.ti, 0x280, ti_flags)                  # TARGET INFO closing / invalid bits
        struct.pack_into('<Q', self.mgr, self.TI_OWNER_WINDOW, a(self.ti) if ti else 0)
        struct.pack_into('<Q', self.ti, 0, 0x2C0870)
        tw = self.hw.twin
        tw.poke(self.UI_MANAGER, struct.pack('<Q', a(self.mgr)))
        tw.poke(th.UI_READY, struct.pack('<I', 1))
        tw.poke(thd.RENDER_FLAGS, struct.pack('<I', thd.RENDER_UI))
        tw.poke(self.PAUSE_A, struct.pack('<Q', pause))
        tw.poke(self.PAUSE_B, struct.pack('<Q', 0))
        tw.poke(self.COMMAND_ACTOR, struct.pack('<I', frontend))
        tw.poke(self.COMMAND_FLAG, struct.pack('<I', 1 if frontend else 0))
        self.d.ILCT_TiGbForcePins(1)

    def test_heartbeat_v2_keeps_the_hud_shown_without_hd01(self):
        """0.1.60 item 7: the UI tick is the primary heartbeat (the HUD window drawn, alpha > 0, the field gate open, no
        pause). It needs neither a TARGET INFO window nor HD-01 ever having run (the blocker hides the first log window,
        which silences HD-01), and the frames keep stamping with an advancing clock."""
        d = self.d
        d.ILCT_SetFixedClock.argtypes = [u64]
        try:
            d.ILCT_OvSetHeartbeat(0, 0)              # HD-01 never ran this launch
            self.target_info_up(ti=False, hud_alpha=0x70)
            frames = P1_ARM_FRAMES + 2
            for k in range(frames):
                t = NOW + 16 * k
                d.ILCT_SetFixedClock(t)
                self.publish({0: thm.seat_view(member=0)}, built=t, sampled=t)
                d.ILCT_OvUiTick()
                self.call()
            c = self.stats()
            self.assertEqual((c.hidden, c.stamps, c.tick_heartbeats, c.hud_alpha, c.heartbeats),
                             (0, frames, frames, 0x70, 0), "the HUD window's alpha; no HD-01")
            last = NOW + 16 * (frames - 1)
            self.assertEqual(d.ILCT_OvLive(last), 1, 'overlay_live: co-op on, RENDER live, presented, field open')
        finally:
            d.ILCT_SetFixedClock(NOW)

    def test_no_tick_heartbeat_when_the_hud_is_not_shown(self):
        d = self.d
        cases = {
            'the HUD window faded out (alpha 0)': dict(hud_alpha=0),
            'the HUD window not drawn': dict(hud_flags=0x82E),
            'the main menu or another frontend': dict(frontend=0x2),
            'the pause screen': dict(pause=0x1234),
        }
        for name, kw in cases.items():
            with self.subTest(name):
                self.setUp()
                d.ILCT_OvSetHeartbeat(NOW - 300, 0x80)
                self.target_info_up(**kw)
                d.ILCT_OvUiTick()
                self.call()
                c = self.stats()
                self.assertEqual((c.tick_heartbeats, c.hidden, c.stamps), (0, 1, 0), name)
                self.tearDown()
        self.setUp()
        d.ILCT_OvSetHeartbeat(NOW - 300, 0x80)
        self.target_info_up(frontend=0x80000001)               # P1's own battle menu (id 1, masked 0x7FFF)
        d.ILCT_OvUiTick()
        self.assertEqual(self.stats().tick_heartbeats, 1, 'P1\'s own battle menu keeps the HUD shown')

    def test_a_menu_hides_the_overlay_the_frame_it_opens(self):
        """Item 1: the game's HUD keeps drawing while it fades under the Y menu, and HD-01 kept the heartbeat fresh, so
        the overlay drew over the menu for about a second. The frame hook reads the field gate itself, and HD-01 no
        longer counts while a menu or the pause screen is up."""
        d = self.d
        self.call()
        self.assertEqual(self.stats().stamps, 1, 'control: drawn with the field open')
        self.assertEqual(d.ILCT_OvFieldOpen(), 1)
        tw = self.hw.twin
        for name, (va, value) in {'the Y menu (frontend 0xD)': (self.COMMAND_FLAG, 1),
                                  'the pause screen': (self.PAUSE_A, 0x1234)}.items():
            with self.subTest(name):
                if va == self.COMMAND_FLAG:
                    tw.poke(self.COMMAND_ACTOR, struct.pack('<I', 0xD))
                    tw.poke(va, struct.pack('<I', value))
                else:
                    tw.poke(va, struct.pack('<Q', value))
                try:
                    before = self.stats()
                    self.call()
                    after = self.stats()
                    self.assertEqual((after.hidden - before.hidden, after.stamps - before.stamps), (1, 0), name)
                    self.assertEqual(d.ILCT_OvFieldOpen(), 0)
                    self.assertEqual(d.ILCT_OvLive(NOW), 0, 'overlay_live off: the vanilla HUD pieces come back')
                finally:
                    tw.poke(self.COMMAND_ACTOR, struct.pack('<I', 0))
                    tw.poke(self.COMMAND_FLAG, struct.pack('<I', 0))
                    tw.poke(self.PAUSE_A, struct.pack('<Q', 0))

    def test_overlay_live_and_its_terms(self):
        d = self.d
        self.assertEqual(d.ILCT_OvLive(NOW), 0, 'nothing presented yet')
        self.call()
        self.assertEqual(d.ILCT_OvLive(NOW), 1)
        self.assertEqual(d.ILCT_OvLive(NOW + LIVE_MS), 1, 'presented 250 ms ago')
        self.assertEqual(d.ILCT_OvLive(NOW + LIVE_MS + 1), 0, 'presented over 250 ms ago')
        self.assertEqual(d.ILCT_OvLogReplaces(NOW), d.ILCT_OvLive(NOW), 'HD-01 skips the stock text whenever live')
        d.ILCT_PatchApplySubsystems(0xFF & ~RENDER)             # RENDER disarmed
        try:
            self.assertEqual(d.ILCT_OvLive(NOW), 0, 'RENDER not live')
        finally:
            d.ILCT_PatchApplySubsystems(0xFF)
        d.ILCT_CtlKill(1)
        d.ILCT_CtlStep(NOW)
        self.assertEqual(d.ILCT_OvLive(NOW), 0, 'co-op off')

    def test_the_heartbeat_from_hd01(self):
        d = self.d
        d.ILCT_OvSetHeartbeat(0, 0)
        mgr = ctypes.create_string_buffer(0x100)
        root = ctypes.create_string_buffer(0x100)
        ctx = (u8 * 0xA0)()
        root_ctx = (u8 * 0xA0)()
        a = ctypes.addressof
        struct.pack_into('<Q', mgr, 0x10, a(root))          # the log manager's parent: the HUD root
        struct.pack_into('<Q', root, 0x38, a(root_ctx))     # the root's context
        struct.pack_into('<h', root_ctx, 0x58, 0x40)        # the HUD half faded
        self.hw.twin.poke(th.UI_READY, struct.pack('<I', 1))
        self.hw.twin.poke(thd.RENDER_FLAGS, struct.pack('<I', thd.RENDER_UI))
        d.ILCT_OvNoteHud(a(mgr), a(ctx))
        c = self.stats()
        self.assertEqual((c.heartbeats, c.hud_ms, c.hud_alpha), (1, NOW, 0x40), 'the root\'s alpha, not the log window\'s')
        for name, poke in (('UI hidden', lambda: self.hw.twin.poke(thd.RENDER_FLAGS, struct.pack('<I', 0))),
                           ('UI not ready', lambda: self.hw.twin.poke(th.UI_READY, struct.pack('<I', 0))),
                           ('faded out', lambda: struct.pack_into('<h', root_ctx, 0x58, 0))):
            with self.subTest(name):
                self.hw.twin.poke(th.UI_READY, struct.pack('<I', 1))
                self.hw.twin.poke(thd.RENDER_FLAGS, struct.pack('<I', thd.RENDER_UI))
                struct.pack_into('<h', root_ctx, 0x58, 0x40)
                poke()
                before = self.stats().heartbeats
                d.ILCT_OvNoteHud(a(mgr), a(ctx))
                self.assertEqual(self.stats().heartbeats, before, name)


# ---- the guests' prompt (IX-21): the gate must open once attach armed it (bug 3, 0.1.57) -------------------------------
@native_test
@unittest.skipUnless(GAME, 'requires IVALICE_LEGACY_GAME (the image twin)')
class PromptGateTest(unittest.TestCase):
    """0.1.57 drew no guest "!": IX-21's island stayed on the callee (gate closed) in the live game. The pins are now
    two groups: the marker's decide whether IX-21 arms, the label's (the button glyph setter another mod may rewrite)
    only switch the label off. On the installed twin the gate must actually open."""

    LABEL_PIN, MARKER_PIN = 0x2C67C1, 0x2C7F90

    @classmethod
    def setUpClass(cls):
        cls.hw = th.world()
        cls.d = declare(cls.hw.d)

    def setUp(self):
        self.hw.setup(attach=False)

    def tearDown(self):
        self.hw.twin.reset()

    def status(self) -> PromptStatus:
        s = PromptStatus()
        self.d.ILCT_IxPromptStatus(ctypes.byref(s))
        return s

    def test_attach_opens_the_gate(self):
        self.assertEqual(self.d.ILCT_IxPromptAttach(th.TWIN_BASE), 1)
        s = self.status()
        self.assertEqual((s.attached, s.marker_pins, s.label_pins, s.gate_open, s.first_mismatch), (1, 1, 1, 1, 0))

    def test_a_rewritten_label_setter_costs_only_the_label(self):
        self.hw.twin.poke(self.LABEL_PIN, b'\x90')
        self.assertEqual(self.d.ILCT_IxPromptAttach(th.TWIN_BASE), 1, 'the marker still arms')
        s = self.status()
        self.assertEqual((s.attached, s.label_pins, s.gate_open, s.first_mismatch), (1, 0, 1, self.LABEL_PIN))

    def test_a_rewritten_drawer_leaves_ix21_stock(self):
        self.hw.twin.poke(self.MARKER_PIN, b'\x90')
        self.assertEqual(self.d.ILCT_IxPromptAttach(th.TWIN_BASE), 0)
        s = self.status()
        self.assertEqual((s.attached, s.marker_pins, s.gate_open, s.first_mismatch), (0, 0, 0, self.MARKER_PIN))


# ---- Player 1 on the game's own frontends ---------------------------------------------------------------------------------
class P1NativeTest(tfe.LayerCase):
    def setUp(self):
        super().setUp()
        declare(self.d)
        self.d.ILCT_FeSetP1Native(1)
        self.beat()

    def tearDown(self):
        self.d.ILCT_FeSetP1Native(0)
        super().tearDown()

    def test_the_host_test_default_is_off_and_reset_restores_it(self):
        self.assertEqual(self.d.ILCT_FeP1Native(), 1)
        self.d.ILCT_FeReset()
        self.assertEqual(self.d.ILCT_FeP1Native(), 0)
        self.d.ILCT_FeSetP1Native(1)

    def test_p1s_buttons_open_nothing_and_the_gate_never_arms(self):
        st = self.state()
        self.assertEqual((st.report.p1_menus, st.report.leader_gate), (0, 0))
        self.assertFalse(self.fw().head.flags & tf_gate(), 'P1\'s stock battle menu is never refused')
        for action in (tfe.A_CONFIRM, tfe.A_UP, tfe.A_DOWN, tfe.A_BATTLE):
            self.press(1, action)
            v = self.seat(0)
            self.assertEqual((v.mode, v.selector_kind), (tfe.CLOSED, tfe.SEL_NONE), action)

    def test_a_guest_menu_leaves_p1s_directions_alone(self):
        self.press(2, tfe.A_BATTLE)
        self.assertEqual(self.seat(1).mode, tfe.LIST)
        import test_coop_firewall as tf
        fw = self.fw()
        self.assertEqual(fw.p1_mask & (tf.DIRS | tf.M(tf.RB, tf.PARTY)), 0, 'P1 keeps its directions, RB and PARTY')
        self.assertNotEqual(fw.other_mask & tf.DIRS, 0, 'the guests\' directions belong to the open menu')

    def test_p1s_own_battle_menu_does_not_suspend_the_guests(self):
        self.press(2, tfe.A_BATTLE)
        self.d.ILCT_FeSetScreen(0x8)                      # SCREEN_P1_BATTLE
        self.publish(facts=tfe.FACT['CONTROL'] | tfe.FACT['MENU'])
        for _ in range(5):
            self.beat()
        st = self.state()
        self.assertEqual((st.susp.suspended, self.seat(1).mode, st.report.guests_open), (0, tfe.LIST, 0b10))
        self.d.ILCT_FeSetScreen(0)                         # a shop or the map: the same fact, another frontend
        for _ in range(5):
            self.beat()
        self.assertEqual(self.state().susp.suspended, 1, 'any other native screen still suspends')

    def test_guest_edges_never_drive_p1s_battle_menu(self):
        import test_coop_firewall as tf
        self.press(2, tfe.A_BATTLE)
        self.d.ILCT_FeSetScreen(0x8)                      # P1's own battle menu is up
        self.publish(facts=tfe.FACT['CONTROL'] | tfe.FACT['MENU'])
        for _ in range(3):
            self.beat()
        self.assertEqual(self.state().report.reserved[0], 1, 'menu_report.p1_battle')
        self.press(2, tfe.A_DOWN)
        self.assertEqual(self.fw().pulse, 0, 'no guest pulse on P1\'s battle menu')
        self.assertEqual(self.seat(1).cursor, 1, 'the guest\'s own window took the press')
        self.press(2, tfe.A_CONFIRM)
        self.assertEqual(self.fw().pulse, 0)
        self.d.ILCT_FeSetScreen(0)                         # a shop or the map: the guests may drive it
        for _ in range(4):
            self.beat()
        self.press(2, tfe.A_CANCEL)
        self.assertEqual(self.fw().pulse, tf.CANCEL, 'other native screens keep the guest pulses')

    def test_one_switch_owned_by_the_session(self):
        self.assertEqual((self.d.ILCT_FeP1Native(), self.d.ILCT_SesP1Native()), (1, 1))
        self.d.ILCT_FeSetP1Native(0)
        self.assertEqual(self.d.ILCT_SesP1Native(), 0, 'frontend::set_p1_native forwards to the session')
        self.d.ILCT_FeSetP1Native(1)

    def test_a_confirm_that_found_nothing_opens_the_guests_menu(self):
        """Bug 1 (0.1.58): interaction's signal (a guest CONFIRM with nothing to use) opens that guest's menu on the
        next beat, through the usual gates; it never closes an open menu, never opens P1's, and never arrives late."""
        self.d.ILCT_IxNoteMenuOpen(tfe.EPOCH, 1 << 1)
        self.beat()
        self.assertEqual(self.seat(1).mode, tfe.LIST, 'P2 opened its menu with A')
        self.d.ILCT_IxNoteMenuOpen(tfe.EPOCH, 1 << 1)
        self.beat()
        self.assertEqual(self.seat(1).mode, tfe.LIST, 'an open menu is never closed by it')
        self.d.ILCT_IxNoteMenuOpen(tfe.EPOCH, 1 << 0)
        self.beat()
        self.assertEqual(self.seat(0).mode, tfe.CLOSED, "P1's A is the game's")
        self.d.ILCT_IxNoteMenuOpen(tfe.EPOCH + 1, 1 << 2)
        self.beat()
        self.assertEqual(self.seat(2).mode, tfe.CLOSED, 'another epoch: nothing')
        self.d.ILCT_IxNoteMenuOpen(tfe.EPOCH, 1 << 2)
        self.d.ILCT_FePresenter(0)                         # the layer idles this beat: the signal is taken, unused
        self.beat()
        self.d.ILCT_FePresenter(1)
        self.beat()
        self.assertEqual(self.seat(2).mode, tfe.CLOSED, 'never late: taken every beat')

    def test_wait_counts_only_the_guests(self):
        self.flags.pop(2)
        self.publish()
        self.beat()
        self.assertFalse(self.wait_requested())
        self.press(2, tfe.A_BATTLE)
        self.assertTrue(self.wait_requested(), 'the only guest has its menu open (P1 not counted)')
        self.press(2, tfe.A_BATTLE)
        self.assertFalse(self.wait_requested())

    def test_a_fallen_p1_gets_no_co_op_ko_selector(self):
        self.flags[0] = tfe.SEAT_FULL & ~tfe.SEATF['ALIVE']
        self.publish()
        self.picker(0, party=self.party_of([(0, 0, tfe.ST_KO), (1, 100, 0), (2, 100, 0), (3, 100, 0)]))
        self.beat()
        st = self.state()
        self.assertEqual((self.seat(0).selector_kind, st.ko_live, st.report.leader_pending, st.report.p1_menus),
                         (tfe.SEL_NONE, 0, 0, 0), 'the game\'s own KO path runs')


class P1ArmLayerTest(tfe.LayerCase):
    """Player 1's menu on the overlay through the frontend layer: the shipped arming rule on and the presenter driven
    by real stamps only (simulate mode -1). While `self.presenting` is 'on', the overlay presents a frame every 16 ms
    up to each beat, as OV-01 does: note_dropped, note_drawn and note_run with the frame's time. 'hidden' reaches the
    frames without presenting them (a screen hides the HUD: break_run per frame); 'off' reaches none (the hook not
    called, the swap chain gone)."""

    PRESENTER = -1
    FRAME_MS = 16

    def setUp(self):
        self.presenting = 'off'
        self.next_frame = 0
        self.dropped = 0
        self.ticks = 0
        self.hitch = False
        super().setUp()
        declare(self.d)
        self.d.ILCT_FeSetP1Arming(1)
        self.d.ILCT_FeSetOverlayLive(2)   # overlay_live while a presented frame is at most 250 ms old (OV-01 stamps both)
        self.present('on')
        self.beat()

    def tearDown(self):
        self.d.ILCT_FeSetP1Arming(0)
        self.d.ILCT_FeSetOverlayLive(-1)
        super().tearDown()

    def present(self, mode):
        if mode != 'off' and self.presenting == 'off':
            self.next_frame = self.now + 1
        self.presenting = mode
        if mode in ('on', 'ready'):
            self.started = self.now

    def beat(self, seats=None, firewall=True, dt=20):
        d = self.d
        while getattr(self, 'presenting', 'off') != 'off' and self.next_frame <= self.now + dt:
            t = self.next_frame
            if self.presenting in ('on', 'ready'):
                d.ILCT_FeNoteDropped(self.dropped, t)
                d.ILCT_FeNoteDrawn(t)
                if self.presenting == 'on':
                    d.ILCT_FePresenterRunFrame(t)      # gpu::DRAWN
                else:
                    d.ILCT_FePresenterRunReady(t)      # gpu::READY: an empty frame proves nothing
                self.last_stamp = t
            else:
                d.ILCT_FePresenterBreakRun()
            self.next_frame += self.FRAME_MS
        super().beat(seats, firewall, dt)

    def miss(self, n):
        """The next n frame slots are reached and not presented (OV-01's break_run)."""
        for _ in range(n):
            self.d.ILCT_FePresenterBreakRun()
            self.next_frame += self.FRAME_MS

    def native(self) -> int:
        return self.d.ILCT_FeP1Native()

    def arm(self):
        for _ in range(40):
            if self.native() == 0:
                break
            self.beat()
        self.assertEqual(self.native(), 0, 'armed: P1\'s menu is ours')

    def arm_state(self) -> P1Arm:
        a = P1Arm()
        self.d.ILCT_FeP1Arm(ctypes.addressof(a))
        return a

    def test_p1_starts_on_vanilla_and_arms_after_a_run(self):
        self.assertEqual(self.native(), 1, 'the module starts with P1 on the vanilla menu')
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.CLOSED, 'P1\'s A is the game\'s')
        self.assertEqual(self.state().report.p1_menus, 0)
        self.arm()
        self.assertGreaterEqual(self.now - self.started, P1_ARM_MS, 'overlay_live for 250 ms')
        st = self.state()
        self.assertEqual((st.report.p1_menus, st.report.leader_gate), (1, 1), 'the gate arms: no vanilla flash')
        self.assertTrue(self.fw().head.flags & tf_gate())
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST, 'P1\'s A opens seat 0\'s co-op menu')
        self.assertEqual(self.arm_state().arms, 1)

    def test_confirm_on_the_arming_beat_is_the_games_alone(self):
        """The firewall applied last beat's report (p1_menus 0) while this beat's CONFIRM was pressed, so the game got
        it: seat 0 must not take it too (one pad, two menus)."""
        for _ in range(40):
            self.press(1, tfe.A_CONFIRM)
            if self.native() == 0:
                break
            self.assertEqual(self.seat(0).mode, tfe.CLOSED)
        self.assertEqual(self.native(), 0, 'armed on a beat with CONFIRM pressed')
        self.assertEqual(self.seat(0).mode, tfe.CLOSED, 'the arming beat\'s CONFIRM is the game\'s alone')
        self.assertEqual(self.state().report.p1_menus, 1, 'the gate arms from this beat\'s report')
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST, 'the next press, under the armed gate, is seat 0\'s')

    def test_the_vanilla_battle_menu_while_armed_wins_for_seat_0_only(self):
        self.arm()
        self.press(2, tfe.A_BATTLE)
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual((self.seat(0).mode, self.seat(1).mode), (tfe.LIST, tfe.LIST))
        self.publish(facts=tfe.FACT['CONTROL'] | tfe.FACT['MENU'])
        self.d.ILCT_FeSetScreen(0x8)                       # P1's own vanilla battle frontend came up (a race)
        self.beat()
        a = self.arm_state()
        self.assertEqual((self.native(), self.seat(0).mode, a.last_event, a.holds), (1, tfe.CLOSED, PA_HELD, 0),
                         'a race disarms with one short hold, never an escalation')
        st = self.state()
        self.assertEqual((st.report.p1_menus, st.susp.suspended, self.seat(1).mode, st.report.guests_open),
                         (0, 0, tfe.LIST, 0b10), 'vanilla wins for seat 0; the guest keeps its menu (no freeze)')
        self.press(2, tfe.A_DOWN)
        self.assertEqual(self.seat(1).cursor, 1, 'and keeps driving it')
        self.assertEqual(self.fw().pulse, 0, 'never onto P1\'s vanilla menu')

    def test_missed_frames_do_not_hold_arming_back(self):
        """0.1.60 item 7: arming follows overlay_live, not the presenter's run: a frame missed now and then (a native
        Target Info request, the D-pad focus panel) neither delays the arm nor disarms."""
        for k in range(40):
            if k % 8 == 0:
                self.miss(1)
            self.beat()
            if self.native() == 0:
                break
        self.assertEqual(self.native(), 0)
        self.assertGreaterEqual(self.now - self.started, P1_ARM_MS)
        for k in range(20):
            if k % 3 == 0:
                self.miss(2)
            self.beat()
            self.assertEqual(self.native(), 0, k)

    def test_never_under_an_open_stock_frontend(self):
        self.publish(facts=tfe.FACT['CONTROL'] | tfe.FACT['MENU'])
        self.d.ILCT_FeSetScreen(0x8)                       # P1's own vanilla battle menu is up
        for _ in range(25):
            self.beat()
        self.assertEqual(self.native(), 1, 'P1\'s open vanilla menu is never pulled away')
        self.publish()
        self.d.ILCT_FeSetScreen(0)
        self.beat()
        self.assertEqual(self.native(), 0, 'arms once it closed (the run is already long enough)')

    def test_give_back_once_not_live_for_over_250_ms(self):
        """Real stamps: the overlay stops presenting (hook not called, swap chain gone). P1 stays armed through the
        250 ms hysteresis and goes back to vanilla on the first beat after it."""
        self.arm()
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST)
        self.present('off')
        while self.now + 20 - self.last_stamp <= LIVE_MS:
            self.beat()
            self.assertEqual(self.native(), 0, 'within the hysteresis')
        for _ in range(3):
            self.beat()
        st = self.state()
        self.assertEqual((self.native(), self.seat(0).mode, st.report.p1_menus), (1, tfe.CLOSED, 0))
        self.assertFalse(self.fw().head.flags & tf_gate(), 'P1\'s vanilla menu is P1\'s again')
        self.present('on')
        self.beat()
        self.assertEqual(self.native(), 1, 'back only after 250 ms live again')
        self.arm()
        self.assertGreaterEqual(self.now - self.started, P1_ARM_MS)

    def test_missed_frames_keep_p1_armed(self):
        """0.1.60 bug 2: two missed frames used to disarm P1 and bring the vanilla HUD back."""
        self.arm()
        self.press(1, tfe.A_CONFIRM)
        self.miss(1)
        self.beat()
        self.beat()
        self.miss(2)
        self.beat()
        self.miss(5)
        self.beat()
        self.assertEqual(self.native(), 0, 'missed frames inside the hysteresis')

    def test_a_screen_that_hides_the_hud_disarms_and_rearms_after(self):
        self.arm()
        self.present('hidden')                             # the main menu, the map, a shop: frames reached, not shown
        self.beat(dt=40)
        self.assertEqual(self.native(), 0, 'the hysteresis')
        for _ in range(14):
            self.beat()
        self.assertEqual(self.native(), 1, 'disarmed once not live for over 250 ms')
        self.present('on')
        self.beat()
        self.assertEqual(self.native(), 1)
        self.arm()
        self.assertGreaterEqual(self.now - self.started, P1_ARM_MS, 're-arms once the run rebuilds')

    def test_a_refused_frame_gives_p1_back_at_once(self):
        """P1's menu closed: a refused frame disarms (no hold); a full run inside 250 ms does not arm."""
        self.arm()
        self.d.ILCT_FePresenterFailed(self.now)             # device removed / back buffer unusable
        failed = self.now
        self.beat()
        self.assertEqual((self.native(), self.arm_state().holds), (1, 0))
        while self.now + 20 - failed <= 250:
            self.beat()
            self.assertEqual(self.native(), 1, 'a full run inside 250 ms of the refusal does not arm')
        self.arm()

    def test_a_refused_frame_with_p1s_menu_open_is_a_give_back(self):
        self.arm()
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.LIST)
        self.d.ILCT_FePresenterFailed(self.now)
        self.beat()
        a = self.arm_state()
        self.assertEqual((self.native(), self.seat(0).mode, a.last_event, a.holds), (1, tfe.CLOSED, PA_HELD, 1),
                         'an overlay failure under an open menu: the escalating hold')

    def test_empty_ready_frames_never_arm_after_a_refusal(self):
        """A persistent back-buffer failure leaves only empty READY frames: they stamp the presenter but prove nothing,
        so P1 stays on vanilla (its first A is never lost to a re-arm that cannot draw)."""
        self.d.ILCT_FePresenterFailed(self.now)
        self.present('ready')
        for _ in range(40):
            self.beat()
            self.assertEqual(self.native(), 1)
        self.present('on')
        self.arm()

    def test_a_legitimate_stock_leader_select_is_no_race(self):
        """Armed; P1's character falls with nobody free: the layer stops owning P1 and the stock leader select (id 3)
        opens legitimately. No hold, no escalation: seat 0 just takes no input while it is up."""
        self.arm()
        self.flags[0] = tfe.SEAT_FULL & ~tfe.SEATF['ALIVE']
        self.publish()
        self.picker(0, party=self.party_of([(0, 0, tfe.ST_KO), (1, 100, 0), (2, 100, 0), (3, 0, tfe.ST_KO)]))   # member 3 down too: nobody free
        self.beat()
        self.assertEqual(self.state().report.p1_menus, 0, 'nobody free: the stock KO path is P1\'s')
        self.publish(facts=tfe.FACT['CONTROL'] | tfe.FACT['MENU'])
        self.d.ILCT_FeSetScreen(0x8 | 0x10)                 # the stock leader select
        for _ in range(4):
            self.beat()
        a = self.arm_state()
        self.assertEqual((a.holds, a.latched), (0, 0), 'no give-back counted')
        self.assertNotEqual(a.last_event, PA_HELD)
        self.press(1, tfe.A_CONFIRM)
        self.assertEqual(self.seat(0).mode, tfe.CLOSED, 'seat 0 takes no input while the stock select is up')

    def test_a_hitch_keeps_p1s_menu_open(self):
        """A whole-game hitch: no frames for 160 ms. P1's open co-op menu survives it (the 250 ms hysteresis)."""
        self.arm()
        self.press(1, tfe.A_CONFIRM)
        self.present('off')
        for _ in range(8):
            self.beat()
        self.assertEqual((self.native(), self.seat(0).mode), (0, tfe.LIST), 'within the hysteresis')
        for _ in range(6):
            self.beat()
        self.assertEqual(self.native(), 1, 'no presented frame for over 250 ms: vanilla')

    def test_a_left_out_window_holds_p1_on_vanilla(self):
        self.arm()
        self.press(1, tfe.A_CONFIRM)
        givebacks = self.d.ILCT_FeP1Givebacks()
        self.dropped = 0b1                                 # the overlay leaves P1's window out of its frames
        self.beat()
        self.dropped = 0
        self.assertEqual(self.d.ILCT_FeP1Givebacks(), givebacks + 1)
        self.assertEqual(self.seat(0).mode, tfe.CLOSED, 'the invisible menu closed at once')
        self.beat()
        a = self.arm_state()
        self.assertEqual((self.native(), a.last_event, a.holds), (1, PA_HELD, 1))
        hold_until = a.hold_until
        while self.now + 200 < hold_until:
            self.beat(dt=200)
            self.assertEqual(self.native(), 1, 'held on vanilla for 5 s, whatever the overlay presents')
        while self.now < hold_until:
            self.beat()
        self.assertEqual(self.native(), 0, 'then the overlay earns it back')

    def test_wait_counts_p1_once_armed(self):
        """Wait ALL (the default): with P1 on vanilla the only guest's menu pauses alone (P1NativeTest); armed, P1 is a
        participant again, so the guest's menu alone does not, and both open do."""
        self.flags.pop(2)
        self.publish()
        self.arm()
        self.press(2, tfe.A_BATTLE)
        self.assertEqual(self.seat(1).mode, tfe.LIST)
        self.assertFalse(self.wait_requested(), 'P1 counts: its menu is closed')
        self.press(1, tfe.A_CONFIRM)
        self.assertTrue(self.wait_requested(), 'every participant has its menu open')

    def test_off_after_reset(self):
        self.assertEqual(self.d.ILCT_FeP1Arming(), 1)
        self.d.ILCT_FeReset()
        self.assertEqual((self.d.ILCT_FeP1Arming(), self.d.ILCT_FeP1Native()), (0, 0))
        self.d.ILCT_FeSetP1Arming(1)


def tf_gate() -> int:
    import test_coop_firewall as tf
    return tf.F_GATE


if __name__ == '__main__':
    unittest.main()
