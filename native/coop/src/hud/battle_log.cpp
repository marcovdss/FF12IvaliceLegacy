// Ivalice Legacy co-op module: the battle log's game-thread side (battle_log.h): the event ring, LG-01, the per-hit
// feed (HR-01, HR-02, HR-03, HR-05) and KR-01, the presentation and the log pass, the native log switch.
//
// Everything here runs on a game thread (LG-01 on whichever thread emits a message, the hit rows on the thread that
// applies a hit, HR-03 and KR-01 on whichever thread shows a number or a kill reward; the pass and replaces_native
// on the thread that draws the UI) or is pure. Nothing blocks, allocates, calls the C runtime or a game function,
// and the only game memory touched is read through the fault-safe probe: the emitter's argument block, and at HR-01
// and HR-02 the actors' handles and records, the hit slot's outcome byte, the action row's element, the target's
// affinities and the caster's weapon id. No handler writes a saved register, the flags or the resume slot.
#include "hud/battle_log.h"
#include "runtime/loopback.h"

#include "hud/battle_log_facts.h"
#include "hud/battle_log_internal.h"
#include "hud/hud_internal.h"
#include "hud/layout.h"
#include "hud/target_info.h"
#include "patch/site_table.h"
#include "runtime/probe.h"

namespace ilc::hud::battle_log {

namespace detail {

view_slot g_slot = {};
counters g_counters = {};
volatile uint32_t g_attached = 0;
volatile uint32_t g_enabled = 1;
volatile uint32_t g_epoch = 0;
volatile uint64_t g_published_ms = 0;

}  // namespace detail

namespace {

namespace f = facts;
using ilc::hud::detail::copy_bytes;
using ilc::hud::detail::zero_bytes;
namespace corner = target_info::corner;

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

constexpr int32_t ROW_FEED = find_row("LG-01");
static_assert(ROW_FEED >= 0 && patch::SITES[ROW_FEED].policy == ILC_SITE_POLICY_PATCH &&
                  patch::SITES[ROW_FEED].kind == ILC_SITE_KIND_DETOUR &&
                  patch::SITES[ROW_FEED].subsystem == ILC_SUBSYS_RENDER &&
                  patch::SITES[ROW_FEED].rva == f::FN_MESSAGE - f::PINNED_BASE,
              "the feed is a RENDER detour at the message emitter's entry");

constexpr int32_t ROW_BEGIN = find_row("HR-01");
constexpr int32_t ROW_OUTCOME = find_row("HR-02");
constexpr int32_t ROW_NUMBER = find_row("HR-03");
constexpr int32_t ROW_END = find_row("HR-05");
constexpr int32_t ROW_KILL = find_row("KR-01");

constexpr bool hook_row(int32_t row, uint64_t va)
{
    return row >= 0 && patch::SITES[row].policy == ILC_SITE_POLICY_PATCH &&
           patch::SITES[row].kind == ILC_SITE_KIND_DETOUR && patch::SITES[row].subsystem == ILC_SUBSYS_RENDER &&
           (patch::SITES[row].flags & ILC_SITE_FLAG_OPTIONAL) != 0 && patch::SITES[row].rva == va - f::PINNED_BASE;
}
static_assert(hook_row(ROW_BEGIN, f::FN_HIT) && hook_row(ROW_OUTCOME, f::HIT_OUTCOME_SITE) &&
                  hook_row(ROW_NUMBER, f::FN_NUMBER) && hook_row(ROW_END, f::HIT_EXIT) &&
                  hook_row(ROW_KILL, f::FN_KILL_POPUP),
              "the per-hit feed and the kill reward are OPTIONAL RENDER detours at their sites");

// No row of ours (any policy) covers the call another mod rewrites next to HR-02's window.
constexpr bool clear_of_other_patch()
{
    const uint32_t a0 = static_cast<uint32_t>(f::HIT_OTHER_PATCH - f::PINNED_BASE);
    const uint32_t a1 = a0 + f::HIT_OTHER_PATCH_LEN;
    for (uint32_t row = 0; row < patch::SITE_COUNT; ++row) {
        const patch::site& s = patch::SITES[row];
        const uint32_t b1 = s.rva + (s.policy == ILC_SITE_POLICY_PATCH ? s.hook_len : patch::checked_len(s));
        if (s.rva < a1 && a0 < b1) {
            return false;
        }
    }
    return true;
}
static_assert(clear_of_other_patch(), "no row covers the call at 0x30760a another mod rewrites");

// The chain read's own verify rows: compared by the sampler before it reads the globals they name (INFO in the
// patch layer: a mismatch there refuses nothing and only turns the chain off here).
constexpr int32_t ROW_CHAIN_RESET = find_row("CH-V1");
constexpr int32_t ROW_CHAIN_SHOWN = find_row("CH-V2");
static_assert(ROW_CHAIN_RESET >= 0 && patch::SITES[ROW_CHAIN_RESET].kind == ILC_SITE_KIND_VERIFY &&
                  patch::SITES[ROW_CHAIN_RESET].policy == ILC_SITE_POLICY_INFO &&
                  patch::SITES[ROW_CHAIN_RESET].rva == f::FN_CHAIN_RESET - f::PINNED_BASE &&
                  ROW_CHAIN_SHOWN >= 0 && patch::SITES[ROW_CHAIN_SHOWN].kind == ILC_SITE_KIND_VERIFY &&
                  patch::SITES[ROW_CHAIN_SHOWN].policy == ILC_SITE_POLICY_INFO &&
                  patch::SITES[ROW_CHAIN_SHOWN].rva == f::FN_CHAIN_SHOWN - f::PINNED_BASE,
              "the chain read's verify rows");

// The globals the chain reset's stores and the display helper's load name (rel32 from the end of each instruction),
// so the facts and the pinned bytes cannot drift apart.
constexpr uint64_t rip_target(int32_t row, uint32_t at, uint32_t end)
{
    const patch::site& s = patch::SITES[row];
    const uint32_t d = static_cast<uint32_t>(static_cast<uint8_t>(s.expected[at])) |
                       static_cast<uint32_t>(static_cast<uint8_t>(s.expected[at + 1])) << 8 |
                       static_cast<uint32_t>(static_cast<uint8_t>(s.expected[at + 2])) << 16 |
                       static_cast<uint32_t>(static_cast<uint8_t>(s.expected[at + 3])) << 24;
    return f::PINNED_BASE + s.rva + end + static_cast<uint64_t>(static_cast<int64_t>(static_cast<int32_t>(d)));
}
static_assert(rip_target(ROW_CHAIN_RESET, 0x1A, 0x1E) == f::CHAIN_LEVEL,
              "0x3183b7: mov [0x22c3158], rax (the chain level and count, one qword)");
static_assert(rip_target(ROW_CHAIN_SHOWN, 0x02, 0x06) == f::CHAIN_SHOWN_PLUS, "0x229e40: mov eax, [0x1e02814]");

void bump(volatile uint64_t& c) { __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED); }

// ---- the ring (lock-free: producers claim a sequence number, stamp the entry odd, fill it, stamp it even) ------------
event g_ring[RING];
volatile uint32_t g_write = 0;     // the last sequence number handed out
uint32_t g_read = 0;               // the last sequence number drained (the co-op thread only)
volatile uint64_t g_lost = 0;
volatile uint64_t g_torn = 0;
volatile uint64_t g_unreadable = 0;

// ---- the hit bracket: written only by the thread that applies hits (HR-01 opens, HR-05 closes) ----------------------
volatile uint32_t g_hit_rows = 0;      // hit_row bits that act (attach; host tests)
volatile uint32_t g_serial_next = 0;   // serials handed out
volatile uint32_t g_open = 0;          // the open bracket's serial (0 = none)
volatile uint64_t g_open_thread = 0;   // the thread that opened it
volatile uint64_t g_open_ms = 0;

// ---- the pass's copy of the view (the game thread that draws the UI; the hook is not re-entered) --------------------
view g_pass;

// ---- colours (0x80 = 1.0 per channel), our own choices ----------------------------------------------------------------
constexpr rgba TONES[TONE_COUNT] = {
    {0x74, 0x76, 0x7C, 0x80},   // text
    {0x80, 0x80, 0x80, 0x80},   // seat 1..4: replaced by the seat colours
    {0x80, 0x80, 0x80, 0x80},
    {0x80, 0x80, 0x80, 0x80},
    {0x80, 0x80, 0x80, 0x80},
    {0x62, 0x66, 0x6E, 0x80},   // gambit ally (silver)
    {0x78, 0x70, 0x68, 0x80},   // foe
    {0x80, 0x6C, 0x50, 0x80},   // damage dealt
    {0x78, 0x2D, 0x2D, 0x80},   // damage taken (red)
    {0x43, 0x72, 0x62, 0x80},   // healing
    {0x6A, 0x78, 0x5C, 0x80},   // reward
    {0x50, 0x54, 0x5C, 0x80},   // dim
    {0x80, 0x72, 0x5C, 0x80},   // important
    {0x80, 0x69, 0x3D, 0x80},   // CRIT
    {0x60, 0x78, 0x3C, 0x80},   // WEAK
    {0x5C, 0x60, 0x68, 0x80},   // MISS, BLOCK, PARRY, IMMUNE
    {0x50, 0x72, 0x60, 0x80},   // HOLDING
};
constexpr rgba DOT_TINT[9] = {
    {0x60, 0x60, 0x68, 0x80},   // none
    {0x80, 0x80, 0x80, 0x80},   // seat 1..4: replaced by the seat colours
    {0x80, 0x80, 0x80, 0x80},
    {0x80, 0x80, 0x80, 0x80},
    {0x80, 0x80, 0x80, 0x80},
    {0x60, 0x64, 0x6C, 0x80},   // gambit: silver
    {0x74, 0x40, 0x1D, 0x80},   // foe: copper
    {0x47, 0x70, 0x3D, 0x80},   // reward: green
    {0x50, 0x50, 0x58, 0x80},   // system: grey
};
constexpr rgba ICON_OFF_TINT = {0x40, 0x40, 0x44, 0x48};

// Our own short names of the 32 statuses (status bit order), for the expanded view's text.
const char* const STATUS_NAMES[32] = {
    "KO",      "Stone",   "Petrify", "Stop",      "Sleep",    "Confuse", "Doom",  "Blind",
    "Poison",  "Silence", "Sap",     "Oil",       "Reverse",  "Disable", "Immobilize", "Slow",
    "Disease", "Lure",    "Protect", "Shell",     "Haste",    "Bravery", "Faith", "Reflect",
    "Invisible", "Regen", "Float",   "Berserk",   "Bubble",   "HP Critical", "Libra", "X-Zone"};

const char* const FILTER_LABELS[FILTER_COUNT] = {"", "PARTY", "FOES", "STATUSES", "MINE"};

rgba unpack(uint32_t c)
{
    return {static_cast<uint8_t>(c), static_cast<uint8_t>(c >> 8), static_cast<uint8_t>(c >> 16),
            static_cast<uint8_t>(c >> 24)};
}

rgba with_alpha(rgba c, uint8_t alpha)
{
    c.a = static_cast<uint8_t>((static_cast<uint32_t>(c.a) * alpha) / 0x80);
    return c;
}

rgba tone_colour(const view& v, uint8_t tone)
{
    if (tone >= TONE_SEAT1 && tone <= TONE_SEAT1 + 3) {
        const rgba c = unpack(v.seat_colour[tone - TONE_SEAT1]);
        return c.a != 0 ? c : TONES[TONE_TEXT];
    }
    return tone < TONE_COUNT ? TONES[tone] : TONES[TONE_TEXT];
}

rgba dot_colour(const view& v, uint8_t dot)
{
    if (dot >= 1 && dot <= 4) {
        const rgba c = unpack(v.seat_colour[dot - 1]);
        return c.a != 0 ? c : DOT_TINT[0];
    }
    return dot < 9 ? DOT_TINT[dot] : DOT_TINT[0];
}

uint32_t length(const char* s, uint32_t cap)
{
    uint32_t n = 0;
    while (n < cap && s[n] != '\0') {
        ++n;
    }
    return n;
}

// ---- runs: one line as coloured pieces of text (struct runs: battle_log.h) ----------------------------------------------

void put(runs& r, const char* s, uint8_t tone)
{
    const uint32_t len = length(s, RUN_TEXT);
    if (len == 0) {
        return;
    }
    if (r.n == 0 || r.tone[r.n - 1] != tone) {
        if (r.n == RUNS) {
            tone = r.tone[r.n - 1];   // out of runs: the rest keeps the last colour
        } else {
            r.start[r.n] = static_cast<uint8_t>(r.used);
            r.len[r.n] = 0;
            r.tone[r.n] = tone;
            ++r.n;
        }
    }
    for (uint32_t k = 0; k < len && r.used + 1 < RUN_TEXT; ++k) {
        r.text[r.used++] = s[k];
        ++r.len[r.n - 1];
    }
    r.text[r.used] = '\0';
}

void put_number(runs& r, const char* prefix, uint32_t v, uint8_t tone)
{
    char digits[12];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + v % 10);
        v /= 10;
    } while (v != 0 && n < sizeof(digits));
    char text[16];
    uint32_t k = 0;
    for (; prefix[k] != '\0' && k < 3; ++k) {
        text[k] = prefix[k];
    }
    for (uint32_t d = 0; d < n; ++d) {
        text[k++] = digits[n - 1 - d];
    }
    text[k] = '\0';
    put(r, text, tone);
}

uint8_t main_tone(const line& l)
{
    switch (l.cls) {
    case LK_KILL:
    case LK_REWARD: return TONE_REWARD;
    case LK_KO: return TONE_HURT;
    case LK_REVIVE: return TONE_HEAL;
    default: break;
    }
    if ((l.flags & LF_IMPORTANT) != 0) {
        return TONE_IMPORTANT;
    }
    return TONE_TEXT;
}

bool has_target(const line& l)
{
    return l.target_count != 0 && l.target != l.actor;
}

void put_targets(runs& r, const line& l, const char* joiner)
{
    if (!has_target(l)) {
        return;
    }
    put(r, joiner, TONE_TEXT);
    put(r, l.target_name, l.target_tone);
    if (l.target_count > 1) {
        put_number(r, " +", static_cast<uint32_t>(l.target_count - 1), TONE_TEXT);
    }
}

void put_amounts(runs& r, const line& l)
{
    for (uint32_t k = 0; k < l.amount_count; ++k) {
        const int32_t a = l.amounts[k];
        put(r, k == 0 ? "  " : ", ", TONE_TEXT);
        if (a > 0) {
            put_number(r, "+", static_cast<uint32_t>(a), TONE_HEAL);
        } else if ((l.flags & LF_PARTY_HIT) != 0) {
            put_number(r, "-", static_cast<uint32_t>(-static_cast<int64_t>(a)), TONE_HURT);
        } else {
            put_number(r, "", static_cast<uint32_t>(-static_cast<int64_t>(a)), TONE_DAMAGE);
        }
    }
    if (l.count > l.amount_count && l.amount_count != 0) {
        put(r, ", ...", TONE_TEXT);
    }
}

void put_tags(runs& r, const line& l)
{
    if ((l.tags & TAG_CRIT) != 0) {
        put(r, "  CRIT", TONE_CRIT);
    }
    if ((l.tags & TAG_WEAK) != 0) {
        put(r, "  WEAK", TONE_WEAK);
    }
    if ((l.tags & TAG_MISS) != 0) {
        put(r, "  MISS", TONE_MISS);
    }
    if ((l.tags & TAG_BLOCK) != 0) {
        put(r, "  BLOCK", TONE_MISS);
    }
    if ((l.tags & TAG_PARRY) != 0) {
        put(r, "  PARRY", TONE_MISS);
    }
    if ((l.tags & TAG_IMMUNE) != 0) {
        put(r, "  IMMUNE", TONE_MISS);
    }
    if ((l.tags & TAG_HOLDING) != 0) {
        put(r, "  HOLDING", TONE_HOLDING);
    }
}

bool same_word(const char* a, const char* b)
{
    uint32_t k = 0;
    for (; a[k] != '\0' && a[k] == b[k]; ++k) {
    }
    return a[k] == b[k];
}

// status_words: the icons as words (the expanded view and tests), else left to the caller as sprites.
void build(const line& l, runs& r, bool status_words)
{
    r.n = 0;
    r.used = 0;
    r.text[0] = '\0';
    const uint8_t tone = main_tone(l);
    switch (l.cls) {
    case LK_DAMAGE:
    case LK_HEAL:
        put(r, l.actor_name, l.actor_tone);
        if ((l.flags & LF_ATTACK) != 0) {
            put(r, " attacks", tone);
            put_targets(r, l, " ");
        } else if (l.body[0] != '\0') {
            put(r, ": ", TONE_TEXT);
            put(r, l.body, tone);
            put_targets(r, l, " on ");
        } else {
            put_targets(r, l, " on ");
        }
        if (l.count > 1 && (l.flags & LF_SINGLE_TARGET) != 0) {
            put_number(r, " x", l.count, TONE_TEXT);
        }
        put_amounts(r, l);
        break;
    case LK_ACTION:
        put(r, l.actor_name, l.actor_tone);
        put(r, " ", TONE_TEXT);
        put(r, l.extra, tone);
        put(r, " ", TONE_TEXT);
        put(r, l.body, tone);
        break;
    case LK_STATUS:
        put(r, l.actor_name, l.actor_tone);
        if (l.body[0] != '\0') {
            put(r, ": ", TONE_TEXT);
            put(r, l.body, tone);
        }
        put_targets(r, l, " on ");
        put_amounts(r, l);
        break;
    case LK_KO:
    case LK_REVIVE:
        put(r, l.actor_name, l.actor_tone);
        put(r, " ", TONE_TEXT);
        put(r, l.body, tone);
        put_amounts(r, l);
        break;
    case LK_KILL:
        put(r, l.actor_name, l.actor_tone);
        put(r, " ", TONE_TEXT);
        put(r, l.body, tone);
        // The reward first, then the chain, then the loot: a line cut at the log's edge loses the loot first.
        if ((l.reward & REWARD_EXP) != 0 && (l.exp != 0 || l.lp != 0)) {
            put_number(r, "  ", l.exp, tone);
            put(r, " EXP ", tone);
            put_number(r, "", l.lp, tone);
            put(r, " LP", tone);
        }
        if ((l.reward & REWARD_CHAIN) != 0 && l.chain != 0) {
            put(r, "  Chain ", tone);
            put_number(r, "", l.chain, tone);
            put(r, " Lv ", tone);   // our own wording: the game shows the level as the counter's colour
            put_number(r, "", static_cast<uint32_t>(l.chain_level) + 1u, tone);
        }
        if (l.extra[0] != '\0') {
            put(r, "  ", TONE_TEXT);
            put(r, l.extra, tone);
        }
        break;
    case LK_REWARD:
        if (l.actor_name[0] != '\0') {
            put(r, l.actor_name, l.actor_tone);
            put(r, " ", TONE_TEXT);
        }
        put(r, l.body, tone);
        if (l.extra[0] != '\0') {
            put(r, " ", TONE_TEXT);
            put(r, l.extra, tone);
        }
        break;
    case LK_STEAL:
        put(r, l.actor_name, l.actor_tone);
        put(r, " ", TONE_TEXT);
        put(r, l.body, tone);
        if (l.extra[0] != '\0') {
            put(r, " ", TONE_TEXT);
            put(r, l.extra, TONE_REWARD);
        }
        put_targets(r, l, " from ");
        break;
    default:
        if (l.actor_name[0] != '\0') {
            put(r, l.actor_name, l.actor_tone);
            put(r, " ", TONE_TEXT);
        }
        put(r, l.body, tone);
        if (l.extra[0] != '\0') {
            put(r, " (", TONE_DIM);
            put(r, l.extra, TONE_DIM);
            put(r, ")", TONE_DIM);
        }
        break;
    }
    if (status_words && l.cls != LK_KO) {   // a KO line says it in words already
        for (uint32_t k = 0; k < l.icon_count; ++k) {
            const uint8_t icon = l.icons[k];
            if ((icon & ICON_OFF) == 0 && same_word(STATUS_NAMES[icon & 0x1F], l.body)) {
                continue;   // "Penelo: Protect on Fran", not "... Protect"
            }
            put(r, " ", TONE_TEXT);
            put(r, STATUS_NAMES[icon & 0x1F], (icon & ICON_OFF) != 0 ? static_cast<uint8_t>(TONE_DIM) : tone);
            if ((icon & ICON_OFF) != 0) {
                put(r, " off", TONE_DIM);
            }
        }
    }
    r.tags = r.n;
    put_tags(r, l);
    if (r.tags > r.n) {
        r.tags = r.n;
    }
}

int32_t advance(uint32_t chars)
{
    return corner::text_width(chars, LINE_SCALE);
}

}  // namespace

// ---- the ring -------------------------------------------------------------------------------------------------------------
void ring_push(uint32_t msg, uint64_t key, uint64_t args_address, uint64_t now_ms)
{
    const uint32_t seq = __atomic_add_fetch(&g_write, 1u, __ATOMIC_ACQ_REL);
    event& e = g_ring[(seq - 1u) % RING];
    __atomic_store_n(&e.stamp, 2u * seq - 1u, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    e.msg = static_cast<uint16_t>(msg > 0xFFFF ? 0xFFFF : msg);
    e.time_ms = now_ms;
    e.key = key;
    uint16_t flags = 0;
    if (args_address != 0 && copy_from_caller(e.args, reinterpret_cast<const void*>(args_address), EVENT_ARGS)) {
        flags = EV_ARGS;
    } else {
        zero_bytes(e.args, EVENT_ARGS);
        __atomic_add_fetch(&g_unreadable, 1, __ATOMIC_RELAXED);
    }
    e.flags = flags;
    __atomic_store_n(&e.stamp, 2u * seq, __ATOMIC_RELEASE);
}

uint32_t ring_drain(event* out, uint32_t cap, uint32_t* lost)
{
    uint32_t lost_now = 0;
    const uint32_t w = __atomic_load_n(&g_write, __ATOMIC_ACQUIRE);
    if (w - g_read > RING) {
        lost_now += w - g_read - RING;   // lapped: those entries were overwritten before anyone read them
        g_read = w - RING;
    }
    uint32_t n = 0;
    while (g_read != w && n < cap) {
        const uint32_t seq = g_read + 1u;
        event& e = g_ring[(seq - 1u) % RING];
        const uint32_t first = __atomic_load_n(&e.stamp, __ATOMIC_ACQUIRE);
        if (first == 2u * seq - 1u || first < 2u * seq - 1u) {
            break;   // claimed, still being written (or not stamped yet): read it next time
        }
        if (first > 2u * seq) {
            ++lost_now;   // a later lap already took the slot
            ++g_read;
            continue;
        }
        copy_bytes(&out[n], &e, sizeof(event));
        __atomic_thread_fence(__ATOMIC_ACQUIRE);
        if (__atomic_load_n(&e.stamp, __ATOMIC_RELAXED) != first) {
            __atomic_add_fetch(&g_torn, 1, __ATOMIC_RELAXED);   // overwritten during the copy
            ++lost_now;
            ++g_read;
            continue;
        }
        out[n].stamp = first;
        ++n;
        ++g_read;
    }
    __atomic_add_fetch(&g_lost, static_cast<uint64_t>(lost_now), __ATOMIC_RELAXED);
    if (lost != nullptr) {
        *lost = lost_now;
    }
    return n;
}

ring_view ring_stats()
{
    ring_view v = {};
    v.written = __atomic_load_n(&g_write, __ATOMIC_ACQUIRE);
    v.read = g_read;
    v.lost = __atomic_load_n(&g_lost, __ATOMIC_RELAXED);
    v.torn = __atomic_load_n(&g_torn, __ATOMIC_RELAXED);
    v.unreadable = __atomic_load_n(&g_unreadable, __ATOMIC_RELAXED);
    return v;
}

// ---- LG-01: copy, then the original (always) -------------------------------------------------------------------------------
int32_t lg01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (__atomic_load_n(&detail::g_attached, __ATOMIC_ACQUIRE) == 0 ||
        __atomic_load_n(&detail::g_enabled, __ATOMIC_ACQUIRE) == 0) {
        bump(detail::g_counters.declined);
        patch::note_declined(static_cast<uint32_t>(ROW_FEED));
        return patch::RUN_ORIGINAL;
    }
    ring_push(static_cast<uint32_t>(c->rdx), c->rcx, c->r8, patch::now_ms());
    bump(detail::g_counters.pushed);
    return patch::RUN_ORIGINAL;
}

// ---- the per-hit feed and the kill reward: copy, then the original (always) ------------------------------------------------
uint64_t current_thread()
{
    uint64_t id;
    asm volatile("movq %%gs:0x48, %0" : "=r"(id));
    return id;
}

void ring_put(uint32_t msg, uint64_t key, const hook_payload& p, uint64_t now_ms)
{
    const uint32_t seq = __atomic_add_fetch(&g_write, 1u, __ATOMIC_ACQ_REL);
    event& e = g_ring[(seq - 1u) % RING];
    __atomic_store_n(&e.stamp, 2u * seq - 1u, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    e.msg = static_cast<uint16_t>(msg > 0xFFFF ? 0xFFFF : msg);
    e.time_ms = now_ms;
    e.key = key;
    copy_bytes(e.args, &p, EVENT_ARGS);
    e.flags = EV_ARGS;
    __atomic_store_n(&e.stamp, 2u * seq, __ATOMIC_RELEASE);
}

namespace {

// One fault-safe read of game memory (never address 0).
bool peek(uint64_t address, void* out, uint32_t n)
{
    return address != 0 && copy_from_caller(out, reinterpret_cast<const void*>(address), n);
}

// The handle of an actor ([actor+8]); sets `bit` in *known when it was read.
uint32_t handle_of(uint64_t actor, uint8_t bit, uint8_t* known)
{
    uint32_t h = 0;
    if (peek(actor + f::ACTOR_HANDLE, &h, sizeof(h))) {
        *known = static_cast<uint8_t>(*known | bit);
        return h;
    }
    return 0;
}

// The handler may act: the element is on and its row's bit is set (hooked and attached).
bool hit_row_acts(uint32_t bit, int32_t row)
{
    if (__atomic_load_n(&detail::g_enabled, __ATOMIC_ACQUIRE) != 0 &&
        (__atomic_load_n(&g_hit_rows, __ATOMIC_ACQUIRE) & bit) != 0) {
        return true;
    }
    bump(detail::g_counters.declined);
    patch::note_declined(static_cast<uint32_t>(row));
    return false;
}

// The bracket this thread has open, or 0.
uint32_t open_here(uint64_t self, uint64_t now)
{
    const uint32_t serial = __atomic_load_n(&g_open, __ATOMIC_ACQUIRE);
    if (serial == 0 || __atomic_load_n(&g_open_thread, __ATOMIC_ACQUIRE) != self) {
        return 0;
    }
    const uint64_t at = __atomic_load_n(&g_open_ms, __ATOMIC_ACQUIRE);
    return now >= at && now - at <= BRACKET_MS ? serial : 0;
}

}  // namespace

// HR-01, the entry of 0x307300 (cx = action, rdx = caster actor, r8 = target actor): opens this thread's bracket.
int32_t hr01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!hit_row_acts(HROW_BEGIN, ROW_BEGIN)) {
        return patch::RUN_ORIGINAL;
    }
    const uint64_t self = current_thread();
    const uint64_t now = patch::now_ms();
    uint32_t serial = __atomic_add_fetch(&g_serial_next, 1u, __ATOMIC_ACQ_REL);
    if (serial == 0) {
        serial = __atomic_add_fetch(&g_serial_next, 1u, __ATOMIC_ACQ_REL);
    }
    // A bracket left open (its exit not seen) is replaced: its later numbers no longer match.
    __atomic_store_n(&g_open, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open_thread, self, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open_ms, now, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open, serial, __ATOMIC_RELEASE);
    hook_payload p = {};
    p.serial = serial;
    p.action = static_cast<uint16_t>(c->rcx);
    p.actor = handle_of(c->rdx, HK_ACTOR, &p.known);
    p.target = handle_of(c->r8, HK_TARGET, &p.known);
    ring_put(EV_HIT_BEGIN, self, p, now);
    return patch::RUN_ORIGINAL;
}

// HR-02 at 0x307601 (rdi = caster actor, rbx = target actor, r15w = action, r14 = slot, r13 = action row, al = flags):
// the outcome, with what WEAK needs (the row's element, the target's affinities, the caster's weapon).
int32_t hr02(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!hit_row_acts(HROW_OUTCOME, ROW_OUTCOME)) {
        return patch::RUN_ORIGINAL;
    }
    const uint64_t self = current_thread();
    const uint64_t now = patch::now_ms();
    hook_payload p = {};
    p.serial = open_here(self, now);
    p.action = static_cast<uint16_t>(c->r15);
    p.flags = static_cast<uint8_t>(c->rax & 0x0F);
    p.actor = handle_of(c->rdi, HK_ACTOR, &p.known);
    p.target = handle_of(c->rbx, HK_TARGET, &p.known);
    if (peek(c->r14 + f::SLOT_OUTCOME, &p.outcome, 1)) {
        p.known = static_cast<uint8_t>(p.known | HK_OUTCOME);
    }
    if (c->r13 != 0 && peek(c->r13 + f::ROW_ELEMENT, &p.element, 1)) {
        p.known = static_cast<uint8_t>(p.known | HK_ELEMENT);
    }
    uint64_t records[2] = {};
    if (peek(c->rdi + f::ACTOR_BATTLE, &records[0], 8) && peek(c->rbx + f::ACTOR_BATTLE, &records[1], 8)) {
        p.actor_record = records[0];
        p.target_record = records[1];
        p.known = static_cast<uint8_t>(p.known | HK_RECORDS);
        uint8_t aff[4] = {};
        if (peek(records[1] + f::REC_AFFINITY, aff, sizeof(aff))) {
            p.affinity[0] = aff[f::AFF_WEAK];
            p.affinity[1] = aff[f::AFF_ABSORB];
            p.affinity[2] = aff[f::AFF_IMMUNE];
            p.known = static_cast<uint8_t>(p.known | HK_AFFINITY);
        }
        if (peek(records[0] + f::REC_WEAPON, &p.weapon, sizeof(p.weapon))) {
            p.known = static_cast<uint8_t>(p.known | HK_WEAPON);
        }
    }
    ring_put(EV_HIT_OUTCOME, self, p, now);
    bump(detail::g_counters.hits);
    return patch::RUN_ORIGINAL;
}

// HR-03, the entry of the number pop-up 0x3283d0 (rcx = actor, edx = amount, r8b = heal, r9b = MP). Recorded only from
// the thread whose bracket is open: the Forge's Lua-thread calls (and every number outside a hit) are counted, not
// copied.
int32_t hr03(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!hit_row_acts(HROW_NUMBER, ROW_NUMBER)) {
        return patch::RUN_ORIGINAL;
    }
    const uint64_t self = current_thread();
    const uint64_t now = patch::now_ms();
    const uint32_t serial = open_here(self, now);
    if (serial == 0) {
        bump(detail::g_counters.foreign);
        return patch::RUN_ORIGINAL;
    }
    hook_payload p = {};
    p.serial = serial;
    p.actor = handle_of(c->rcx, HK_ACTOR, &p.known);
    p.value = static_cast<int32_t>(static_cast<uint32_t>(c->rdx));
    p.value2 = static_cast<int32_t>(static_cast<uint8_t>(c->r8));
    p.kind = static_cast<uint8_t>(c->r9);
    ring_put(EV_HIT_NUMBER, self, p, now);
    bump(detail::g_counters.numbers);
    return patch::RUN_ORIGINAL;
}

// HR-05 at 0x307b91, the common exit: closes this thread's bracket.
int32_t hr05(patch::ctx* c, uint32_t epoch)
{
    (void)c;
    (void)epoch;
    if (!hit_row_acts(HROW_END, ROW_END)) {
        return patch::RUN_ORIGINAL;
    }
    const uint64_t self = current_thread();
    const uint32_t serial = __atomic_load_n(&g_open, __ATOMIC_ACQUIRE);
    if (serial == 0 || __atomic_load_n(&g_open_thread, __ATOMIC_ACQUIRE) != self) {
        return patch::RUN_ORIGINAL;   // nothing open here (the entry was missed, or another thread's)
    }
    __atomic_store_n(&g_open, 0u, __ATOMIC_RELEASE);
    hook_payload p = {};
    p.serial = serial;
    ring_put(EV_HIT_END, self, p, patch::now_ms());
    return patch::RUN_ORIGINAL;
}

// KR-01, the entry of the kill pop-up 0x28fb80 (ecx = victim handle, edx = EXP, r8d = LP): registers only.
int32_t kr01(patch::ctx* c, uint32_t epoch)
{
    (void)epoch;
    if (!hit_row_acts(HROW_KILL, ROW_KILL)) {
        return patch::RUN_ORIGINAL;
    }
    hook_payload p = {};
    p.actor = static_cast<uint32_t>(c->rcx);
    p.value = static_cast<int32_t>(static_cast<uint32_t>(c->rdx));
    p.value2 = static_cast<int32_t>(static_cast<uint32_t>(c->r8));
    ring_put(EV_KILL, current_thread(), p, patch::now_ms());
    bump(detail::g_counters.kills);
    return patch::RUN_ORIGINAL;
}

// ---- presentation ----------------------------------------------------------------------------------------------------------
uint8_t time_alpha(const line& l, uint64_t now, uint64_t life)
{
    static_assert(LINGER_MS == 2 * LIFE_MS, "an important line holds twice as long as an ordinary one");
    const uint64_t hold = (l.flags & LF_IMPORTANT) != 0 ? 2 * life : life;
    const uint64_t age = now > l.touched_ms ? now - l.touched_ms : 0;
    if (age <= hold) {
        return 0x80;
    }
    if (age >= hold + FADE_MS) {
        return 0;
    }
    return static_cast<uint8_t>((0x80u * (hold + FADE_MS - age)) / FADE_MS);
}

bool passes(const line& l, uint8_t filter, uint8_t filter_seat)
{
    switch (filter) {
    case FILTER_PARTY: return (l.categories & (CAT_PARTY | CAT_REWARD)) != 0;
    case FILTER_FOES: return (l.categories & CAT_FOE) != 0;
    case FILTER_STATUSES: return (l.categories & CAT_STATUS) != 0;
    case FILTER_MINE:
        return filter_seat >= 1 && filter_seat <= 4 && (l.seats >> (filter_seat - 1) & 1u) != 0;
    default: return true;
    }
}

uint32_t expanded_lines(const view& v)
{
    return v.expanded_lines == 0 || v.expanded_lines > EXPANDED_LINES ? EXPANDED_LINES : v.expanded_lines;
}

uint32_t compact_lines(const view& v)
{
    const uint32_t most = expanded_lines(v);
    const uint32_t n = v.compact_lines == 0 ? COMPACT_LINES
                       : (v.compact_lines > ILC_LOG_LINES_COMPACT_MAX ? ILC_LOG_LINES_COMPACT_MAX : v.compact_lines);
    return n < most ? n : most;
}

uint64_t life_ms(const view& v)
{
    return v.fade_s == 0 ? LIFE_MS : static_cast<uint64_t>(v.fade_s) * 1000u;
}

uint32_t select(const view& v, uint64_t now, uint8_t* index, uint8_t* alpha, uint32_t cap)
{
    const uint32_t count = v.line_count < HISTORY ? v.line_count : HISTORY;
    const bool expanded = (v.head.flags & VIEW_EXPANDED) != 0;
    const uint32_t want = expanded ? expanded_lines(v) : compact_lines(v);
    const uint64_t life = life_ms(v);
    uint8_t picked[EXPANDED_LINES];
    uint32_t n = 0;
    // Newest first: lines that pass the filter (and, compact, have not faded out).
    for (uint32_t k = count; k-- > 0 && n < want;) {
        const line& l = v.lines[k];
        if (l.id == 0 || !passes(l, v.filter, v.filter_seat) || (!expanded && time_alpha(l, now, life) == 0)) {
            continue;
        }
        picked[n++] = static_cast<uint8_t>(k);
    }
    if (!expanded) {
        // Important lines linger: one still alive but pushed out takes the place of the oldest ordinary line shown.
        uint32_t scan = n != 0 ? picked[n - 1] : count;
        for (uint32_t k = scan; k-- > 0;) {
            const line& l = v.lines[k];
            if (l.id == 0 || (l.flags & LF_IMPORTANT) == 0 || !passes(l, v.filter, v.filter_seat) ||
                time_alpha(l, now, life) == 0) {
                continue;
            }
            int32_t replace = -1;
            for (uint32_t j = n; j-- > 0;) {
                if ((v.lines[picked[j]].flags & LF_IMPORTANT) == 0) {
                    replace = static_cast<int32_t>(j);
                    break;
                }
            }
            if (replace < 0) {
                break;   // every line shown is important
            }
            for (uint32_t j = static_cast<uint32_t>(replace); j + 1 < n; ++j) {
                picked[j] = picked[j + 1];
            }
            picked[n - 1] = static_cast<uint8_t>(k);
        }
    }
    // Oldest first (newest at the bottom).
    for (uint32_t a = 1; a < n; ++a) {
        for (uint32_t b = a; b > 0 && picked[b - 1] > picked[b]; --b) {
            const uint8_t t = picked[b - 1];
            picked[b - 1] = picked[b];
            picked[b] = t;
        }
    }
    uint32_t out = 0;
    for (uint32_t k = 0; k < n && out < cap; ++k) {
        const line& l = v.lines[picked[k]];
        uint8_t a = expanded ? 0x80 : time_alpha(l, now, life);
        if (!expanded && (l.flags & LF_IMPORTANT) == 0) {
            const uint32_t from_newest = n - 1 - k;
            if (from_newest == 3) {
                a = static_cast<uint8_t>((static_cast<uint32_t>(a) * DIM_POS3) / 0x80);
            } else if (from_newest >= 4) {
                a = static_cast<uint8_t>((static_cast<uint32_t>(a) * DIM_POS4) / 0x80);
            }
        }
        index[out] = picked[k];
        alpha[out] = a;
        ++out;
    }
    return out;
}

bool header_shown(const view& v, uint64_t now)
{
    if ((v.head.flags & VIEW_EXPANDED) != 0) {
        return true;
    }
    const uint32_t count = v.line_count < HISTORY ? v.line_count : HISTORY;
    for (uint32_t k = 0; k < count; ++k) {
        const line& l = v.lines[k];
        if (l.id != 0 && passes(l, v.filter, v.filter_seat) &&
            (now <= l.touched_ms || now - l.touched_ms <= HEADER_HOLD_MS)) {
            return true;
        }
    }
    return false;
}

void build_runs(const line& l, runs& r, bool status_words)
{
    build(l, r, status_words);
}

uint8_t line_tone(const line& l)
{
    return main_tone(l);
}

const char* status_name(uint32_t status_id)
{
    return status_id < 32 ? STATUS_NAMES[status_id] : "";
}

uint32_t line_text(const line& l, char* out, uint32_t cap)
{
    runs r;
    build(l, r, true);
    uint32_t n = 0;
    for (; n + 1 < cap && n < r.used; ++n) {
        out[n] = r.text[n];
    }
    if (cap != 0) {
        out[n] = '\0';
    }
    return n;
}

uint32_t compose(canvas& cv, const view& v, uint64_t now, int16_t dy)
{
    // The room left (the canvas already keeps the open menu windows' floor back: canvas::reserve), less what is
    // still missing of MENU_RESERVE / TEXT_RESERVE on top of that floor, so the menus get more than their floor when
    // room is plenty. The frame driver kept LOG_FLOOR / LOG_TEXT_FLOOR back from the passes before this one
    // (log_floor_due), so room() holds at least the floor here.
    const uint32_t used0 = cv.view().count;
    const uint32_t room = cv.room();
    const uint32_t kept = cv.reserved_commands();
    const uint32_t extra = MENU_RESERVE > kept ? MENU_RESERVE - kept : 0;
    uint32_t budget = room > extra + LOG_FLOOR ? room - extra : (room < LOG_FLOOR ? room : LOG_FLOOR);
    if (budget > MAX_COMMANDS_LOG) {
        budget = MAX_COMMANDS_LOG;
    }
    const uint32_t text_room = cv.text_room();
    const uint32_t kept_text = cv.reserved_text();
    const uint32_t extra_text = TEXT_RESERVE > kept_text ? TEXT_RESERVE - kept_text : 0;
    const uint32_t text_budget = text_room > extra_text + LOG_TEXT_FLOOR
                                     ? text_room - extra_text
                                     : (text_room < LOG_TEXT_FLOOR ? text_room : LOG_TEXT_FLOOR);
    uint8_t index[EXPANDED_LINES];
    uint8_t alpha[EXPANDED_LINES];
    uint32_t n = select(v, now, index, alpha, EXPANDED_LINES);
    const uint32_t fit = layout::log_lines(dy, LINE_PITCH, LINES_DY, EXPANDED_LINES);
    if (n > fit) {   // the boss gauge moved the header down: the oldest lines go, nothing shrinks
        const uint32_t drop = n - fit;
        for (uint32_t k = 0; k < fit; ++k) {
            index[k] = index[k + drop];
            alpha[k] = alpha[k + drop];
        }
        n = fit;
    }
    if (n == 0 && !header_shown(v, now)) {
        return 0;
    }
    const bool expanded = (v.head.flags & VIEW_EXPANDED) != 0;
    uint32_t used = 0;
    uint32_t text_used = 0;
    if (budget >= 2) {
        char label[32] = "BATTLE LOG";
        if (v.filter != FILTER_ALL && v.filter < FILTER_COUNT) {
            uint32_t k = length(label, sizeof(label));
            const char* add = FILTER_LABELS[v.filter];
            label[k++] = ' ';
            label[k++] = '-';
            label[k++] = ' ';
            for (uint32_t j = 0; add[j] != '\0' && k + 1 < sizeof(label); ++j) {
                label[k++] = add[j];
            }
            if (v.filter == FILTER_MINE && v.filter_seat >= 1 && v.filter_seat <= 4 && k + 3 < sizeof(label)) {
                label[k++] = ' ';
                label[k++] = 'P';
                label[k++] = static_cast<char>('0' + v.filter_seat);
            }
            label[k] = '\0';
        }
        corner::header(cv, corner::RIGHT, static_cast<int16_t>(corner::HEADER_Y + dy), label, 0x80);
        used += 2;
        text_used += length(label, sizeof(label)) + 1;
    }
    const corner::box hb = corner::header_box(corner::RIGHT);
    const int16_t x0 = static_cast<int16_t>(hb.x);
    // Newest first, so a short budget drops the oldest lines; each line keeps its row (newest at the bottom).
    for (uint32_t k = n; k-- > 0;) {
        const line& l = v.lines[index[k]];
        const uint8_t a = alpha[k];
        if (a == 0) {
            continue;
        }
        const int16_t y = static_cast<int16_t>(corner::HEADER_Y + dy + LINES_DY + static_cast<int32_t>(k) * LINE_PITCH);
        runs r;
        build(l, r, expanded);
        const uint32_t icons = expanded ? 0 : l.icon_count;
        uint32_t cost = 1 + (expanded ? 1 : r.n) + icons;
        bool single = expanded;
        if (used + cost > budget || text_used + r.used + r.n > text_budget) {
            single = true;   // the line in one colour, no icons
            cost = 2;
            if (used + cost > budget || text_used + r.used + 1 > text_budget) {
                bump(detail::g_counters.capped);
                break;
            }
        }
        cv.sprite(ATLAS_TARGETLINE_P, 0, 4, 1, static_cast<int16_t>(x0 - DOT_SIZE / 2 + 6),
                  static_cast<int16_t>(y + 12 - DOT_SIZE / 2), with_alpha(dot_colour(v, l.dot), a), DOT_SIZE,
                  DOT_SIZE, DOT_CLUT);
        ++used;
        int32_t x = x0 + TEXT_DX;
        if (single) {
            if (!expanded) {
                build(l, r, true);   // the icons as words, since no sprites follow
            }
            const uint32_t fit = static_cast<uint32_t>((RIGHT_LIMIT - x) * 100 / (corner::ADVANCE_X100 * LINE_SCALE / 100));
            if (r.used > fit) {
                r.text[fit] = '\0';
            }
            cv.text(static_cast<int16_t>(x), y, r.text, with_alpha(tone_colour(v, main_tone(l)), a), LINE_SCALE);
            ++used;
            text_used += length(r.text, RUN_TEXT) + 1;
            continue;
        }
        for (uint32_t j = 0; j <= r.n; ++j) {
            if (j == r.tags) {
                // The status icons, between the text and the tags.
                for (uint32_t i = 0; i < icons; ++i) {
                    if (x + ICON_W > RIGHT_LIMIT) {
                        break;
                    }
                    const uint8_t icon = l.icons[i];
                    const bool off = (icon & ICON_OFF) != 0;
                    cv.sprite(ATLAS_PARTYTOP_4_C, 3, 0, static_cast<uint16_t>(icon & 0x1F), static_cast<int16_t>(x + 4),
                              static_cast<int16_t>(y + 1), with_alpha(off ? ICON_OFF_TINT : WHITE, a), ICON_W,
                              ICON_H);
                    ++used;
                    x += ICON_W + 4;
                }
            }
            if (j == r.n) {
                break;
            }
            const int32_t room_px = RIGHT_LIMIT - x;
            if (room_px <= advance(1)) {
                break;
            }
            char piece[RUN_TEXT];
            uint32_t len = r.len[j];
            const uint32_t fit = static_cast<uint32_t>(room_px * 100 / (corner::ADVANCE_X100 * LINE_SCALE / 100));
            if (len > fit) {
                len = fit;
            }
            for (uint32_t c = 0; c < len; ++c) {
                piece[c] = r.text[r.start[j] + c];
            }
            piece[len] = '\0';
            if (len == 0) {
                continue;
            }
            bool blank = true;
            for (uint32_t c = 0; c < len && blank; ++c) {
                blank = piece[c] == ' ';
            }
            if (!blank) {
                cv.text(static_cast<int16_t>(x), y, piece, with_alpha(tone_colour(v, r.tone[j]), a), LINE_SCALE);
                ++used;
                text_used += len + 1;
            }
            x += advance(len);
        }
    }
    return cv.view().count - used0;
}

// ---- the pass and the native log switch ----------------------------------------------------------------------------------
void draw(canvas& cv, const driver::pass_input& in)
{
    if (__atomic_load_n(&detail::g_enabled, __ATOMIC_ACQUIRE) == 0) {
        return;
    }
    if (!latest(g_pass) || in.snap == nullptr || g_pass.head.epoch != in.snap->head.epoch ||
        in.now_ms > g_pass.built_ms + STALE_MS || g_pass.built_ms > in.now_ms + STALE_MS) {
        bump(detail::g_counters.skipped);
        return;
    }
    if (compose(cv, g_pass, in.now_ms, layout::header_dy(in.layout, layout::boss_gauge(*in.snap))) != 0) {
        bump(detail::g_counters.drawn);
    }
}

bool replaces_native(uint32_t epoch, uint64_t now_ms)
{
    if (__atomic_load_n(&detail::g_enabled, __ATOMIC_ACQUIRE) == 0 || epoch == 0 ||
        __atomic_load_n(&detail::g_epoch, __ATOMIC_ACQUIRE) != epoch) {
        return false;
    }
    const uint64_t at = __atomic_load_n(&detail::g_published_ms, __ATOMIC_ACQUIRE);
    return at != 0 && now_ms >= at ? now_ms - at <= LIVE_MS : at > now_ms && at - now_ms <= LIVE_MS;
}

bool log_floor_due(uint32_t epoch, uint64_t now_ms)
{
    return replaces_native(epoch, now_ms);
}

void note_native_hidden()
{
    bump(detail::g_counters.native_hidden);
}

// ---- publication -------------------------------------------------------------------------------------------------------
bool latest(view& out)
{
    const uint32_t first = __atomic_load_n(&detail::g_slot.seq, __ATOMIC_ACQUIRE);
    if ((first & 1u) != 0) {
        return false;
    }
    const uint32_t ok = detail::g_slot.valid;
    copy_bytes(&out, const_cast<const view*>(&detail::g_slot.value), sizeof(view));
    __atomic_thread_fence(__ATOMIC_ACQUIRE);
    return __atomic_load_n(&detail::g_slot.seq, __ATOMIC_RELAXED) == first && ok == 1;
}

namespace detail {

void publish(const view& v_in)
{
    ILC_LOOPBACK_COPY_STATIC(view, FAM_LOG_VIEW, v_in, v);  // the seam (host tests: the loopback codec)
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    ilc::hud::detail::copy_bytes(const_cast<view*>(&g_slot.value), &v, sizeof(view));
    g_slot.valid = 1;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_epoch, v.head.epoch, __ATOMIC_RELEASE);
    __atomic_store_n(&g_published_ms, v.built_ms != 0 ? v.built_ms : 1, __ATOMIC_RELEASE);
    __atomic_add_fetch(&g_counters.published, 1, __ATOMIC_RELAXED);
}

void withdraw()
{
    const uint32_t odd = (g_slot.seq + 1u) | 1u;
    __atomic_store_n(&g_slot.seq, odd, __ATOMIC_RELAXED);
    __atomic_thread_fence(__ATOMIC_RELEASE);
    g_slot.valid = 0;
    __atomic_store_n(&g_slot.seq, odd + 1u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_epoch, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_published_ms, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_add_fetch(&g_counters.withdrawn, 1, __ATOMIC_RELAXED);
}

}  // namespace detail

const counters* stats()
{
    return &detail::g_counters;
}

// Host tests: the ring back to empty (the co-op thread side resets the rest).
void ring_test_reset()
{
    zero_bytes(g_ring, sizeof(g_ring));
    __atomic_store_n(&g_write, 0u, __ATOMIC_RELEASE);
    g_read = 0;
    __atomic_store_n(&g_lost, uint64_t{0}, __ATOMIC_RELAXED);
    __atomic_store_n(&g_torn, uint64_t{0}, __ATOMIC_RELAXED);
    __atomic_store_n(&g_unreadable, uint64_t{0}, __ATOMIC_RELAXED);
    __atomic_store_n(&g_hit_rows, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_serial_next, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open, 0u, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open_thread, uint64_t{0}, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open_ms, uint64_t{0}, __ATOMIC_RELEASE);
}

int32_t feed_row()
{
    return ROW_FEED;
}

void chain_rows(int32_t* reset, int32_t* shown)
{
    *reset = ROW_CHAIN_RESET;
    *shown = ROW_CHAIN_SHOWN;
}

int32_t hit_row_index(uint32_t bit)
{
    switch (bit) {
    case HROW_BEGIN: return ROW_BEGIN;
    case HROW_OUTCOME: return ROW_OUTCOME;
    case HROW_NUMBER: return ROW_NUMBER;
    case HROW_END: return ROW_END;
    case HROW_KILL: return ROW_KILL;
    default: return -1;
    }
}

void set_hit_rows(uint8_t rows)
{
    // An amount needs the bracket; the bracket needs both of its ends.
    if ((rows & (HROW_BEGIN | HROW_END)) != (HROW_BEGIN | HROW_END)) {
        rows = static_cast<uint8_t>(rows & ~(HROW_BEGIN | HROW_END | HROW_NUMBER));
    }
    __atomic_store_n(&g_hit_rows, static_cast<uint32_t>(rows), __ATOMIC_RELEASE);
    if ((rows & HROW_BEGIN) == 0) {
        __atomic_store_n(&g_open, 0u, __ATOMIC_RELEASE);
    }
}

uint8_t hit_rows()
{
    return static_cast<uint8_t>(__atomic_load_n(&g_hit_rows, __ATOMIC_ACQUIRE));
}

void test_set_bracket(uint32_t serial, uint64_t thread, uint64_t ms)
{
    __atomic_store_n(&g_open_thread, thread, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open_ms, ms, __ATOMIC_RELEASE);
    __atomic_store_n(&g_open, serial, __ATOMIC_RELEASE);
}

uint64_t test_ring_address()
{
    return reinterpret_cast<uint64_t>(&g_ring[0]);
}

uint64_t test_ring_write_address()
{
    return reinterpret_cast<uint64_t>(&g_write);
}

}  // namespace ilc::hud::battle_log
