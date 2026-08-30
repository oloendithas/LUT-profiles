#!/usr/bin/env python3
"""
Write a log-encoded test wedge and the result each LUT should produce.

Drop the wedge on a timeline, apply a LUT, and compare against the matching
expected image. If they differ, the LUT is not reaching the footage the way
you think it is - stale cache, wrong slot, or a colour management step in
between. If they match, the LUT is doing exactly what it was built to do.
"""

from __future__ import annotations

import importlib.util
import os
import struct
import sys
import zlib

import numpy as np

_here = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location(
    "vfy", os.path.join(_here, "verify_cube.py"))
vfy = importlib.util.module_from_spec(_spec)
sys.modules["vfy"] = vfy
_spec.loader.exec_module(vfy)
gen = vfy.gen

WIDTH, RAMP_H, STEP_H = 1024, 110, 90
STEPS = 16


def write_png(path, rgb):
    """Minimal 8-bit RGB PNG writer."""
    h, w, _ = rgb.shape
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))

    def chunk(tag, data):
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 9))
           + chunk(b"IEND", b""))
    with open(path, "wb") as fh:
        fh.write(png)


def build_wedge():
    """Neutral log code values: a continuous ramp over a stepped wedge."""
    ramp = np.linspace(0.0, 1.0, WIDTH)
    steps = np.floor(np.linspace(0.0, STEPS - 1e-9, WIDTH)) / (STEPS - 1)
    rows = [np.repeat(ramp[None, :], RAMP_H, axis=0),
            np.repeat(steps[None, :], STEP_H, axis=0)]
    grey = np.concatenate(rows, axis=0)
    return np.repeat(grey[..., None], 3, axis=-1)


def to_u8(x):
    return np.clip(np.rint(x * 255.0), 0, 255).astype(np.uint8)


def main():
    outdir = os.path.join(_here, os.pardir, "docs")
    os.makedirs(outdir, exist_ok=True)
    wedge = build_wedge()
    write_png(os.path.join(outdir, "test_wedge_log.png"), to_u8(wedge))
    print("wrote docs/test_wedge_log.png  (feed this to the LUT)")

    flat = wedge.reshape(-1, 3)
    for i, profile in enumerate(gen.PROFILES, start=1):
        name = f"Underwater_Sea_{i:02d}_{profile.key}"
        table, size, dmin, dmax = vfy.read_cube(
            os.path.join(_here, os.pardir, "luts", name + ".cube"))
        out = vfy.apply_cube(flat, table, size, dmin, dmax).reshape(wedge.shape)
        write_png(os.path.join(outdir, f"test_wedge_{profile.key.lower()}.png"),
                  to_u8(out))
        print(f"wrote docs/test_wedge_{profile.key.lower()}.png")

    # the same thing as numbers, for checking with a colour picker
    probes = np.linspace(0.0, 1.0, STEPS)
    table, size, dmin, dmax = vfy.read_cube(
        os.path.join(_here, os.pardir, "luts", "Underwater_Sea_02_Medium.cube"))
    got = vfy.apply_cube(np.repeat(probes[:, None], 3, axis=1),
                         table, size, dmin, dmax)
    print("\nMedium profile, 8-bit in -> 8-bit out (neutral input):")
    print("  in  :", "  ".join(f"{int(round(v * 255)):3d}" for v in probes))
    for c, lbl in enumerate("RGB"):
        print(f"  {lbl}   :", "  ".join(f"{int(round(v * 255)):3d}" for v in got[:, c]))


if __name__ == "__main__":
    main()
