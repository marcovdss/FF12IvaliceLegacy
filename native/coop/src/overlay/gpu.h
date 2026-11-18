// Ivalice Legacy co-op overlay: the D3D11 side (the only part of the module that talks to the GPU).
//
// Everything here runs on the render thread, inside the OV-01 handler, inside guard::call, between the game's last
// draw of the frame and its present wrapper (which flushes and presents right after). It uses the game's own device,
// immediate context and swap chain, read from the renderer at +0x108 / +0x110 / +0x118 (proven by OV-V1..OV-V3), and
// its own objects: one vertex and one pixel shader (precompiled, shaders.inc), an input layout, a dynamic vertex buffer
// of QUAD_CAP quads, the theme's font atlas as an immutable R8 texture (re-created when the theme changes), a linear clamp sampler, a premultiplied-alpha blend
// state, a no-cull no-scissor rasterizer state and a depth-off state. They are created once per device; nothing is
// created per frame except the back buffer's render target view, which is made and released every frame (the swap
// chain's buffer is never held across frames, so the game's own teardown frees it).
//
// State guard. Every pipeline state the draw changes is read first and put back after it: input layout, topology,
// vertex buffer slot 0, VS (with class instances), PS (with class instances), PS shader resource 0, PS sampler 0,
// GS/HS/DS (nulled for the draw), rasterizer state, viewports, scissor rects, blend state with factor and mask,
// depth-stencil state with reference, and every render target with the depth-stencil view. UAVs, constant buffers,
// the index buffer, CS and stream output are never touched (the draw does not need them). If the draw faults midway,
// restore_after_fault() puts the saved state back (called in its own guarded call).
//
// Failure. A creation failure, a swap chain whose device is not the renderer's, a removed device, a back buffer
// smaller than 640x360 or in a format we cannot draw to: nothing is drawn (fail-open), the reason is recorded once
// (status()) and the module logs it from the co-op thread. A creation failure is final for that device.
#pragma once

#include <stdint.h>

#include "overlay/batch.h"

namespace ilc::overlay::gpu {

enum result : int32_t {
    DRAWN = 0,             // the batch was drawn
    READY = 1,             // the device is usable; nothing to draw (empty batch)
    NO_RENDERER = 2,       // a renderer field is null or unreadable
    MISMATCH = 3,          // the swap chain's device is not the renderer's device
    CREATE_FAILED = 4,     // one of our objects could not be created (final for this device)
    DEVICE_REMOVED = 5,
    NO_BACK_BUFFER = 6,    // GetBuffer failed
    BAD_BACK_BUFFER = 7,   // smaller than 640x360, or a format we do not draw to
    RTV_FAILED = 8,
    MAP_FAILED = 9,
};

enum object_id : uint32_t {
    OBJ_NONE = 0, OBJ_VS, OBJ_PS, OBJ_LAYOUT, OBJ_VB, OBJ_ATLAS, OBJ_SRV, OBJ_SAMPLER, OBJ_BLEND, OBJ_RASTER, OBJ_DEPTH,
    OBJ_ATLAS_DATA,        // the atlas could not be decoded (no D3D call made)
};

struct frame_info {
    uint32_t w, h;         // back buffer
    uint32_t format;       // DXGI_FORMAT
    uint32_t samples;
    uint32_t quads;        // drawn
};

// The active theme has a decoded atlas (theme::attach made the default one at the boot, before any frame). The texture
// follows the theme: a switch (theme::atlas_generation) re-creates it on the next frame.
bool prepare_atlas();

// Draws `b` (may be empty: then only the device is checked and prepared). renderer = rcx of the present wrapper.
int32_t frame(uint64_t renderer, const batch& b, frame_info* info);
// After a fault inside frame(): put back the state frame() saved, if it saved any and has not restored it.
void restore_after_fault();

struct status_view {
    uint64_t device;       // the device our objects belong to (0 = none yet)
    uint32_t ready;        // our objects exist for it
    uint32_t failed_object;// object_id that failed (0 = none)
    int32_t failed_hr;     // its HRESULT
    uint32_t last_w, last_h, last_format;
};
status_view status();

// Names for the log.
const char* object_name(uint32_t id);
const char* result_name(int32_t r);

// ---- host tests -------------------------------------------------------------------------------------------------------
// The state slots the guard saves and the ones the draw binds (names), to check the save list covers the bind list.
uint32_t test_saved_states(const char** out, uint32_t cap);
uint32_t test_bound_states(const char** out, uint32_t cap);
// A format the draw accepts.
bool test_format_ok(uint32_t dxgi_format);
// 1: the next frame() faults (a load from address 8) right after binding its pipeline state, before the draw: the
// restore drill. Cleared by that frame. 0: off.
void test_set_fault(uint32_t where);

}  // namespace ilc::overlay::gpu
