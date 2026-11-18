// Ivalice Legacy co-op module: diagnostic lines in the loader's log (see log.h).
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include "runtime/log.h"

namespace ilc::log {

namespace {

constexpr char PREFIX[] = "Ivalice Legacy Co-op: ";
constexpr wchar_t LOGGER_MODULE[] = L"simpleLog.dll";
constexpr char LOGGER_EXPORT[] = "_log";

using logger_fn = void (*)(uint32_t level, const char* text);

struct entry {
    uint32_t level;
    char text[LINE_MAX];
};

SRWLOCK g_lock = SRWLOCK_INIT;
entry g_ring[RING];
uint32_t g_head = 0;    // next to drain
uint32_t g_count = 0;
volatile uint32_t g_verbose = 0;
counters g_counters;

// The sink: resolved once by the draining thread (a missing logger is looked up again at most once per 256 drains).
logger_fn g_logger = nullptr;
uint32_t g_lookups = 0;
sink_fn volatile g_test_sink = nullptr;
volatile uint32_t g_test_sink_set = 0;

void bump(volatile uint64_t& c)
{
    __atomic_add_fetch(&c, 1, __ATOMIC_RELAXED);
}

void copy_text(char* out, const char* in, uint32_t cap)
{
    uint32_t n = 0;
    for (; n + 1 < cap && in[n] != '\0'; ++n) {
        const char c = in[n];
        out[n] = (c == '\r' || c == '\n') ? ' ' : c;   // one entry, one log line
    }
    out[n] = '\0';
}

logger_fn sink()
{
    if (__atomic_load_n(&g_test_sink_set, __ATOMIC_ACQUIRE) != 0) {
        return reinterpret_cast<logger_fn>(g_test_sink);
    }
    if (g_logger == nullptr && (g_lookups++ & 0xFFu) == 0) {
        HMODULE module = GetModuleHandleW(LOGGER_MODULE);
        if (module != nullptr) {
            g_logger = reinterpret_cast<logger_fn>(reinterpret_cast<void*>(GetProcAddress(module, LOGGER_EXPORT)));
        }
    }
    return g_logger;
}

}  // namespace

line::line() : len(0)
{
    text[0] = '\0';
}

line& line::s(const char* str)
{
    if (str == nullptr) {
        return *this;
    }
    while (*str != '\0' && len + 1 < LINE_MAX) {
        text[len++] = *str++;
    }
    text[len] = '\0';
    return *this;
}

line& line::u(uint64_t value)
{
    char digits[24];
    uint32_t n = 0;
    do {
        digits[n++] = static_cast<char>('0' + value % 10);
        value /= 10;
    } while (value != 0);
    while (n > 0 && len + 1 < LINE_MAX) {
        text[len++] = digits[--n];
    }
    text[len] = '\0';
    return *this;
}

line& line::i(int64_t value)
{
    if (value < 0) {
        s("-");
        return u(static_cast<uint64_t>(0) - static_cast<uint64_t>(value));
    }
    return u(static_cast<uint64_t>(value));
}

line& line::x(uint64_t value)
{
    char digits[20];
    uint32_t n = 0;
    do {
        digits[n++] = "0123456789abcdef"[value & 0xF];
        value >>= 4;
    } while (value != 0);
    s("0x");
    while (n > 0 && len + 1 < LINE_MAX) {
        text[len++] = digits[--n];
    }
    text[len] = '\0';
    return *this;
}

bool post(uint32_t lvl, const line& l, bool is_verbose)
{
    return post(lvl, l.text, is_verbose);
}

bool post(uint32_t lvl, const char* text, bool is_verbose)
{
    if (text == nullptr) {
        return false;
    }
    if (is_verbose && __atomic_load_n(&g_verbose, __ATOMIC_ACQUIRE) == 0) {
        bump(g_counters.dropped_quiet);
        return false;
    }
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        bump(g_counters.dropped_busy);
        return false;
    }
    bool kept = false;
    if (g_count < RING) {
        entry& e = g_ring[(g_head + g_count) % RING];
        e.level = lvl <= ERR ? lvl : static_cast<uint32_t>(INFO);
        constexpr uint32_t prefix = sizeof(PREFIX) - 1;
        for (uint32_t k = 0; k < prefix; ++k) {
            e.text[k] = PREFIX[k];
        }
        copy_text(e.text + prefix, text, LINE_MAX - prefix - 1);
        // the loader's logger writes the text as given: end the line ourselves, or the next line runs on
        uint32_t end = prefix;
        while (e.text[end] != '\0') {
            ++end;
        }
        e.text[end] = '\n';
        e.text[end + 1] = '\0';
        ++g_count;
        kept = true;
    }
    ReleaseSRWLockExclusive(&g_lock);
    bump(kept ? g_counters.posted : g_counters.dropped_full);
    return kept;
}

void set_verbose(bool on)
{
    __atomic_store_n(&g_verbose, on ? 1u : 0u, __ATOMIC_RELEASE);
}

bool verbose()
{
    return __atomic_load_n(&g_verbose, __ATOMIC_ACQUIRE) != 0;
}

uint32_t drain()
{
    static entry taken[RING];   // drained by one thread at a time (the try-lock below), never concurrently
    if (!TryAcquireSRWLockExclusive(&g_lock)) {
        return 0;               // a producer holds the ring: next beat
    }
    const uint32_t n = g_count;
    for (uint32_t k = 0; k < n; ++k) {
        taken[k] = g_ring[(g_head + k) % RING];
    }
    g_head = (g_head + n) % RING;
    g_count = 0;
    // Still under the lock, so a second drainer cannot reuse `taken` meanwhile; producers only try the lock.
    const logger_fn out = n != 0 ? sink() : nullptr;
    ReleaseSRWLockExclusive(&g_lock);
    if (n == 0) {
        return 0;
    }
    if (out == nullptr) {
        __atomic_add_fetch(&g_counters.dropped_no_sink, n, __ATOMIC_RELAXED);
        return 0;
    }
    for (uint32_t k = 0; k < n; ++k) {
        out(taken[k].level, taken[k].text);
    }
    __atomic_add_fetch(&g_counters.written, n, __ATOMIC_RELAXED);
    return n;
}

const counters* stats()
{
    return &g_counters;
}

void test_set_sink(sink_fn s)
{
    g_test_sink = s;
    __atomic_store_n(&g_test_sink_set, s != nullptr ? 1u : 0u, __ATOMIC_RELEASE);
}

void test_reset()
{
    AcquireSRWLockExclusive(&g_lock);
    g_head = 0;
    g_count = 0;
    ReleaseSRWLockExclusive(&g_lock);
    g_counters = counters{};
    __atomic_store_n(&g_verbose, 0u, __ATOMIC_RELEASE);
}

}  // namespace ilc::log
