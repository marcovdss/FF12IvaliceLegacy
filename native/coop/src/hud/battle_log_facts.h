// Ivalice Legacy co-op module: game facts the battle log reads (FFXII_TZA.exe 1.0.4.0, VAs at the pinned base
// 0x120000). Read only: the feed copies what the game hands its message emitter and never writes game memory.
#pragma once

#include <stdint.h>

namespace ilc::hud::battle_log::facts {

constexpr uint64_t PINNED_BASE = 0x120000;

// ---- the message feed (LG-01) -------------------------------------------------------------------------------------
// The battle message emitter 0x46ab10(rcx = producer context, edx = message id, r8 = argument block). Its entry is
// `push rsi; push rdi; push r14; sub rsp, 0x20`. It filters (block +0x28 below 0x1ff; the visibility test 0x469570;
// a 10-entry dedupe ring at 0x2ebb560 keyed by context and id for templates whose row byte 4 bit 0 is set), then
// formats the template (0x5369c0) and queues the line for the native log. The detour copies edx, rcx and the block
// at its entry and runs the original.
constexpr uint64_t FN_MESSAGE = 0x46AB10;          // VA: the emitter (LG-01, a MinHook detour at its entry)
constexpr uint32_t ARGS_SIZE = 0x30;               // NOTADDR: bytes of the argument block the formatter reads
constexpr uint32_t ARG_ACTOR_A = 0x00;             // battle record pointer (template slot kind 1)
constexpr uint32_t ARG_ACTOR_B = 0x08;             // battle record pointer (kind 2)
constexpr uint32_t ARG_VALUE = 0x10;               // s32 (kind 3: a gil amount)
constexpr uint32_t ARG_STATUS = 0x18;              // s32 (kind 5: a status id)
constexpr uint32_t ARG_LEVEL = 0x1C;               // s32 (kind 6: a level)
constexpr uint32_t ARG_ITEM = 0x20;                // s32 (kind 7: an item id, or a gil amount in the gil steals)
constexpr uint32_t ARG_COUNT = 0x24;               // s32 (kind 9: a count)
constexpr uint32_t ARG_ACTION = 0x28;              // s32 (kind 8: an action id); the emitter drops >= ACTION_BOUND
constexpr int32_t ACTION_BOUND = 0x1FF;            // NOTADDR: the emitter's own first test on block +0x28

// ---- the per-hit feed (HR-01, HR-02, HR-03, HR-05) -------------------------------------------------------------------
// 0x307300(cx = action, rdx = caster actor, r8 = target actor, r9 = hit slot, [rsp+0x28], [rsp+0x30]) applies one
// action to one target; its only caller is 0x319362. After the formula (native, or the Forge's Lua while the game
// thread waits inside), the outcome byte is in slot +8 and al holds the flag nibble (eax is only tested from 0x3075c7
// on). At 0x307601 (mov r8, r13; mov rdx, rbx; mov rcx, rdi; call 0x3216e0) rdi = caster actor, rbx = target actor,
// r15w = action, r14 = slot, r13 = the action's row (0 without one). Every path leaves through 0x307b91.
constexpr uint64_t FN_HIT = 0x307300;              // VA: HR-01, the entry (push rbp; push rbx; push rsi; push rdi)
constexpr uint64_t HIT_OUTCOME_SITE = 0x307601;    // VA: HR-02, mov r8, r13; mov rdx, rbx
constexpr uint64_t HIT_EXIT = 0x307B91;            // VA: HR-05, mov rcx, [rbp+0xa0] (the common exit)
constexpr uint64_t HIT_OTHER_PATCH = 0x30760A;     // VA: the call 0x3216e0 another mod (Hunter's Insight) rewrites
constexpr uint32_t HIT_OTHER_PATCH_LEN = 5;        // NOTADDR: no row of ours may cover these bytes
// The number pop-up 0x3283d0(rcx = actor, edx = signed amount, r8b = heal, r9b = 1 for MP): the native HP and MP
// appliers call it inside 0x307300, and the Forge calls it from its Lua thread while the game thread waits.
constexpr uint64_t FN_NUMBER = 0x3283D0;           // VA: HR-03, the entry (mov [rsp+8], rbx)
constexpr uint32_t SLOT_OUTCOME = 0x08;            // u8 in the hit slot
constexpr uint32_t ROW_ELEMENT = 0x13;             // u8 element mask of an action row (the weakness bit order)
constexpr uint32_t REC_WEAPON = 0x50;              // u16 item id of the weapon (EQUIP_FIRST = none)
constexpr uint32_t REC_AFFINITY = 0x40;            // u8 x 4: weak, absorb, half, immune
constexpr uint32_t AFF_WEAK = 0, AFF_ABSORB = 1, AFF_HALF = 2, AFF_IMMUNE = 3;
// The weapon's element (Attack): equipment row +0x1e (battle pack section 13, one row per item id - EQUIP_FIRST).
constexpr uint64_t EQUIP_TABLE_PTR = 0x2EBF0F8;    // VA: u64, the equipment table header
constexpr uint64_t RESOURCE_BASE_PTR = 0x1F83530;  // VA: u64, 0 in practice (the tables are refused otherwise)
constexpr uint32_t TABLE_COUNT = 0x04;             // st2e header: u32 rows
constexpr uint32_t TABLE_STRIDE = 0x08;            // u16 row size
constexpr uint32_t TABLE_ROWS = 0x0C;              // u32 address of the first row
constexpr uint16_t EQUIP_FIRST = 0x1000;           // NOTADDR: item ids of equipment start here
constexpr uint32_t EQUIP_ELEMENT = 0x1E;           // u8 element mask of a weapon (or ammunition) row

// Outcome byte (slot +8) as 0x30dae0 writes it; the tags the game draws for it come from the switch at 0x30760f.
enum hit_outcome : uint8_t {
    OUT_HIT = 0, OUT_PARRY = 1, OUT_EVADE = 2, OUT_SHIELD = 3, OUT_EVADE_COUNTER = 4, OUT_SHIELD_COUNTER = 5,
    OUT_MISS = 6, OUT_IMMUNE = 7, OUT_REFLECTED = 8, OUT_SAFETY = 9, OUT_MAGICK_EVADE = 10, OUT_SKIPPED = 11,
};
// The flag nibble in al at HR-02.
enum hit_flag : uint8_t { HIT_CRIT = 0x01, HIT_COMBO = 0x02, HIT_COUNTER = 0x04, HIT_KNOCKBACK = 0x08 };

// ---- the kill reward (KR-01) and the chain ---------------------------------------------------------------------------
// 0x312280 (KO, Stone or X-Zone of a foe) updates the chain (0x317ed0), credits EXP and LP, then calls the pop-up pair
// over the victim at 0x312518: 0x28fb80(ecx = victim handle, edx = EXP, r8d = LP). Under the Forge it runs on the Lua
// thread.
constexpr uint64_t FN_KILL_POPUP = 0x28FB80;       // VA: KR-01, the entry (mov [rsp+8], rbx)
// Chain globals, written only by 0x317ed0 (before the pop-up) and reset by 0x3183a0, whose stores name them.
constexpr uint64_t CHAIN_LEVEL = 0x22C3158;        // VA: s32 0..3
constexpr uint64_t CHAIN_COUNT = 0x22C315C;        // VA: s32 0..999
constexpr uint64_t CHAIN_SHOWN_PLUS = 0x1E02814;   // VA: u32; non-zero: the chain counter shows count + 1 (0x229e40)
constexpr uint64_t FN_CHAIN_RESET = 0x3183A0;      // VA: CH-V1 (verify-only: its stores pin the globals above)
constexpr uint64_t FN_CHAIN_SHOWN = 0x229E40;      // VA: CH-V2 (verify-only: mov eax, [0x1e02814]; ret)
constexpr int32_t CHAIN_MAX = 999;                 // NOTADDR
constexpr int32_t CHAIN_LEVEL_MAX = 3;             // NOTADDR

// ---- a battle record as the formatter reads it (0x536310) ---------------------------------------------------------
constexpr uint32_t REC_MEMBER = 0x04;              // u8: party id (when REC_SIDE is 0)
constexpr uint32_t REC_SIDE = 0x05;                // u8: 0 = a party member (named from the member table), else a unit
constexpr uint32_t REC_HP_MAX = 0x24;              // u32
constexpr uint32_t REC_STATUS_LASTING = 0x3C;      // u32
constexpr uint32_t REC_WEAKNESS = 0x40;            // u8
constexpr uint32_t REC_HP = 0x48;                  // u32
constexpr uint32_t REC_STATUS_TIMED = 0x64;        // u32

// ---- actors (restated from the snapshot, party rows and target picker facts) ---------------------------------------
constexpr uint32_t ACTOR_HANDLE = 0x08;            // u32
constexpr uint32_t ACTOR_UNIT = 0x10;              // pointer to the unit block
constexpr uint32_t ACTOR_BATTLE = 0x698;           // pointer to the battle record
constexpr uint32_t ACTOR_TARGET = 0x710;           // u32 handle of the current target (0 none)
constexpr uint32_t ACTOR_ACTION = 0x714;           // u16 the action the actor performs (0xffff none)
constexpr uint16_t ACTION_LIMIT = 0x21F;           // NOTADDR: battle action ids are below the action table's bound
constexpr uint16_t ACTION_ATTACK = 150;            // NOTADDR: Attack
constexpr uint32_t UNIT_NAME = 0xF8;               // pointer to the name text (game encoding)
constexpr uint32_t UNIT_INSTANCE = 0x100;          // u32, low 16 bits: the copy letter (1 = A)
constexpr uint64_t ACTOR_MANAGER_PTR = 0x2098E18;  // VA: u32 count, then one entry per actor index (+0x30 the actor)
constexpr uint32_t MANAGER_COUNT = 0x00;
constexpr uint32_t MANAGER_ENTRIES = 0x08;
constexpr uint32_t MANAGER_COUNT_MAX = 4096;       // NOTADDR
constexpr uint32_t ENTRY_ACTOR = 0x30;

// ---- status bits (the masks' bit order) ---------------------------------------------------------------------------
constexpr uint32_t ST_KO = 0;
constexpr uint32_t ST_STONE = 1;
constexpr uint32_t ST_PETRIFY = 2;
constexpr uint32_t ST_DOOM = 6;
constexpr uint32_t ST_POISON = 8;
constexpr uint32_t ST_SAP = 10;
constexpr uint32_t ST_LURE = 17;
constexpr uint32_t ST_REGEN = 25;
constexpr uint32_t ST_HP_CRITICAL = 29;
constexpr uint32_t ST_LIBRA = 30;
constexpr uint32_t ST_XZONE = 31;

// ---- message ids (battle_message.bin, 102 templates; vanilla extraction) ------------------------------------------
enum message_id : uint16_t {
    MSG_TRAP_FIRST = 0, MSG_TRAP_LAST = 3,     // B triggered <trap>
    MSG_LEVEL_UP = 4,                          // A is now level N
    MSG_CANCEL_TIME = 5, MSG_CANCEL_INTERRUPT = 6, MSG_CANCEL_RANGE = 7,   // A cancels <action>
    MSG_BEGINS_CASTING = 13,                   // A begins casting <action>
    MSG_READIES = 14,                          // A readies <action>
    MSG_USES = 15,                             // A uses <action>
    MSG_FALLEN = 16,                           // B has fallen
    MSG_VOID = 17,                             // B is cast into the void
    MSG_STONE = 18,                            // B turns to stone
    MSG_RESTORED_FIRST = 19, MSG_RESTORED_LAST = 23,   // B's HP/MP restored
    MSG_CURED_OF = 24,                         // B is cured of <status>
    MSG_CURED_VARIOUS = 25,
    MSG_FADES_FROM = 26,                       // <status> fades from B
    MSG_FADES_VARIOUS = 27,
    MSG_REVIVED = 28,                          // B regains consciousness
    MSG_NO_EFFECT = 29,                        // <action> had no effect
    MSG_OBTAIN_FIRST = 36, MSG_OBTAIN_LAST = 37,       // you obtain <item> x N
    MSG_OBTAIN_GIL = 38,                       // you obtain N gil
    MSG_USES_EFFECT_FIRST = 39, MSG_USES_EFFECT_LAST = 44,  // A uses <action>, with an effect line
    MSG_STOLE_FIRST = 45, MSG_STOLE_LAST = 48, // A stole <item> from B
    MSG_STOLE_GIL_FIRST = 49, MSG_STOLE_GIL_LAST = 50,
    MSG_STEAL_FAILED_FIRST = 51, MSG_STEAL_FAILED_LAST = 52,
    MSG_POACHED = 55, MSG_POACH_FAILED = 56,
    MSG_LIBRA_INFO = 59,
    MSG_SHIFT = 61, MSG_PALING_UP = 62, MSG_PALING_DOWN = 63, MSG_SHIELD_UP = 64, MSG_SHIELD_DOWN = 65,
    MSG_WHITE_WIND_UP = 66, MSG_WHITE_WIND_DOWN = 67,
    MSG_CALLS_HELP = 74, MSG_SPLITS = 75, MSG_SUMMONS_UNDEAD = 76, MSG_FEEDS = 77, MSG_STEALS_LOOT = 78,
    MSG_FIELD_FIRST = 79, MSG_FIELD_LAST = 96,
    MSG_BACK_ATTACK = 97,
    MSG_ADDED_FIRST = 100, MSG_ADDED_LAST = 101,   // added <item> x N to inventory
    MSG_TEMPLATES = 102,
};

}  // namespace ilc::hud::battle_log::facts
