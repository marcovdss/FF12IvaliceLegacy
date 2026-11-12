// Ivalice Legacy co-op module, host tests only (never linked into the shipped DLL): device worlds for the
// input service.
//
//   * world_xinput / world_sdl: a scripted device world. A test sets what each XInput slot returns and which SDL
//     devices exist (with failures: unreadable info, failed open, failed read, omitted from enumeration,
//     reordered between query and open, failed global update).
//   * recorder: wraps any xinput_api/sdl_api and writes every call and its answer to a transcript.
//   * transcript_xinput / transcript_sdl: answer from a transcript, call for call. A call that does not match the
//     next record (other kind or other arguments) is counted as a mismatch and fails closed.
#pragma once

#include <stdint.h>

#include "input/input_api.h"

namespace ilc::input::test {

inline constexpr uint32_t WORLD_SDL_MAX = 24;

struct world_sdl_device {
    int32_t instance_id;
    uint8_t guid[16];
    uint8_t is_controller;
    uint8_t info_fails;   // device_info() fails for this index (a broken virtual device)
    uint8_t open_fails;
    uint8_t attached;
    uint8_t read_fails;
    uint8_t omitted;      // left out of enumeration while still attached
    uint8_t reserved[2];
    char path[SDL_PATH_MAX];
    char name[SDL_NAME_MAX];
    sdl_reading reading;
};

struct world_state {
    uint32_t xinput_rc[4];
    xinput_reading xinput[4];
    uint32_t sdl_count;
    world_sdl_device sdl[WORLD_SDL_MAX];
    uint32_t pump_fails;
    uint32_t count_fails;
    int32_t reorder_open;  // open(i) opens enumerated index (i + reorder_open) mod n
};

world_state& world();
void world_reset();
uint32_t world_open_handles();  // handles opened and not yet closed

xinput_api* world_xinput();
sdl_api* world_sdl();

// Recording: wraps the given APIs (either may be null).
void recorder_begin(xinput_api* xinput, sdl_api* sdl, xinput_api** out_xinput, sdl_api** out_sdl);
uint32_t recorder_size();
uint32_t recorder_copy(void* out, uint32_t cap);

// Replay from a transcript (copied).
bool transcript_begin(const void* data, uint32_t size, xinput_api** out_xinput, sdl_api** out_sdl);
uint32_t transcript_mismatches();
uint32_t transcript_remaining();
uint32_t transcript_record_bytes();

}  // namespace ilc::input::test
