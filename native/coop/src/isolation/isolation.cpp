// Ivalice Legacy co-op module: input isolation (see isolation.h).
#include "isolation/isolation.h"

#include <string.h>

#include "input/edge_history.h"
#include "input/pad_mirror.h"
#include "movement/movement.h"
#include "patch/call_island.h"
#include "patch/patch_engine.h"
#include "runtime/probe.h"
#include "session/session.h"

namespace ilc::isolation {
namespace {

// ---- game facts (FFXII_TZA.exe 1.0.4.0; VAs at the pinned image base, see sites.inc) ----------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t VT_PAD_XINPUT = 0x1CB2678;    // VA: PInputDevicePadXInput vtable
constexpr uint64_t VT_PAD_DINPUT = 0x1CB2638;    // VA: PInputDevicePadDirectInput vtable
constexpr uint64_t VT_PAD_STEAM = 0x1CB26B8;     // VA: PInputDevicePadSteamController vtable
constexpr uint64_t VT_KEYBOARD = 0x1CB2430;      // VA: PInputDeviceKeyboard vtable
constexpr uint64_t KEYMAP = 0x1F3DAB0;           // VA: internal key index -> DIK code (256 bytes)
constexpr uint64_t PAD_FOCUS_FLAG = 0x2EA5213;   // VA: shared focus flag the pad and desktop inputs write
constexpr uint64_t PAD_BLOCK_RESUME = 0x7A2243;  // VA: the pad block right after the focused store
constexpr uint32_t DEV_XINPUT_INDEX = 0x458;     // XInput objects only: the polled user index
constexpr uint32_t DEV_XINPUT_PACKET = 0x45C;    // XInput objects only: last packet number
constexpr uint32_t DEV_PRODUCT_GUID = 0x1E4;     // DIDEVICEINSTANCEA.guidProduct (Data1 = VID | PID << 16)
constexpr uint32_t DEV_FLEE = 0x443;             // RT > 0 byte (flee)
constexpr uint32_t DEV_XINPUT_RT = 0x463;        // XInput objects: bRightTrigger of the last XINPUT_STATE copied
constexpr uint32_t KB_STATE_0 = 0x30;            // keyboard state arrays the game also clears on a lost device
constexpr uint32_t KB_STATE_1 = 0x130;
constexpr uint32_t KB_KEYS = 0x231;              // normalized key state, 256 bytes, read by get-button
constexpr uint32_t KB_RAW_AT_ENTRY = 0x28;       // the raw 256-byte sample, from RSP at the finalizer's entry
// Keys that stay global to the shared game (internal indices): M, Z and numpad + (map), F1/F2/F3 (speed),
// Tab (map transparency). Camera keys and Esc are not here: they belong to P1, or would double a guest's Pause.
constexpr uint8_t SHARED_KEYS[] = {0x0C, 0x19, 0x25, 0x26, 0x27, 0x33, 0x56};

constexpr uint64_t game(uint64_t va)
{
    return patch::IMAGE_BASE + (va - PINNED_BASE);
}

constexpr uint32_t RELEASE_BEATS = 10;  // co-op beats the release flag stays up after isolation stops

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

constexpr int32_t ROW_IN01 = find_row("IN-01");
constexpr int32_t ROW_IN02 = find_row("IN-02");
constexpr int32_t ROW_IN03 = find_row("IN-03");
constexpr int32_t ROW_IN05 = find_row("IN-05");
constexpr int32_t ROW_IN06 = find_row("IN-06");
constexpr int32_t ROW_IN07 = find_row("IN-07");
static_assert(patch::island::is_call_row(ROW_IN01) && patch::island::is_call_row(ROW_IN02) &&
                  patch::island::is_call_row(ROW_IN03) && patch::island::is_call_row(ROW_IN05) &&
                  patch::island::is_call_row(ROW_IN07),
              "the finalize and focus-loss hooks are call rows: their bodies may copy records and read edges");
static_assert(ROW_IN06 >= 0 && !patch::island::is_call_row(ROW_IN06), "IN-06 is the mid-function focus gate");
static_assert(patch::SITES[ROW_IN01].subsystem == ILC_SITE_SUBSYS_INPUT_ISOLATION &&
                  patch::SITES[ROW_IN06].subsystem == ILC_SITE_SUBSYS_INPUT_ISOLATION &&
                  patch::SITES[ROW_IN07].subsystem == ILC_SITE_SUBSYS_INPUT_ISOLATION,
              "all isolation rows carry the INPUT_ISOLATION arm bit");

// ---- state ------------------------------------------------------------------------------------------------------
patch::record_slot<iso_record> g_slot;
counters g_counters;
volatile int32_t g_budget = KB_BUDGET;
volatile uint32_t g_variant = VARIANT_OK;
volatile uint32_t g_published = 0;
// Guests' flee requests: the movement subsystem's epoch-tagged word (bit s = seat s asks). Replaceable in tests.
uint32_t (*volatile g_flee_source)(uint32_t epoch) = movement::flee_seats;

// Co-op thread only.
struct config {
    uint8_t kbm_seat;
    uint8_t guest_seats;
    uint16_t p1_vid, p1_pid;
};
config g_cfg = {};
uint32_t g_active_beats_ago = RELEASE_BEATS + 1;  // beats since GUARD|SUBST was last up

// Release for a disarm (begin_release / release_settled); the co-op thread writes, anyone reads.
volatile uint32_t g_release_requested = 0;
volatile uint32_t g_release_composed = 0;   // release-only records built since begin_release()
// The last coherent P1 mirror sample (co-op thread): a torn read keeps it instead of flipping the policy.
pads::pad_view g_last_p1 = {};

// Tick thread only (the pad finalize bodies all run there, one device after another); g_host is also read by
// release_settled() on the co-op thread (atomic loads/stores).
volatile uint64_t g_host = 0;
bool g_flee_last = false;  // flee was written onto P1's own device last tick: rewrite it from the real RT
uint32_t g_host_rank = 0;
bool g_host_seen = false;
uint64_t g_round[8];
uint32_t g_round_n = 0;
edges::edge_cursor g_cursor = {};
uint32_t g_cursor_epoch = 0;

enum pad_class : uint32_t { PAD_XINPUT = 0, PAD_DINPUT = 1, PAD_STEAM = 2 };

void count(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

template <typename T>
bool read_game(uint64_t address, T* out)
{
    return copy_from_caller(out, reinterpret_cast<const void*>(address), sizeof(T));
}

bool write_game(uint64_t address, const void* data, size_t n)
{
    return copy_to_caller(reinterpret_cast<void*>(address), data, n);
}

bool class_is(uint64_t dev, uint64_t vtable_va)
{
    uint64_t vt = 0;
    if (!read_game(dev, &vt)) {
        count(g_counters.probe_failed);
        return false;
    }
    if (vt != game(vtable_va)) {
        count(g_counters.wrong_class);
        return false;
    }
    return true;
}

// XInput button bit -> platform byte (the game's own interpretation of a packet).
struct button_map {
    uint16_t xinput;
    uint16_t control;  // ilc_pad_control
    uint32_t offset;
};
constexpr button_map BUTTONS[] = {
    {0x4000, ILC_PAD_X, 0x43C},          {0x1000, ILC_PAD_A, 0x43D},         {0x2000, ILC_PAD_B, 0x43E},
    {0x8000, ILC_PAD_Y, 0x43F},          {0x0100, ILC_PAD_LB, 0x440},        {0x0200, ILC_PAD_RB, 0x441},
    {0x0020, ILC_PAD_BACK, 0x444},       {0x0010, ILC_PAD_START, 0x445},     {0x0040, ILC_PAD_L3, 0x446},
    {0x0080, ILC_PAD_R3, 0x447},         {0x0001, ILC_PAD_DPAD_UP, 0x448},   {0x0008, ILC_PAD_DPAD_RIGHT, 0x449},
    {0x0002, ILC_PAD_DPAD_DOWN, 0x44A},  {0x0004, ILC_PAD_DPAD_LEFT, 0x44B},
};

int32_t tdiv128(int32_t v, bool shift)
{
    return shift ? (v >> 7) : v / 128;  // C division truncates toward zero, as the game's cdq/and/add/sar does
}

void put32(uint8_t* block, uint32_t offset, int32_t v)
{
    memcpy(block + (offset - BLOCK_OFFSET), &v, 4);
}

// Rank of a device as P1's substitution host (higher wins).
uint32_t host_rank(uint64_t dev, uint32_t cls, const iso_record& r)
{
    if (cls == PAD_DINPUT) {
        uint32_t product = 0;
        if (r.p1_vid != 0 && read_game(dev + DEV_PRODUCT_GUID, &product) &&
            product == (static_cast<uint32_t>(r.p1_vid) | static_cast<uint32_t>(r.p1_pid) << 16)) {
            return 3;  // P1's own pad as the game enumerated it
        }
        return 0;
    }
    return cls == PAD_XINPUT ? 2 : 1;
}

void forget_host()
{
    __atomic_store_n(&g_host, 0, __ATOMIC_RELEASE);
    g_host_rank = 0;
    g_host_seen = false;
    g_round_n = 0;
}

// One visit of the device loop. A device seen twice starts a new round; a host not seen during the round that
// just ended is gone (unplugged, or a lost DirectInput device that skips its finalizer).
void elect(uint64_t dev, uint32_t rank)
{
    bool repeat = false;
    for (uint32_t k = 0; k < g_round_n; ++k) {
        repeat = repeat || g_round[k] == dev;
    }
    if (repeat) {
        if (!g_host_seen) {
            __atomic_store_n(&g_host, 0, __ATOMIC_RELEASE);
            g_host_rank = 0;
        }
        g_host_seen = false;
        g_round_n = 0;
    }
    if (g_round_n < sizeof(g_round) / sizeof(g_round[0])) {
        g_round[g_round_n++] = dev;
    }
    if (g_host == 0 || rank > g_host_rank) {
        if (g_host != dev) {
            count(g_counters.host_changes);
        }
        __atomic_store_n(&g_host, dev, __ATOMIC_RELEASE);
        g_host_rank = rank;
    }
    g_host_seen = g_host_seen || dev == g_host;
}

bool write_neutral(uint64_t dev, uint32_t cls)
{
    uint8_t block[BLOCK_SIZE] = {};
    bool ok = write_game(dev + BLOCK_OFFSET, block, sizeof(block));
    if (cls == PAD_XINPUT) {
        const uint32_t zero = 0;  // the next real packet is interpreted again
        ok = write_game(dev + DEV_XINPUT_PACKET, &zero, 4) && ok;
    }
    return ok;
}

bool flee_requested(const iso_record& r)
{
    return (r.head.flags & ISO_FLEE) != 0 || g_flee_source(r.head.epoch) != 0;
}

bool write_substitute(uint64_t dev, const iso_record& r, bool xinput)
{
    const uint32_t variant = g_variant;
    if (variant == VARIANT_NONE) {
        return true;
    }
    pads::pad_view v;
    ilc_xinput_gamepad pad = {};
    const bool live = pads::read(1, &v) && v.source == ILC_FRAME_SOURCE_SDL && (v.flags & ILC_FRAMEF_CONNECTED) != 0;
    if (live) {
        pad = v.pad;
    }
    // Presses since this consumer's last read: a tap that started and ended between two device ticks still
    // reaches the game. A new epoch, an overrun or a re-seeded cursor contributes nothing (never a replay).
    if (r.head.epoch != g_cursor_epoch) {
        memset(&g_cursor, 0, sizeof(g_cursor));
        g_cursor_epoch = r.head.epoch;
    }
    uint32_t extra = 0;
    edges::edge_delta d;
    if (edges::since(g_cursor, 1, &d) && !d.overrun && !d.reseeded && live) {
        extra = d.pad_pressed;
    }
    uint8_t block[BLOCK_SIZE];
    platform_block(pad, extra, flee_requested(r), block);
    if (variant == VARIANT_NO_CLEAR) {
        // Instrument control: write only the fields XInput writes, leave the rest as the class left them.
        return write_game(dev + 0x414, block + 0x00, 8) && write_game(dev + 0x420, block + 0x0C, 8) &&
               write_game(dev + 0x43C, block + 0x28, 16);
    }
    bool ok = write_game(dev + BLOCK_OFFSET, block, sizeof(block));
    if (xinput) {
        // The game interprets every packet again from the next tick (our block still wins before the finalizer),
        // so after a hard disarm the host shows its real pad at once instead of the last substituted state.
        const uint32_t zero = 0;
        ok = write_game(dev + DEV_XINPUT_PACKET, &zero, 4) && ok;
    }
    return ok;
}

int32_t pad_body(patch::ctx* c, const iso_record& r, uint32_t cls, uint64_t vtable_va)
{
    const uint64_t dev = c->rcx;  // the finalizer's argument (call row: registers at its entry)
    if (!class_is(dev, vtable_va)) {
        return patch::RUN_ORIGINAL;
    }
    const uint32_t f = r.head.flags;
    if ((f & (ISO_GUARD | ISO_SUBST)) == 0) {
        if ((f & ISO_RELEASE) != 0 && dev == g_host && g_host != 0) {
            write_neutral(dev, cls);  // the last substituted state must not persist (XInput skips old packets)
            count(g_counters.released);
            forget_host();
        } else {
            count(g_counters.stock);
            if ((f & ISO_RELEASE) == 0) {
                forget_host();
            }
        }
        return patch::RUN_ORIGINAL;
    }
    if (cls == PAD_XINPUT && (f & ISO_P1_NATIVE) != 0) {
        uint32_t index = 0xFFFFFFFFu;
        if (!read_game(dev + DEV_XINPUT_INDEX, &index)) {
            count(g_counters.probe_failed);
            return patch::RUN_ORIGINAL;
        }
        if (index == r.p1_slot) {
            // P1's own RT OR a guest's flee request. XInput interprets only new packets, so a flee byte written
            // once would stick after the guest lets go: while flee is requested, and on the tick after, the byte
            // is rewritten from the real RT of the last packet (+0x463). Without flee activity it is not touched.
            const bool flee = flee_requested(r);
            if (flee || g_flee_last) {
                uint8_t rt = 0;
                read_game(dev + DEV_XINPUT_RT, &rt);
                const uint8_t value = (rt != 0 || flee) ? 1 : 0;
                write_game(dev + DEV_FLEE, &value, 1);
            }
            g_flee_last = flee;
            count(g_counters.passed);
            return patch::RUN_ORIGINAL;
        }
    }
    // A disconnected XInput object is never the host (IN-01: edi = XInputGetState's result, 0 = connected): the
    // game never interprets it again, so a state substituted there would outlive a hard disarm.
    const bool eligible = cls != PAD_XINPUT || static_cast<uint32_t>(c->rdi) == 0;
    if ((f & ISO_SUBST) != 0 && eligible) {
        elect(dev, host_rank(dev, cls, r));
        if (dev == g_host) {
            if (write_substitute(dev, r, cls == PAD_XINPUT)) {
                count(g_counters.substituted);
            } else {
                count(g_counters.probe_failed);
            }
            return patch::RUN_ORIGINAL;
        }
    } else if ((f & ISO_SUBST) == 0 && g_host != 0) {
        forget_host();  // the old host is just another pad now: masked below on its next visit
    } else if (dev == g_host) {
        forget_host();  // the host went disconnected: re-elected among the others
    }
    if (write_neutral(dev, cls)) {
        count(g_counters.masked);
    } else {
        count(g_counters.probe_failed);
    }
    return patch::RUN_ORIGINAL;  // the finalizer runs: edges release, nothing stays latched
}

int32_t body01(patch::ctx* c, const iso_record& r)
{
    return pad_body(c, r, PAD_XINPUT, VT_PAD_XINPUT);
}

int32_t body02(patch::ctx* c, const iso_record& r)
{
    return pad_body(c, r, PAD_DINPUT, VT_PAD_DINPUT);
}

int32_t body03(patch::ctx* c, const iso_record& r)
{
    return pad_body(c, r, PAD_STEAM, VT_PAD_STEAM);
}

int32_t body05(patch::ctx* c, const iso_record& r)
{
    const uint64_t dev = c->rcx;
    if ((r.head.flags & ISO_KB_FILTER) == 0 || !class_is(dev, VT_KEYBOARD)) {
        return patch::RUN_ORIGINAL;
    }
    // The lease: one unit per keyboard update; the co-op thread refills it every beat.
    if (__atomic_load_n(&g_budget, __ATOMIC_ACQUIRE) <= 0) {
        count(g_counters.kb_expired);
        return patch::RUN_ORIGINAL;
    }
    __atomic_sub_fetch(&g_budget, 1, __ATOMIC_ACQ_REL);
    static const uint8_t zero[256] = {};
    uint8_t raw[256];
    uint8_t map[256];
    if (!read_game(c->rsp + KB_RAW_AT_ENTRY, &raw) || !read_game(game(KEYMAP), &map)) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    if (!write_game(dev + KB_STATE_0, zero, 256) || !write_game(dev + KB_STATE_1, zero, 256) ||
        !write_game(dev + KB_KEYS, zero, 256)) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    for (const uint8_t key : SHARED_KEYS) {
        const uint8_t down = static_cast<int8_t>(raw[map[key]]) < 0 ? 1 : 0;  // the game's own rule
        write_game(dev + KB_KEYS + key, &down, 1);
    }
    count(g_counters.kb_filtered);
    return patch::RUN_ORIGINAL;
}

int32_t body06(patch::ctx* c, const iso_record& r)
{
    // Mid-function (jne at the focus gate): ZF set = focused, the stock path continues. Unfocused: run the pad
    // block anyway, keeping the shared focus flag honest (0) as the stock unfocused path would have.
    if ((r.head.flags & ISO_BACKGROUND) == 0 || (c->rflags & 0x40) != 0) {
        return patch::RUN_ORIGINAL;
    }
    const uint8_t zero = 0;
    if (!write_game(game(PAD_FOCUS_FLAG), &zero, 1)) {
        count(g_counters.probe_failed);
        return patch::RUN_ORIGINAL;
    }
    count(g_counters.background);
    c->resume = game(PAD_BLOCK_RESUME);
    return patch::RESUME;
}

int32_t body07(patch::ctx* c, const iso_record& r)
{
    // rdx = &event {type 4, value 0}; value 1 makes the focus loss read as "active": the game keeps running.
    if ((r.head.flags & ISO_BACKGROUND) == 0) {
        return patch::RUN_ORIGINAL;
    }
    const uint32_t active = 1;
    if (write_game(c->rdx + 4, &active, 4)) {
        count(g_counters.focus_kept);
    } else {
        count(g_counters.probe_failed);
    }
    return patch::RUN_ORIGINAL;
}

}  // namespace

void platform_block(const ilc_xinput_gamepad& pad, uint32_t extra, bool flee, uint8_t out[BLOCK_SIZE])
{
    const uint32_t variant = g_variant;
    const bool shift = variant == VARIANT_SHIFT;
    const int32_t ysign = variant == VARIANT_NO_NEGATE ? 1 : -1;
    memset(out, 0, BLOCK_SIZE);  // +0x41C, +0x428, +0x42C..+0x438, +0x44C..+0x44E: XInput never writes them
    put32(out, 0x414, tdiv128(pad.lx, shift));
    put32(out, 0x418, tdiv128(pad.rx, shift));
    put32(out, 0x420, ysign * tdiv128(pad.ly, shift));
    put32(out, 0x424, ysign * tdiv128(pad.ry, shift));
    for (const button_map& b : BUTTONS) {
        const bool down = (pad.buttons & b.xinput) != 0 || (extra & ilc_pad_bit(static_cast<ilc_pad_control>(b.control))) != 0;
        out[b.offset - BLOCK_OFFSET] = down ? 1 : 0;
    }
    out[0x442 - BLOCK_OFFSET] = (pad.lt > 0 || (extra & ilc_pad_bit(ILC_PAD_LT)) != 0) ? 1 : 0;
    out[0x443 - BLOCK_OFFSET] = (pad.rt > 0 || (extra & ilc_pad_bit(ILC_PAD_RT)) != 0 || flee) ? 1 : 0;
}

void configure_from_settings(const ilc_settings& s)
{
    config c = {};
    c.kbm_seat = s.kbm_seat <= ILC_ACTIVE_SEATS_MAX ? s.kbm_seat : 0;
    for (uint32_t i = 1; i < ILC_ACTIVE_SEATS_MAX && i < ILC_SEATS; ++i) {
        if (s.seats[i].device.kind != ILC_DEVICE_NONE || c.kbm_seat == i + 1) {
            c.guest_seats |= static_cast<uint8_t>(1u << i);
        }
    }
    const ilc_seat_device& p1 = s.seats[0].device;
    if (p1.kind == ILC_DEVICE_SDL) {
        const uint8_t* g = p1.sdl.guid;
        if (g[6] == 0 && g[7] == 0 && g[10] == 0 && g[11] == 0) {  // SDL's bus/vendor/product GUID layout
            c.p1_vid = static_cast<uint16_t>(g[4] | g[5] << 8);
            c.p1_pid = static_cast<uint16_t>(g[8] | g[9] << 8);
        }
    }
    g_cfg = c;
}

iso_record compose(uint32_t epoch, uint64_t generation, const session::session_record* s)
{
    iso_record r = {};
    r.head.epoch = epoch;
    r.head.generation = generation;
    r.kbm_seat = g_cfg.kbm_seat;
    r.guest_seats = g_cfg.guest_seats;
    r.p1_vid = g_cfg.p1_vid;
    r.p1_pid = g_cfg.p1_pid;
    r.p1_slot = 0xFF;
    uint32_t f = 0;
    if (g_cfg.guest_seats != 0) {
        f |= ISO_GUARD;
    }
    pads::pad_view p1 = g_last_p1;
    for (uint32_t attempt = 0; attempt < 4; ++attempt) {
        pads::pad_view fresh;
        if (pads::read(1, &fresh)) {
            p1 = fresh;
            g_last_p1 = fresh;
            break;
        }
    }
    if ((p1.flags & ILC_FRAMEF_CONNECTED) != 0) {
        if (p1.source == ILC_FRAME_SOURCE_XINPUT && p1.xinput_slot < 4) {
            f |= ISO_P1_NATIVE;
            r.p1_slot = p1.xinput_slot;
        } else if (p1.source == ILC_FRAME_SOURCE_SDL) {
            f |= ISO_SUBST;
        }
    }
    if ((f & ISO_GUARD) != 0 && g_cfg.kbm_seat != 1) {
        f |= ISO_KB_FILTER;
    }
    // Flee is not decided here: the movement subsystem publishes the guests' requests (flee_seats, tagged with
    // the epoch) and the pad bodies read that word when they write P1's flee byte. The session record is only
    // passed through for future use.
    (void)s;
    if (__atomic_load_n(&g_release_requested, __ATOMIC_ACQUIRE) != 0) {
        // A disarm is coming: no masking, no substitution; the host gets the neutral block once.
        f = ISO_RELEASE;
        g_active_beats_ago = RELEASE_BEATS;
        __atomic_add_fetch(&g_release_composed, 1, __ATOMIC_RELEASE);
    } else if ((f & (ISO_GUARD | ISO_SUBST)) != 0) {
        f |= ISO_BACKGROUND;
        g_active_beats_ago = 0;
    } else if (g_active_beats_ago < RELEASE_BEATS) {
        ++g_active_beats_ago;
        f |= ISO_RELEASE;
    }
    r.head.flags = f;
    return r;
}

void publish(uint64_t now_ms)
{
    (void)now_ms;
    __atomic_store_n(&g_budget, KB_BUDGET, __ATOMIC_RELEASE);  // the keyboard lease, every beat
    const uint32_t epoch = session::epoch();
    if (epoch == 0) {
        g_slot.withdraw();
        return;
    }
    session::session_record s;
    const bool have = session::read(&s);
    g_slot.publish(compose(epoch, session::generation(), have ? &s : nullptr));
    __atomic_add_fetch(&g_published, 1, __ATOMIC_RELAXED);
}

void withdraw()
{
    g_slot.withdraw();
}

void begin_release()
{
    __atomic_store_n(&g_release_composed, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_release_requested, 1u, __ATOMIC_RELEASE);
}

void end_release()
{
    __atomic_store_n(&g_release_requested, 0u, __ATOMIC_RELEASE);
}

bool release_settled()
{
    return __atomic_load_n(&g_release_requested, __ATOMIC_ACQUIRE) != 0 &&
           __atomic_load_n(&g_release_composed, __ATOMIC_ACQUIRE) != 0 &&
           __atomic_load_n(&g_host, __ATOMIC_ACQUIRE) == 0;
}

namespace {

// Isolation's fail-open check. It reads no world memory, so a session epoch change (a map jump, a load) does not
// make its record wrong, only one beat older: the co-op thread moves the liveness epoch first and publishes the
// isolation record right after, and a game thread may sample either side of that. A record for the dispatcher's
// epoch, or one epoch either side of it, therefore still acts; anything else (no record, withdrawn, torn, older)
// is stock. Without this, every epoch change ran the pads stock for a beat and a held guest button came back as
// a fresh press.
bool epoch_near(uint32_t record, uint32_t dispatcher)
{
    return record != 0 && dispatcher != 0 &&
           (record == dispatcher || record + 1u == dispatcher || dispatcher + 1u == record);
}

template <int32_t (*Body)(patch::ctx*, const iso_record&)>
int32_t iso_open(patch::ctx* c, uint32_t epoch)
{
    const uint32_t row = static_cast<uint32_t>(c->index);
    if (row >= patch::SITE_COUNT || !patch::subsystem_live(patch::SITES[row].subsystem)) {
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    iso_record copy;
    if (!g_slot.read(copy) || !epoch_near(copy.head.epoch, epoch)) {
        patch::note_declined(row);
        return patch::RUN_ORIGINAL;
    }
    return Body(c, copy);
}

}  // namespace

int32_t in01(patch::ctx* c, uint32_t epoch) { return iso_open<body01>(c, epoch); }
int32_t in02(patch::ctx* c, uint32_t epoch) { return iso_open<body02>(c, epoch); }
int32_t in03(patch::ctx* c, uint32_t epoch) { return iso_open<body03>(c, epoch); }
int32_t in05(patch::ctx* c, uint32_t epoch) { return iso_open<body05>(c, epoch); }
int32_t in06(patch::ctx* c, uint32_t epoch) { return iso_open<body06>(c, epoch); }
int32_t in07(patch::ctx* c, uint32_t epoch) { return iso_open<body07>(c, epoch); }

bool attach()
{
    const struct {
        int32_t row;
        patch::handler_fn fn;
    } hooks[] = {{ROW_IN01, in01}, {ROW_IN02, in02}, {ROW_IN03, in03},
                 {ROW_IN05, in05}, {ROW_IN06, in06}, {ROW_IN07, in07}};
    bool ok = true;
    for (const auto& h : hooks) {
        ok = h.row >= 0 && patch::set_handler(static_cast<uint32_t>(h.row), h.fn) &&
             patch::set_site_armed(static_cast<uint32_t>(h.row), true) && ok;
    }
    return ok;
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view v;
    v.in06_armed = patch::trampoline(static_cast<uint32_t>(ROW_IN06)) != nullptr ? 1u : 0u;
    v.published = g_published;
    v.host = g_host;
    return v;
}

patch::record_slot<iso_record>& slot()
{
    return g_slot;
}

void test_publish(const iso_record& r)
{
    g_slot.publish(r);
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    forget_host();
    memset(&g_cursor, 0, sizeof(g_cursor));
    g_cursor_epoch = 0;
    g_budget = KB_BUDGET;
    g_variant = VARIANT_OK;
    g_active_beats_ago = RELEASE_BEATS + 1;
    g_cfg = {};
    g_flee_source = movement::flee_seats;
    g_flee_last = false;
    g_release_requested = 0;
    g_release_composed = 0;
    g_last_p1 = {};
}

int32_t test_budget()
{
    return g_budget;
}

void test_set_budget(int32_t value)
{
    g_budget = value;
}

void test_block_variant(uint32_t v)
{
    g_variant = v;
}

void test_flee_source(uint32_t (*source)(uint32_t epoch))
{
    g_flee_source = source != nullptr ? source : movement::flee_seats;
}

}  // namespace ilc::isolation
