// Ivalice Legacy co-op module, host tests only: a scripted keyboard and the action/seat-command views of the
// manual input service (exports of the test build, ilc-coop-test.dll).
#include <string.h>

#include "input/actions.h"
#include "input/input_service.h"
#include "input/keyboard.h"

using namespace ilc;
using namespace ilc::input;

namespace {

class world_keys final : public keyboard_api {
public:
    bool focused() override
    {
        ++focus_queries;
        return has_focus != 0;
    }

    bool down(uint8_t vk) override
    {
        ++reads;
        key_set(asked, vk);
        return key_is_down(state, vk);
    }

    uint32_t has_focus = 1;
    uint32_t reads = 0;
    uint32_t focus_queries = 0;
    key_state state = {};
    key_state asked = {};
};

world_keys g_keys;

}  // namespace

extern "C" {

// on = 1: the manual service reads the scripted keyboard; 0: no keyboard. Resets keys, focus (on) and counters.
void ILCT_KeyboardUse(uint32_t on)
{
    g_keys.state = {};
    g_keys.asked = {};
    g_keys.reads = 0;
    g_keys.focus_queries = 0;
    g_keys.has_focus = 1;
    manual_keyboard(on ? &g_keys : nullptr);
}

void ILCT_KeySet(uint32_t vk, uint32_t down)
{
    if (vk >= 256) {
        return;
    }
    if (down) {
        key_set(g_keys.state, vk);
    } else {
        g_keys.state.down[vk >> 5] &= ~(1u << (vk & 31u));
    }
}

void ILCT_Focus(uint32_t on)
{
    g_keys.has_focus = on;
}

// reads, focus queries, then the 256-bit set of keys asked since the last call (8 u32); the counters reset.
void ILCT_KeyReads(uint32_t out[10])
{
    out[0] = g_keys.reads;
    out[1] = g_keys.focus_queries;
    memcpy(&out[2], g_keys.asked.down, sizeof(g_keys.asked.down));
    g_keys.reads = 0;
    g_keys.focus_queries = 0;
    g_keys.asked = {};
}

int32_t ILCT_Actions(uint32_t seat, ilc_seat_action_frame* out)
{
    return local_actions().latest(seat, out) ? 1 : 0;
}

uint32_t ILCT_ActionFrameSize(void)
{
    return sizeof(ilc_seat_action_frame);
}

void ILCT_SetFast(uint32_t seat, uint32_t on)
{
    set_fast(seat, on != 0);
}

void ILCT_SetDecideSwap(uint32_t on)
{
    set_decide_swap(on != 0);
}

int32_t ILCT_Control(control_view* out)
{
    snapshot s;
    const bool ok = snapshot_read(&s);
    *out = s.control;
    return ok ? 1 : 0;
}

uint32_t ILCT_ControlViewSize(void)
{
    return sizeof(control_view);
}

// The binding a seat's table holds after the decide swap (for prompt tests): kind | code << 8.
uint32_t ILCT_EffectiveBinding(uint32_t kind, uint32_t code, uint32_t action, uint32_t swap)
{
    ilc_binding b;
    b.kind = static_cast<uint8_t>(kind);
    b.code = static_cast<uint8_t>(code);
    const ilc_binding e = effective_binding(b, action, swap != 0);
    return static_cast<uint32_t>(e.kind) | (static_cast<uint32_t>(e.code) << 8);
}

}  // extern "C"
