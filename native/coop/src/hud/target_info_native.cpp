// Ivalice Legacy co-op module: the native TARGET INFO give-back and the target mode's aim (see target_info_native.h).
#include "hud/target_info_native.h"

#include "control/control.h"
#include "firewall/firewall_facts.h"
#include "hud/hud_internal.h"
#include "hud/snapshot_facts.h"
#include "hud/target_info.h"
#include "hud/target_info_internal.h"
#include "interaction/interaction.h"
#include "interaction/interaction_facts.h"
#include "patch/patch_engine.h"
#include "runtime/probe.h"

namespace ilc::hud::target_info::giveback {
namespace {

namespace gf = facts;

// The facts other modules already read, restated here: they must stay the same addresses.
static_assert(gf::RENDER_FLAGS == snapfacts::RENDER_FLAGS && gf::RENDER_UI == snapfacts::RENDER_UI,
              "the UI drawn flag is the snapshot's");
static_assert(gf::PAUSE_PRIMARY == ilc::firewall::facts::PAUSE_PRIMARY &&
                  gf::PAUSE_SECONDARY == ilc::firewall::facts::PAUSE_SECONDARY &&
                  gf::UI_MANAGER_PTR == ilc::firewall::facts::UI_MANAGER &&
                  gf::UI_OWNER + gf::OWNER_WINDOW == ilc::firewall::facts::UI_TARGET_INFO,
              "the pause pointers, the UI manager and the open-window pointer are the firewall's");
static_assert(gf::FRONTEND_ACTIVE == ilc::interaction::facts::FRONTEND_ACTIVE &&
                  gf::ENTRY_ACTOR == ilc::interaction::facts::E_ACTOR &&
                  gf::HANDLE_REGISTRY_SHIFT == ilc::interaction::facts::HANDLE_REGISTRY_SHIFT,
              "the frontend flag and the registry layout are the interaction module's");
static_assert(gf::ACTOR_BATTLE == snapfacts::ACTOR_BATTLE, "the battle record pointer is the snapshot's");

// ---- the TI-V rows ------------------------------------------------------------------------------------------------
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

constexpr int32_t PIN[PIN_ROWS] = {find_row("TI-V1"), find_row("TI-V2"), find_row("TI-V3"),
                                   find_row("TI-V4"), find_row("TI-V5"), find_row("TI-V6"),
                                   find_row("TI-V7"), find_row("TI-V8"), find_row("TI-V9")};
constexpr uint32_t PIN_RVA[PIN_ROWS] = {0x19E290, 0x19E4D4, 0x1A0F49, 0x19FD40, 0x19FEC1,
                                        0x2388CB, 0x2A9432, 0x124865, 0x16FE40};
constexpr uint32_t PIN_BYTES_MAX = 80;

constexpr bool pins_are_info_rows()
{
    for (uint32_t k = 0; k < PIN_ROWS; ++k) {
        if (PIN[k] < 0) {
            return false;
        }
        const patch::site& s = patch::SITES[PIN[k]];
        if (s.kind != ILC_SITE_KIND_VERIFY || s.policy != ILC_SITE_POLICY_INFO || s.rva != PIN_RVA[k] ||
            s.length > PIN_BYTES_MAX || s.subsystem != ILC_SUBSYS_RENDER) {
            return false;
        }
    }
    return true;
}
static_assert(pins_are_info_rows(), "TI-V1..TI-V9: verify-only INFO rows (a mismatch refuses nothing)");

constexpr uint8_t pin_byte(uint32_t k, uint32_t at)
{
    return static_cast<uint8_t>(patch::SITES[PIN[k]].expected[at]);
}
constexpr uint32_t pin_u16(uint32_t k, uint32_t at)
{
    return static_cast<uint32_t>(pin_byte(k, at)) | static_cast<uint32_t>(pin_byte(k, at + 1)) << 8;
}
// The global a RIP-relative operand names (disp32 at `at`, instruction end at `end`).
constexpr uint64_t rip_target(uint32_t k, uint32_t at, uint32_t end)
{
    const uint32_t d = pin_u16(k, at) | pin_u16(k, at + 2) << 16;
    return gf::PINNED_BASE + patch::SITES[PIN[k]].rva + end +
           static_cast<uint64_t>(static_cast<int64_t>(static_cast<int32_t>(d)));
}
// So the facts and the pinned bytes cannot drift apart.
static_assert(pin_u16(0, 8) == gf::OWNER_STATE && pin_byte(0, 12) == gf::STATE_PENDING &&
                  pin_u16(0, 42) == gf::OWNER_WINDOW && pin_u16(0, 54) == gf::WINDOW_TI_FLAGS &&
                  pin_byte(0, 59) == gf::TI_CLOSING && pin_byte(0, 63) == gf::TI_INVALID,
              "0x2be290: test owner+0x57e bit 0; owner+0x570; window+0x280 bits 0 and 2");
static_assert(pin_u16(1, 3) == gf::HUD_OWNER, "0x2be4d4: lea rcx, [hud + 0xac0]; call 0x2be290");
static_assert(pin_u16(2, 3) == gf::OWNER_ID && pin_u16(2, 27) == gf::OWNER_STATE,
              "0x2c0f49: the creator reads owner+0x578 and owner+0x57e");
static_assert(pin_u16(3, 3) == gf::WINDOW_TI_FLAGS && pin_byte(3, 14) == gf::TI_INVALID,
              "0x2bfd40: window+0x280 bit 2 skips the update");
static_assert(pin_u16(4, 23) == gf::WINDOW_TI_FLAGS && pin_byte(4, 27) == gf::TI_INVALID,
              "0x2bfec1: or word [window+0x280], 4 when the id no longer resolves");
static_assert(pin_byte(5, 4) == 20 && pin_byte(5, 7) == gf::HANDLE_REGISTRY_SHIFT,
              "0x3588cb: generation from bit 20, registry from bit 16");
static_assert(rip_target(6, 15, 19) == gf::TMODE_PTR && pin_u16(6, 21) == gf::TMODE_TARGET,
              "0x3c9432: mov rax, [0x2b47730]; cmp [rax+0xc4], ebx");
static_assert(pin_byte(7, 2) == gf::WINDOW_FLAGS && pin_byte(7, 3) == static_cast<uint8_t>(~gf::WINDOW_FOCUS),
              "0x244865: and dword [rcx+0x40], ~0x10 (focus lost)");
static_assert(rip_target(8, 17, 21) == gf::UI_MANAGER_PTR && pin_u16(8, 42) == gf::UI_HUD,
              "0x28fe40: mov rcx, [0x209be80]; add rcx, 0x8fa0");

// ---- state ----------------------------------------------------------------------------------------------------------
counters g_counters;
uintptr_t g_base = 0;                     // 0 = the pinned base
volatile uint32_t g_pins = PINS_UNKNOWN;  // co-op thread writes, any thread reads
volatile int32_t g_pin_row = -1;
volatile int32_t g_force_pins = -1;
volatile int32_t g_force_live = -1;
volatile int32_t g_force_gate = -1;
uint64_t g_pins_ms = 0;                   // co-op thread only
bool g_pins_once = false;

// Game thread only.
struct pending_t {
    bool active;
    uint32_t target;
    uint32_t ticks;
    uintptr_t ui;
    uintptr_t tmode;
};
struct latch_t {
    bool valid;
    uint32_t target;
    uint32_t epoch;
};
struct state_t {
    bool live;
    bool seen;                // a tick ran (live / off times are meaningful)
    bool gate_shut;           // the last tick found the gate shut (F8, RENDER off, DRY)
    uint64_t live_from;       // ours live since (while live)
    uint64_t off_from;        // ours not live since (while not live)
    pending_t pending;
    latch_t given;            // given back in this target-mode session
    latch_t rejected;         // target that did not resolve (counted once)
};
state_t g;
volatile uint32_t g_phase = PHASE_IDLE;
volatile uint32_t g_target = 0;
volatile uint32_t g_live_seen = 0;

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

uintptr_t base() { return g_base != 0 ? g_base : static_cast<uintptr_t>(gf::PINNED_BASE); }
uintptr_t global(uint64_t va) { return base() + static_cast<uintptr_t>(va - gf::PINNED_BASE); }

template <typename T>
bool load(uintptr_t address, T* out)
{
    *out = 0;
    return address != 0 && load_game(out, reinterpret_cast<const void*>(address), sizeof(T));
}

template <typename T>
bool store(uintptr_t address, T value)
{
    return address != 0 && store_game(reinterpret_cast<void*>(address), &value, sizeof(T));
}

bool pins_now()
{
    const int32_t forced = g_force_pins;
    if (forced >= 0) {
        return forced == PINS_OK;
    }
    return __atomic_load_n(&g_pins, __ATOMIC_ACQUIRE) == PINS_OK;
}

// F8 / co-op off means the give-back writes nothing. It may act only while co-op is ON and the
// RENDER subsystem is live (LIVE mode, armed, bit set, not faulted). With the gate shut, the native window returns
// at the next target change, as it does without the module.
bool gate_open()
{
    const int32_t forced = g_force_gate;
    if (forced >= 0) {
        return forced != 0;
    }
    // Read once per tick: F8 landing just after this read lets at most this one tick's writes through (harmless:
    // the next tick sees the gate shut and writes nothing more).
    return control::current_state() == control::STATE_ON && patch::subsystem_live(ILC_SUBSYS_RENDER);
}

bool live_now(uint64_t now)
{
    const int32_t forced = g_force_live;
    if (forced >= 0) {
        return forced != 0;
    }
    return native_hidden_now(now);
}

// The target mode's window and target while it holds input focus (0 otherwise).
uint32_t focused_target(uintptr_t* tmode_out)
{
    uintptr_t tmode = 0;
    uint32_t flags = 0;
    uint32_t target = 0;
    *tmode_out = 0;
    if (!load(global(gf::TMODE_PTR), &tmode) || tmode == 0 || !load(tmode + gf::WINDOW_FLAGS, &flags) ||
        (flags & gf::WINDOW_FOCUS) == 0 || !load(tmode + gf::TMODE_TARGET, &target)) {
        return 0;
    }
    *tmode_out = tmode;
    return target;
}

// Registry 0, the resolver's checks (generation included), an actor with a battle record.
bool resolves(session::game_reader& r, uint32_t target)
{
    if (target == 0 || ((target >> gf::HANDLE_REGISTRY_SHIFT) & 0x0F) != 0) {
        return false;
    }
    const uintptr_t entry = interaction::resolve_handle(r, target);
    uintptr_t actor = 0;
    uintptr_t battle = 0;
    return entry != 0 && r.ptr(entry + gf::ENTRY_ACTOR, &actor) && actor != 0 &&
           r.ptr(actor + gf::ACTOR_BATTLE, &battle) && battle != 0;
}

void set_phase(uint32_t phase, uint32_t target)
{
    __atomic_store_n(&g_target, target, __ATOMIC_RELAXED);
    __atomic_store_n(&g_phase, phase, __ATOMIC_RELEASE);
}

// Withdraws our pending create: bit 0 cleared only while the owner still holds our target and no window is open.
void withdraw(uintptr_t ui)
{
    uint32_t id = 0;
    uint64_t window = 0;
    uint16_t state = 0;
    if (load(ui + gf::UI_OWNER + gf::OWNER_ID, &id) && id == g.pending.target &&
        load(ui + gf::UI_OWNER + gf::OWNER_WINDOW, &window) && window == 0 &&
        load(ui + gf::UI_OWNER + gf::OWNER_STATE, &state) && (state & gf::STATE_PENDING) != 0) {
        store<uint16_t>(ui + gf::UI_OWNER + gf::OWNER_STATE, static_cast<uint16_t>(state & ~gf::STATE_PENDING));
    }
    g.pending = {};
    set_phase(PHASE_IDLE, 0);
}

// Ours live long enough: any native window under it closes (the game's stale-target close), a pending create goes.
void rehide(uintptr_t ui)
{
    uint64_t window = 0;
    uint64_t handler = 0;
    uint16_t flags = 0;
    if (load(ui + gf::UI_OWNER + gf::OWNER_WINDOW, &window) && window != 0 && load(window, &handler) &&
        handler == global(gf::WINDOW_HANDLER) && load(window + gf::WINDOW_TI_FLAGS, &flags) &&
        (flags & gf::TI_CLOSING) == 0 && (flags & gf::TI_INVALID) == 0 &&
        store<uint16_t>(window + gf::WINDOW_TI_FLAGS, static_cast<uint16_t>(flags | gf::TI_INVALID))) {
        bump(g_counters.rehides);
    }
    uint16_t state = 0;
    if (load(ui + gf::UI_OWNER + gf::OWNER_STATE, &state) && (state & gf::STATE_PENDING) != 0 &&
        store<uint16_t>(ui + gf::UI_OWNER + gf::OWNER_STATE, static_cast<uint16_t>(state & ~gf::STATE_PENDING))) {
        bump(g_counters.pending_cleared);
    }
}

// Our pending create, while ours is still not live: done, superseded, cancelled or given up.
void follow(uintptr_t ui, uint32_t epoch)
{
    uint64_t window = 0;
    uint16_t state = 0;
    if (!load(ui + gf::UI_OWNER + gf::OWNER_WINDOW, &window) || !load(ui + gf::UI_OWNER + gf::OWNER_STATE, &state)) {
        g.pending = {};
        set_phase(PHASE_IDLE, 0);
        return;
    }
    if (window != 0) {
        bump(g_counters.opened);
        g.given = {true, g.pending.target, epoch};
        set_phase(PHASE_GIVEN, g.pending.target);
        g.pending = {};
        return;
    }
    if ((state & gf::STATE_PENDING) == 0) {
        // The game cleared it itself (a new request, the cursor's end): nothing of ours is left to withdraw.
        bump(g_counters.superseded);
        g.given = {true, g.pending.target, epoch};
        g.pending = {};
        set_phase(PHASE_IDLE, 0);
        return;
    }
    uintptr_t tmode = 0;
    const uint32_t target = focused_target(&tmode);
    if (target == 0 || tmode != g.pending.tmode || target != g.pending.target) {
        withdraw(ui);
        bump(g_counters.cancels);
        return;
    }
    if (++g.pending.ticks >= GIVEUP_TICKS) {
        const uint32_t target_given = g.pending.target;
        withdraw(ui);
        g.given = {true, target_given, epoch};   // once per target-mode session: no retry loop
        bump(g_counters.giveups);
    }
}

void try_open(uintptr_t ui, uint32_t epoch, uint32_t target, uintptr_t tmode)
{
    if ((g.given.valid && g.given.target == target && g.given.epoch == epoch) ||
        (g.rejected.valid && g.rejected.target == target && g.rejected.epoch == epoch)) {
        return;
    }
    uint64_t hud = 0;
    uint64_t window = 0;
    uint16_t state = 0;
    uint32_t render = 0;
    uint64_t pause_a = 0;
    uint64_t pause_b = 0;
    uint32_t frontend = 0;
    if (!load(ui + gf::UI_HUD, &hud) || hud != global(gf::HUD_HANDLER) ||
        !load(ui + gf::UI_OWNER + gf::OWNER_WINDOW, &window) || window != 0 ||
        !load(ui + gf::UI_OWNER + gf::OWNER_STATE, &state) || (state & gf::STATE_PENDING) != 0 ||
        !load(global(gf::RENDER_FLAGS), &render) || (render & gf::RENDER_UI) == 0 ||
        !load(global(gf::PAUSE_PRIMARY), &pause_a) || pause_a != 0 ||
        !load(global(gf::PAUSE_SECONDARY), &pause_b) || pause_b != 0 ||
        !load(global(gf::FRONTEND_ACTIVE), &frontend) || frontend == 1) {
        return;
    }
    session::game_reader r(base());
    if (!resolves(r, target)) {
        g.rejected = {true, target, epoch};
        bump(g_counters.rejected);
        return;
    }
    // The id first, then the flag: the HUD's next update creates the window for it.
    if (!store<uint32_t>(ui + gf::UI_OWNER + gf::OWNER_ID, target)) {
        return;
    }
    const uint16_t next =
        static_cast<uint16_t>((state & ~(gf::STATE_EXTRAS | gf::STATE_HANDLE_FORM)) | gf::STATE_PENDING);
    if (!store<uint16_t>(ui + gf::UI_OWNER + gf::OWNER_STATE, next)) {
        return;
    }
    g.pending = {true, target, 0, ui, tmode};
    set_phase(PHASE_PENDING, target);
    bump(g_counters.opens);
}

bool rows_stock(int32_t* bad)
{
    session::game_reader r(base());
    for (uint32_t k = 0; k < PIN_ROWS; ++k) {
        const patch::site& s = patch::SITES[PIN[k]];
        uint8_t bytes[PIN_BYTES_MAX];
        bool same = r.read(base() + s.rva, bytes, s.length);
        for (uint32_t i = 0; same && i < s.length; ++i) {
            same = bytes[i] == static_cast<uint8_t>(s.expected[i]);
        }
        if (!same) {
            *bad = PIN[k];
            return false;
        }
    }
    *bad = -1;
    return true;
}

}  // namespace

void attach(uintptr_t image_base)
{
    g_base = image_base;
}

void sample(uint64_t now_ms)
{
    if (g_pins_once && now_ms - g_pins_ms < PIN_RECHECK_MS) {
        return;
    }
    g_pins_once = true;
    g_pins_ms = now_ms;
    int32_t bad = -1;
    const bool ok = rows_stock(&bad);
    bump(g_counters.pin_checks);
    if (!ok) {
        bump(g_counters.pin_off);
    }
    __atomic_store_n(&g_pin_row, bad, __ATOMIC_RELAXED);
    __atomic_store_n(&g_pins, ok ? uint32_t{PINS_OK} : uint32_t{PINS_OFF}, __ATOMIC_RELEASE);
}

bool pins_ok()
{
    return pins_now();
}

uint32_t aim(session::game_reader& r)
{
    if (!pins_now()) {
        return 0;
    }
    uintptr_t tmode = 0;
    uint32_t flags = 0;
    uint32_t target = 0;
    if (!r.ptr(r.global(gf::TMODE_PTR), &tmode) || tmode == 0 || !r.u32(tmode + gf::WINDOW_FLAGS, &flags) ||
        (flags & gf::WINDOW_FOCUS) == 0 || !r.u32(tmode + gf::TMODE_TARGET, &target) || !resolves(r, target)) {
        return 0;
    }
    bump(g_counters.aims);
    return target;
}

void tick(uint64_t now_ms, uint32_t epoch)
{
    const bool live = live_now(now_ms);
    if (!g.seen || live != g.live) {
        g.seen = true;
        g.live = live;
        g.live_from = now_ms;
        g.off_from = now_ms;
    }
    g_live_seen = live ? 1u : 0u;
    if (!gate_open()) {
        g.gate_shut = true;
        // No reads, no writes. A create already requested is the game's to finish (the HUD update consumes it);
        // nothing is withdrawn. Our bookkeeping starts over once the gate opens again.
        if (g.pending.active || g_phase != PHASE_IDLE) {
            bump(g_counters.gated);
        }
        g.pending = {};
        g.given = {};
        g.rejected = {};
        if (g_phase != PHASE_IDLE) {
            set_phase(PHASE_IDLE, 0);
        }
        return;
    }
    if (g.gate_shut) {
        // The gate just opened (co-op back on, RENDER back on): ours needs a beat or two to go live again, so both
        // clocks start over. The 500 ms off rule then applies afresh and no native window flashes beside ours.
        g.gate_shut = false;
        g.live_from = now_ms;
        g.off_from = now_ms;
    }
    const bool armed = pins_now() && __atomic_load_n(&detail_ti::g_attached, __ATOMIC_ACQUIRE) != 0;
    if (!armed && !g.pending.active) {
        return;   // off (a TI-V row off, not compared yet, TI-01 not attached): no game memory is even read
    }
    uintptr_t ui = 0;
    if (!load(global(gf::UI_MANAGER_PTR), &ui) || ui == 0) {
        g.pending = {};   // no UI manager: nothing of ours can be left in it
        set_phase(PHASE_IDLE, 0);
        return;
    }
    if (g.pending.active && g.pending.ui != ui) {
        g.pending = {};   // a new UI manager: the HUD was created again (its creation clears the owner)
        set_phase(PHASE_IDLE, 0);
    }
    if (!armed) {
        if (g.pending.active) {
            withdraw(ui);
            bump(g_counters.cancels);
        }
        return;
    }
    if (live) {
        if (g.pending.active) {
            withdraw(ui);
            bump(g_counters.cancels);
        }
        if (now_ms - g.live_from >= REHIDE_MS) {
            rehide(ui);
            g.given = {};   // ours took over again: a later stop may give the window back again
            if (g_phase == PHASE_GIVEN) {
                set_phase(PHASE_IDLE, 0);
            }
        }
        return;
    }
    if (g.pending.active) {
        follow(ui, epoch);
        return;
    }
    uintptr_t tmode = 0;
    const uint32_t target = focused_target(&tmode);
    if (target == 0) {
        g.given = {};      // the target-mode session ended (or never began): the next one may be given back
        g.rejected = {};
        return;
    }
    if (now_ms - g.off_from < OFF_MS) {
        return;
    }
    try_open(ui, epoch, target, tmode);
}

const counters* stats()
{
    return &g_counters;
}

status_view status()
{
    status_view s = {};
    const int32_t forced = g_force_pins;
    s.pins = forced >= 0 ? static_cast<uint32_t>(forced) : __atomic_load_n(&g_pins, __ATOMIC_ACQUIRE);
    s.pin_row = __atomic_load_n(&g_pin_row, __ATOMIC_RELAXED);
    s.phase = static_cast<uint8_t>(__atomic_load_n(&g_phase, __ATOMIC_ACQUIRE));
    s.live = static_cast<uint8_t>(g_live_seen);
    s.target = __atomic_load_n(&g_target, __ATOMIC_RELAXED);
    return s;
}

void test_reset()
{
    hud::detail::zero_bytes(const_cast<counters*>(&g_counters), sizeof(counters));
    hud::detail::zero_bytes(&g, sizeof(g));
    g_base = 0;
    g_pins = PINS_UNKNOWN;
    g_pin_row = -1;
    g_force_pins = -1;
    g_force_live = -1;
    g_force_gate = -1;
    g_pins_ms = 0;
    g_pins_once = false;
    g_phase = PHASE_IDLE;
    g_target = 0;
    g_live_seen = 0;
}

void test_set_image_base(uintptr_t image_base)
{
    g_base = image_base;
}

void test_force_pins(int32_t state)
{
    g_force_pins = state;
}

void test_force_live(int32_t live)
{
    g_force_live = live;
}

void test_force_gate(int32_t open)
{
    g_force_gate = open;
}

bool test_gate_open()
{
    return gate_open();
}

int32_t test_pin_row(uint32_t k)
{
    return k < PIN_ROWS ? PIN[k] : -1;
}

}  // namespace ilc::hud::target_info::giveback
