// Ivalice Legacy co-op module, host tests only: the player options word, the arm mask a record asks for, the Wait
// rule's ANY/ALL choice, the guests' PAUSE, and the hook.log lines (ILCT_Opt*, ILCT_Log*).
#include <windows.h>

#include <string.h>

#include "firewall/policy.h"
#include "ilc/bridge_defaults.h"
#include "runtime/log.h"
#include "runtime/options.h"
#include "wait/wait.h"

using namespace ilc;

namespace {

// The capture sink: every line the drain hands over, NUL-separated, with its level byte in front.
SRWLOCK g_capture_lock = SRWLOCK_INIT;
char g_capture[16384];
uint32_t g_capture_used = 0;
uint32_t g_capture_lines = 0;

void capture_sink(uint32_t level, const char* text)
{
    AcquireSRWLockExclusive(&g_capture_lock);
    const uint32_t n = static_cast<uint32_t>(strlen(text));
    if (g_capture_used + n + 2 <= sizeof(g_capture)) {
        g_capture[g_capture_used++] = static_cast<char>('0' + (level & 0x7));
        memcpy(g_capture + g_capture_used, text, n + 1);
        g_capture_used += n + 1;
        ++g_capture_lines;
    }
    ReleaseSRWLockExclusive(&g_capture_lock);
}

}  // namespace

extern "C" {

void ILCT_OptDefaults(ilc_settings* out)
{
    constexpr ilc_settings d = default_settings();
    memcpy(out, &d, sizeof(d));
}

uint32_t ILCT_OptEffective(const ilc_settings* s) { return options::effective_subsystems(*s); }
void ILCT_OptFromSettings(const ilc_settings* s, options::view* out) { *out = options::from_settings(*s); }
void ILCT_OptConfigure(const ilc_settings* s) { options::configure_from_settings(*s); }
void ILCT_OptCurrent(options::view* out) { *out = options::current(); }
void ILCT_OptSet(const options::view* v) { options::test_set(*v); }
void ILCT_OptReset(void) { options::test_reset(); }

int32_t ILCT_WaitPauseWantedRule(const wait::seat_view* seats, uint32_t rule)
{
    return wait::pause_wanted(seats, static_cast<uint8_t>(rule)) ? 1 : 0;
}

uint32_t ILCT_FwGuestPulseMay(uint32_t pressed, uint32_t held, int32_t native_screen, int32_t may_pause)
{
    return firewall::guest_pulse(pressed, held, native_screen != 0, may_pause != 0);
}

// ---- hook.log ----------------------------------------------------------------------------------------------------------
int32_t ILCT_LogPost(uint32_t level, const char* text, int32_t verbose)
{
    return log::post(level, text, verbose != 0) ? 1 : 0;
}

int32_t ILCT_LogPostNumbers(uint32_t level, const char* text, uint64_t u, int64_t i, uint64_t x)
{
    log::line l;
    l.s(text).s(" u=").u(u).s(" i=").i(i).s(" x=").x(x);
    return log::post(level, l) ? 1 : 0;
}

uint32_t ILCT_LogDrain(void) { return log::drain(); }
void ILCT_LogSetVerbose(int32_t on) { log::set_verbose(on != 0); }
void ILCT_LogStats(log::counters* out) { memcpy(out, const_cast<const log::counters*>(log::stats()), sizeof(*out)); }

// on: 1 = the capture sink, 0 = none (the drain looks the loader's logger up, which a test process does not have).
void ILCT_LogCapture(int32_t on)
{
    AcquireSRWLockExclusive(&g_capture_lock);
    g_capture_used = 0;
    g_capture_lines = 0;
    ReleaseSRWLockExclusive(&g_capture_lock);
    log::test_set_sink(on != 0 ? capture_sink : nullptr);
}

// The captured lines: returns the bytes copied (level byte + text + NUL, line after line); *lines = the line count.
uint32_t ILCT_LogCaptured(char* out, uint32_t cap, uint32_t* lines)
{
    AcquireSRWLockExclusive(&g_capture_lock);
    const uint32_t n = g_capture_used < cap ? g_capture_used : cap;
    memcpy(out, g_capture, n);
    if (lines != nullptr) {
        *lines = g_capture_lines;
    }
    ReleaseSRWLockExclusive(&g_capture_lock);
    return n;
}

void ILCT_LogReset(void)
{
    log::test_reset();
    ILCT_LogCapture(0);
}

uint32_t ILCT_LogSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(options::view), sizeof(log::counters), log::LINE_MAX, log::RING};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

}  // extern "C"
