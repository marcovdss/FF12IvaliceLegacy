// Ivalice Legacy co-op module, host tests only: our own overlay (ILCT_Ov*) and the P1-native frontend switch.
//
// Pure parts (font, batch, scene) are passed through. The GPU side runs for real on a WARP device (Windows' software
// D3D11 rasteriser, loaded with LoadLibrary: the host-test DLL only, never the module): a render-target texture
// stands in for the back buffer, a small fake swap chain hands it out, and a fake renderer holds device, context and
// swap chain at +0x108 / +0x110 / +0x118 exactly where the game keeps them. The tests then read the pixels back and
// compare the pipeline state before and after a frame.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <string.h>

#include "frontend/frontend.h"
#include "interaction/interaction.h"
#include "interaction/prompt.h"
#include "interaction/treasure_log.h"
#include "overlay/batch.h"
#include "overlay/font.h"
#include "overlay/gpu.h"
#include "overlay/hud_scene.h"
#include "overlay/overlay.h"
#include "overlay/scene.h"
#include "runtime/guard.h"
#include "runtime/log.h"

using namespace ilc;
using namespace ilc::overlay;

namespace {

template <typename T>
void drop(T*& p)
{
    if (p != nullptr) {
        p->Release();
        p = nullptr;
    }
}

// ---- the fake swap chain ----------------------------------------------------------------------------------------------
class fake_swap final : public IDXGISwapChain {
public:
    ID3D11Device* device = nullptr;      // what GetDevice answers (the renderer's, or another for the mismatch drill)
    ID3D11Texture2D* back = nullptr;     // what GetBuffer(0) answers
    uint32_t get_buffer_calls = 0;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID riid, void** out) override
    {
        if (riid == __uuidof(IUnknown) || riid == __uuidof(IDXGIObject) || riid == __uuidof(IDXGIDeviceSubObject) ||
            riid == __uuidof(IDXGISwapChain)) {
            *out = this;
            return S_OK;
        }
        *out = nullptr;
        return E_NOINTERFACE;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return 2; }
    ULONG STDMETHODCALLTYPE Release() override { return 1; }
    HRESULT STDMETHODCALLTYPE SetPrivateData(REFGUID, UINT, const void*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE SetPrivateDataInterface(REFGUID, const IUnknown*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetPrivateData(REFGUID, UINT*, void*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetParent(REFIID, void** out) override
    {
        *out = nullptr;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE GetDevice(REFIID riid, void** out) override
    {
        if (device == nullptr) {
            *out = nullptr;
            return E_FAIL;
        }
        return device->QueryInterface(riid, out);
    }
    HRESULT STDMETHODCALLTYPE Present(UINT, UINT) override { return S_OK; }
    HRESULT STDMETHODCALLTYPE GetBuffer(UINT index, REFIID riid, void** out) override
    {
        ++get_buffer_calls;
        if (index != 0 || back == nullptr) {
            *out = nullptr;
            return E_FAIL;
        }
        return back->QueryInterface(riid, out);
    }
    HRESULT STDMETHODCALLTYPE SetFullscreenState(WINBOOL, IDXGIOutput*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFullscreenState(WINBOOL*, IDXGIOutput**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetDesc(DXGI_SWAP_CHAIN_DESC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ResizeBuffers(UINT, UINT, UINT, DXGI_FORMAT, UINT) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE ResizeTarget(const DXGI_MODE_DESC*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetContainingOutput(IDXGIOutput**) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetFrameStatistics(DXGI_FRAME_STATISTICS*) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE GetLastPresentCount(UINT*) override { return E_NOTIMPL; }
};

struct warp_world {
    HMODULE d3d11;
    ID3D11Device* device;
    ID3D11DeviceContext* context;
    ID3D11Device* other;                 // a second device (the mismatch drill)
    ID3D11Texture2D* back;
    ID3D11Texture2D* staging;
    uint32_t w, h, format;
    fake_swap swap;
    alignas(16) uint8_t renderer[0x200]; // device +0x108, context +0x110, swap chain +0x118
};
warp_world g_warp = {};
batch g_batch;

using create_fn = HRESULT(WINAPI*)(IDXGIAdapter*, D3D_DRIVER_TYPE, HMODULE, UINT, const D3D_FEATURE_LEVEL*, UINT, UINT,
                                   ID3D11Device**, D3D_FEATURE_LEVEL*, ID3D11DeviceContext**);

bool make_device(ID3D11Device** d, ID3D11DeviceContext** c)
{
    if (g_warp.d3d11 == nullptr) {
        g_warp.d3d11 = LoadLibraryW(L"d3d11.dll");
    }
    if (g_warp.d3d11 == nullptr) {
        return false;
    }
    const auto create = reinterpret_cast<create_fn>(reinterpret_cast<void*>(GetProcAddress(g_warp.d3d11, "D3D11CreateDevice")));
    if (create == nullptr) {
        return false;
    }
    const D3D_FEATURE_LEVEL levels[] = {D3D_FEATURE_LEVEL_11_0, D3D_FEATURE_LEVEL_10_0};
    return SUCCEEDED(create(nullptr, D3D_DRIVER_TYPE_WARP, nullptr, 0, levels, 2, D3D11_SDK_VERSION, d, nullptr, c));
}

void put_renderer(uint32_t offset, uint64_t value)
{
    memcpy(g_warp.renderer + offset, &value, sizeof(value));
}

void compose(uint32_t kind, const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s, uint64_t now,
             const scene::card_info* ci, scene::menus_out* mo)
{
    // As the module's frame job: the seat windows claim the batch first, the card is composed after them and moved
    // underneath.
    g_batch.clear();
    scene::menus_out out = {0, 0, 0};
    if ((kind & 2u) != 0 && v != nullptr && s != nullptr) {
        out = scene::seat_windows(g_batch, *v, *s, now, (kind & 4u) != 0);
    }
    const uint32_t card_from = g_batch.count();
    if ((kind & 1u) != 0 && ci != nullptr) {
        scene::test_card(g_batch, *ci);
        g_batch.to_front(card_from);
    }
    if (mo != nullptr) {
        *mo = out;
    }
}

uint32_t copy_quads(const batch& b, quad* out, uint32_t cap)
{
    const uint32_t n = b.count();
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = b.at(k);
    }
    return n;
}

// ---- a distinctive pipeline state, to see that a frame puts everything back ----------------------------------------
struct probe_state {
    ID3D11Buffer* vb;
    ID3D11Texture2D* tex;
    ID3D11ShaderResourceView* srv;
    ID3D11SamplerState* sampler;
    ID3D11BlendState* blend;
    ID3D11RasterizerState* raster;
    ID3D11DepthStencilState* depth;
    ID3D11Texture2D* rt_tex;
    ID3D11RenderTargetView* rtv;
};
probe_state g_probe = {};

bool make_probe_state()
{
    ID3D11Device* d = g_warp.device;
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = 256;
    bd.Usage = D3D11_USAGE_DEFAULT;
    bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = td.Height = 8;
    td.MipLevels = td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8G8B8A8_UNORM;
    td.SampleDesc.Count = 1;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_TEXTURE2D_DESC rd = td;
    rd.BindFlags = D3D11_BIND_RENDER_TARGET;
    D3D11_SAMPLER_DESC sd = {};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_POINT;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_WRAP;
    sd.ComparisonFunc = D3D11_COMPARISON_NEVER;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    D3D11_BLEND_DESC bl = {};
    bl.RenderTarget[0].BlendEnable = TRUE;
    bl.RenderTarget[0].SrcBlend = D3D11_BLEND_SRC_COLOR;
    bl.RenderTarget[0].DestBlend = D3D11_BLEND_ZERO;
    bl.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE;
    bl.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_ZERO;
    bl.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].RenderTargetWriteMask = 0x5;
    D3D11_RASTERIZER_DESC rs = {};
    rs.FillMode = D3D11_FILL_WIREFRAME;
    rs.CullMode = D3D11_CULL_FRONT;
    rs.ScissorEnable = TRUE;
    D3D11_DEPTH_STENCIL_DESC ds = {};
    ds.DepthEnable = TRUE;
    ds.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ALL;
    ds.DepthFunc = D3D11_COMPARISON_GREATER;
    ds.FrontFace.StencilFailOp = ds.FrontFace.StencilDepthFailOp = ds.FrontFace.StencilPassOp = D3D11_STENCIL_OP_KEEP;
    ds.FrontFace.StencilFunc = D3D11_COMPARISON_ALWAYS;
    ds.BackFace = ds.FrontFace;
    return SUCCEEDED(d->CreateBuffer(&bd, nullptr, &g_probe.vb)) && SUCCEEDED(d->CreateTexture2D(&td, nullptr, &g_probe.tex)) &&
           SUCCEEDED(d->CreateShaderResourceView(g_probe.tex, nullptr, &g_probe.srv)) &&
           SUCCEEDED(d->CreateSamplerState(&sd, &g_probe.sampler)) && SUCCEEDED(d->CreateBlendState(&bl, &g_probe.blend)) &&
           SUCCEEDED(d->CreateRasterizerState(&rs, &g_probe.raster)) &&
           SUCCEEDED(d->CreateDepthStencilState(&ds, &g_probe.depth)) &&
           SUCCEEDED(d->CreateTexture2D(&rd, nullptr, &g_probe.rt_tex)) &&
           SUCCEEDED(d->CreateRenderTargetView(g_probe.rt_tex, nullptr, &g_probe.rtv));
}

void free_probe_state()
{
    drop(g_probe.rtv);
    drop(g_probe.rt_tex);
    drop(g_probe.depth);
    drop(g_probe.raster);
    drop(g_probe.blend);
    drop(g_probe.sampler);
    drop(g_probe.srv);
    drop(g_probe.tex);
    drop(g_probe.vb);
}

const FLOAT PROBE_FACTOR[4] = {0.25f, 0.5f, 0.75f, 1.0f};
const D3D11_VIEWPORT PROBE_VIEWPORTS[2] = {{1.0f, 2.0f, 13.0f, 17.0f, 0.1f, 0.9f}, {3.0f, 4.0f, 5.0f, 6.0f, 0.0f, 1.0f}};
const D3D11_RECT PROBE_SCISSOR = {1, 2, 7, 8};

void set_probe_state()
{
    ID3D11DeviceContext* c = g_warp.context;
    const UINT stride = 12;
    const UINT offset = 4;
    c->IASetInputLayout(nullptr);
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_POINTLIST);
    c->IASetVertexBuffers(0, 1, &g_probe.vb, &stride, &offset);
    c->VSSetShader(nullptr, nullptr, 0);
    c->PSSetShader(nullptr, nullptr, 0);
    c->PSSetShaderResources(0, 1, &g_probe.srv);
    c->PSSetSamplers(0, 1, &g_probe.sampler);
    c->RSSetState(g_probe.raster);
    c->RSSetViewports(2, PROBE_VIEWPORTS);
    c->RSSetScissorRects(1, &PROBE_SCISSOR);
    c->OMSetBlendState(g_probe.blend, PROBE_FACTOR, 0x0F0F0F0Fu);
    c->OMSetDepthStencilState(g_probe.depth, 7);
    c->OMSetRenderTargets(1, &g_probe.rtv, nullptr);
}

// Bit per slot that differs from set_probe_state()'s.
uint32_t check_probe_state()
{
    ID3D11DeviceContext* c = g_warp.context;
    uint32_t bad = 0;
    ID3D11InputLayout* il = nullptr;
    c->IAGetInputLayout(&il);
    bad |= il != nullptr ? 1u << 0 : 0u;
    drop(il);
    D3D11_PRIMITIVE_TOPOLOGY topo = D3D11_PRIMITIVE_TOPOLOGY_UNDEFINED;
    c->IAGetPrimitiveTopology(&topo);
    bad |= topo != D3D11_PRIMITIVE_TOPOLOGY_POINTLIST ? 1u << 1 : 0u;
    ID3D11Buffer* vb = nullptr;
    UINT stride = 0;
    UINT offset = 0;
    c->IAGetVertexBuffers(0, 1, &vb, &stride, &offset);
    bad |= (vb != g_probe.vb || stride != 12 || offset != 4) ? 1u << 2 : 0u;
    drop(vb);
    ID3D11VertexShader* vs = nullptr;
    c->VSGetShader(&vs, nullptr, nullptr);
    bad |= vs != nullptr ? 1u << 3 : 0u;
    drop(vs);
    ID3D11PixelShader* ps = nullptr;
    c->PSGetShader(&ps, nullptr, nullptr);
    bad |= ps != nullptr ? 1u << 4 : 0u;
    drop(ps);
    ID3D11ShaderResourceView* srv = nullptr;
    c->PSGetShaderResources(0, 1, &srv);
    bad |= srv != g_probe.srv ? 1u << 5 : 0u;
    drop(srv);
    ID3D11SamplerState* smp = nullptr;
    c->PSGetSamplers(0, 1, &smp);
    bad |= smp != g_probe.sampler ? 1u << 6 : 0u;
    drop(smp);
    ID3D11RasterizerState* rs = nullptr;
    c->RSGetState(&rs);
    bad |= rs != g_probe.raster ? 1u << 7 : 0u;
    drop(rs);
    D3D11_VIEWPORT vps[16];
    UINT nv = 16;
    c->RSGetViewports(&nv, vps);
    bad |= (nv != 2 || memcmp(vps, PROBE_VIEWPORTS, sizeof(PROBE_VIEWPORTS)) != 0) ? 1u << 8 : 0u;
    D3D11_RECT sc[16];
    UINT ns = 16;
    c->RSGetScissorRects(&ns, sc);
    bad |= (ns != 1 || memcmp(&sc[0], &PROBE_SCISSOR, sizeof(PROBE_SCISSOR)) != 0) ? 1u << 9 : 0u;
    ID3D11BlendState* bl = nullptr;
    FLOAT factor[4];
    UINT mask = 0;
    c->OMGetBlendState(&bl, factor, &mask);
    bad |= (bl != g_probe.blend || memcmp(factor, PROBE_FACTOR, sizeof(factor)) != 0 || mask != 0x0F0F0F0Fu) ? 1u << 10
                                                                                                             : 0u;
    drop(bl);
    ID3D11DepthStencilState* ds = nullptr;
    UINT ref = 0;
    c->OMGetDepthStencilState(&ds, &ref);
    bad |= (ds != g_probe.depth || ref != 7) ? 1u << 11 : 0u;
    drop(ds);
    ID3D11RenderTargetView* rtv[8] = {};
    ID3D11DepthStencilView* dsv = nullptr;
    c->OMGetRenderTargets(8, rtv, &dsv);
    bad |= (rtv[0] != g_probe.rtv || rtv[1] != nullptr || dsv != nullptr) ? 1u << 12 : 0u;
    for (auto*& r : rtv) {
        drop(r);
    }
    drop(dsv);
    ID3D11GeometryShader* gs = nullptr;
    c->GSGetShader(&gs, nullptr, nullptr);
    bad |= gs != nullptr ? 1u << 13 : 0u;
    drop(gs);
    return bad;
}

struct fault_job {
    uint64_t renderer;
    int32_t result;
};

void fault_run(void* arg)
{
    fault_job& j = *static_cast<fault_job*>(arg);
    gpu::test_set_fault(1);
    j.result = gpu::frame(j.renderer, g_batch, nullptr);
}

void restore_run(void*)
{
    gpu::restore_after_fault();
}

// ---- log capture ----------------------------------------------------------------------------------------------------
char g_log[8192];
uint32_t g_log_len = 0;

// Each line as "<level> <text>\n".
void log_sink(uint32_t lvl, const char* text)
{
    if (g_log_len + 4 >= sizeof(g_log)) {
        return;
    }
    g_log[g_log_len++] = static_cast<char>('0' + (lvl > 9 ? 9 : lvl));
    g_log[g_log_len++] = ' ';
    while (*text != '\0' && g_log_len + 2 < sizeof(g_log)) {
        g_log[g_log_len++] = *text++;
    }
    g_log[g_log_len++] = '\n';
    g_log[g_log_len] = '\0';
}

}  // namespace

extern "C" {

// ---- sizes and constants ---------------------------------------------------------------------------------------------
uint32_t ILCT_OvSizes(uint32_t* out, uint32_t cap)
{
    const uint32_t sizes[] = {sizeof(quad), sizeof(vertex), QUAD_CAP, font::atlas_w(), font::atlas_h(), font::em(),
                              font::spread(), font::active().row_count, sizeof(overlay::counters), sizeof(text_style),
                              sizeof(space), sizeof(scene::card_info), sizeof(scene::menus_out), sizeof(font::glyph),
                              sizeof(gpu::status_view), sizeof(overlay::status_view)};
    const uint32_t n = sizeof(sizes) / sizeof(sizes[0]);
    for (uint32_t k = 0; k < n && k < cap; ++k) {
        out[k] = sizes[k];
    }
    return n;
}

// ---- font ------------------------------------------------------------------------------------------------------------
const char* ILCT_OvFontDigest(void) { return font::active().digest; }

int32_t ILCT_OvFontAtlas(uint8_t* out, uint32_t cap) { return font::decode_atlas(font::active(), out, cap) ? 1 : 0; }

int32_t ILCT_OvFontGlyph(uint32_t face, uint32_t code, font::glyph* out)
{
    const font::glyph* g = font::find(face, code);
    if (g == nullptr) {
        return 0;
    }
    *out = *g;
    return 1;
}

void ILCT_OvFontMetrics(uint32_t face, float* ascent, float* descent)
{
    *ascent = font::ascent(face);
    *descent = font::descent(face);
}

float ILCT_OvMeasure(uint32_t face, float em, const char* text, uint32_t n) { return font::measure(face, em, text, n); }

uint32_t ILCT_OvFit(uint32_t face, float em, const char* text, uint32_t n, float max_w, int32_t* cut, float* width)
{
    bool c = false;
    const uint32_t k = font::fit(face, em, text, n, max_w, &c, width);
    *cut = c ? 1 : 0;
    return k;
}

// ---- batch -----------------------------------------------------------------------------------------------------------
void ILCT_OvSpace(uint32_t w, uint32_t h, space* out) { *out = make_space(w, h); }

uint32_t ILCT_OvExpand(const quad* q, const space* s, vertex* out) { return expand(*q, *s, out); }

uint32_t ILCT_OvText(uint32_t face, float x, float y, float em, const char* s, uint32_t n, const text_style* st,
                     float max_w, uint32_t align, quad* out, uint32_t cap, float* width)
{
    g_batch.clear();
    *width = text(g_batch, face, x, y, em, s, n, *st, max_w, align);
    return copy_quads(g_batch, out, cap);
}

uint32_t ILCT_OvIcon(uint32_t id, float x, float y, float size, const text_style* st, quad* out, uint32_t cap)
{
    g_batch.clear();
    icon(g_batch, id, x, y, size, *st);
    return copy_quads(g_batch, out, cap);
}

// kind: 0 solid, 1 vertical gradient, 2 horizontal gradient, 3 faded bar (param = fade), 4 glass (param = radius)
uint32_t ILCT_OvPrim(uint32_t kind, float x, float y, float w, float h, uint32_t a, uint32_t b, uint32_t c,
                     float param, float alpha, quad* out, uint32_t cap)
{
    g_batch.clear();
    g_batch.alpha = alpha;
    switch (kind) {
    case 0: solid(g_batch, x, y, w, h, a); break;
    case 1: gradient_v(g_batch, x, y, w, h, a, b); break;
    case 2: gradient_h(g_batch, x, y, w, h, a, b); break;
    case 3: faded_bar(g_batch, x, y, w, h, a, param); break;
    default: glass(g_batch, x, y, w, h, a, b, c, param); break;
    }
    return copy_quads(g_batch, out, cap);
}

// Pushes `n` solid quads: the count kept and the count refused.
void ILCT_OvFill(uint32_t n, uint32_t* kept, uint32_t* refused)
{
    g_batch.clear();
    for (uint32_t k = 0; k < n; ++k) {
        solid(g_batch, 0.0f, 0.0f, 1.0f, 1.0f, 0xFFFFFFFFu);
    }
    *kept = g_batch.count();
    *refused = g_batch.refused();
}

uint32_t ILCT_OvMix(uint32_t a, uint32_t b, float t) { return mix(a, b, t); }
uint32_t ILCT_OvFromPs2(uint32_t packed) { return from_ps2(packed); }
uint32_t ILCT_OvWithAlpha(uint32_t c, float k) { return with_alpha(c, k); }

// ---- scene -----------------------------------------------------------------------------------------------------------
// kind: bit 0 the test card, bit 1 the seat windows, bit 2 Player 1's window is ours (else reported left out).
// Returns the quads composed (copied up to cap).
uint32_t ILCT_OvScene(uint32_t kind, const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s, uint64_t now,
                      const scene::card_info* ci, quad* out, uint32_t cap, scene::menus_out* mo)
{
    compose(kind, v, s, now, ci, mo);
    return copy_quads(g_batch, out, cap);
}

// The batch holds `prefill` solid quads first, then the seat windows, P1's included (the full-batch drill).
uint32_t ILCT_OvSceneFull(uint32_t prefill, const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s,
                          uint64_t now, quad* out, uint32_t cap, scene::menus_out* mo)
{
    g_batch.clear();
    for (uint32_t k = 0; k < prefill; ++k) {
        solid(g_batch, 0.0f, 0.0f, 1.0f, 1.0f, 0xFF000000u);
    }
    *mo = scene::seat_windows(g_batch, *v, *s, now, true);
    return copy_quads(g_batch, out, cap);
}

// The batch holds `prefill` solid quads, then the seat windows (P1's included), then the test card moved underneath:
// the module's order with little room left (the windows keep their room, the card is cut).
uint32_t ILCT_OvSceneCardLast(uint32_t prefill, const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s,
                              uint64_t now, const scene::card_info* ci, quad* out, uint32_t cap, scene::menus_out* mo)
{
    g_batch.clear();
    for (uint32_t k = 0; k < prefill; ++k) {
        solid(g_batch, 0.0f, 0.0f, 1.0f, 1.0f, 0xFF000000u);
    }
    *mo = scene::seat_windows(g_batch, *v, *s, now, true);
    const uint32_t card_from = g_batch.count();
    scene::test_card(g_batch, *ci);
    g_batch.to_front(card_from);
    return copy_quads(g_batch, out, cap);
}

// The seats' fixed slots: *out = the slot of `seat` for the window in `sv`; returns slot_layout's result.
int32_t ILCT_OvSlot(uint32_t seat, const hud::menus_draw::seat_view* sv, scene::slot* out)
{
    return scene::slot_layout(seat, *sv, out) ? 1 : 0;
}

// The slot constants: left, width, gap, height, bottom, tab height, pitch, rows.
void ILCT_OvSlotConstants(float* out)
{
    const float k[8] = {scene::SLOT_LEFT, scene::SLOT_W, scene::SLOT_GAP, scene::SLOT_H, scene::SLOT_BOTTOM,
                        scene::SLOT_TAB_H, scene::SLOT_PITCH, static_cast<float>(scene::SLOT_ROWS)};
    memcpy(out, k, sizeof(k));
}

float ILCT_OvEase(uint64_t opened, uint64_t now) { return scene::ease(opened, now); }

int32_t ILCT_OvRenderLive(void) { return patch::subsystem_live(ILC_SUBSYS_RENDER) ? 1 : 0; }

// ---- the GPU side's state lists --------------------------------------------------------------------------------------
uint32_t ILCT_OvStates(const char** saved, uint32_t saved_cap, const char** bound, uint32_t bound_cap, uint32_t* bound_n)
{
    *bound_n = gpu::test_bound_states(bound, bound_cap);
    return gpu::test_saved_states(saved, saved_cap);
}

int32_t ILCT_OvFormatOk(uint32_t format) { return gpu::test_format_ok(format) ? 1 : 0; }

void ILCT_OvGpuStatus(gpu::status_view* out) { *out = gpu::status(); }

const char* ILCT_OvResultName(int32_t r) { return gpu::result_name(r); }

// ---- WARP ------------------------------------------------------------------------------------------------------------
void ILCT_OvWarpFree(void)
{
    free_probe_state();
    drop(g_warp.staging);
    drop(g_warp.back);
    drop(g_warp.other);
    drop(g_warp.context);
    drop(g_warp.device);
    g_warp.swap.device = nullptr;
    g_warp.swap.back = nullptr;
    memset(g_warp.renderer, 0, sizeof(g_warp.renderer));
}

// A WARP device and a w x h back buffer of `format`; returns the fake renderer's address (0: no D3D11 here).
uint64_t ILCT_OvWarpInit(uint32_t w, uint32_t h, uint32_t format)
{
    ILCT_OvWarpFree();
    if (!gpu::prepare_atlas() || !make_device(&g_warp.device, &g_warp.context)) {
        return 0;
    }
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = w;
    td.Height = h;
    td.MipLevels = td.ArraySize = 1;
    td.Format = static_cast<DXGI_FORMAT>(format);
    td.SampleDesc.Count = 1;
    td.BindFlags = D3D11_BIND_RENDER_TARGET;
    D3D11_TEXTURE2D_DESC sd = td;
    sd.BindFlags = 0;
    sd.Usage = D3D11_USAGE_STAGING;
    sd.CPUAccessFlags = D3D11_CPU_ACCESS_READ;
    if (FAILED(g_warp.device->CreateTexture2D(&td, nullptr, &g_warp.back)) ||
        FAILED(g_warp.device->CreateTexture2D(&sd, nullptr, &g_warp.staging)) || !make_probe_state()) {
        ILCT_OvWarpFree();
        return 0;
    }
    g_warp.w = w;
    g_warp.h = h;
    g_warp.format = format;
    g_warp.swap.device = g_warp.device;
    g_warp.swap.back = g_warp.back;
    put_renderer(0x108, reinterpret_cast<uint64_t>(g_warp.device));
    put_renderer(0x110, reinterpret_cast<uint64_t>(g_warp.context));
    put_renderer(0x118, reinterpret_cast<uint64_t>(static_cast<IDXGISwapChain*>(&g_warp.swap)));
    return reinterpret_cast<uint64_t>(g_warp.renderer);
}

void ILCT_OvWarpClear(uint32_t rgba)
{
    ID3D11RenderTargetView* rtv = nullptr;
    if (SUCCEEDED(g_warp.device->CreateRenderTargetView(g_warp.back, nullptr, &rtv))) {
        const FLOAT c[4] = {static_cast<FLOAT>(rgba & 0xFF) / 255.0f, static_cast<FLOAT>((rgba >> 8) & 0xFF) / 255.0f,
                            static_cast<FLOAT>((rgba >> 16) & 0xFF) / 255.0f, static_cast<FLOAT>(rgba >> 24) / 255.0f};
        g_warp.context->ClearRenderTargetView(rtv, c);
        rtv->Release();
    }
}

// Composes (kind as ILCT_OvScene) and draws through gpu::frame on the fake renderer; returns gpu::result.
int32_t ILCT_OvWarpFrame(uint32_t kind, const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s, uint64_t now,
                         const scene::card_info* ci, gpu::frame_info* info, scene::menus_out* mo)
{
    compose(kind, v, s, now, ci, mo);
    return gpu::frame(reinterpret_cast<uint64_t>(g_warp.renderer), g_batch, info);
}

// The whole HUD on WARP: the seat windows (view non-null; p1_ours as OV-01 passes it), then the HUD elements from
// the given inputs (any may be null), as the module's frame job composes them; then the GPU frame.
int32_t ILCT_OvWarpHud(const hud::menus_draw::menu_view* v, const hud::hud_snapshot* s,
                       const hud::party_rows::rows_record* rows, const overlay::feed::record* feed,
                       const hud::target_info::view* ti, const hud::battle_log::view* log,
                       const overlay::states::record* st, uint64_t now, uint32_t boss, uint32_t p1_ours,
                       gpu::frame_info* info, overlay::hud_scene::output* out)
{
    g_batch.clear();
    if (v != nullptr && s != nullptr) {
        scene::seat_windows(g_batch, *v, *s, now, p1_ours != 0);
    }
    overlay::hud_scene::input in = {};
    in.snap = s;
    in.rows = rows;
    in.feed = feed;
    in.menus = v;
    in.ti = ti;
    in.log = log;
    in.states = st;
    in.now = now;
    in.boss_gauge = (boss & 1u) != 0 ? 1 : 0;
    in.party_on = (boss & 2u) != 0 ? 0 : 1;
    const overlay::hud_scene::output o = overlay::hud_scene::compose(g_batch, in);
    if (out != nullptr) {
        *out = o;
    }
    return gpu::frame(reinterpret_cast<uint64_t>(g_warp.renderer), g_batch, info);
}

uint32_t ILCT_OvWarpQuads(void) { return g_batch.count(); }

// One themed window body on WARP (marks' glass_band: the whole window as one band): chamfer 0/1 with `cut` units,
// shine and bias 0..1 (packed into the vertex mode's fraction), an opaque fill and rim. Returns gpu::result.
int32_t ILCT_OvWarpGlass(uint32_t chamfer, float shine, float bias, float x, float y, float w, float h, float cut,
                         uint32_t fill, gpu::frame_info* info)
{
    g_batch.clear();
    glass_shape g = {};
    g.chamfer = chamfer != 0 ? 1 : 0;
    g.radius = 0.0f;
    g.cut = cut;
    g.rim_w = 1.0f;
    g.soft = 0.0f;
    g.shine = shine;
    g.bias = bias;
    glass_band(g_batch, x, y, w, h, g, 0.0f, 1.0f, fill, fill, fill);
    return gpu::frame(reinterpret_cast<uint64_t>(g_warp.renderer), g_batch, info);
}

// The back buffer as w * h * 4 bytes, rows packed.
uint32_t ILCT_OvWarpRead(uint8_t* out, uint32_t cap)
{
    g_warp.context->CopyResource(g_warp.staging, g_warp.back);
    D3D11_MAPPED_SUBRESOURCE m = {};
    if (FAILED(g_warp.context->Map(g_warp.staging, 0, D3D11_MAP_READ, 0, &m))) {
        return 0;
    }
    const uint32_t row = g_warp.w * 4;
    uint32_t n = 0;
    for (uint32_t y = 0; y < g_warp.h && n + row <= cap; ++y) {
        memcpy(out + n, static_cast<const uint8_t*>(m.pData) + static_cast<size_t>(y) * m.RowPitch, row);
        n += row;
    }
    g_warp.context->Unmap(g_warp.staging, 0);
    return n;
}

// The distinctive state, one frame of `kind`, then the bits of every slot that differs (0 = all put back).
uint32_t ILCT_OvWarpRoundTrip(uint32_t kind, const scene::card_info* ci, int32_t* result)
{
    set_probe_state();
    const uint32_t before = check_probe_state();
    compose(kind, nullptr, nullptr, 0, ci, nullptr);
    *result = gpu::frame(reinterpret_cast<uint64_t>(g_warp.renderer), g_batch, nullptr);
    return check_probe_state() | (before != 0 ? 0x80000000u : 0u);
}

// The restore drill: the frame faults right after binding its state (inside guard::call); the guarded restore puts
// it back. *faulted = guard result of the frame (1 = FAULTED). Returns the state bits as ILCT_OvWarpRoundTrip.
uint32_t ILCT_OvWarpFaultDrill(const scene::card_info* ci, int32_t* faulted)
{
    guard::attach();
    set_probe_state();
    compose(1, nullptr, nullptr, 0, ci, nullptr);
    fault_job j = {reinterpret_cast<uint64_t>(g_warp.renderer), -1};
    *faulted = guard::call(fault_run, &j);
    const uint32_t during = check_probe_state();
    guard::call(restore_run, nullptr);
    return check_probe_state() | (during == 0 ? 0x40000000u : 0u);   // bit 30: the fault left nothing to restore
}

// The swap chain answers another device (1) or the renderer's (0).
int32_t ILCT_OvWarpMismatch(int32_t other)
{
    if (other != 0 && g_warp.other == nullptr) {
        ID3D11DeviceContext* c = nullptr;
        if (!make_device(&g_warp.other, &c)) {
            return 0;
        }
        drop(c);
    }
    g_warp.swap.device = other != 0 ? g_warp.other : g_warp.device;
    return 1;
}

void ILCT_OvWarpRendererField(uint32_t offset, uint64_t value) { put_renderer(offset, value); }

uint64_t ILCT_OvWarpRenderer(void) { return reinterpret_cast<uint64_t>(g_warp.renderer); }

uint32_t ILCT_OvWarpGetBufferCalls(void) { return g_warp.swap.get_buffer_calls; }

// ---- the module side (the OV-01 handler, the heartbeat, the log) -----------------------------------------------------
void ILCT_OvReset(void) { overlay::test_reset(); }
int32_t ILCT_OvAttach(uint64_t base) { return overlay::test_attach(static_cast<uintptr_t>(base)) ? 1 : 0; }
void ILCT_OvSetTestCard(int32_t on) { overlay::test_set_test_card(on != 0); }
void ILCT_OvSetHeartbeat(uint64_t ms, uint32_t alpha) { overlay::test_set_heartbeat(ms, alpha); }
void ILCT_OvForceLive(int32_t state) { overlay::test_force_live(state); }
void ILCT_OvSetPresented(uint64_t ms) { overlay::test_set_presented(ms); }
int32_t ILCT_OvLive(uint64_t now) { return overlay::live(now) ? 1 : 0; }
int32_t ILCT_OvFieldOpen(void) { return overlay::field_open_now() ? 1 : 0; }
int32_t ILCT_OvLogReplaces(uint64_t now) { return overlay::log_replaces_native(now) ? 1 : 0; }
void ILCT_OvInjectFault(uint32_t where) { overlay::test_inject_fault(where); }
int32_t ILCT_OvCall(patch::ctx* c, uint32_t epoch) { return overlay::frame_hook(c, epoch); }
void ILCT_OvNoteHud(uint64_t manager, uint64_t context) { overlay::note_hud(manager, context); }
void ILCT_OvUiTick(void) { overlay::note_ui_tick(); }
void ILCT_OvStats(overlay::counters* out) { memcpy(out, const_cast<const overlay::counters*>(overlay::stats()), sizeof(*out)); }
void ILCT_OvStatus(overlay::status_view* out) { *out = overlay::status(); }
void ILCT_OvConfigure(const ilc_settings* s) { overlay::configure_from_settings(*s); }
uint32_t ILCT_OvLastDropped(void) { return overlay::test_last_dropped(); }

uint32_t ILCT_OvLastBatch(quad* out, uint32_t cap)
{
    return copy_quads(*static_cast<const batch*>(overlay::test_last_batch()), out, cap);
}

void ILCT_OvLogCapture(int32_t on)
{
    g_log_len = 0;
    g_log[0] = '\0';
    log::test_set_sink(on != 0 ? log_sink : nullptr);
}

// overlay::step, then the queued lines drained into the capture.
void ILCT_OvStep(uint64_t now)
{
    overlay::step(now);
    log::drain();
}

const char* ILCT_OvLogText(void) { return g_log; }

// ---- the P1-native frontend switch ------------------------------------------------------------------------------------
void ILCT_FeSetP1Native(int32_t on) { frontend::set_p1_native(on != 0); }
int32_t ILCT_FeP1Native(void) { return frontend::p1_native() ? 1 : 0; }
int32_t ILCT_SesP1Native(void) { return session::p1_native() ? 1 : 0; }

// ---- Player 1's menu on the overlay: arming and give-back (frontend/rules.h p1_arm_step) -----------------------------
void ILCT_FeSetP1Arming(int32_t on) { frontend::set_p1_arming(on != 0); }
int32_t ILCT_FeP1Arming(void) { return frontend::p1_arming() ? 1 : 0; }
void ILCT_FeP1Arm(frontend::p1_arm* out) { frontend::read_p1_arm(out); }
uint32_t ILCT_FeP1ArmStep(frontend::p1_arm* s, const frontend::p1_arm_in* in, uint64_t now)
{
    return frontend::p1_arm_step(s, *in, now);
}
// A presented frame the GPU side really drew (OV-01: note_run, and note_gpu_drawn on gpu::DRAWN).
void ILCT_FePresenterRunFrame(uint64_t now)
{
    frontend::presenter::note_run(now);
    frontend::presenter::note_gpu_drawn(now);
}
// A presented frame with nothing drawn (gpu::READY: an empty layer).
void ILCT_FePresenterRunReady(uint64_t now) { frontend::presenter::note_run(now); }
void ILCT_FeSetOverlayLive(int32_t state) { frontend::test_set_overlay_live(state); }
uint32_t ILCT_TlSoundDue(void) { return interaction::treasure_log::test_sound_due(); }
void ILCT_FePresenterBreakRun(void) { frontend::presenter::break_run(); }
void ILCT_FePresenterFailed(uint64_t now) { frontend::presenter::note_failed(now); }
void ILCT_FePresenterRunView(frontend::presenter::run_view* out) { *out = frontend::presenter::run(); }
uint64_t ILCT_FeP1Givebacks(void) { return frontend::stats()->p1_givebacks; }

// ---- a guest's CONFIRM that found nothing opens its menu (interaction -> frontend) -----------------------------------
uint32_t ILCT_IxTakeMenuOpens(uint32_t epoch) { return interaction::take_menu_opens(epoch); }
void ILCT_IxNoteMenuOpen(uint32_t epoch, uint32_t seats) { interaction::test_note_menu_open(epoch, seats); }
uint64_t ILCT_IxMenuOpensCount(void) { return interaction::stats()->menu_opens; }

// ---- the guests' prompt (IX-21) arming and gate ----------------------------------------------------------------------
void ILCT_IxPromptStatus(interaction::prompt::status_view* out) { *out = interaction::prompt::status(); }

}  // extern "C"
