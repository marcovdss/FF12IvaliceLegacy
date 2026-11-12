// Ivalice Legacy co-op module: the compiled-in patch site table (sites.inc, generated) as a constexpr array.
//
// Every address is an RVA added to the module base; the base itself is pinned (0x120000) and checked before use.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

// Column tokens used by sites.inc.
#define ILC_SITE_KIND_CALL 1u
#define ILC_SITE_KIND_DETOUR 2u
#define ILC_SITE_KIND_IMM 3u
#define ILC_SITE_KIND_BYTES 4u
#define ILC_SITE_KIND_VERIFY 5u

#define ILC_SITE_POLICY_PATCH 1u    // may be hooked; a mismatch refuses the whole install
#define ILC_SITE_POLICY_REQUIRE 2u  // must be stock; a mismatch refuses the whole install
#define ILC_SITE_POLICY_GATE 3u     // a mismatch skips the one site it gates
#define ILC_SITE_POLICY_INFO 4u     // read and reported, never written, never a refusal

// Subsystem of a site: one bit of ilc_settings.subsystems_armed, or CORE (no bit: armed while installed), or NONE
// (never written).
#define ILC_SITE_SUBSYS_INPUT_ISOLATION static_cast<uint32_t>(ILC_SUBSYS_INPUT_ISOLATION)
#define ILC_SITE_SUBSYS_MOVEMENT static_cast<uint32_t>(ILC_SUBSYS_MOVEMENT)
#define ILC_SITE_SUBSYS_PARTY static_cast<uint32_t>(ILC_SUBSYS_PARTY)
#define ILC_SITE_SUBSYS_CAMERA static_cast<uint32_t>(ILC_SUBSYS_CAMERA)
#define ILC_SITE_SUBSYS_FIREWALL_MENUS static_cast<uint32_t>(ILC_SUBSYS_FIREWALL_MENUS)
#define ILC_SITE_SUBSYS_WAIT static_cast<uint32_t>(ILC_SUBSYS_WAIT)
#define ILC_SITE_SUBSYS_RENDER static_cast<uint32_t>(ILC_SUBSYS_RENDER)
#define ILC_SITE_SUBSYS_TARGETS_CARDS static_cast<uint32_t>(ILC_SUBSYS_TARGETS_CARDS)
#define ILC_SITE_SUBSYS_CORE 0u
#define ILC_SITE_SUBSYS_NONE 0x80000000u  // NOTADDR: sentinel, not a subsystem bit

#define ILC_SITE_FLAG_INTERIOR 0x01u
#define ILC_SITE_FLAG_DROPPED 0x02u
#define ILC_SITE_FLAG_RIPREL 0x04u

#define ILC_SITE_NO_GATE 0xFFFFu

#include "patch/sites.inc"

namespace ilc::patch {

struct site {
    const char* id;
    uint32_t rva;          // added to the module base
    uint8_t length;        // recorded site length: the bytes of `expected`
    uint8_t kind;          // ILC_SITE_KIND_*
    uint8_t policy;        // ILC_SITE_POLICY_*
    uint8_t flags;         // ILC_SITE_FLAG_*
    uint32_t subsystem;    // ILC_SITE_SUBSYS_*
    uint16_t gate;         // GATE policy: index of the site a mismatch skips; else ILC_SITE_NO_GATE
    uint8_t hook_len;      // PATCH: whole instructions under the 5-byte jump (what MinHook relocates); else 0
    uint8_t tail_len;      // bytes of hook_tail
    const char* expected;  // `length` pinned original bytes
    const char* hook_tail; // pinned original bytes [length, hook_len) when hook_len > length
    const char* purpose;
};

constexpr uint32_t SITE_COUNT = ILC_SITE_COUNT;
constexpr uintptr_t IMAGE_BASE = ILC_SITE_BUILD_IMAGE_BASE;
constexpr uint32_t IMAGE_TIMESTAMP = ILC_SITE_BUILD_TIMESTAMP;
constexpr uint32_t IMAGE_SIZE = ILC_SITE_BUILD_SIZE_OF_IMAGE;
constexpr uint32_t HOOK_JUMP = 5;  // MinHook x64: E9 rel32 at the site

#define ILC_SITE_ROW(id, rva, len, kind, policy, subsys, flags, gate, expected, hook_len, tail, purpose)    \
    site{id, rva, len, kind, policy, flags, subsys, gate, hook_len, sizeof(tail) - 1, expected, tail, purpose},
inline constexpr site SITES[SITE_COUNT] = {ILC_SITE_LIST(ILC_SITE_ROW)};
#undef ILC_SITE_ROW

// Static checks on every row: the byte strings have the declared lengths, a PATCH row covers the 5-byte jump
// with whole instructions and pins every byte of it, and no other row claims a hook window.
#define ILC_SITE_CHECK(id, rva, len, kind, policy, subsys, flags, gate, expected, hook_len, tail, purpose)  \
    static_assert(sizeof(expected) - 1 == (len), "expected bytes != length");                               \
    static_assert(sizeof(tail) - 1 == ((hook_len) > (len) ? (hook_len) - (len) : 0), "hook_tail length");   \
    static_assert((policy) != ILC_SITE_POLICY_PATCH || (hook_len) >= 5, "PATCH site without a hook window"); \
    static_assert((policy) == ILC_SITE_POLICY_PATCH || (hook_len) == 0, "hook window on a non-PATCH site");  \
    static_assert((policy) != ILC_SITE_POLICY_PATCH || (subsys) != ILC_SITE_SUBSYS_NONE, "PATCH site in NONE"); \
    static_assert((rva) > 0 && (rva) + 32 < ILC_SITE_BUILD_SIZE_OF_IMAGE, "rva outside the image");
ILC_SITE_LIST(ILC_SITE_CHECK)
#undef ILC_SITE_CHECK

static_assert((ILC_ENUM_OR(ILC_SUBSYSTEMS) & ILC_SITE_SUBSYS_NONE) == 0, "NONE sentinel collides with a bit");

// Every GATE row names a PATCH row, no two GATE rows name the same one, and only GATE rows carry a gate.
constexpr bool gates_well_formed()
{
    for (uint32_t i = 0; i < SITE_COUNT; ++i) {
        const site& g = SITES[i];
        if (g.policy != ILC_SITE_POLICY_GATE) {
            if (g.gate != ILC_SITE_NO_GATE) {
                return false;
            }
            continue;
        }
        if (g.gate >= SITE_COUNT || SITES[g.gate].policy != ILC_SITE_POLICY_PATCH) {
            return false;
        }
        for (uint32_t j = 0; j < i; ++j) {
            if (SITES[j].policy == ILC_SITE_POLICY_GATE && SITES[j].gate == g.gate) {
                return false;
            }
        }
    }
    return true;
}
static_assert(gates_well_formed(), "a GATE row must target its own PATCH row");

// Bytes of a row that the preflight compares: `expected`, then the hook tail.
constexpr uint32_t checked_len(const site& s)
{
    return s.length + s.tail_len;
}

constexpr uint8_t checked_byte(const site& s, uint32_t k)
{
    return static_cast<uint8_t>(k < s.length ? s.expected[k] : s.hook_tail[k - s.length]);
}

}  // namespace ilc::patch
