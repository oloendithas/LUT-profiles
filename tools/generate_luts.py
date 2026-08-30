#!/usr/bin/env python3
"""
Generate .cube 3D LUTs that correct sea / underwater footage.

Pipeline (per LUT entry):

    log code value
      -> decode to scene-linear (camera log curve)
      -> camera gamut  ->  Rec.709 linear (matrix from primaries)
      -> veiling-glare (backscatter) subtraction
      -> depth-dependent white balance with shadow protection
      -> filmic tone map (ACES fit) -> display linear
      -> Rec.709 / BT.1886 encode (gamma 2.4)
      -> S-curve contrast around mid grey
      -> hue-targeted saturation + cyan-to-blue hue nudge
      -> Rec.709 display code value

Three strengths are shipped: Light, Medium, Strong.  Run with --log to
rebuild the same three profiles for a specific camera log encoding.
"""

from __future__ import annotations

import argparse
import math
import os
from dataclasses import dataclass

import numpy as np

# --------------------------------------------------------------------------
# Colour primaries
# --------------------------------------------------------------------------

D65 = (0.3127, 0.3290)

PRIMARIES = {
    # name: (R xy, G xy, B xy, W xy)
    "rec709": ((0.6400, 0.3300), (0.3000, 0.6000), (0.1500, 0.0600), D65),
    "sgamut3cine": ((0.7660, 0.2750), (0.2250, 0.8000), (0.0890, -0.0870), D65),
    "awg3": ((0.6840, 0.3130), (0.2210, 0.8480), (0.0861, -0.1020), D65),
    "dgamut": ((0.7100, 0.3100), (0.2100, 0.8800), (0.0900, -0.0800), D65),
}


def xy_to_xyz(xy):
    x, y = xy
    return np.array([x / y, 1.0, (1.0 - x - y) / y])


def rgb_to_xyz_matrix(prims):
    r, g, b, w = prims
    m = np.column_stack([xy_to_xyz(r), xy_to_xyz(g), xy_to_xyz(b)])
    s = np.linalg.solve(m, xy_to_xyz(w))
    return m * s


def gamut_matrix(src: str) -> np.ndarray:
    """3x3 matrix converting linear `src` RGB into linear Rec.709 RGB."""
    if src == "rec709":
        return np.eye(3)
    to_xyz = rgb_to_xyz_matrix(PRIMARIES[src])
    from_xyz = np.linalg.inv(rgb_to_xyz_matrix(PRIMARIES["rec709"]))
    return from_xyz @ to_xyz


# --------------------------------------------------------------------------
# Log encodings (decode: code value in [0,1] -> scene linear, 0.18 = mid grey)
# --------------------------------------------------------------------------


def _solve_generic_log():
    """Cineon-style curve  y = c*log10(x + b) + d  fitted so that
    black -> 0.075, 0.18 grey -> 0.420, 100% white -> 0.660."""
    def f(b):
        return (math.log10((0.18 + b) / b) / math.log10((1.0 + b) / (0.18 + b))
                - 0.345 / 0.240)

    lo, hi = 1e-6, 1.0
    for _ in range(200):
        mid = math.sqrt(lo * hi)
        if f(lo) * f(mid) <= 0:
            hi = mid
        else:
            lo = mid
    b = math.sqrt(lo * hi)
    c = 0.345 / math.log10((0.18 + b) / b)
    d = 0.075 - c * math.log10(b)
    return b, c, d


_GEN_B, _GEN_C, _GEN_D = _solve_generic_log()


def decode_generic(y):
    return 10.0 ** ((y - _GEN_D) / _GEN_C) - _GEN_B


def decode_slog3(y):
    """Sony S-Log3."""
    brk = 171.2102946929 / 1023.0
    hi = (10.0 ** ((np.clip(y, brk, None) * 1023.0 - 420.0) / 261.5)) * 0.19 - 0.01
    lo = (y * 1023.0 - 95.0) * 0.01125000 / (171.2102946929 - 95.0)
    return np.where(y >= brk, hi, lo)


def decode_logc3(y):
    """ARRI LogC3, EI 800."""
    cut, a, b = 0.010591, 5.555556, 0.052272
    c, d, e, f = 0.247190, 0.385537, 5.367655, 0.092809
    brk = e * cut + f
    hi = (10.0 ** ((np.clip(y, brk, None) - d) / c) - b) / a
    lo = (y - f) / e
    return np.where(y > brk, hi, lo)


def decode_dlog(y):
    """DJI D-Log."""
    hi = (10.0 ** ((np.clip(y, 0.14, None) - 0.584555) / 0.256663) - 0.0108) / 0.9892
    lo = (y - 0.0929) / 6.025
    return np.where(y <= 0.14, lo, hi)


def decode_protune(y):
    """GoPro Protune (flat) log curve."""
    return (113.5 ** y - 1.0) / 112.5


LOG_FORMATS = {
    # key: (pretty name, decode fn, source gamut)
    "generic": ("Generic Log", decode_generic, "rec709"),
    "slog3": ("Sony S-Log3 / S-Gamut3.Cine", decode_slog3, "sgamut3cine"),
    "logc3": ("ARRI LogC3 / AWG3", decode_logc3, "awg3"),
    "dlog": ("DJI D-Log / D-Gamut", decode_dlog, "dgamut"),
    "protune": ("GoPro Protune Flat", decode_protune, "rec709"),
}


# --------------------------------------------------------------------------
# Profile definitions
# --------------------------------------------------------------------------


@dataclass
class Profile:
    key: str
    name: str
    blurb: str
    # linear RGB a neutral grey card reads as, through the water column
    cast: tuple = (0.38, 0.80, 1.00)
    # veiling glare / backscatter pedestal, in linear
    veil: tuple = (0.000, 0.008, 0.020)
    shadow_knee: float = 0.010     # luminance below which WB is eased off
    shadow_floor: float = 0.32     # residual WB strength in deep shadow
    contrast: float = 0.45         # tanh contrast parameter (0 = off)
    saturation: float = 1.08       # global saturation
    warm_sat: float = 1.20         # saturation on reds / oranges / yellows
    cool_sat: float = 0.88         # saturation on cyans / blues
    hue_pull: float = 6.0          # degrees, cyan pulled toward blue
    exposure: float = 1.0


PROFILES = [
    Profile(
        key="Light",
        name="Underwater Sea - Light",
        blurb="Shallow / clear water, roughly 3-8 m. Gentle red recovery.",
        cast=(0.62, 0.90, 1.00),
        veil=(0.000, 0.004, 0.010),
        shadow_knee=0.008,
        shadow_floor=0.40,
        contrast=0.30,
        saturation=1.04,
        warm_sat=1.10,
        cool_sat=0.94,
        hue_pull=3.0,
    ),
    Profile(
        key="Medium",
        name="Underwater Sea - Medium",
        blurb="Typical reef depth, roughly 8-18 m. Balanced everyday grade.",
        cast=(0.38, 0.80, 1.00),
        veil=(0.000, 0.008, 0.020),
        shadow_knee=0.010,
        shadow_floor=0.32,
        contrast=0.45,
        saturation=1.08,
        warm_sat=1.20,
        cool_sat=0.88,
        hue_pull=6.0,
    ),
    Profile(
        key="Strong",
        name="Underwater Sea - Strong",
        blurb="Deep, green or turbid water, roughly 18-30 m. Aggressive rescue.",
        cast=(0.20, 0.68, 1.00),
        veil=(0.000, 0.014, 0.034),
        shadow_knee=0.014,
        shadow_floor=0.25,
        contrast=0.60,
        saturation=1.12,
        warm_sat=1.32,
        cool_sat=0.80,
        hue_pull=9.0,
        exposure=1.05,
    ),
]


# --------------------------------------------------------------------------
# Image operations
# --------------------------------------------------------------------------

LUMA = np.array([0.2126, 0.7152, 0.0722])


def soft_pos(x, eps=1e-5):
    """Smooth, monotonic max(x, 0) - avoids a hard kink in the shadows."""
    return 0.5 * (x + np.sqrt(x * x + eps * eps))


def veil_subtract(x, veil):
    """Remove the backscatter pedestal and rescale to white.

    Straight subtraction leaves a corner where the signal meets the pedestal,
    and the output gamma turns that corner into a visible edge in the shadows.
    Below twice the pedestal the curve becomes the parabola through the origin
    that matches the straight line in both value and slope, so black stays
    black and the toe is smooth.
    """
    v = np.asarray(veil, dtype=float)
    straight = (x - v) / (1.0 - v)
    with np.errstate(divide="ignore", invalid="ignore"):
        toe = np.where(v > 0.0, x * x / (4.0 * np.maximum(v, 1e-12) * (1.0 - v)), x)
    return np.where(x >= 2.0 * v, straight, np.maximum(toe, 0.0))


def smoothstep(edge0, edge1, x):
    t = np.clip((x - edge0) / (edge1 - edge0), 0.0, 1.0)
    return t * t * (3.0 - 2.0 * t)


def white_balance_gains(cast):
    """Per-channel gains that neutralise `cast` while holding its luminance."""
    cast = np.asarray(cast, dtype=float)
    return float(LUMA @ cast) / cast


GREY_OUT = 0.42          # output code for 0.18 scene grey
MIDTONE_SLOPE = 0.125    # output code gained per stop through the midtones
TOE_POWER = 0.60         # < 1 keeps deep shadows separated instead of crushed
SHOULDER_STOPS = 3.5     # stops over grey where the highlight roll-off starts
WHITE_STOPS = 5.6        # stops over grey that reach full white
OUTPUT_GAMMA = 2.4       # BT.1886 display the curve is built to be seen on

# Toe joins the straight portion where the straight line would otherwise start
# falling as fast as the light itself; below that the curve rolls off as a
# power law so shadows keep separating all the way down to black.
_TOE_Y = MIDTONE_SLOPE / (TOE_POWER * math.log(2.0))
_TOE_E = (_TOE_Y - GREY_OUT) / MIDTONE_SLOPE
_SHOULDER_E = SHOULDER_STOPS
_SHOULDER_Y = GREY_OUT + MIDTONE_SLOPE * _SHOULDER_E

# The shoulder is a cubic Hermite landing on white with zero slope. It only
# decelerates the whole way - rather than speeding up first and then braking -
# when its entry slope is at least 1.5x its average slope.
assert MIDTONE_SLOPE * (WHITE_STOPS - _SHOULDER_E) >= 1.5 * (1.0 - _SHOULDER_Y)


def display_curve(x):
    """Scene linear -> Rec.709 code value.

    A straight line through the midtones in log exposure, with a power-law toe
    and a cubic shoulder that lands on white with zero slope. Deliberately
    gentler than a film-emulation S-curve: the point is to keep highlights and
    shadows separated rather than to drive them to the ends of the range.
    """
    x = np.asarray(x, dtype=float)
    safe = np.maximum(x, 1e-10)
    e = np.log2(safe / 0.18)

    straight = GREY_OUT + MIDTONE_SLOPE * e
    toe = _TOE_Y * np.exp2((e - _TOE_E) * TOE_POWER)

    span = WHITE_STOPS - _SHOULDER_E
    head = 1.0 - _SHOULDER_Y
    m0 = MIDTONE_SLOPE * span
    t = np.clip((e - _SHOULDER_E) / span, 0.0, 1.0)
    shoulder = (_SHOULDER_Y + m0 * t
                + (3.0 * head - 2.0 * m0) * t * t
                + (m0 - 2.0 * head) * t * t * t)

    y = np.where(e <= _TOE_E, toe, np.where(e >= _SHOULDER_E, shoulder, straight))
    return np.clip(np.where(x <= 0.0, 0.0, y), 0.0, 1.0)


def contrast_curve(x, c, pivot=GREY_OUT):
    """Endpoint-preserving S-curve. c = 0 is identity, higher is punchier."""
    if c <= 0.0:
        return x
    k = math.tanh(c)
    up = pivot + (1.0 - pivot) * np.tanh(c * (x - pivot) / (1.0 - pivot)) / k
    dn = pivot + pivot * np.tanh(c * (x - pivot) / pivot) / k
    return np.where(x >= pivot, up, dn)


def rgb_to_hsv(rgb):
    r, g, b = rgb[..., 0], rgb[..., 1], rgb[..., 2]
    mx = np.max(rgb, axis=-1)
    mn = np.min(rgb, axis=-1)
    d = mx - mn
    safe = np.where(d == 0.0, 1.0, d)
    h = np.select(
        [d == 0.0, mx == r, mx == g],
        [np.zeros_like(mx),
         ((g - b) / safe) % 6.0,
         ((b - r) / safe) + 2.0],
        default=((r - g) / safe) + 4.0,
    ) * 60.0
    s = np.where(mx == 0.0, 0.0, d / np.where(mx == 0.0, 1.0, mx))
    return h % 360.0, s, mx


def hsv_to_rgb(h, s, v):
    h = h % 360.0
    c = v * s
    x = c * (1.0 - np.abs((h / 60.0) % 2.0 - 1.0))
    m = v - c
    z = np.zeros_like(h)
    idx = (h / 60.0).astype(int) % 6
    r = np.select([idx == 0, idx == 1, idx == 2, idx == 3, idx == 4],
                  [c, x, z, z, x], default=c)
    g = np.select([idx == 0, idx == 1, idx == 2, idx == 3, idx == 4],
                  [x, c, c, x, z], default=z)
    b = np.select([idx == 0, idx == 1, idx == 2, idx == 3, idx == 4],
                  [z, z, x, c, c], default=x)
    return np.stack([r + m, g + m, b + m], axis=-1)


def hue_weight(h, center, width):
    """Raised cosine over the shortest hue distance, 1 at centre, 0 at width."""
    d = np.abs((h - center + 180.0) % 360.0 - 180.0)
    t = np.clip(d / width, 0.0, 1.0)
    return 0.5 * (1.0 + np.cos(math.pi * t))


WARM_CENTER, WARM_WIDTH = 25.0, 60.0
COOL_CENTER, COOL_WIDTH = 195.0, 55.0
BLUE_TARGET = 215.0
SAT_SHADOW_KNEE = 0.22   # output code below which hue-selective work fades out
SAT_KNEE = 0.80          # saturation above this rolls off instead of clipping
SAT_CEILING = (0.75, 1.00)   # boosts fade out across this saturation range


def soft_saturation(s, knee=SAT_KNEE):
    """Monotonic roll-off toward full saturation - a hard clip at 1.0 would
    crush the minimum channel to zero and put a kink in shadow gradients."""
    head = 1.0 - knee
    return np.where(s <= knee, s, knee + head * np.tanh((s - knee) / head))


def limit_boost(mult, s):
    """Fade any saturation *increase* out as the colour approaches full
    saturation. There is nothing left to add there, and pushing anyway drags
    the middle channel around and breaks smooth gradients."""
    head = 1.0 - smoothstep(SAT_CEILING[0], SAT_CEILING[1], s)
    return np.where(mult > 1.0, 1.0 + (mult - 1.0) * head, mult)


# --------------------------------------------------------------------------
# The transform
# --------------------------------------------------------------------------


def apply_profile(code, profile: Profile, decode, cam_to_709):
    """code: (N,3) log code values in [0,1] -> (N,3) Rec.709 code values."""
    lin = np.asarray(decode(code), dtype=float)
    lin = lin @ cam_to_709.T
    lin = np.clip(lin, 0.0, None)

    # 1. veiling glare / backscatter pedestal
    lin = veil_subtract(lin, profile.veil)

    # 2. depth white balance, eased off in deep shadow so red noise
    #    is not multiplied up along with red signal
    gains = white_balance_gains(profile.cast)
    y = lin @ LUMA
    # ramp the gain in perceptually, so the transition is spread over a wide
    # band of shadow code values instead of a narrow spike in linear light
    w = profile.shadow_floor + (1.0 - profile.shadow_floor) * smoothstep(
        0.0, profile.shadow_knee ** (1.0 / OUTPUT_GAMMA), soft_pos(y) ** (1.0 / OUTPUT_GAMMA))
    lin = lin * (1.0 + (gains - 1.0) * w[..., None])

    # 3. display rendering
    out = display_curve(lin * profile.exposure)

    # 4. contrast recovered from the scattering veil
    out = contrast_curve(out, profile.contrast)

    # 5. hue-targeted saturation and a nudge of leftover cyan toward blue
    h, s, v = rgb_to_hsv(np.clip(out, 0.0, 1.0))
    ww = hue_weight(h, WARM_CENTER, WARM_WIDTH)
    cw = hue_weight(h, COOL_CENTER, COOL_WIDTH)
    # noisy shadows are the worst place to push saturation around, so fade
    # the whole hue-selective stage in as the pixel gets brighter
    vw = smoothstep(0.0, SAT_SHADOW_KNEE, v)
    sat = profile.saturation * (1.0 + (profile.warm_sat - 1.0) * ww * vw) \
                            * (1.0 + (profile.cool_sat - 1.0) * cw * vw)
    h = h + np.clip(BLUE_TARGET - h, -profile.hue_pull, profile.hue_pull) * cw * vw
    sat = limit_boost(sat, s)
    out = hsv_to_rgb(h, soft_saturation(np.maximum(s * sat, 0.0)), v)

    return np.clip(out, 0.0, 1.0)


# --------------------------------------------------------------------------
# .cube writer
# --------------------------------------------------------------------------


def lut_grid(size):
    axis = np.linspace(0.0, 1.0, size)
    r, g, b = np.meshgrid(axis, axis, axis, indexing="ij")
    # .cube order: red varies fastest, then green, then blue
    return np.stack([r.ravel(order="F"), g.ravel(order="F"), b.ravel(order="F")], axis=-1)


def write_cube(path, title, header_lines, size, rgb):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(f'TITLE "{title}"\n')
        for line in header_lines:
            fh.write(f"# {line}\n" if line else "#\n")
        fh.write(f"\nLUT_3D_SIZE {size}\n")
        fh.write("DOMAIN_MIN 0.0 0.0 0.0\n")
        fh.write("DOMAIN_MAX 1.0 1.0 1.0\n\n")
        for r, g, b in rgb:
            fh.write(f"{r:.6f} {g:.6f} {b:.6f}\n")


# --------------------------------------------------------------------------


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--log", default="generic", choices=sorted(LOG_FORMATS),
                    help="input log encoding (default: generic)")
    ap.add_argument("--size", type=int, default=33, help="3D LUT cube size")
    ap.add_argument("--outdir", default="luts")
    ap.add_argument("--suffix", default="", help="appended to output filenames")
    args = ap.parse_args()

    log_name, decode, src_gamut = LOG_FORMATS[args.log]
    mat = gamut_matrix(src_gamut)
    grid = lut_grid(args.size)
    os.makedirs(args.outdir, exist_ok=True)

    for i, profile in enumerate(PROFILES, start=1):
        rgb = apply_profile(grid, profile, decode, mat)
        fname = f"Underwater_Sea_{i:02d}_{profile.key}{args.suffix}.cube"
        path = os.path.join(args.outdir, fname)
        write_cube(
            path,
            profile.name,
            [
                profile.blurb,
                "",
                f"Input : {log_name} (scene-referred log, full range 0-1)",
                "Output: Rec.709 primaries, BT.1886 / gamma 2.4 display encode",
                "",
                f"Strength {i} of 3  (01 Light / 02 Medium / 03 Strong)",
                "Apply to log footage before any creative grade.",
                "Generated by tools/generate_luts.py",
            ],
            args.size,
            rgb,
        )
        print(f"wrote {path}  ({len(rgb)} entries)")

    verify(decode, mat)


def verify(decode, mat):
    print("\n--- checks ---")
    gain_ref = np.array([0.18, 0.18, 0.18])
    for profile in PROFILES:
        # a grey card seen through the water column, exposed at mid grey
        cast = np.asarray(profile.cast, dtype=float)
        veil = np.asarray(profile.veil, dtype=float)
        cast_lin = gain_ref * cast / float(LUMA @ cast)
        cast_lin = cast_lin * (1.0 - veil) + veil
        cam = np.linalg.solve(mat, cast_lin)
        # encode back through the log curve by inverting numerically
        code = invert_decode(decode, cam)
        out = apply_profile(code[None, :], profile, decode, mat)[0]
        h, s, _ = rgb_to_hsv(out[None, :])
        print(f"{profile.key:7s} grey card in -> out {np.round(out, 4)} "
              f"(saturation {float(s[0]):.3f})")

    axis = np.linspace(0.0, 1.0, 512)
    neutral = np.stack([axis] * 3, axis=-1)
    for profile in PROFILES:
        # the ramp an underwater grey card actually records, which is what the
        # LUT is built to straighten out
        cast = np.asarray(profile.cast, dtype=float)
        veil = np.asarray(profile.veil, dtype=float)
        lin = axis[:, None] * 8.0 * cast / float(LUMA @ cast)
        lin = lin * (1.0 - veil) + veil
        code = np.stack([invert_decode(decode, np.linalg.solve(mat, r)) for r in lin])
        rec = apply_profile(code, profile, decode, mat)
        out = apply_profile(neutral, profile, decode, mat)
        print(f"{profile.key:7s} recorded ramp: step {np.diff(rec, axis=0).min():+.1e} "
              f"tint {np.abs(rec.max(1) - rec.min(1)).max():.4f} | "
              f"neutral ramp: step {np.diff(out, axis=0).min():+.1e} "
              f"black {np.round(out[0], 4)} white {np.round(out[-1], 4)}")
        assert np.all(np.isfinite(out)) and out.min() >= 0.0 and out.max() <= 1.0


def invert_decode(decode, target_lin):
    """Numerically invert a log decode for a linear triplet."""
    lo = np.zeros(3)
    hi = np.ones(3)
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        v = np.asarray(decode(mid), dtype=float)
        too_high = v > target_lin
        hi = np.where(too_high, mid, hi)
        lo = np.where(too_high, lo, mid)
    return 0.5 * (lo + hi)


if __name__ == "__main__":
    main()
