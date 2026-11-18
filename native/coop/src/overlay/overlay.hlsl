// Ivalice Legacy co-op overlay: the one vertex shader and the one pixel shader (compiled to DXBC by
// tools/gen_overlay_shaders.py into shaders.inc; shader model 4.0 so any D3D11 feature level the game runs on takes it).
//
// Every quad carries its own mode (m.x, not interpolated); the mode is floor(m.x), never a threshold on m.x itself: the
// GLASS modes carry a fraction up to 1023/1024 (review ★2: 3 + 0.6 once read as HATCH):
//   0 SOLID   colour c0 (per vertex: gradients and end fades come from the vertex colours)
//   1 SDF     text and icons from the R8 distance atlas: core c0 inside the outline, edge c1 in the band p.x below
//             it, a soft glow p.y beyond that (c1 again), p.z widens the anti-alias ramp (soft shadows)
//   2 GLASS   a rounded rectangle in pixels: uv = position from the window's centre, p.xy = half size, p.z = corner
//             radius, p.w = edge softness (a halo; 0 = crisp), m.y = rim width in pixels; fill c0 (vertical gradient),
//             rim c1. The fraction of m.x packs the sheen amount and the rim's dimming toward the bottom:
//             (shine * 32 + bias) / 1024, each 0..31 (theme window.shine.amount, window.rim_top_bias).
//   3 GLASS_CHAMFER  GLASS with the top-left and bottom-right corners cut by p.w pixels (theme window.shape chamfer)
//   4 HATCH   SOLID with diagonal stripes in screen pixels (the damage trail; m.y = pixels per authoring unit)
// Output is premultiplied alpha (blend ONE, INV_SRC_ALPHA).

struct vs_in {
    float2 pos : POSITION;    // clip space
    float2 uv : TEXCOORD0;
    float4 p : TEXCOORD1;
    float4 c0 : COLOR0;
    float4 c1 : COLOR1;
    float2 m : TEXCOORD2;
};

struct ps_in {
    float4 pos : SV_Position;
    float2 uv : TEXCOORD0;
    float4 p : TEXCOORD1;
    float4 c0 : COLOR0;
    float4 c1 : COLOR1;
    nointerpolation float2 m : TEXCOORD2;
};

Texture2D atlas : register(t0);
SamplerState linear_clamp : register(s0);

ps_in vs_main(vs_in i)
{
    ps_in o;
    o.pos = float4(i.pos, 0.0, 1.0);
    o.uv = i.uv;
    o.p = i.p;
    o.c0 = i.c0;
    o.c1 = i.c1;
    o.m = i.m;
    return o;
}

float4 over(float4 top, float4 under)
{
    return top + under * (1.0 - top.a);
}

float4 ps_main(ps_in i) : SV_Target
{
    // The integer mode; + 0.0001 absorbs a representation error below a whole number (the fraction never reaches 1).
    float base = floor(i.m.x + 0.0001);
    if (base < 0.5) {
        return float4(i.c0.rgb * i.c0.a, i.c0.a);
    }
    if (base < 1.5) {
        float d = atlas.Sample(linear_clamp, i.uv).r;
        float w = max(fwidth(d) * 0.75, 0.0001) + i.p.z;
        float core = smoothstep(0.5 - w, 0.5 + w, d);
        float edge = smoothstep(0.5 - i.p.x - w, 0.5 - i.p.x + w, d);
        float glow = 0.0;
        if (i.p.y > 0.0) {
            glow = smoothstep(0.5 - i.p.x - i.p.y, 0.5 - i.p.x, d) * 0.55;
        }
        float a_core = core * i.c0.a;
        float a_edge = max(edge, glow) * i.c1.a;
        return over(float4(i.c0.rgb * a_core, a_core), float4(i.c1.rgb * a_edge, a_edge));
    }
    if (base > 3.5) {
        if (base > 4.5) {
            return float4(0.0, 0.0, 0.0, 0.0);   // no such mode: nothing drawn
        }
        float s = frac((i.pos.x + i.pos.y) / (4.4 * max(i.m.y, 0.5)));
        float ha = i.c0.a * (1.0 - 0.28 * step(0.5, s));
        return float4(i.c0.rgb * ha, ha);
    }
    // GLASS (2) and GLASS_CHAMFER (3): the fraction packs the sheen and the rim bias.
    float packed = floor((i.m.x - base) * 1024.0 + 0.5);
    float shine_q = floor(packed / 32.0);
    float shine = shine_q / 31.0;
    float bias = (packed - shine_q * 32.0) / 31.0;
    float rim_w = max(i.m.y, 1.0);
    float2 q = abs(i.uv) - (i.p.xy - i.p.z);
    float dist = length(max(q, 0.0)) + min(max(q.x, q.y), 0.0) - i.p.z;   // pixels; negative inside
    float cover = saturate(0.5 - dist);
    if (base > 2.5) {
        float cut = i.p.w;
        float tl = ((-i.uv.x - i.uv.y) - (i.p.x + i.p.y - cut)) * 0.70710678;
        float br = ((i.uv.x + i.uv.y) - (i.p.x + i.p.y - cut)) * 0.70710678;
        dist = max(dist, max(tl, br));
        cover = saturate(0.5 - dist);
    } else if (i.p.w > 0.0) {
        cover = saturate(1.0 - max(dist, 0.0) / i.p.w);   // a halo: fades over p.w pixels outside the edge
    }
    float inside = -dist;
    float t = saturate((i.uv.y + i.p.y) / max(2.0 * i.p.y, 1.0));   // 0 top .. 1 bottom
    float rim = saturate(1.0 - abs(inside - rim_w * 0.5) / (rim_w * 0.5 + 0.5)) * (1.0 - bias * t);
    float3 rgb = i.c0.rgb;
    if (shine > 0.0) {
        float hy2 = max(2.0 * i.p.y, 1.0);
        float band = saturate(1.0 - abs(frac(i.uv.x / hy2 * 2.0 + (i.uv.y + i.p.y) / hy2) - 0.5) * 9.0);
        rgb = lerp(rgb, float3(0.784, 0.878, 1.0), band * shine * (1.0 - 0.6 * t) * step(rim_w, inside));
    }
    float rim_a = rim * i.c1.a;
    rgb = lerp(rgb, i.c1.rgb, rim_a);
    float a = cover * max(i.c0.a, rim_a);
    return float4(rgb * a, a);
}
