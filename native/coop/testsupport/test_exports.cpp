// Ivalice Legacy co-op module, host tests only: extra exports of the test build (ilc-coop-test.dll).
//
// The shipped DLL has none of these. The test build is the shipped sources plus this folder, so a test drives
// the real input service and the real bridge (GET_DEVICES) through ctypes.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <string.h>

#include "ilc/bridge_abi.h"
#include "input/input_core.h"
#include "input/input_service.h"
#include "input/sha256.h"
#include "input_replay.h"

extern "C" int32_t ILC_Bridge(uint64_t op, const void* in, uint64_t in_len, void* out, uint64_t out_cap);

using namespace ilc;
using namespace ilc::input;

extern "C" {

// ---- the scripted world --------------------------------------------------------------------------------------
void ILCT_WorldReset(void)
{
    test::world_reset();
}

void ILCT_XInputSet(uint32_t slot, uint32_t rc, uint32_t packet, const ilc_xinput_gamepad* pad)
{
    if (slot >= 4) {
        return;
    }
    test::world().xinput_rc[slot] = rc;
    test::world().xinput[slot].packet = packet;
    if (pad != nullptr) {
        test::world().xinput[slot].pad = *pad;
    }
}

// Adds or replaces (same instance id) an SDL device; returns its list index or -1.
int32_t ILCT_SdlPut(const test::world_sdl_device* device)
{
    test::world_state& w = test::world();
    for (uint32_t i = 0; i < w.sdl_count; ++i) {
        if (w.sdl[i].instance_id == device->instance_id) {
            w.sdl[i] = *device;
            return static_cast<int32_t>(i);
        }
    }
    if (w.sdl_count >= test::WORLD_SDL_MAX) {
        return -1;
    }
    w.sdl[w.sdl_count] = *device;
    return static_cast<int32_t>(w.sdl_count++);
}

int32_t ILCT_SdlRemove(int32_t instance_id)
{
    test::world_state& w = test::world();
    for (uint32_t i = 0; i < w.sdl_count; ++i) {
        if (w.sdl[i].instance_id == instance_id) {
            memmove(&w.sdl[i], &w.sdl[i + 1], (w.sdl_count - i - 1) * sizeof(w.sdl[0]));
            --w.sdl_count;
            return 1;
        }
    }
    return 0;
}

// Moves the device with this instance id to list position `position` (enumeration order).
int32_t ILCT_SdlMove(int32_t instance_id, uint32_t position)
{
    test::world_state& w = test::world();
    for (uint32_t i = 0; i < w.sdl_count; ++i) {
        if (w.sdl[i].instance_id == instance_id) {
            test::world_sdl_device d = w.sdl[i];
            memmove(&w.sdl[i], &w.sdl[i + 1], (w.sdl_count - i - 1) * sizeof(w.sdl[0]));
            const uint32_t pos = position < w.sdl_count - 1 ? position : w.sdl_count - 1;
            memmove(&w.sdl[pos + 1], &w.sdl[pos], (w.sdl_count - 1 - pos) * sizeof(w.sdl[0]));
            w.sdl[pos] = d;
            return 1;
        }
    }
    return 0;
}

void ILCT_SdlGlobal(uint32_t pump_fails, uint32_t count_fails, int32_t reorder_open)
{
    test::world().pump_fails = pump_fails;
    test::world().count_fails = count_fails;
    test::world().reorder_open = reorder_open;
}

uint32_t ILCT_SdlOpenHandles(void)
{
    return test::world_open_handles();
}

uint32_t ILCT_WorldDeviceSize(void)
{
    return sizeof(test::world_sdl_device);
}

// ---- the service in manual mode --------------------------------------------------------------------------------
// flags: bit 0 XInput present, bit 1 SDL present, bit 2 record every device call.
void ILCT_Begin(uint32_t flags, uint32_t session)
{
    xinput_api* xinput = (flags & 1u) ? test::world_xinput() : nullptr;
    sdl_api* sdl = (flags & 2u) ? test::world_sdl() : nullptr;
    if (flags & 4u) {
        test::recorder_begin(xinput, sdl, &xinput, &sdl);
    }
    manual_begin(xinput, sdl, session);
}

int32_t ILCT_BeginReplay(const void* transcript, uint32_t size, uint32_t session)
{
    xinput_api* xinput = nullptr;
    sdl_api* sdl = nullptr;
    if (!test::transcript_begin(transcript, size, &xinput, &sdl)) {
        return 0;
    }
    manual_begin(xinput, sdl, session);
    return 1;
}

void ILCT_Step(uint64_t now_us)
{
    manual_step(now_us);
}

void ILCT_End(void)
{
    manual_end();
}

uint32_t ILCT_RecordingSize(void)
{
    return test::recorder_size();
}

uint32_t ILCT_RecordingCopy(void* out, uint32_t cap)
{
    return test::recorder_copy(out, cap);
}

uint32_t ILCT_ReplayMismatches(void)
{
    return test::transcript_mismatches();
}

uint32_t ILCT_ReplayRemaining(void)
{
    return test::transcript_remaining();
}

uint32_t ILCT_RecordBytes(void)
{
    return test::transcript_record_bytes();
}

// The input part of a settings record (validated by the caller through SET_SETTINGS first, or not at all).
int32_t ILCT_ConfigureFromSettings(const void* settings, uint32_t size)
{
    if (size != sizeof(ilc_settings)) {
        return 0;
    }
    ilc_settings copy;
    memcpy(&copy, settings, sizeof(copy));
    configure_from_settings(copy);
    return 1;
}

// The bridge's applied settings (after SET_SETTINGS and the safe point), read back through ILC_Bridge.
int32_t ILCT_ConfigureFromApplied(void)
{
    static ilc_settings applied;
    const int32_t n = ILC_Bridge(ILC_OP_GET_SETTINGS, nullptr, 0, &applied, sizeof(applied));
    if (n != static_cast<int32_t>(sizeof(applied))) {
        return 0;
    }
    configure_from_settings(applied);
    return 1;
}

int32_t ILCT_Frame(uint32_t seat, ilc_seat_input_frame* out)
{
    return local_frames().latest(seat, out) ? 1 : 0;
}

// state, backends, issues, session, seq, inventory count, then the core counters (8 u32).
int32_t ILCT_Status(uint32_t out[14])
{
    snapshot s;
    const bool ok = snapshot_read(&s);
    out[0] = s.state;
    out[1] = s.backends;
    out[2] = s.issues;
    out[3] = s.session;
    out[4] = s.seq;
    out[5] = s.inventory_count;
    memcpy(&out[6], &s.counters, sizeof(s.counters));
    return ok ? 1 : 0;
}

uint32_t ILCT_FrameSize(void)
{
    return sizeof(ilc_seat_input_frame);
}

// ---- pure functions ----------------------------------------------------------------------------------------------
void ILCT_Sha256(const void* data, uint32_t size, uint8_t out[32])
{
    sha256(data, size, out);
}

void ILCT_Uid(const char* path, uint8_t out[16])
{
    device_uid(path, out);
}

void ILCT_Deadzone(float x, float y, float deadzone, float out[2])
{
    radial_deadzone(x, y, deadzone, &out[0], &out[1]);
}

// strengths[24], then left x/y, right x/y as int16 in sticks[4].
void ILCT_StrengthsXInput(const ilc_xinput_gamepad* pad, uint16_t strengths[24], int16_t sticks[4])
{
    control_strengths(normalize_xinput(*pad), strengths, &sticks[0], &sticks[2]);
}

void ILCT_StrengthsSdl(const sdl_reading* reading, uint16_t strengths[24], int16_t sticks[4])
{
    control_strengths(normalize_sdl(*reading), strengths, &sticks[0], &sticks[2]);
}

void ILCT_PackSdl(const sdl_reading* reading, ilc_xinput_gamepad* out)
{
    *out = pack_xinput(normalize_sdl(*reading));
}

uint32_t ILCT_Sanitize(const char* in, char out[96])
{
    return sanitize_name(in, out);
}

// ---- the live service on this machine (no game) --------------------------------------------------------------------
int32_t ILCT_StartLive(void)
{
    return start_live() ? 1 : 0;
}

int32_t ILCT_StopLive(uint32_t timeout_ms)
{
    return stop_live(timeout_ms) ? 1 : 0;
}

}  // extern "C"
