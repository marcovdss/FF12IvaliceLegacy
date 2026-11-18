// Ivalice Legacy co-op module: target circles off (see circles.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "hud/circles.h"

#include <string.h>

#include "hud/native_facts.h"
#include "runtime/probe.h"

namespace ilc::hud::circles {
namespace {

namespace nf = native_facts;

counters g_counters;
volatile uint32_t g_state = CIRCLES_UNKNOWN;
volatile uint32_t g_site = SITE_UNREADABLE;
volatile uint32_t g_owned = 0;
uint8_t g_bytes[8];
uint64_t g_site_override = 0;
volatile uint32_t g_fail_cas = 0;
uint32_t g_restore_failures = 0;  // consecutive failed restores while the bytes are still ours
constexpr uint32_t RESTORE_TRIES = 8;   // then ownership is dropped (the circles stay off; nothing is retried)

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

uint64_t site_address()
{
    return g_site_override != 0 ? g_site_override : nf::CIRCLE_SITE;
}

uint64_t pack(const uint8_t b[8])
{
    uint64_t v = 0;
    memcpy(&v, b, 8);
    return v;
}

// Length of one recognised nop form at p (at most `left` bytes), or 0.
uint32_t nop_length(const uint8_t* p, uint32_t left)
{
    if (left >= 1 && p[0] == 0x90) {
        return 1;
    }
    if (left >= 2 && p[0] == 0x66 && p[1] == 0x90) {
        return 2;
    }
    if (left >= 3 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x00) {
        return 3;
    }
    if (left >= 4 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x40 && p[3] == 0x00) {
        return 4;
    }
    if (left >= 5 && p[0] == 0x0F && p[1] == 0x1F && p[2] == 0x44 && p[3] == 0x00 && p[4] == 0x00) {
        return 5;
    }
    return 0;
}

bool vision_form(const uint8_t w[8])
{
    // cmp eax,eax in either encoding, then nops filling the rest of the 7-byte instruction, je kept.
    if (!((w[0] == 0x39 || w[0] == 0x3B) && w[1] == 0xC0) || w[7] != nf::CIRCLE_JE) {
        return false;
    }
    uint32_t at = 2;
    while (at < nf::CIRCLE_LEN) {
        const uint32_t n = nop_length(w + at, nf::CIRCLE_LEN - at);
        if (n == 0) {
            return false;
        }
        at += n;
    }
    return true;
}

bool read_window(uint8_t out[8])
{
    return load_game(out, reinterpret_cast<const void*>(site_address()), 8);
}

// One locked compare-exchange of the aligned qword under a temporarily writable page. False: nothing changed.
bool exchange(uint64_t expected, uint64_t desired)
{
    bump(g_counters.writes);
    if (__atomic_exchange_n(&g_fail_cas, 0u, __ATOMIC_ACQ_REL) != 0) {
        return false;
    }
    void* at = reinterpret_cast<void*>(site_address());
    DWORD old = 0;
    if (!VirtualProtect(at, 8, PAGE_EXECUTE_READWRITE, &old)) {
        return false;
    }
    uint64_t seen = expected;
    const bool swapped = __atomic_compare_exchange_n(static_cast<uint64_t*>(at), &seen, desired, false,
                                                     __ATOMIC_SEQ_CST, __ATOMIC_SEQ_CST);
    DWORD ignored = 0;
    VirtualProtect(at, 8, old, &ignored);
    FlushInstructionCache(GetCurrentProcess(), at, 8);
    return swapped;
}

void stock_and_ours(uint64_t* stock, uint64_t* ours)
{
    uint8_t b[8];
    memcpy(b, nf::CIRCLE_STOCK, 8);
    *stock = pack(b);
    b[nf::CIRCLE_IMM] = nf::CIRCLE_IMM_OFF;
    *ours = pack(b);
}

void publish(uint32_t state, uint32_t site, const uint8_t window[8])
{
    g_state = state;
    g_site = site;
    if (window != nullptr) {
        memcpy(g_bytes, window, 8);
    }
}

}  // namespace

uint32_t classify(const uint8_t w[8])
{
    if (memcmp(w, nf::CIRCLE_STOCK, 8) == 0) {
        return SITE_STOCK;
    }
    uint8_t ours[8];
    memcpy(ours, nf::CIRCLE_STOCK, 8);
    ours[nf::CIRCLE_IMM] = nf::CIRCLE_IMM_OFF;
    if (memcmp(w, ours, 8) == 0) {
        return SITE_OURS;
    }
    return vision_form(w) ? SITE_VISION : SITE_FOREIGN;
}

uint32_t ensure_off()
{
    bump(g_counters.looks);
    uint8_t w[8];
    if (!read_window(w)) {
        publish(CIRCLES_UNREADABLE, SITE_UNREADABLE, nullptr);
        return CIRCLES_UNREADABLE;
    }
    const uint32_t site = classify(w);
    switch (site) {
    case SITE_STOCK: {
        g_owned = 0;  // whatever we wrote before is gone (someone put the stock bytes back)
        uint64_t stock = 0;
        uint64_t ours = 0;
        stock_and_ours(&stock, &ours);
        if (!exchange(stock, ours)) {
            bump(g_counters.write_failed);
            read_window(w);
            publish(CIRCLES_WRITE_FAILED, classify(w), w);
            return CIRCLES_WRITE_FAILED;
        }
        g_owned = 1;
        bump(g_counters.applied);
        read_window(w);
        publish(CIRCLES_OFF_OURS, classify(w), w);
        return CIRCLES_OFF_OURS;
    }
    case SITE_OURS:
        if (g_owned != 0) {
            publish(CIRCLES_OFF_OURS, site, w);
            return CIRCLES_OFF_OURS;
        }
        bump(g_counters.adopted);
        publish(CIRCLES_OFF_ADOPTED, site, w);
        return CIRCLES_OFF_ADOPTED;
    case SITE_VISION:
        if (g_owned != 0) {
            g_owned = 0;  // Vision wrote over ours: its bytes now, and it restores them itself
            bump(g_counters.dropped);
        }
        bump(g_counters.adopted);
        publish(CIRCLES_OFF_ADOPTED, site, w);
        return CIRCLES_OFF_ADOPTED;
    default:
        if (g_owned != 0) {
            g_owned = 0;
            bump(g_counters.dropped);
        }
        bump(g_counters.foreign);
        publish(CIRCLES_FOREIGN, site, w);
        return CIRCLES_FOREIGN;
    }
}

int32_t restore()
{
    if (g_owned == 0) {
        return RESTORE_NOTHING;
    }
    uint8_t w[8];
    if (!read_window(w) || classify(w) != SITE_OURS) {
        g_owned = 0;
        bump(g_counters.dropped);
        publish(CIRCLES_UNKNOWN, read_window(w) ? classify(w) : SITE_UNREADABLE, w);
        return RESTORE_GONE;
    }
    uint64_t stock = 0;
    uint64_t ours = 0;
    stock_and_ours(&stock, &ours);
    if (!exchange(ours, stock)) {
        bump(g_counters.write_failed);
        if (read_window(w) && classify(w) != SITE_OURS) {
            g_owned = 0;  // changed under us: not ours any more
            g_restore_failures = 0;
            bump(g_counters.dropped);
            return RESTORE_GONE;
        }
        if (++g_restore_failures >= RESTORE_TRIES) {
            g_owned = 0;
            g_restore_failures = 0;
            bump(g_counters.dropped);
        }
        return RESTORE_FAILED;
    }
    g_owned = 0;
    g_restore_failures = 0;
    bump(g_counters.restored);
    read_window(w);
    publish(CIRCLES_ON, classify(w), w);
    return RESTORE_DONE;
}

bool owned()
{
    return g_owned != 0;
}

status_view status()
{
    status_view v;
    memset(&v, 0, sizeof(v));
    v.state = g_state;
    v.site = g_site;
    v.owned = g_owned != 0 ? 1 : 0;
    memcpy(v.bytes, g_bytes, sizeof(v.bytes));
    return v;
}

const counters* stats()
{
    return &g_counters;
}

void test_reset()
{
    memset(&g_counters, 0, sizeof(g_counters));
    g_state = CIRCLES_UNKNOWN;
    g_site = SITE_UNREADABLE;
    g_owned = 0;
    memset(g_bytes, 0, sizeof(g_bytes));
    g_fail_cas = 0;
    g_restore_failures = 0;
}

void test_set_site(uint64_t va)
{
    g_site_override = va;
}

void test_fail_cas(bool fail)
{
    g_fail_cas = fail ? 1u : 0u;
}

}  // namespace ilc::hud::circles
