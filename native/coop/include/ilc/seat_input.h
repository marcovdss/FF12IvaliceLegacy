// Ivalice Legacy co-op module: the per-seat input frame, and the interface the session reads it through.
//
// This is the network seam for input. The session core never reads a device: it reads one frame per seat from a
// seat_frame_source. Phase 1 has one implementation, the local input service (src/input/), which turns local
// pads into frames. A later transport can implement the same interface with frames that arrived from another
// machine; the frame is flat, little-endian and has no pointers, so it can be sent as bytes.
//
// A frame describes one seat's pad after device handling: which source fed it (XInput slot or SDL controller),
// its connection generation, control strengths after the radial deadzone, held/pressed/released bits, the
// "absorbed" bits (controls that were already held when the source changed and stay absorbed until released),
// and the raw pad packed as an XINPUT_GAMEPAD image. Bindings, repeat and actions are built on top of it.
#pragma once

#include <stdint.h>

#include "ilc/bridge_abi.h"

// Control indices follow ilc_pad_control: bit (code - 1) in the masks, element (code - 1) in strength[].
inline constexpr uint32_t ILC_PAD_CONTROL_COUNT = 24;
static_assert(ILC_ENUM_COUNT(ILC_PAD_CONTROLS) == ILC_PAD_CONTROL_COUNT, "pad control count");

inline constexpr uint32_t ilc_pad_bit(ilc_pad_control code)
{
    return 1u << (static_cast<uint32_t>(code) - 1u);
}

// XINPUT_GAMEPAD layout (12 bytes). Button bits are XInput's: DPAD_UP 0x0001 ... Y 0x8000.
struct ilc_xinput_gamepad {
    uint16_t buttons;
    uint8_t lt;  // 0..255
    uint8_t rt;
    int16_t lx;
    int16_t ly;
    int16_t rx;
    int16_t ry;
};
static_assert(sizeof(ilc_xinput_gamepad) == 12, "ilc_xinput_gamepad");

enum ilc_frame_source : uint8_t {
    ILC_FRAME_SOURCE_NONE = 0,
    ILC_FRAME_SOURCE_XINPUT = 1,
    ILC_FRAME_SOURCE_SDL = 2,
    ILC_FRAME_SOURCE_REMOTE = 3,  // reserved for a transport; never produced in phase 1
};

enum ilc_frame_flag : uint8_t {
    ILC_FRAMEF_CONNECTED = 0x01,  // a live source fed this frame; clear = neutral frame
    ILC_FRAMEF_ASSIGNED = 0x02,   // the seat has a configured device (it may be disconnected)
    ILC_FRAMEF_BRIDGED = 0x04,    // a read failed; this is the last good sample inside the 0.100 s grace
    ILC_FRAMEF_RESEEDED = 0x08,   // the source changed this frame; everything held was absorbed
    ILC_FRAMEF_VIA_ALIAS = 0x10,  // the source is an alias of the configured device, not the device itself
};

struct ilc_seat_input_frame {
    uint32_t seq;                // input tick that produced the frame (monotonic per service run)
    uint32_t source_generation;  // connection generation of the source; 0 = no source
    uint64_t time_us;            // service clock of the tick
    uint8_t seat;                // 1..4
    uint8_t source;              // ilc_frame_source
    uint8_t xinput_slot;         // 0..3 when source is XINPUT, else 0xFF
    uint8_t flags;               // ilc_frame_flag bits
    uint32_t held;               // strength >= 0.5
    uint32_t absorbed;           // held, but already held when the source changed: produces no press
    uint32_t pressed;            // held now, not held last frame, not absorbed
    uint32_t released;           // held last frame, not now
    uint16_t strength[ILC_PAD_CONTROL_COUNT];  // 0..65535 = 0.0..1.0, sticks after the radial deadzone
    int16_t left_stick[2];       // x, y (up positive) after the radial deadzone, -32767..32767
    int16_t right_stick[2];
    ilc_xinput_gamepad pad;      // raw (no deadzone) pad as an XINPUT_GAMEPAD; all zero when not connected
    uint8_t reserved[8];
};
static_assert(sizeof(ilc_seat_input_frame) == 112, "ilc_seat_input_frame");
static_assert(__builtin_offsetof(ilc_seat_input_frame, reserved) + 8 == sizeof(ilc_seat_input_frame),
              "ilc_seat_input_frame has no implicit padding");

// ---------------------------------------------------------------------------------------------------------
// The per-seat action frame: one seat's logical actions after bindings, edges, repeat and hold absorption.
// This is what a remote machine would send for its seat (logical actions only, never device data).
// Bit n of each mask is ilc_action n; strength[n] is that action's strength 0..65535.

enum ilc_prompt_device : uint8_t {
    ILC_PROMPT_NONE = 0,
    ILC_PROMPT_PAD = 1,       // the last fresh press came from the seat's pad
    ILC_PROMPT_KEYBOARD = 2,  // the last fresh press came from the keyboard or mouse (the seat owns them)
};

enum ilc_action_flag : uint8_t {
    ILC_ACTF_PAD = 0x01,         // the seat had a connected pad this tick
    ILC_ACTF_KBM = 0x02,         // the seat owns the keyboard and mouse
    ILC_ACTF_FOCUSED = 0x04,     // the game window was in the foreground (keyboard and mouse are read)
    ILC_ACTF_SUPPRESSED = 0x08,  // pairing or capture owns this seat's input: every action reads neutral
    ILC_ACTF_RESEEDED = 0x10,    // a discontinuity this tick: everything held was absorbed
    ILC_ACTF_FAST = 0x20,        // fast repeat is on for this seat
};

struct ilc_seat_action_frame {
    uint32_t seq;                // input tick that produced the frame
    uint32_t source_generation;  // the pad frame's source generation (0 = no pad)
    uint64_t time_us;            // service clock of the tick
    uint8_t seat;                // 1..4
    uint8_t prompt_device;       // ilc_prompt_device
    uint8_t flags;               // ilc_action_flag bits
    uint8_t reserved0;
    uint32_t held;               // held now, absorbed holds included (for steering and other continuous use)
    uint32_t pressed;            // a fresh press this tick (never from an absorbed hold)
    uint32_t released;           // stopped being held this tick
    uint32_t repeated;           // a repeat pulse this tick (held menu directions only; never on the press tick)
    uint32_t absorbed;           // held only through controls/keys absorbed by a discontinuity: no edges until released
    uint16_t strength[ILC_ACTIONS];  // 0 while not held
    uint8_t reserved[2];
};
static_assert(sizeof(ilc_seat_action_frame) == 80, "ilc_seat_action_frame");
static_assert(__builtin_offsetof(ilc_seat_action_frame, reserved) + 2 == sizeof(ilc_seat_action_frame),
              "ilc_seat_action_frame has no implicit padding");

namespace ilc {

// What the session core consumes. latest() copies the newest whole frame for seat 1..4 and returns false if no
// frame exists yet (service not running). Implementations never block the caller.
class seat_frame_source {
public:
    virtual bool latest(uint32_t seat, ilc_seat_input_frame* out) = 0;

protected:
    ~seat_frame_source() = default;
};

// The same for action frames.
class seat_action_source {
public:
    virtual bool latest(uint32_t seat, ilc_seat_action_frame* out) = 0;

protected:
    ~seat_action_source() = default;
};

}  // namespace ilc
