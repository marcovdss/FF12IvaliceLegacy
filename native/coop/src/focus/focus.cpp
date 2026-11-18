// Ivalice Legacy co-op module: "Keep running in background" (see focus.h).
#include "focus/focus.h"

#include <windows.h>

#include <string.h>

#include "patch/patch_engine.h"
#include "runtime/probe.h"

namespace ilc::focus {
namespace {

// ---- game facts (FFXII_TZA.exe 1.0.4.0; VAs at the pinned image base, see sites.inc) ----------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t VT_STATE_WINDOW = 0x1CB3F50;      // VA: VTS::Phyre::ScreenStateWindow vtable
constexpr uint64_t VT_STATE_BORDERLESS = 0x1CB4000;  // VA: VTS::Phyre::ScreenStateBorderless vtable
constexpr uint32_t STATE_ACTIVE = 0x18;              // u8: the state's active byte (Window/Borderless)
constexpr uint64_t WINDOW_HANDLE = 0x2EA5248;        // VA: the game window's HWND (written at WM_CREATE)
constexpr uint64_t XI_FOCUSED_PATH = 0x7A223C;       // VA: the XInput update's focused path (the jne not taken: the
                                                     // focus flag 0x2EA5213 = 1, then the real packet interpreted)
constexpr uint64_t DI_PAD_FINALIZE = 0x7A1C9F;       // VA: mov rcx,rbx; call the shared finalizer (pad update)
constexpr uint32_t DI_PAD_BLOCK = 0x414;             // the device platform block, through +0x44E
constexpr uint32_t DI_PAD_BLOCK_SIZE = 0x3B;
constexpr uint64_t DI_MOUSE_FINALIZE = 0x7A1A1E;     // VA: mov rcx,rbx; call the shared finalizer (mouse update)
constexpr uint32_t DI_MOUSE_STATE = 0x1E8;           // deltas and buttons, through +0x1F6
constexpr uint32_t DI_MOUSE_STATE_SIZE = 15;
constexpr uint64_t ZERO_FLAG = 0x40;                 // NOTADDR: RFLAGS.ZF

constexpr uint64_t game(uint64_t va)
{
    return patch::IMAGE_BASE + (va - PINNED_BASE);
}

constexpr bool same_id(const char* a, const char* b)
{
    while (*a != '\0' && *a == *b) {
        ++a;
        ++b;
    }
    return *a == *b;
}

constexpr int32_t find_row(const char* id)
{
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        if (same_id(patch::SITES[row].id, id)) {
            return static_cast<int32_t>(row);
        }
    }
    return -1;
}

constexpr int32_t ROW_FP01 = find_row("FP-01");
constexpr int32_t ROW_FP03 = find_row("FP-03");
constexpr int32_t ROW_FP04 = find_row("FP-04");
constexpr int32_t ROW_IN06 = find_row("IN-06");
constexpr bool core_patch(int32_t row)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH &&
           patch::SITES[row].subsystem == ILC_SITE_SUBSYS_CORE && patch::SITES[row].kind == ILC_SITE_KIND_DETOUR;
}
static_assert(core_patch(ROW_FP01) && core_patch(ROW_FP03) && core_patch(ROW_FP04) && core_patch(ROW_IN06),
              "the background rows are CORE detours: they act on the player's setting alone");

// Off until the player's saved setting has been read: the CORE rows act from the first armed loop, and the native
// default (on) must not run the game in the background for a player who saved "Off" (configure_from_settings).
volatile uint32_t g_keep_running = 0;   // co-op thread writes, game threads read
volatile uint32_t g_neutral_ready = 0;  // attach() decides
volatile int32_t g_foreground = -1;     // host tests only
counters g_counters;

void count(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

template <typename T>
bool read_game(uint64_t address, T* out)
{
    return ilc::read_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool write_game(uint64_t address, const void* data, size_t n)
{
    return store_game(reinterpret_cast<void*>(address), data, n);
}

bool hooked(int32_t row)
{
    return row >= 0 && patch::trampoline(static_cast<uint32_t>(row)) != nullptr;
}

}  // namespace

// ---- co-op thread ----------------------------------------------------------------------------------------------
void configure_from_settings(const ilc_settings& s)
{
    // Generation 0 is the native defaults record (nothing read from the player's settings yet): it never turns the
    // setting on. The first record Lua sends (generation >= 1: the saved value, or the schema default for a new store)
    // decides from then on.
    const bool on = s.header.generation != 0 && s.keep_running != 0;
    __atomic_store_n(&g_keep_running, on ? 1u : 0u, __ATOMIC_RELEASE);
}

// ---- any thread --------------------------------------------------------------------------------------------------
bool keep_running()
{
    return __atomic_load_n(&g_keep_running, __ATOMIC_ACQUIRE) != 0;
}

bool neutral_ready()
{
    return __atomic_load_n(&g_neutral_ready, __ATOMIC_ACQUIRE) != 0;
}

// The game window has the foreground (the game's own test: GetForegroundWindow == its HWND). Unreadable, or no window
// yet (HWND 0), = focused, so nothing is changed on a doubt.
bool window_focused()
{
    const int32_t forced = __atomic_load_n(&g_foreground, __ATOMIC_RELAXED);
    if (forced >= 0) {
        return forced != 0;
    }
    uint64_t hwnd = 0;
    if (!read_game(game(WINDOW_HANDLE), &hwnd) || hwnd == 0) {
        return true;
    }
    return reinterpret_cast<uint64_t>(GetForegroundWindow()) == hwnd;
}

bool running_unfocused()
{
    return keep_running() && !window_focused();
}

// ---- handlers (behind the dispatcher's gate, mode, fault and liveness checks) -------------------------------------

// FP-01, function entry 0x7A4150: rcx = the current screen state, dl = its active byte.
int32_t fp01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!keep_running() || !neutral_ready()) {
        count(g_counters.declined);
        return patch::RUN_ORIGINAL;
    }
    uint64_t vtable = 0;
    if (!read_game(c->rcx, &vtable)) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    if (vtable != game(VT_STATE_WINDOW) && vtable != game(VT_STATE_BORDERLESS)) {
        count(g_counters.passed);  // exclusive fullscreen and its transitions keep the stock pause
        return patch::RUN_ORIGINAL;
    }
    // dl = 1 always: the application stays active this loop. The state's own byte is held at 1 only while the window
    // has the focus. Unfocused it stays the game's (the Window update clears it every unfocused frame, the Borderless
    // focus-loss event cleared it), so the first loop FP-01 does not act on (setting off, stale liveness, a closed
    // gate) passes 0 and the stock pause starts: the game never runs unfocused with a stale stick. Focused, the byte
    // must be 1: WA_ACTIVE never sets it back, so turning the setting off after an alt-tab would pause a focused game.
    if (window_focused()) {
        const uint8_t one = 1;
        if (!write_game(c->rcx + STATE_ACTIVE, &one, 1)) {
            count(g_counters.probe_failed);
            return patch::RUN_ORIGINAL;
        }
    }
    c->rdx = (c->rdx & ~uint64_t{0xFF}) | 1u;
    count(g_counters.forced);
    return patch::RUN_ORIGINAL;
}

// IN-06, the XInput focus gate 0x7A2236 (jne, ZF clear = another window has the foreground). XInputGetState has already
// filled the state on the update's frame. Stock, the unfocused branch (0x7A2410) only clears the focus flag and returns,
// so the last interpreted pad stays latched. With the setting on, an unfocused update takes the focused path instead
// (the branch not taken: 0x7A223C sets the focus flag and interprets the real packet), so player 1's pad keeps playing
// while another window has the foreground (Marco, 2026-11-16: no frozen controllers on alt-tab).
int32_t in06(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if ((c->rflags & ZERO_FLAG) != 0 || !keep_running()) {
        return patch::RUN_ORIGINAL;  // focused, or the setting off (the game is paused: stock)
    }
    count(g_counters.xinput);
    c->resume = game(XI_FOCUSED_PATH);
    return patch::RESUME;
}

// FP-03, the DirectInput pad update 0x7A1AAA (cmp eax, DIERR_INPUTLOST after a failed Poll and an Acquire); rbx = the
// device object.
int32_t fp03(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!keep_running() || window_focused()) {
        return patch::RUN_ORIGINAL;
    }
    const uint8_t neutral[DI_PAD_BLOCK_SIZE] = {};
    if (!write_game(c->rbx + DI_PAD_BLOCK, neutral, sizeof(neutral))) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    count(g_counters.dinput_pad);
    c->resume = game(DI_PAD_FINALIZE);
    return patch::RESUME;
}

// FP-04, the DirectInput mouse update 0x7A17E9 (right after GetDeviceState failed); rbx = the device object.
int32_t fp04(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!keep_running() || window_focused()) {
        return patch::RUN_ORIGINAL;
    }
    const uint8_t neutral[DI_MOUSE_STATE_SIZE] = {};
    if (!write_game(c->rbx + DI_MOUSE_STATE, neutral, sizeof(neutral))) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    count(g_counters.dinput_mouse);
    c->resume = game(DI_MOUSE_FINALIZE);
    return patch::RESUME;
}

// ---- boot ----------------------------------------------------------------------------------------------------------
bool attach()
{
    const struct {
        int32_t row;
        patch::handler_fn fn;
    } hooks[] = {{ROW_IN06, in06}, {ROW_FP01, fp01}, {ROW_FP03, fp03}, {ROW_FP04, fp04}};
    bool ok = true;
    for (const auto& h : hooks) {
        ok = patch::set_handler(static_cast<uint32_t>(h.row), h.fn) && ok;
    }
    // FP-01 never acts without the XInput neutral state (a held stick would keep driving P1 in the background).
    __atomic_store_n(&g_neutral_ready, ok && hooked(ROW_IN06) ? 1u : 0u, __ATOMIC_RELEASE);
    for (const auto& h : hooks) {
        ok = patch::set_site_armed(static_cast<uint32_t>(h.row), ok) && ok;
    }
    if (!ok) {
        __atomic_store_n(&g_neutral_ready, 0u, __ATOMIC_RELEASE);
        for (const auto& h : hooks) {
            patch::set_site_armed(static_cast<uint32_t>(h.row), false);
        }
    }
    return ok;
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view v = {};
    v.keep_running = keep_running() ? 1 : 0;
    v.fp01_armed = hooked(ROW_FP01) ? 1 : 0;
    v.in06_armed = hooked(ROW_IN06) ? 1 : 0;
    v.fp03_armed = hooked(ROW_FP03) ? 1 : 0;
    v.fp04_armed = hooked(ROW_FP04) ? 1 : 0;
    return v;
}

// ---- host tests --------------------------------------------------------------------------------------------------
void test_set_keep_running(bool on)
{
    __atomic_store_n(&g_keep_running, on ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_set_foreground(int32_t forced)
{
    __atomic_store_n(&g_foreground, forced < 0 ? -1 : (forced != 0 ? 1 : 0), __ATOMIC_RELAXED);
}

void test_set_neutral_ready(bool ready)
{
    __atomic_store_n(&g_neutral_ready, ready ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_reset()
{
    memset(const_cast<counters*>(&g_counters), 0, sizeof(g_counters));
    __atomic_store_n(&g_keep_running, 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_foreground, -1, __ATOMIC_RELAXED);
}

}  // namespace ilc::focus
