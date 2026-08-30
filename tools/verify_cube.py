#!/usr/bin/env python3
"""
Closed-loop check on the generated .cube files.

Takes a ColorChecker, simulates how it would record through the water column
each profile is built for, encodes that to log, pushes it through the .cube
file with trilinear interpolation, and compares the result against the same
chart rendered with no water in the way. Also writes an SVG contact sheet.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys

import numpy as np

_spec = importlib.util.spec_from_file_location(
    "gen", os.path.join(os.path.dirname(os.path.abspath(__file__)), "generate_luts.py"))
gen = importlib.util.module_from_spec(_spec)
sys.modules["gen"] = gen
_spec.loader.exec_module(gen)


COLORCHECKER = np.array([
    (115, 82, 68), (194, 150, 130), (98, 122, 157), (87, 108, 67),
    (133, 128, 177), (103, 189, 170), (214, 126, 44), (80, 91, 166),
    (193, 90, 99), (94, 60, 108), (157, 188, 64), (224, 163, 46),
    (56, 61, 150), (70, 148, 73), (175, 54, 60), (231, 199, 31),
    (187, 86, 149), (8, 133, 161), (243, 243, 242), (200, 200, 200),
    (160, 160, 160), (122, 122, 121), (85, 85, 85), (52, 52, 52),
], dtype=float) / 255.0

PATCH_NAMES = [
    "dark skin", "light skin", "blue sky", "foliage", "blue flower",
    "bluish green", "orange", "purplish blue", "moderate red", "purple",
    "yellow green", "orange yellow", "blue", "green", "red", "yellow",
    "magenta", "cyan", "white", "neutral 8", "neutral 6.5", "neutral 5",
    "neutral 3.5", "black",
]


def srgb_to_linear(c):
    return np.where(c <= 0.04045, c / 12.92, ((c + 0.055) / 1.055) ** 2.4)


# --------------------------------------------------------------------------


def read_cube(path):
    size = None
    data = []
    dmin, dmax = np.zeros(3), np.ones(3)
    with open(path, encoding="utf-8") as fh:
        for raw in fh:
            line = raw.split("#")[0].strip()
            if not line:
                continue
            head = line.split()[0].upper()
            if head == "LUT_3D_SIZE":
                size = int(line.split()[1])
            elif head == "DOMAIN_MIN":
                dmin = np.array([float(v) for v in line.split()[1:4]])
            elif head == "DOMAIN_MAX":
                dmax = np.array([float(v) for v in line.split()[1:4]])
            elif head in ("TITLE", "LUT_1D_SIZE", "LUT_IN_VIDEO_RANGE"):
                continue
            else:
                data.append([float(v) for v in line.split()[:3]])
    if size is None:
        raise ValueError(f"{path}: no LUT_3D_SIZE")
    arr = np.asarray(data)
    if arr.shape != (size ** 3, 3):
        raise ValueError(f"{path}: expected {size ** 3} entries, found {arr.shape[0]}")
    # .cube stores red fastest -> reshape to [b][g][r] then move to [r][g][b]
    table = arr.reshape(size, size, size, 3).transpose(2, 1, 0, 3)
    return table, size, dmin, dmax


def apply_cube(rgb, table, size, dmin, dmax):
    """Trilinear interpolation, the way a grading application would."""
    t = np.clip((rgb - dmin) / (dmax - dmin), 0.0, 1.0) * (size - 1)
    i0 = np.floor(t).astype(int)
    i0 = np.minimum(i0, size - 2)
    f = t - i0
    out = np.zeros(rgb.shape)
    for dr in (0, 1):
        for dg in (0, 1):
            for db in (0, 1):
                w = (np.where(dr, f[..., 0], 1 - f[..., 0])
                     * np.where(dg, f[..., 1], 1 - f[..., 1])
                     * np.where(db, f[..., 2], 1 - f[..., 2]))
                out += w[..., None] * table[i0[..., 0] + dr,
                                            i0[..., 1] + dg,
                                            i0[..., 2] + db]
    return out


# --------------------------------------------------------------------------


def render_reference(scene_lin, profile):
    """The chart with no water in front of it, same display rendering."""
    disp = gen.aces_tonemap(scene_lin * profile.exposure * gen.TONEMAP_EXPOSURE)
    out = np.clip(disp, 0.0, 1.0) ** (1.0 / gen.OUTPUT_GAMMA)
    return np.clip(gen.contrast_curve(out, profile.contrast), 0.0, 1.0)


def simulate_underwater(scene_lin, profile):
    cast = np.asarray(profile.cast, dtype=float)
    veil = np.asarray(profile.veil, dtype=float)
    attenuated = scene_lin * cast / float(gen.LUMA @ cast)
    return attenuated * (1.0 - veil) + veil


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--log", default="generic", choices=sorted(gen.LOG_FORMATS))
    ap.add_argument("--dir", default="luts")
    ap.add_argument("--svg", default="docs/colorchecker.svg")
    args = ap.parse_args()

    _, decode, src_gamut = gen.LOG_FORMATS[args.log]
    mat = gen.gamut_matrix(src_gamut)
    scene_lin = srgb_to_linear(COLORCHECKER)

    rows, worst_all = [], 0.0
    for i, profile in enumerate(gen.PROFILES, start=1):
        path = os.path.join(args.dir, f"Underwater_Sea_{i:02d}_{profile.key}.cube")
        table, size, dmin, dmax = read_cube(path)

        recorded = simulate_underwater(scene_lin, profile)
        code = np.stack([gen.invert_decode(decode, np.linalg.solve(mat, p))
                         for p in recorded])
        corrected = apply_cube(code, table, size, dmin, dmax)
        reference = render_reference(scene_lin, profile)
        uncorrected = render_reference(recorded, profile)

        err = np.abs(corrected - reference)
        before = np.abs(uncorrected - reference)
        worst = int(err.mean(axis=1).argmax())
        worst_all = max(worst_all, err.max())
        print(f"{profile.key:7s} {os.path.basename(path)}  grid {size}^3")
        print(f"         mean |error| {err.mean() * 255:5.1f}/255  "
              f"max {err.max() * 255:5.1f}/255  "
              f"(uncorrected: mean {before.mean() * 255:5.1f}, "
              f"max {before.max() * 255:5.1f})")
        print(f"         worst patch: {PATCH_NAMES[worst]}")
        neutrals = slice(18, 24)
        n_err = np.abs(corrected[neutrals].max(1) - corrected[neutrals].min(1))
        print(f"         grey patch tint after correction: "
              f"max {n_err.max() * 255:4.1f}/255")
        rows.append((profile, uncorrected, corrected, reference))

    write_svg(args.svg, rows)
    print(f"\nwrote {args.svg}")


def write_svg(path, rows):
    """Contact sheet: one strip per variant, 24 patches across."""
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    left, strip, gap, width = 96, 30, 6, 760
    band = 3 * (strip + gap) + 26
    height = 40 + len(rows) * band
    patch = (width - left - 12) / 24.0

    def hexc(c):
        return "#%02x%02x%02x" % tuple(int(round(v * 255)) for v in np.clip(c, 0, 1))

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" '
           f'height="{height}" viewBox="0 0 {width} {height}" '
           f'font-family="ui-sans-serif, system-ui, sans-serif">',
           f'<rect width="{width}" height="{height}" fill="#171717"/>',
           '<text x="16" y="24" fill="#e8e8e8" font-size="13">'
           'ColorChecker through the water column, corrected by each LUT</text>']

    y = 40
    for profile, before, after, reference in rows:
        out.append(f'<text x="16" y="{y + 12}" fill="#c8ccd0" font-size="12">'
                   f'{profile.name}</text>')
        for k, (label, patches) in enumerate((("recorded", before),
                                              ("corrected", after),
                                              ("reference", reference))):
            ry = y + 20 + k * (strip + gap)
            out.append(f'<text x="16" y="{ry + strip * 0.68:.0f}" fill="#7c8286" '
                       f'font-size="10">{label}</text>')
            for j, colour in enumerate(patches):
                out.append(f'<rect x="{left + j * patch:.1f}" y="{ry}" '
                           f'width="{patch:.1f}" height="{strip}" '
                           f'fill="{hexc(colour)}"/>')
        y += band

    out.append("</svg>")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(out))


if __name__ == "__main__":
    main()
