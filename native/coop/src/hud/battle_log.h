// Ivalice Legacy co-op module: the battle log (top-right), drawn by the frame driver's log pass.
//
// What it shows: outlined text straight on the world, no boxes, under a "BATTLE LOG" header on the same
// line as Target Info's header. Each line starts with a small glowing dot (the target line's glow dot, tinted): the
// actor's seat colour, silver for a gambit ally, copper for a foe, green for a kill or reward line.
//
//     BATTLE LOG ---------------------------------
//     o Balthier attacks Hyena C x2  412, 726  CRIT     repeats collapse, amounts listed
//     o Ashe: Lure  [lure]  HOLDING                     tags; a status icon when it lands
//     o Penelo: Cure on Fran  +348                      heals in green
//     o Hyena B: Bite on Fran  -357                     damage taken in red on the victim's line
//     o Fran: Fira on Hyena A  880  WEAK                WEAK, CRIT, MISS, BLOCK, PARRY, IMMUNE from the hit itself
//     o Hyena A defeated  120 EXP 2 LP  Chain 12 Lv 2  Wolf Pelt   kill line: reward, chain, loot (green)
//
// Up to COMPACT_LINES lines, newest at the bottom; a line holds LIFE_MS after its last change and fades over
// FADE_MS, the two oldest positions dimmer; important lines (readies, KO, stone, doom, X-Zone, back attack) linger
// LINGER_MS and are kept over ordinary ones. Expanded (a pad control or the Battle HUD page): the last
// EXPANDED_LINES lines, no fade. A filter (All, Party, Foes, Statuses, Mine) applies to both.
//
// Where the lines come from:
//  * LG-01, a copy-only detour at the battle message emitter's entry (0x46ab10): message id, producer context and the
//    0x30-byte argument block go into a bounded event ring (RING entries, sequence numbers); the original always runs.
//    The 102 templates carry actors, actions, items, statuses and gil, never damage numbers.
//  * The per-hit feed, copy-only detours in the hit function 0x307300 (battle_log_facts.h): HR-02 in its middle copies
//    caster, target, action, the outcome byte (hit, MISS, BLOCK, PARRY, IMMUNE, reflected) and the flag nibble (CRIT),
//    the action's element and the target's affinities (WEAK). HR-01 (entry) and HR-05 (common exit) bracket one hit
//    on the thread that runs it; HR-03, at the number pop-up 0x3283d0, records an amount only from that same thread
//    while the bracket is open, so each exact number belongs to its hit. The Forge calls the pop-up from its Lua
//    thread while the game thread waits inside the bracket: those calls are never recorded (their hits keep the diff
//    amounts below, with the real attacker and tags from HR-02).
//  * KR-01, a copy-only detour at the kill pop-up 0x28fb80: victim, EXP and LP. The chain count and level are read
//    from their globals each beat (after the drain, so a kill drained is already counted), once the chain reset's
//    pinned stores (CH-V1) and the counter's display rule (CH-V2) are seen stock.
//  * HP and status diffs between two samples of the units the HUD knows (party rows, targets, and the actors the
//    feed named), attributed to the caster of a pending hit on the victim, else to the actor whose target is the
//    victim, preferring the one whose latest action event is the most recent (the action it names; Attack when the
//    actor's current action is Attack or unknown). A diff that exact numbers already showed is absorbed.
// The model that turns all of it into lines is pure (tested with recorded sequences). model_hint stays as the seam
// for a tag without a hit (tests); element_fn is the diff path's own WEAK seam.
//
// Threads. LG-01 and KR-01 run on whichever thread calls them, HR-01/02/05 on the thread that applies hits, HR-03 on
// whichever thread shows a number: each copies into the ring (lock-free, multi-producer, fault-safe reads, no game
// call, no allocation) and returns. The co-op thread drains the ring, resolves actors and names, samples the units,
// runs the model and publishes a view (seqlock). The pass, on the game thread inside the frame hook, copies the view
// and draws it; it never reads game memory, never calls a game function, never waits.
//
// The native combat log. While the log is live (element on, a view of this epoch at most LIVE_MS old), the frame
// hook skips the native log draw and draws the co-op layer in its place (replaces_native); otherwise the native log
// draws as before (HUD off, element off, RENDER disarmed, module off).
#pragma once

#include <stdint.h>

#include "hud/canvas.h"
#include "hud/driver.h"
#include "patch/detour.h"
#include "patch/patch_engine.h"

namespace ilc::hud::battle_log {

// ---- the event ring (LG-01 writes, the co-op thread drains) -------------------------------------------------------
constexpr uint32_t RING = 256;            // entries; fixed, never grows
constexpr uint32_t EVENT_ARGS = 0x30;     // NOTADDR: bytes of the emitter's argument block

enum event_flag : uint16_t {
    EV_ARGS = 0x01,                       // the argument block was copied (else it was unreadable: zeros)
};

struct event {
    volatile uint32_t stamp;              // 2 * seq while complete, odd while being written (seq = 1-based)
    uint16_t msg;                         // message id (edx)
    uint16_t flags;                       // event_flag
    uint64_t time_ms;                     // module clock at the copy
    uint64_t key;                         // the producer context (rcx), as the native dedupe ring keys it
    uint8_t args[EVENT_ARGS];
};
static_assert(sizeof(event) == 72, "event layout (host tests read it)");

// Copies one message into the ring (any thread; the detour's body). Never fails: a full ring overwrites its oldest
// entry, which the reader then counts as lost.
void ring_push(uint32_t msg, uint64_t key, uint64_t args_address, uint64_t now_ms);
// Copies out the complete entries in order, oldest first (co-op thread, the only reader). Entries overwritten
// before they were read count in *lost; an entry still being written ends the drain (read next time).
uint32_t ring_drain(event* out, uint32_t cap, uint32_t* lost);

struct ring_view {
    uint32_t written;                     // sequence numbers handed out (the last seq)
    uint32_t read;                        // the last seq drained
    uint64_t lost;                        // overwritten before they were drained
    uint64_t torn;                        // copied while overwritten (discarded)
    uint64_t unreadable;                  // argument blocks the probe could not read
};
ring_view ring_stats();

// ---- the per-hit feed and the kill reward: events in the same ring (msg >= EV_HOOK_FIRST, key = the thread id) -------
enum hook_event : uint16_t {
    EV_HOOK_FIRST = 0x8001,
    EV_HIT_BEGIN = 0x8001,                // HR-01: one hit starts on this thread (serial, caster, target, action)
    EV_HIT_OUTCOME = 0x8002,              // HR-02: outcome, flags, element, affinities, weapon, records
    EV_HIT_NUMBER = 0x8003,               // HR-03: an amount shown on `actor` inside the open bracket of this thread
    EV_HIT_END = 0x8004,                  // HR-05: the hit left 0x307300
    EV_KILL = 0x8005,                     // KR-01: victim, EXP, LP
};

// hook_payload.known (which fault-safe reads succeeded).
enum hook_known : uint8_t {
    HK_ACTOR = 0x01, HK_TARGET = 0x02, HK_OUTCOME = 0x04, HK_ELEMENT = 0x08, HK_AFFINITY = 0x10, HK_WEAPON = 0x20,
    HK_RECORDS = 0x40,
};

struct hook_payload {                     // the 0x30 bytes of an event's args
    uint32_t serial;                      // 0x00 the bracket (0 = none open on this thread)
    uint32_t actor;                       // 0x04 caster (BEGIN, OUTCOME), the number's unit (NUMBER), victim (KILL)
    uint32_t target;                      // 0x08 target handle (BEGIN, OUTCOME)
    uint16_t action;                      // 0x0C
    uint8_t outcome;                      // 0x0E slot +8 (facts::hit_outcome)
    uint8_t flags;                        // 0x0F al & 0xF (facts::hit_flag)
    uint64_t actor_record;                // 0x10 caster battle record (OUTCOME)
    uint64_t target_record;               // 0x18 target battle record (OUTCOME)
    int32_t value;                        // 0x20 NUMBER: the signed amount; KILL: EXP
    int32_t value2;                       // 0x24 NUMBER: the heal flag; KILL: LP
    uint16_t weapon;                      // 0x28 OUTCOME: the caster's weapon id (record +0x50)
    uint8_t kind;                         // 0x2A NUMBER: 0 HP, 1 MP
    uint8_t element;                      // 0x2B OUTCOME: the action row's element mask
    uint8_t affinity[3];                  // 0x2C OUTCOME: target record +0x40 weak, +0x41 absorb, +0x43 immune
    uint8_t known;                        // 0x2F hook_known
};
static_assert(sizeof(hook_payload) == EVENT_ARGS, "a hook payload fills one event's args");

// Copies a payload the hook built into the ring (any thread). Never fails, like ring_push.
void ring_put(uint32_t msg, uint64_t key, const hook_payload& p, uint64_t now_ms);

// ---- the model's inputs (resolved by the co-op thread; host tests build them directly) -----------------------------
constexpr uint32_t NAME = 24;             // bytes incl. NUL
constexpr uint32_t ACTION_NAME = 24;

enum side : uint8_t { SIDE_NONE = 0, SIDE_PARTY = 1, SIDE_FOE = 2 };

struct who {
    uint32_t handle;                      // 0 = not an actor we can follow
    uint8_t side;                         // side
    uint8_t seat;                         // 1..4 = held by that seat; 0 = a gambit ally (party) or a foe
    uint16_t member;                      // party id, 0xffff none
    char name[NAME];
};
static_assert(sizeof(who) == 32, "who layout");

struct message {
    uint32_t seq;
    uint16_t msg;
    uint16_t action;                      // block +0x28 (0xffff none)
    uint64_t time_ms;
    int32_t value;                        // +0x10
    int32_t status;                       // +0x18
    int32_t level;                        // +0x1C
    int32_t item;                         // +0x20
    int32_t count;                        // +0x24
    uint32_t reserved;
    who a;
    who b;
    char action_name[ACTION_NAME];
    char item_name[ACTION_NAME];
};
static_assert(sizeof(message) == 152, "message layout (host tests build it)");

enum obs_flag : uint8_t {
    OBS_PRESENT = 0x01,                   // HP and statuses were read
    OBS_TARGET = 0x02,                    // target and action_now are valid
};

struct unit_obs {
    uint32_t handle;
    uint8_t side;
    uint8_t seat;
    uint8_t flags;                        // obs_flag
    uint8_t weakness;
    uint32_t hp;
    uint32_t hp_max;
    uint32_t status;                      // timed | lasting
    uint32_t target;                      // current target handle
    uint16_t action_now;                  // the action the actor performs (0xffff none)
    uint16_t member;
    char name[NAME];
    char action_name[ACTION_NAME];        // name of action_now (empty when none or Attack)
};
static_assert(sizeof(unit_obs) == 76, "unit_obs layout (host tests build it)");

// Element of an action (bit mask in the weakness bit order), or ELEMENT_UNKNOWN. No live source yet.
constexpr uint8_t ELEMENT_UNKNOWN = 0xFF;
using element_fn = uint8_t (*)(void* ctx, uint16_t action);

// ---- lines ------------------------------------------------------------------------------------------------------------
constexpr uint32_t HISTORY = 32;          // lines kept
constexpr uint32_t COMPACT_LINES = 5;
constexpr uint32_t EXPANDED_LINES = 20;
static_assert(EXPANDED_LINES == ILC_LOG_LINES_MAX && COMPACT_LINES <= ILC_LOG_LINES_COMPACT_MAX, "the settings ranges match the view");
constexpr uint32_t TARGETS_SHOWN = 3;     // distinct target names on one line
constexpr uint32_t AMOUNTS_SHOWN = 4;     // amounts listed on one line (the count keeps going)
constexpr uint32_t ICONS = 4;

constexpr uint64_t ATTRIB_MS = 6000;      // an action event this recent names the action of an HP or status change
constexpr uint64_t MERGE_MS = 2500;       // a repeat this soon after the line's last change merges into it
constexpr uint64_t REWARD_MS = 4000;      // loot and gil this soon after a kill join its line
constexpr uint64_t KO_MERGE_MS = 2000;    // the KO message and the KO diff of one unit make one line
constexpr uint64_t LIFE_MS = 6000;        // an ordinary line holds this long after its last change
constexpr uint64_t LINGER_MS = 12000;     // an important line holds this long
constexpr uint64_t FADE_MS = 1000;        // then fades over this
constexpr uint64_t HEADER_HOLD_MS = 20000;// the header stays this long after the last line changed (compact)
constexpr uint64_t HINT_MS = 500;         // a CRIT/MISS hint joins a damage line on its victim this close in time
constexpr uint64_t LIVE_MS = 250;         // a view this fresh keeps the native log hidden

enum line_class : uint8_t {
    LK_NONE = 0,
    LK_DAMAGE,                            // actor hits target(s)
    LK_HEAL,                              // actor heals target(s)
    LK_ACTION,                            // actor uses / readies / begins casting (no number yet)
    LK_STATUS,                            // a status lands on / wears off a unit
    LK_KO,                                // a party member falls (or is cast into the void, turns to stone)
    LK_REVIVE,
    LK_KILL,                              // a foe is defeated (+ loot and gil)
    LK_REWARD,                            // loot, gil, level up without a kill line to join
    LK_STEAL,                             // steal and poach results
    LK_SYSTEM,                            // fields, back attack, cancels, foe calls
};

enum line_flag : uint8_t {
    LF_IMPORTANT = 0x01,                  // lingers (readies, KO, stone, doom, X-Zone, back attack, ...)
    LF_PARTY_HIT = 0x02,                  // a party member took the damage: amounts in red
    LF_ATTACK = 0x04,                     // the action is Attack: "attacks"
    LF_SINGLE_TARGET = 0x08,              // every merged hit was on the first target (shows "xN")
};

enum tag_bit : uint8_t {
    TAG_CRIT = 0x01, TAG_WEAK = 0x02, TAG_MISS = 0x04, TAG_HOLDING = 0x08, TAG_BLOCK = 0x10, TAG_PARRY = 0x20,
    TAG_IMMUNE = 0x40,
};

enum dot_kind : uint8_t {
    DOT_NONE = 0,                         // 1..4: the seat
    DOT_GAMBIT = 5,                       // silver
    DOT_FOE = 6,                          // copper
    DOT_REWARD = 7,                       // green
    DOT_SYSTEM = 8,                       // grey
};

enum tone : uint8_t {
    TONE_TEXT = 0,                        // plain text
    TONE_SEAT1 = 1,                       // 1..4 seat colours
    TONE_GAMBIT = 5,
    TONE_FOE = 6,
    TONE_DAMAGE = 7,                      // damage dealt to a foe
    TONE_HURT = 8,                        // damage taken by a party member (red)
    TONE_HEAL = 9,
    TONE_REWARD = 10,
    TONE_DIM = 11,
    TONE_IMPORTANT = 12,
    TONE_CRIT = 13,                       // the tags, each in its own colour
    TONE_WEAK = 14,
    TONE_MISS = 15,
    TONE_HOLDING = 16,
    TONE_COUNT
};

// Filter categories (a line has one or more) and the filters.
enum category : uint8_t { CAT_PARTY = 0x01, CAT_FOE = 0x02, CAT_STATUS = 0x04, CAT_REWARD = 0x08, CAT_SYSTEM = 0x10 };
enum filter : uint8_t { FILTER_ALL = 0, FILTER_PARTY = 1, FILTER_FOES = 2, FILTER_STATUSES = 3, FILTER_MINE = 4,
                        FILTER_COUNT };

constexpr uint8_t ICON_OFF = 0x80;        // icon byte: status id | ICON_OFF when it wore off

struct line {
    uint32_t id;                          // grows by one per new line (0 = empty slot)
    uint8_t cls;                          // line_class
    uint8_t flags;                        // line_flag
    uint8_t tags;                         // tag_bit
    uint8_t dot;                          // dot_kind
    uint8_t categories;                   // category bits
    uint8_t seats;                        // bit s: seat s + 1 acts or is hit on this line (the Mine filter)
    uint8_t actor_tone;                   // tone of the actor's name
    uint8_t target_tone;                  // tone of the first target's name
    uint8_t count;                        // hits merged (1 = one)
    uint8_t target_count;                 // distinct targets merged
    uint8_t amount_count;                 // amounts listed (<= AMOUNTS_SHOWN)
    uint8_t icon_count;
    uint8_t icons[ICONS];
    uint16_t action;                      // the action (0xffff none)
    uint16_t reserved;
    uint32_t actor;                       // handles (merge keys)
    uint32_t target;                      // the first target
    int32_t amounts[AMOUNTS_SHOWN];       // signed: < 0 damage, > 0 healing; gil on a kill line
    uint64_t born_ms;                     // the first event of the line
    uint64_t touched_ms;                  // its last change (fade and merge clock)
    char actor_name[NAME];
    char body[ACTION_NAME];               // the action's name, or the verb text of a system line
    char target_name[NAME];               // the first target's name; "+N" more are counted in target_count
    char extra[NAME];                     // loot names on a kill line, the reason of a cancel, ...
    uint32_t exp;                         // kill line: EXP of the kill (reward & REWARD_EXP)
    uint16_t lp;                          // kill line: LP
    uint16_t chain;                       // kill line: the chain counter after the kill (reward & REWARD_CHAIN)
    uint8_t chain_level;                  // 0..3
    uint8_t reward;                       // reward_bit
    uint8_t reserved3[6];
};
static_assert(sizeof(line) == 176, "line layout (host tests read it)");

enum reward_bit : uint8_t { REWARD_EXP = 0x01, REWARD_CHAIN = 0x02 };

// ---- the pure model ----------------------------------------------------------------------------------------------------
constexpr uint32_t TRACKED = 24;          // units followed between two samples

struct tracked {
    uint32_t handle;
    uint8_t side;
    uint8_t seat;
    uint8_t seen;                         // 1 = hp/status below are a baseline
    uint8_t weakness;
    uint32_t hp;
    uint32_t status;
    uint32_t target;
    uint16_t action_now;
    uint16_t event_action;                // the latest action event's action (0xffff none)
    uint64_t event_ms;                    // its time (0 none)
    uint64_t sampled_ms;                  // the sample that saw it last
    uint8_t event_kind;                   // the message that set it (13/14/15)
    uint8_t reserved[3];
    int32_t absorb;                       // HP change exact numbers already showed (the diff that follows is absorbed)
    uint64_t absorb_ms;                   // when they were shown
    char name[NAME];
    char event_name[ACTION_NAME];
    char action_name[ACTION_NAME];
};

// A hit the feed saw without its amount (the Forge's numbers come from its Lua thread): the next HP change of the
// victim within PENDING_MS is this caster's, with these tags. Never claimed: a line without an amount.
constexpr uint32_t PENDING = 8;
constexpr uint64_t PENDING_MS = 400;
constexpr uint64_t ABSORB_MS = 1000;      // exact numbers absorb the victim's diffs this long

struct pending {
    uint32_t victim;                      // 0 = free
    uint32_t actor;
    uint8_t side;
    uint8_t seat;
    uint8_t tags;
    uint8_t count;                        // hits folded into it (same caster and action)
    uint16_t action;
    uint8_t victim_side;
    uint8_t victim_seat;
    uint64_t ms;
    char name[NAME];
    char action_name[ACTION_NAME];
    char victim_name[NAME];
};

struct model {
    uint32_t next_id;
    uint32_t head;                        // next slot in `lines` (ring of HISTORY)
    uint32_t count;                       // lines held
    uint32_t reserved;
    uint64_t last_kill_ms;
    uint32_t last_kill_id;
    uint32_t hint_victim;
    uint64_t hint_ms;
    uint8_t hint_tags;
    uint8_t reserved2[7];
    line lines[HISTORY];
    tracked units[TRACKED];
    pending pend[PENDING];
};

void model_reset(model* m);
// One feed message (resolved). Returns the id of the line it created or changed, 0 if none.
uint32_t model_message(model* m, const message& msg);
// One sample of the followed units at `now`: HP and status diffs become lines. element may be null.
void model_units(model* m, uint64_t now, const unit_obs* units, uint32_t n, element_fn element, void* ctx);
// CRIT/MISS seam: a damage line on `victim` within HINT_MS gets the tags (now, or when it appears).
void model_hint(model* m, uint32_t victim, uint8_t tags, uint64_t now);
// The lines held, oldest first (by born time). Returns how many were copied.
uint32_t model_lines(const model& m, line* out, uint32_t cap);

// One hit from the per-hit feed (resolved by the co-op thread).
constexpr uint8_t OUTCOME_UNKNOWN = 0xFF; // the hit was bracketed but HR-02 did not report it

struct hit {
    uint32_t serial;
    uint16_t action;
    uint8_t outcome;                      // facts::hit_outcome, or OUTCOME_UNKNOWN
    uint8_t flags;                        // facts::hit_flag
    uint64_t time_ms;
    who caster;
    who target;
    uint8_t weak;                         // the element meets the target's weakness (no immunity, no absorption)
    uint8_t pinned;                       // amounts[] are the hit's own numbers (HR-03 inside the bracket)
    uint8_t amount_count;
    uint8_t reserved;
    int32_t amounts[AMOUNTS_SHOWN];
    int32_t total;                        // every HP amount of the hit, summed
    uint32_t reserved2;
    char action_name[ACTION_NAME];
};
static_assert(sizeof(hit) == 136, "hit layout (host tests build it)");

struct kill {
    uint64_t time_ms;
    who victim;
    uint32_t exp;
    uint32_t lp;
    uint8_t chain_known;
    uint8_t chain_level;
    uint16_t chain;                       // the counter as the game shows it
    uint32_t reserved;
};
static_assert(sizeof(kill) == 56, "kill layout (host tests build it)");

// The tags an outcome draws (MISS, BLOCK, PARRY, IMMUNE), 0 for a hit, reflected, skipped or unknown.
uint8_t outcome_tags(uint8_t outcome);
// One hit: a tag-only line for an outcome without damage, exact amounts when pinned, else a pending attribution for
// the victim's next HP change. Returns the line created or changed, 0 if none (pending, skipped).
uint32_t model_hit(model* m, const hit& h);
// A kill reward: the victim's kill line (created, or joined within KO_MERGE_MS) carries EXP, LP and the chain.
uint32_t model_kill(model* m, const kill& k);

// ---- the view (co-op thread -> pass) ------------------------------------------------------------------------------------
enum view_flag : uint32_t {
    VIEW_EXPANDED = 0x01,
    VIEW_FEED = 0x02,                     // LG-01 is hooked (else lines come from diffs only)
    VIEW_HITS = 0x04,                     // HR-02 reports outcomes
    VIEW_PINNED = 0x08,                   // HR-01, HR-03 and HR-05 pin exact numbers to their hit
    VIEW_KILLS = 0x10,                    // KR-01 reports EXP and LP
    VIEW_CHAIN = 0x20,                    // the chain rows were seen stock and the globals read
};

struct view {
    patch::record_head head;              // epoch, flags = view_flag, generation
    uint64_t built_ms;
    uint8_t filter;                       // filter
    uint8_t filter_seat;                  // 1..4 for FILTER_MINE
    uint8_t line_count;
    uint8_t reserved;
    uint32_t seat_colour[4];              // packed rgba (0x80 = 1.0) of each seat
    uint8_t compact_lines;                // the settings' line counts and hold time; 0 = the built-in default
    uint8_t expanded_lines;
    uint8_t fade_s;
    uint8_t reserved2;
    line lines[HISTORY];                  // oldest first
};

// ---- presentation (pure; tested directly) ------------------------------------------------------------------------------
// Authoring units (1920x1080): the column starts under the header box on the right (target_info::corner).
constexpr int16_t LINE_SCALE = 80;        // text scale x100
constexpr int16_t LINE_PITCH = 30;
constexpr int16_t LINES_DY = 34;          // header top -> first line
constexpr int16_t DOT_SIZE = 30;          // the glow dot's display size (its core sits at the centre)
constexpr int16_t TEXT_DX = 24;           // line x -> text
constexpr int16_t ICON_W = 20;
constexpr int16_t ICON_H = 24;
constexpr int16_t RIGHT_LIMIT = 1904;     // the log zone's right edge (menus keep clear of {1360, 16, 544, 680})
constexpr uint32_t MAX_COMMANDS_LOG = 44; // the pass's own cap (compact: 5 lines of <= 8; expanded: 20 of 2 + header)
constexpr uint32_t MENU_RESERVE = 48;     // commands left for the menu pass (drawn after the log) when room is short
constexpr uint32_t LOG_FLOOR = 12;        // the pass may always use this many (header + a few lines): the frame driver
                                          // keeps them back while party extras and Target Info draw (log_floor_due)
constexpr uint32_t LOG_TEXT_FLOOR = 160;  // ... and this many text bytes
constexpr uint32_t TEXT_RESERVE = 700;    // text bytes left for the menu pass
constexpr uint8_t DOT_CLUT = 6;           // the glow dot's white palette (tinted per line)
constexpr uint8_t DIM_POS3 = 92;          // compact: alpha of the 4th newest line (0x80 = 1.0)
constexpr uint8_t DIM_POS4 = 58;          // compact: alpha of the 5th newest line

// Alpha (0..0x80) of a line at `now` from its age (no position dimming): holds, then fades. An ordinary line holds
// `life_ms`, an important one twice as long (the defaults: LIFE_MS and LINGER_MS).
uint8_t time_alpha(const line& l, uint64_t now, uint64_t life_ms = LIFE_MS);
// The view's line counts and hold time, the built-in defaults where the view carries 0.
uint32_t compact_lines(const view& v);
uint32_t expanded_lines(const view& v);
uint64_t life_ms(const view& v);
// Whether a line passes the filter.
bool passes(const line& l, uint8_t filter, uint8_t filter_seat);
// The lines shown, oldest first, as indices into v.lines; alpha per shown line. Returns how many.
uint32_t select(const view& v, uint64_t now, uint8_t* index, uint8_t* alpha, uint32_t cap);
// Whether the header shows (some line changed within HEADER_HOLD_MS, or expanded).
bool header_shown(const view& v, uint64_t now);
// The line's text as drawn in one run (expanded view, tests): actor, body, targets, amounts, tags.
uint32_t line_text(const line& l, char* out, uint32_t cap);

// One line as coloured pieces of text (the overlay draws them with its own font: overlay/hud_scene.h). Each run is a
// piece of `text` in one tone; `tags` is the first run of the tags (CRIT, WEAK, ...), n when none (status icons go
// before them). status_words: the line's status icons as words too (the overlay has no icon atlas yet: true).
constexpr uint32_t RUN_TEXT = 160;
constexpr uint32_t RUNS = 10;
struct runs {
    char text[RUN_TEXT];
    uint8_t start[RUNS];
    uint8_t len[RUNS];
    uint8_t tone[RUNS];
    uint32_t n;
    uint32_t used;
    uint32_t tags;
};
void build_runs(const line& l, runs& r, bool status_words);
// The main tone of a line (its kill/reward/KO/revive/important colour, else plain text).
uint8_t line_tone(const line& l);
// Our short name of a status (status bit order), "" for an id beyond 31.
const char* status_name(uint32_t status_id);

// The chain as the co-op thread read it on its last beat: false when the chain rows are not stock or nothing was read
// (any thread, wait-free; one packed word).
bool chain_now(uint16_t* shown, uint8_t* level);
// Draws the view (the pass's body; pure: the view, now and the room left). Returns the commands added.
// dy: the corner headers' drop (layout::header_dy: the boss gauge). The expanded log keeps its zone's bottom and
// drops its oldest lines instead (layout::log_lines).
uint32_t compose(canvas& cv, const view& v, uint64_t now, int16_t dy = 0);

// ---- settings (the Battle HUD page wires these: settings fields only, no schema field yet) ------------------------------
struct settings {
    uint8_t enabled;                      // the element (on by default)
    uint8_t expanded;                     // the expanded view (also toggled by expand_control)
    uint8_t expand_control;               // 0 = none, else an ilc_pad_control (1..24): any seat's press toggles expanded
    uint8_t filter;                       // filter
    uint8_t filter_seat;                  // 1..4 for FILTER_MINE
    uint8_t compact_lines;                // lines in the compact view, 1..ILC_LOG_LINES_COMPACT_MAX (0 = COMPACT_LINES)
    uint8_t expanded_lines;               // lines in the expanded view, 5..EXPANDED_LINES (0 = EXPANDED_LINES)
    uint8_t fade_s;                       // seconds an ordinary line holds before it fades, 3..15 (0 = LIFE_MS)
};
settings defaults();
void configure(const settings& s);
settings current();
void toggle_expanded();

// ---- the module side -------------------------------------------------------------------------------------------------
bool attach(uintptr_t image_base);       // LG-01's handler; false (row stays stock, diffs only) if it cannot be set
void step(uint64_t now_ms);              // co-op thread, after the snapshot (and the menus' names)
void stop();                             // withdraw the view; the native log draws again

// The frame driver's log pass (game thread).
void draw(canvas& cv, const driver::pass_input& in);
// The frame hook: may the native log draw be skipped this frame (our log is live)? Game thread, atomics only.
bool replaces_native(uint32_t epoch, uint64_t now_ms);
// The frame driver, before the first pass: is the log's floor (LOG_FLOOR commands, LOG_TEXT_FLOOR text bytes) kept
// back from party extras and Target Info this frame? Exactly while ours replaces the native log (replaces_native),
// so a frame that hides the native log always has room for our header and a few lines. Game thread, atomics only.
bool log_floor_due(uint32_t epoch, uint64_t now_ms);
void note_native_hidden();               // the frame hook skipped the native log draw this frame (counter)

// LG-01's handler (also called by host tests with a synthetic ctx): copy-only, always RUN_ORIGINAL.
int32_t lg01(patch::ctx* c, uint32_t epoch);
// The per-hit feed and the kill reward: copy-only, always RUN_ORIGINAL (host tests call them with synthetic ctxs).
int32_t hr01(patch::ctx* c, uint32_t epoch);   // 0x307300 entry: opens this thread's bracket
int32_t hr02(patch::ctx* c, uint32_t epoch);   // 0x307601: the outcome
int32_t hr03(patch::ctx* c, uint32_t epoch);   // 0x3283d0 entry: an amount, only from the bracket's own thread
int32_t hr05(patch::ctx* c, uint32_t epoch);   // 0x307b91: closes the bracket
int32_t kr01(patch::ctx* c, uint32_t epoch);   // 0x28fb80 entry: EXP and LP of a kill

// Rows of the per-hit feed whose handler may act: hit_row bits. HROW_NUMBER is only set with HROW_BEGIN and
// HROW_END (an amount needs the bracket), HROW_BEGIN and HROW_END only together.
enum hit_row : uint8_t {
    HROW_BEGIN = 0x01, HROW_OUTCOME = 0x02, HROW_NUMBER = 0x04, HROW_END = 0x08, HROW_KILL = 0x10,
};
constexpr uint64_t BRACKET_MS = 1000;     // a bracket older than this (its exit missed) pins nothing
uint64_t current_thread();                // the calling thread's id, from the TEB (no import)

bool latest(view& out);                  // any thread, wait-free

struct counters {
    volatile uint64_t pushed;            // messages copied by LG-01
    volatile uint64_t declined;          // LG-01 calls with the element off or not attached
    volatile uint64_t drained;           // events the co-op thread consumed
    volatile uint64_t filtered;          // events the emitter's own first test drops (action >= 0x1ff), dropped too
    volatile uint64_t unresolved;        // actors that could not be named
    volatile uint64_t lines;             // lines created
    volatile uint64_t published;
    volatile uint64_t withdrawn;
    volatile uint64_t drawn;             // pass frames that drew something
    volatile uint64_t skipped;           // pass frames without a usable view
    volatile uint64_t capped;            // commands the pass's budget left out
    volatile uint64_t toggles;           // expanded toggled by a pad press
    volatile uint64_t native_hidden;     // frames the native log draw was skipped (ours drawn in its place)
    volatile uint64_t hits;              // HR-02 outcomes copied
    volatile uint64_t numbers;           // HR-03 amounts copied (inside a bracket, on its thread)
    volatile uint64_t foreign;           // HR-03 calls not recorded: another thread (the Forge's Lua), no bracket, stale
    volatile uint64_t kills;             // KR-01 rewards copied
    volatile uint64_t chain_off;         // beats the chain could not be read (rows not stock, unreadable)
};
const counters* stats();

struct status_view {
    uint8_t attached;
    uint8_t feed;                        // LG-01 hooked
    uint8_t enabled;
    uint8_t expanded;
    uint8_t hit_rows;                    // hit_row bits that act
    uint8_t chain;                       // 1 = the chain rows were seen stock and the globals read
    uint8_t reserved[2];
};
status_view status();

// ---- host tests ----------------------------------------------------------------------------------------------------------
void test_reset();
void test_set_image_base(uintptr_t image_base);
void test_set_attached(bool on);
// The co-op thread's step on a given snapshot (no published-snapshot read); feeds the drained ring too.
bool test_step(const void* snapshot, uint64_t now_ms, view* out);
void test_publish(const view& v);
model* test_model();
// Resolves one record pointer the way the sampler does (host memory as the game).
bool test_resolve(uint64_t record, who* out);
void test_set_names_only(bool on);       // resolve names without the menus' cache (host tests)
void test_set_hit_rows(uint8_t rows);    // hit_row bits as if those rows were hooked
void test_set_bracket(uint32_t serial, uint64_t thread, uint64_t ms);
// The chain as the sampler reads it on the current image base: true = read (counter shown, level), false = off.
bool test_chain(uint16_t* shown, uint8_t* level);
uint64_t test_ring_address();            // &ring[0] (emulator probes)
uint64_t test_ring_write_address();      // &the last sequence number handed out

}  // namespace ilc::hud::battle_log
