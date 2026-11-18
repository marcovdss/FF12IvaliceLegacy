// Ivalice Legacy co-op overlay: the D3D11 side (see gpu.h). Render thread only (the OV-01 handler), inside the guard.
#define WIN32_LEAN_AND_MEAN
#include <windows.h>

#include <d3d11.h>
#include <stddef.h>

#include "overlay/font.h"
#include "overlay/gpu.h"
#include "overlay/shaders.inc"
#include "overlay/theme.h"
#include "runtime/probe.h"

namespace ilc::overlay::gpu {
namespace {

// The renderer's D3D objects (the creation call's out-parameters, 0x625a64: OV-V3).
constexpr uint32_t RENDERER_DEVICE = 0x108;
constexpr uint32_t RENDERER_CONTEXT = 0x110;
constexpr uint32_t RENDERER_SWAP = 0x118;
constexpr uint32_t MIN_W = 640;
constexpr uint32_t MIN_H = 360;
constexpr uint32_t CLASS_INSTANCES = 256;   // the most class instances a stage can have bound

constexpr unsigned char VS_BYTES[] = {ILC_OVERLAY_VS_BYTES};
constexpr unsigned char PS_BYTES[] = {ILC_OVERLAY_PS_BYTES};
static_assert(sizeof(VS_BYTES) == ILC_OVERLAY_VS_SIZE && sizeof(PS_BYTES) == ILC_OVERLAY_PS_SIZE, "shaders.inc");

static_assert(offsetof(vertex, x) == 0 && offsetof(vertex, u) == 8 && offsetof(vertex, p) == 16 &&
                  offsetof(vertex, c0) == 32 && offsetof(vertex, c1) == 36 && offsetof(vertex, mode) == 40,
              "the input layout below");

volatile uint32_t g_fault = 0;   // host tests: the restore drill

template <typename T>
void drop(T*& p)
{
    if (p != nullptr) {
        p->Release();
        p = nullptr;
    }
}

struct resources {
    ID3D11Device* device;              // not counted: every object below holds it
    ID3D11VertexShader* vs;
    ID3D11PixelShader* ps;
    ID3D11InputLayout* layout;
    ID3D11Buffer* vb;
    ID3D11Texture2D* atlas;
    ID3D11ShaderResourceView* srv;
    ID3D11SamplerState* sampler;
    ID3D11BlendState* blend;
    ID3D11RasterizerState* raster;
    ID3D11DepthStencilState* depth;
    uint32_t ready;
    uint32_t failed;
    uint32_t atlas_gen;               // the theme atlas generation the texture holds
    uint32_t atlas_failed_gen;        // a generation whose texture could not be created (not tried again)
};
resources g_res = {};
uint64_t g_pair_device = 0;   // the renderer's device and swap chain last proven to belong together
uint64_t g_pair_swap = 0;
uint64_t g_pair_context = 0;
status_view g_status = {};

void release_all()
{
    drop(g_res.depth);
    drop(g_res.raster);
    drop(g_res.blend);
    drop(g_res.sampler);
    drop(g_res.srv);
    drop(g_res.atlas);
    drop(g_res.vb);
    drop(g_res.layout);
    drop(g_res.ps);
    drop(g_res.vs);
    g_res.device = nullptr;
    g_res.ready = 0;
    g_res.failed = 0;
    g_res.atlas_gen = 0;
    g_res.atlas_failed_gen = 0;
}

// A creation failure is final for that device: g_res.device keeps it, so frame() does not try again every frame.
bool fail(uint32_t object, HRESULT hr)
{
    ID3D11Device* const d = g_res.device;
    release_all();
    g_res.device = d;
    g_res.failed = 1;
    g_status.failed_object = object;
    g_status.failed_hr = static_cast<int32_t>(hr);
    g_status.ready = 0;
    return false;
}

// The active theme's atlas as an immutable R8 texture and its view (both or neither).
HRESULT make_atlas(ID3D11Device* d, ID3D11Texture2D** tex, ID3D11ShaderResourceView** srv)
{
    *tex = nullptr;
    *srv = nullptr;
    const uint8_t* bytes = theme::atlas_bytes();
    if (bytes == nullptr || font::atlas_w() == 0 || font::atlas_h() == 0) {
        return E_FAIL;
    }
    D3D11_TEXTURE2D_DESC td = {};
    td.Width = font::atlas_w();
    td.Height = font::atlas_h();
    td.MipLevels = 1;
    td.ArraySize = 1;
    td.Format = DXGI_FORMAT_R8_UNORM;
    td.SampleDesc.Count = 1;
    td.Usage = D3D11_USAGE_IMMUTABLE;
    td.BindFlags = D3D11_BIND_SHADER_RESOURCE;
    D3D11_SUBRESOURCE_DATA init = {};
    init.pSysMem = bytes;
    init.SysMemPitch = font::atlas_w();
    HRESULT hr = d->CreateTexture2D(&td, &init, tex);
    if (FAILED(hr) || *tex == nullptr) {
        *tex = nullptr;
        return FAILED(hr) ? hr : E_FAIL;
    }
    hr = d->CreateShaderResourceView(*tex, nullptr, srv);
    if (FAILED(hr) || *srv == nullptr) {
        drop(*tex);
        *srv = nullptr;
        return FAILED(hr) ? hr : E_FAIL;
    }
    return S_OK;
}

// A theme switch (theme::take_pending moved the generation on): the new atlas texture replaces the old one. A texture
// that cannot be created is reported to the theme module (the co-op thread falls back to the default) and that
// generation is not tried again; the frame draws nothing meanwhile.
bool refresh_atlas(ID3D11Device* d)
{
    const uint32_t gen = theme::atlas_generation();
    if (gen == g_res.atlas_gen && g_res.srv != nullptr) {
        return true;
    }
    if (gen == g_res.atlas_failed_gen) {
        return false;
    }
    ID3D11Texture2D* tex = nullptr;
    ID3D11ShaderResourceView* srv = nullptr;
    if (FAILED(make_atlas(d, &tex, &srv))) {
        g_res.atlas_failed_gen = gen;
        theme::note_atlas_failed();
        return false;
    }
    drop(g_res.srv);
    drop(g_res.atlas);
    g_res.atlas = tex;
    g_res.srv = srv;
    g_res.atlas_gen = gen;
    return true;
}

bool create(ID3D11Device* d)
{
    release_all();
    g_res.device = d;
    g_status.device = reinterpret_cast<uint64_t>(d);
    if (theme::atlas_bytes() == nullptr) {
        return fail(OBJ_ATLAS_DATA, E_FAIL);
    }
    HRESULT hr = d->CreateVertexShader(VS_BYTES, sizeof(VS_BYTES), nullptr, &g_res.vs);
    if (FAILED(hr) || g_res.vs == nullptr) {
        return fail(OBJ_VS, hr);
    }
    hr = d->CreatePixelShader(PS_BYTES, sizeof(PS_BYTES), nullptr, &g_res.ps);
    if (FAILED(hr) || g_res.ps == nullptr) {
        return fail(OBJ_PS, hr);
    }
    const D3D11_INPUT_ELEMENT_DESC elements[] = {
        {"POSITION", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 0, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 0, DXGI_FORMAT_R32G32_FLOAT, 0, 8, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 1, DXGI_FORMAT_R32G32B32A32_FLOAT, 0, 16, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 0, DXGI_FORMAT_R8G8B8A8_UNORM, 0, 32, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"COLOR", 1, DXGI_FORMAT_R8G8B8A8_UNORM, 0, 36, D3D11_INPUT_PER_VERTEX_DATA, 0},
        {"TEXCOORD", 2, DXGI_FORMAT_R32G32_FLOAT, 0, 40, D3D11_INPUT_PER_VERTEX_DATA, 0},
    };
    hr = d->CreateInputLayout(elements, sizeof(elements) / sizeof(elements[0]), VS_BYTES, sizeof(VS_BYTES),
                              &g_res.layout);
    if (FAILED(hr) || g_res.layout == nullptr) {
        return fail(OBJ_LAYOUT, hr);
    }
    D3D11_BUFFER_DESC bd = {};
    bd.ByteWidth = QUAD_CAP * 6 * sizeof(vertex);
    bd.Usage = D3D11_USAGE_DYNAMIC;
    bd.BindFlags = D3D11_BIND_VERTEX_BUFFER;
    bd.CPUAccessFlags = D3D11_CPU_ACCESS_WRITE;
    hr = d->CreateBuffer(&bd, nullptr, &g_res.vb);
    if (FAILED(hr) || g_res.vb == nullptr) {
        return fail(OBJ_VB, hr);
    }
    hr = make_atlas(d, &g_res.atlas, &g_res.srv);
    if (FAILED(hr)) {
        return fail(g_res.atlas == nullptr ? OBJ_ATLAS : OBJ_SRV, hr);
    }
    g_res.atlas_gen = theme::atlas_generation();
    D3D11_SAMPLER_DESC sd = {};
    sd.Filter = D3D11_FILTER_MIN_MAG_MIP_LINEAR;
    sd.AddressU = sd.AddressV = sd.AddressW = D3D11_TEXTURE_ADDRESS_CLAMP;
    sd.MaxAnisotropy = 1;
    sd.ComparisonFunc = D3D11_COMPARISON_NEVER;
    sd.MaxLOD = D3D11_FLOAT32_MAX;
    hr = d->CreateSamplerState(&sd, &g_res.sampler);
    if (FAILED(hr) || g_res.sampler == nullptr) {
        return fail(OBJ_SAMPLER, hr);
    }
    D3D11_BLEND_DESC bl = {};
    bl.RenderTarget[0].BlendEnable = TRUE;
    bl.RenderTarget[0].SrcBlend = D3D11_BLEND_ONE;               // premultiplied alpha
    bl.RenderTarget[0].DestBlend = D3D11_BLEND_INV_SRC_ALPHA;
    bl.RenderTarget[0].BlendOp = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].SrcBlendAlpha = D3D11_BLEND_ONE;
    bl.RenderTarget[0].DestBlendAlpha = D3D11_BLEND_INV_SRC_ALPHA;
    bl.RenderTarget[0].BlendOpAlpha = D3D11_BLEND_OP_ADD;
    bl.RenderTarget[0].RenderTargetWriteMask = D3D11_COLOR_WRITE_ENABLE_ALL;
    hr = d->CreateBlendState(&bl, &g_res.blend);
    if (FAILED(hr) || g_res.blend == nullptr) {
        return fail(OBJ_BLEND, hr);
    }
    D3D11_RASTERIZER_DESC rd = {};
    rd.FillMode = D3D11_FILL_SOLID;
    rd.CullMode = D3D11_CULL_NONE;
    rd.DepthClipEnable = TRUE;
    hr = d->CreateRasterizerState(&rd, &g_res.raster);
    if (FAILED(hr) || g_res.raster == nullptr) {
        return fail(OBJ_RASTER, hr);
    }
    D3D11_DEPTH_STENCIL_DESC dd = {};
    dd.DepthEnable = FALSE;
    dd.DepthWriteMask = D3D11_DEPTH_WRITE_MASK_ZERO;
    dd.DepthFunc = D3D11_COMPARISON_ALWAYS;
    dd.StencilEnable = FALSE;
    dd.StencilReadMask = D3D11_DEFAULT_STENCIL_READ_MASK;
    dd.StencilWriteMask = D3D11_DEFAULT_STENCIL_WRITE_MASK;
    dd.FrontFace.StencilFailOp = dd.FrontFace.StencilDepthFailOp = dd.FrontFace.StencilPassOp = D3D11_STENCIL_OP_KEEP;
    dd.FrontFace.StencilFunc = D3D11_COMPARISON_ALWAYS;
    dd.BackFace = dd.FrontFace;
    hr = d->CreateDepthStencilState(&dd, &g_res.depth);
    if (FAILED(hr) || g_res.depth == nullptr) {
        return fail(OBJ_DEPTH, hr);
    }
    g_res.ready = 1;
    g_status.ready = 1;
    g_status.failed_object = OBJ_NONE;
    g_status.failed_hr = 0;
    return true;
}

// ---- the state guard ---------------------------------------------------------------------------------------------------
struct saved_state {
    uint32_t valid;
    ID3D11DeviceContext* context;
    ID3D11InputLayout* layout;
    D3D11_PRIMITIVE_TOPOLOGY topology;
    ID3D11Buffer* vb;
    UINT vb_stride, vb_offset;
    ID3D11VertexShader* vs;
    ID3D11PixelShader* ps;
    ID3D11GeometryShader* gs;
    ID3D11HullShader* hs;
    ID3D11DomainShader* ds;
    UINT vs_n, ps_n, gs_n, hs_n, ds_n;
    ID3D11ClassInstance* vs_i[CLASS_INSTANCES];
    ID3D11ClassInstance* ps_i[CLASS_INSTANCES];
    ID3D11ClassInstance* gs_i[CLASS_INSTANCES];
    ID3D11ClassInstance* hs_i[CLASS_INSTANCES];
    ID3D11ClassInstance* ds_i[CLASS_INSTANCES];
    ID3D11ShaderResourceView* ps_srv;
    ID3D11SamplerState* ps_sampler;
    ID3D11RasterizerState* raster;
    D3D11_VIEWPORT viewports[D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE];
    UINT viewport_n;
    D3D11_RECT scissors[D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE];
    UINT scissor_n;
    ID3D11BlendState* blend;
    FLOAT blend_factor[4];
    UINT sample_mask;
    ID3D11DepthStencilState* depth;
    UINT stencil_ref;
    ID3D11RenderTargetView* rtv[D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT];
    ID3D11DepthStencilView* dsv;
};
saved_state g_saved = {};

// The slots the guard saves and the ones the draw binds (host tests: every bound slot is saved).
constexpr const char* SAVED[] = {"IA.layout", "IA.topology", "IA.vb0", "VS", "PS", "GS", "HS", "DS", "PS.srv0",
                                 "PS.sampler0", "RS.state", "RS.viewports", "RS.scissors", "OM.blend", "OM.depth",
                                 "OM.targets"};
constexpr const char* BOUND[] = {"IA.layout", "IA.topology", "IA.vb0", "VS", "PS", "GS", "HS", "DS", "PS.srv0",
                                 "PS.sampler0", "RS.state", "RS.viewports", "OM.blend", "OM.depth", "OM.targets"};

void release_instances(ID3D11ClassInstance** list, UINT n)
{
    for (UINT k = 0; k < n && k < CLASS_INSTANCES; ++k) {
        drop(list[k]);
    }
}

void save(ID3D11DeviceContext* c)
{
    saved_state& s = g_saved;
    s.context = c;
    c->IAGetInputLayout(&s.layout);
    c->IAGetPrimitiveTopology(&s.topology);
    c->IAGetVertexBuffers(0, 1, &s.vb, &s.vb_stride, &s.vb_offset);
    s.vs_n = s.ps_n = s.gs_n = s.hs_n = s.ds_n = CLASS_INSTANCES;
    c->VSGetShader(&s.vs, s.vs_i, &s.vs_n);
    c->PSGetShader(&s.ps, s.ps_i, &s.ps_n);
    c->GSGetShader(&s.gs, s.gs_i, &s.gs_n);
    c->HSGetShader(&s.hs, s.hs_i, &s.hs_n);
    c->DSGetShader(&s.ds, s.ds_i, &s.ds_n);
    c->PSGetShaderResources(0, 1, &s.ps_srv);
    c->PSGetSamplers(0, 1, &s.ps_sampler);
    c->RSGetState(&s.raster);
    s.viewport_n = D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
    c->RSGetViewports(&s.viewport_n, s.viewports);
    s.scissor_n = D3D11_VIEWPORT_AND_SCISSORRECT_OBJECT_COUNT_PER_PIPELINE;
    c->RSGetScissorRects(&s.scissor_n, s.scissors);
    c->OMGetBlendState(&s.blend, s.blend_factor, &s.sample_mask);
    c->OMGetDepthStencilState(&s.depth, &s.stencil_ref);
    c->OMGetRenderTargets(D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT, s.rtv, &s.dsv);
    s.valid = 1;
}

void restore()
{
    saved_state& s = g_saved;
    if (s.valid == 0 || s.context == nullptr) {
        return;
    }
    s.valid = 0;   // first: a fault inside the restore must not run it twice
    ID3D11DeviceContext* c = s.context;
    c->IASetInputLayout(s.layout);
    c->IASetPrimitiveTopology(s.topology);
    c->IASetVertexBuffers(0, 1, &s.vb, &s.vb_stride, &s.vb_offset);
    c->VSSetShader(s.vs, s.vs_n != 0 ? s.vs_i : nullptr, s.vs_n);
    c->PSSetShader(s.ps, s.ps_n != 0 ? s.ps_i : nullptr, s.ps_n);
    c->GSSetShader(s.gs, s.gs_n != 0 ? s.gs_i : nullptr, s.gs_n);
    c->HSSetShader(s.hs, s.hs_n != 0 ? s.hs_i : nullptr, s.hs_n);
    c->DSSetShader(s.ds, s.ds_n != 0 ? s.ds_i : nullptr, s.ds_n);
    c->PSSetShaderResources(0, 1, &s.ps_srv);
    c->PSSetSamplers(0, 1, &s.ps_sampler);
    c->RSSetState(s.raster);
    c->RSSetViewports(s.viewport_n, s.viewport_n != 0 ? s.viewports : nullptr);
    c->RSSetScissorRects(s.scissor_n, s.scissor_n != 0 ? s.scissors : nullptr);
    c->OMSetBlendState(s.blend, s.blend_factor, s.sample_mask);
    c->OMSetDepthStencilState(s.depth, s.stencil_ref);
    // Render targets back as they were, the UAVs left alone (KEEP): the draw never touched them.
    UINT n = 0;
    for (UINT k = 0; k < D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT; ++k) {
        if (s.rtv[k] != nullptr) {
            n = k + 1;
        }
    }
    c->OMSetRenderTargetsAndUnorderedAccessViews(n, n != 0 ? s.rtv : nullptr, s.dsv, 0,
                                                 D3D11_KEEP_UNORDERED_ACCESS_VIEWS, nullptr, nullptr);
    // The references the Get calls took.
    drop(s.layout);
    drop(s.vb);
    drop(s.vs);
    drop(s.ps);
    drop(s.gs);
    drop(s.hs);
    drop(s.ds);
    release_instances(s.vs_i, s.vs_n);
    release_instances(s.ps_i, s.ps_n);
    release_instances(s.gs_i, s.gs_n);
    release_instances(s.hs_i, s.hs_n);
    release_instances(s.ds_i, s.ds_n);
    drop(s.ps_srv);
    drop(s.ps_sampler);
    drop(s.raster);
    drop(s.blend);
    drop(s.depth);
    for (UINT k = 0; k < D3D11_SIMULTANEOUS_RENDER_TARGET_COUNT; ++k) {
        drop(s.rtv[k]);
    }
    drop(s.dsv);
    s.context = nullptr;
}

void bind(ID3D11DeviceContext* c, ID3D11RenderTargetView* rtv, uint32_t w, uint32_t h)
{
    const UINT stride = sizeof(vertex);
    const UINT offset = 0;
    c->IASetInputLayout(g_res.layout);
    c->IASetPrimitiveTopology(D3D11_PRIMITIVE_TOPOLOGY_TRIANGLELIST);
    c->IASetVertexBuffers(0, 1, &g_res.vb, &stride, &offset);
    c->VSSetShader(g_res.vs, nullptr, 0);
    c->PSSetShader(g_res.ps, nullptr, 0);
    c->GSSetShader(nullptr, nullptr, 0);
    c->HSSetShader(nullptr, nullptr, 0);
    c->DSSetShader(nullptr, nullptr, 0);
    c->PSSetShaderResources(0, 1, &g_res.srv);
    c->PSSetSamplers(0, 1, &g_res.sampler);
    c->RSSetState(g_res.raster);
    D3D11_VIEWPORT vp = {};
    vp.Width = static_cast<FLOAT>(w);
    vp.Height = static_cast<FLOAT>(h);
    vp.MaxDepth = 1.0f;
    c->RSSetViewports(1, &vp);
    const FLOAT factor[4] = {0.0f, 0.0f, 0.0f, 0.0f};
    c->OMSetBlendState(g_res.blend, factor, 0xFFFFFFFFu);
    c->OMSetDepthStencilState(g_res.depth, 0);
    c->OMSetRenderTargetsAndUnorderedAccessViews(1, &rtv, nullptr, 0, D3D11_KEEP_UNORDERED_ACCESS_VIEWS, nullptr,
                                                 nullptr);
}

bool format_ok(uint32_t f)
{
    switch (f) {
    case DXGI_FORMAT_R8G8B8A8_UNORM:
    case DXGI_FORMAT_R8G8B8A8_UNORM_SRGB:
    case DXGI_FORMAT_B8G8R8A8_UNORM:
    case DXGI_FORMAT_B8G8R8A8_UNORM_SRGB:
    case DXGI_FORMAT_R10G10B10A2_UNORM:
    case DXGI_FORMAT_R16G16B16A16_FLOAT:
        return true;
    default:
        return false;
    }
}

template <typename T>
bool field(uint64_t renderer, uint32_t offset, T** out)
{
    uint64_t v = 0;
    if (!load_game(&v, reinterpret_cast<const void*>(renderer + offset), sizeof(v)) || v == 0) {
        return false;
    }
    *out = reinterpret_cast<T*>(v);
    return true;
}

// The swap chain's device is the renderer's device, and the context is that device's immediate context.
bool pair_ok(ID3D11Device* d, ID3D11DeviceContext* c, IDXGISwapChain* sc)
{
    const uint64_t dv = reinterpret_cast<uint64_t>(d);
    const uint64_t sv = reinterpret_cast<uint64_t>(sc);
    const uint64_t cv = reinterpret_cast<uint64_t>(c);
    if (dv == g_pair_device && sv == g_pair_swap && cv == g_pair_context) {
        return true;
    }
    ID3D11Device* owner = nullptr;
    if (FAILED(sc->GetDevice(__uuidof(ID3D11Device), reinterpret_cast<void**>(&owner))) || owner == nullptr) {
        return false;
    }
    const bool same = owner == d;
    owner->Release();
    ID3D11DeviceContext* immediate = nullptr;
    d->GetImmediateContext(&immediate);
    const bool ours = immediate == c;
    drop(immediate);
    if (!same || !ours) {
        return false;
    }
    g_pair_device = dv;
    g_pair_swap = sv;
    g_pair_context = cv;
    return true;
}

}  // namespace

bool prepare_atlas()
{
    // The active theme's decoded atlas: theme::attach made it at the boot; host tests (and an attach that ran before
    // the boot's theme step) get the built-in default here. Never on the render thread.
    if (theme::atlas_bytes() == nullptr) {
        theme::attach(nullptr);
    }
    return theme::atlas_bytes() != nullptr;
}

int32_t frame(uint64_t renderer, const batch& b, frame_info* info)
{
    if (info != nullptr) {
        *info = frame_info{};
    }
    ID3D11Device* device = nullptr;
    ID3D11DeviceContext* context = nullptr;
    IDXGISwapChain* swap = nullptr;
    if (renderer == 0 || !field(renderer, RENDERER_DEVICE, &device) || !field(renderer, RENDERER_CONTEXT, &context) ||
        !field(renderer, RENDERER_SWAP, &swap)) {
        return NO_RENDERER;
    }
    if (!pair_ok(device, context, swap)) {
        return MISMATCH;
    }
    if (g_res.device != device || (g_res.ready == 0 && g_res.failed == 0)) {
        if (!create(device)) {
            return CREATE_FAILED;
        }
    }
    if (g_res.failed != 0 || g_res.ready == 0) {
        return CREATE_FAILED;
    }
    if (device->GetDeviceRemovedReason() != S_OK) {
        return DEVICE_REMOVED;
    }
    if (!refresh_atlas(device)) {
        return CREATE_FAILED;   // not final: the next theme (the default) gets a new texture
    }
    if (b.count() == 0) {
        return READY;
    }
    ID3D11Texture2D* back = nullptr;
    if (FAILED(swap->GetBuffer(0, __uuidof(ID3D11Texture2D), reinterpret_cast<void**>(&back))) || back == nullptr) {
        return NO_BACK_BUFFER;
    }
    D3D11_TEXTURE2D_DESC td = {};
    back->GetDesc(&td);
    g_status.last_w = td.Width;
    g_status.last_h = td.Height;
    g_status.last_format = td.Format;
    if (info != nullptr) {
        info->w = td.Width;
        info->h = td.Height;
        info->format = td.Format;
        info->samples = td.SampleDesc.Count;
    }
    if (td.Width < MIN_W || td.Height < MIN_H || !format_ok(td.Format)) {
        back->Release();
        return BAD_BACK_BUFFER;
    }
    ID3D11RenderTargetView* rtv = nullptr;
    if (FAILED(device->CreateRenderTargetView(back, nullptr, &rtv)) || rtv == nullptr) {
        back->Release();
        return RTV_FAILED;
    }
    D3D11_MAPPED_SUBRESOURCE mapped = {};
    if (FAILED(context->Map(g_res.vb, 0, D3D11_MAP_WRITE_DISCARD, 0, &mapped)) || mapped.pData == nullptr) {
        rtv->Release();
        back->Release();
        return MAP_FAILED;
    }
    const space s = make_space(td.Width, td.Height);
    vertex* v = static_cast<vertex*>(mapped.pData);
    uint32_t quads = 0;
    for (uint32_t k = 0; k < b.count() && k < QUAD_CAP; ++k) {
        expand(b.at(k), s, v + 6 * quads);
        ++quads;
    }
    context->Unmap(g_res.vb, 0);
    save(context);
    bind(context, rtv, td.Width, td.Height);
    if (__atomic_exchange_n(&g_fault, 0u, __ATOMIC_RELAXED) == 1) {
        asm volatile("movq 8, %%rax" : : : "rax", "memory");   // the restore drill (host tests): a load from address 8
    }
    context->Draw(quads * 6, 0);
    restore();
    rtv->Release();
    back->Release();
    if (info != nullptr) {
        info->quads = quads;
    }
    return DRAWN;
}

void restore_after_fault()
{
    restore();
}

status_view status()
{
    return g_status;
}

const char* object_name(uint32_t id)
{
    switch (id) {
    case OBJ_VS: return "vertex shader";
    case OBJ_PS: return "pixel shader";
    case OBJ_LAYOUT: return "input layout";
    case OBJ_VB: return "vertex buffer";
    case OBJ_ATLAS: return "font atlas texture";
    case OBJ_SRV: return "font atlas view";
    case OBJ_SAMPLER: return "sampler";
    case OBJ_BLEND: return "blend state";
    case OBJ_RASTER: return "rasterizer state";
    case OBJ_DEPTH: return "depth-stencil state";
    case OBJ_ATLAS_DATA: return "font atlas data";
    default: return "none";
    }
}

const char* result_name(int32_t r)
{
    switch (r) {
    case DRAWN: return "drawn";
    case READY: return "ready";
    case NO_RENDERER: return "no renderer";
    case MISMATCH: return "swap chain/device mismatch";
    case CREATE_FAILED: return "object creation failed";
    case DEVICE_REMOVED: return "device removed";
    case NO_BACK_BUFFER: return "no back buffer";
    case BAD_BACK_BUFFER: return "back buffer too small or unsupported format";
    case RTV_FAILED: return "render target view failed";
    case MAP_FAILED: return "vertex buffer map failed";
    default: return "unknown";
    }
}

uint32_t test_saved_states(const char** out, uint32_t cap)
{
    uint32_t n = 0;
    for (const char* s : SAVED) {
        if (n < cap) {
            out[n] = s;
        }
        ++n;
    }
    return n;
}

uint32_t test_bound_states(const char** out, uint32_t cap)
{
    uint32_t n = 0;
    for (const char* s : BOUND) {
        if (n < cap) {
            out[n] = s;
        }
        ++n;
    }
    return n;
}

bool test_format_ok(uint32_t dxgi_format)
{
    return format_ok(dxgi_format);
}

void test_set_fault(uint32_t where)
{
    __atomic_store_n(&g_fault, where, __ATOMIC_RELAXED);
}

}  // namespace ilc::overlay::gpu
