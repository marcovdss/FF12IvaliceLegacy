// Ivalice Legacy co-op module: chests open without a pause, their reward goes to the combat log (MOVEMENT).
//
// Stock, opening a chest is a script sequence [verified static, FFXII_TZA.exe 1.0.4.0]:
//   1. begin (0x355830): resolves the controlled actor and, at 0x35588d (TL-02), calls 0x269090(actor, chest, data)
//      -> 0x328640, which (only while the pending word 0x208cd18 is 0) copies {1, actor, chest, data} to the treasure
//      record 0x208ccf8..0x208cd17, sets the actor's action to 0x201c (the open animation on P1's leader) and pending
//      to 1;
//   2. wait: the script polls pending (0x319180);
//   3. complete (0x325260 -> 0x317850, only while pending is 1): the chest's own animation, an effect handle into the
//      result word 0x208cd1c, pending back to 0;
//   4. grant (0x50faf0): gil or the item, party-wide, then the notice in modal presentation: `mov dword [rsi],
//      0x10000` at 0x50fb3a (TL-01) pauses the game until it is dismissed;
//   5. end (0x32a170, only while pending is 0): the chest marked taken (0x26dc60 on the record's chest), the end pose
//      0x2001 on the record's actor, the result word's effect released when it is a real handle (not 0 or -1), then
//      -1.
//
// Ivalice Together (Marco, 2026-11-16: "taking chests would not pause the game, it would just move the reward to the
// log ... seamless is the keyword") changed two things, ported here in our own code:
//   * TL-01 (detour on the 6-byte store): the notice's presentation mode is 1, the game's own combat-log
//     presentation, instead of 0x10000 (modal): no pause, the reward is a log line;
//   * TL-02 (call row on the begin call): the record is written as 0x328640 would ({1, actor, chest, data}) with
//     pending left 0, and the call is skipped: nobody plays the open animation, the wait returns at once, the
//     complete step never runs, the grant and the end step run as stock (the chest is marked taken, the reward is
//     granted). The result word keeps 0 (bss) or -1 (the last end step), so the end step releases nothing.
// Together's third site, TL-03 (the notice's sound call), stays an INFO row (never written), but what Together did
// there is ported: its wrapper queued UI sound 0x27 in its own sound pump on every chest, on top of the stock notice
// (the fork's native_treasure_log.py and build_sound_pump). Here TL-02 marks a chest it took; the co-op thread's step
// then queues sound 0x27 for the game thread through the menus' sound operation (submit::play_system: the UI sound
// function 0x249c60 behind its pinned prologue), so a chest in log mode still sounds (seamless, not silent).
//
// Every chest, whoever opens it: P1's own, and a guest's (IX-20 runs the same script through the consumer, with P1's
// leader as the controlled actor). Fail closed:
//   * both rows are OPTIONAL: another mod that rewrote either site skips that row, and then neither is armed (all or
//     nothing, one hook.log line at the first live beat);
//   * the dispatcher runs the handlers only while co-op is armed and live and MOVEMENT is live; anything else is the
//     stock modal and animation;
//   * TL-02 leaves a begin call alone (stock) when a stock open is already in flight (pending not 0), when the actor
//     argument is null (the end step dereferences the record's actor) or outside a valid world.
#pragma once

#include <stdint.h>

#include "patch/detour.h"

namespace ilc::interaction::treasure_log {

// ---- game facts (VAs at the pinned base 0x120000) ----------------------------------------------------------------
constexpr uint64_t PINNED_BASE = 0x120000;
constexpr uint64_t SITE_PRESENTATION = 0x50FB3A;   // TL-01: mov dword [rsi], 0x10000 (c7 06 00 00 01 00)
constexpr uint32_t PRESENTATION_LEN = 6;
constexpr uint32_t MODE_MODAL = 0x10000;
constexpr uint32_t MODE_COMBAT_LOG = 1;
constexpr uint64_t SITE_BEGIN_CALL = 0x35588D;     // TL-02: call 0x269090 (actor, chest, data)
constexpr uint64_t FN_BEGIN = 0x269090;
constexpr uint64_t RECORD = 0x208CCF8;             // {u64 active, u64 actor, u64 chest, u64 data, u32 pending, i32 result}
constexpr uint32_t RECORD_PENDING = 0x20;
constexpr uint32_t RECORD_RESULT = 0x24;

struct record_image {
    uint64_t active;     // 1
    uint64_t actor;      // rcx of the begin call
    uint64_t chest;      // rdx
    uint64_t data;       // r8
    uint32_t pending;    // 0: the wait returns at once, the end step runs
};
// The bytes written: through `pending` (0x24). The struct pads to 0x28; the padding is the game's result word, which
// is never written.
constexpr uint32_t RECORD_WRITE = RECORD_PENDING + sizeof(uint32_t);
static_assert(__builtin_offsetof(record_image, pending) == RECORD_PENDING && RECORD_WRITE == RECORD_RESULT,
              "the record as written stops right before the result word");

// The record TL-02 writes for a begin call (pure).
record_image record_for(uint64_t actor, uint64_t chest, uint64_t data);

// Boot, after the patch install: handlers on TL-01 and TL-02 and both armed, or neither (a row another mod
// rewrote was skipped by the install). False: chests stay stock (the reason is logged at the first live beat).
bool attach();
// Co-op thread, every beat: the one-time hook.log line once the module is live.
void step(uint64_t now_ms);

int32_t tl01(patch::ctx* c, uint32_t epoch);   // game thread (MinHook row)
int32_t tl02(patch::ctx* c, uint32_t epoch);   // game thread (call row)

struct counters {
    volatile uint64_t log_notices;    // TL-01: notices shown in the combat log
    volatile uint64_t no_pause_opens; // TL-02: chests opened without the actor action
    volatile uint64_t stock_in_flight;// TL-02: left stock, a stock open was in flight
    volatile uint64_t stock_other;    // TL-02: left stock, no actor, no valid world or not live
    volatile uint64_t sounds;         // chest sounds queued for the game thread
    volatile uint64_t sounds_retried; // beats the sound slot was taken (queued on a later beat)
};
const counters* stats();

struct status_view {
    uint8_t attached;      // handlers set and both rows armed
    uint8_t presentation;  // TL-01 hooked by the install
    uint8_t begin;         // TL-02 hooked by the install
    uint8_t logged;        // the one-time line was written
};
status_view status();

// ---- host tests -------------------------------------------------------------------------------------------------------
void test_reset();
uint32_t test_sound_due();   // chests taken whose sound is not queued yet

}  // namespace ilc::interaction::treasure_log
