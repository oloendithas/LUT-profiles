# LUT-profiles

3D LUTs (`.cube`) that correct sea / underwater video.

**Input:** scene-referred **log**, full range 0–1.
**Output:** **Rec.709** primaries with a BT.1886 / gamma 2.4 display encode.

## The three profiles

| File | Strength | Built for |
| --- | --- | --- |
| `luts/Underwater_Sea_01_Light.cube` | Light | Shallow, clear water, roughly 3–8 m. Gentle red recovery, minimal contrast work. |
| `luts/Underwater_Sea_02_Medium.cube` | Medium | Typical reef depth, roughly 8–18 m. Balanced everyday grade. |
| `luts/Underwater_Sea_03_Strong.cube` | Strong | Deep, green or turbid water, roughly 18–30 m. Aggressive rescue, +0.07 stop lift. |

All three are 33×33×33 cubes with `DOMAIN_MIN 0 0 0` / `DOMAIN_MAX 1 1 1`, so
they load in Resolve, Premiere (Lumetri), Final Cut, After Effects, FFmpeg
(`lut3d`), OBS and anything else that reads Adobe Cube.

Pick by how blue-green the shot is, not by the depth number — a murky 10 m
dive in temperate water often needs the Strong profile, while a bright 20 m
tropical shot may only want Medium.

## Using them

Apply the LUT **first**, to the untouched log clip, then grade on top of it.
Set your clip's input colour space to log and drop the LUT in the first node /
effect slot.

FFmpeg:

```sh
ffmpeg -i dive.mov -vf lut3d=luts/Underwater_Sea_02_Medium.cube -c:v prores_ks out.mov
```

Two things worth doing before the LUT rather than after:

- **Exposure.** These profiles assume mid grey sits where the log curve puts
  it. If the clip is under- or over-exposed, correct that first.
- **Noise.** Deep water leaves almost no red signal, and the Strong profile
  multiplies red by about 3×. Whatever red noise is in the clip gets that
  multiplication too. Denoise before the LUT if the shot is grainy.

The LUT eases the red gain off in deep shadow specifically to keep that
multiplication away from the noise floor, but it cannot invent red that the
sensor never recorded.

## What each profile does

Per pixel, in order:

1. **Log decode** to scene-linear, then a matrix from the camera's primaries to
   Rec.709 linear.
2. **Backscatter removal** — subtract the veiling-glare pedestal that the water
   column adds (mostly to blue and green) and rescale to white. The subtraction
   uses a parabolic toe below twice the pedestal so black stays exactly black
   without a corner in the shadows.
3. **Depth white balance** — per-channel gains that neutralise the cast the
   profile is built for, holding the luminance of a mid grey. The gains fade
   toward unity in deep shadow, on a perceptual ramp, so red noise is not
   amplified along with red signal.
4. **Display rendering** — a curve that is straight in log exposure through
   the midtones at 0.110 code per stop, with a power-law toe and a cubic
   shoulder that lands on white with zero slope. See *Tonal range* below.
5. **Contrast** — a gentle endpoint-preserving S-curve pivoted on mid grey,
   putting back some of the contrast the scattering veil took out.
6. **Hue-targeted saturation** — reds, oranges and yellows lifted; leftover
   cyan pulled toward blue and eased down so the water reads as water instead
   of neon turquoise. Both fade out in the shadows and as a colour approaches
   full saturation, where pushing further only breaks gradients.
7. **Near-white neutralising** — the white balance holds blue back by about
   half a stop, so a clipped white would otherwise land warm. Colours whose
   *dimmest* channel is already near the top are pulled the rest of the way to
   white; a saturated highlight with one clipped channel is left alone.

Every stage is monotonic and smooth, so gradients through the LUT stay clean.

## Tonal range

The display rendering is deliberately relaxed. It holds a constant 0.110 code
values per stop from about a stop and a half under mid grey up to +3.5, so
midtones map evenly instead of being thrown at the ends of the range, and only
then rolls off — a power-law toe below, and a shoulder that decelerates all the
way to white at +6.4 stops.

For reference, a plain gamma 2.4 encode gives 0.144 code per stop at mid grey,
so these profiles are noticeably gentler than that, with far more room at both
ends. Mid grey lands on code 0.420.

| stops from grey | −6 | −4 | −2 | 0 | +2 | +3 | +4 | +5 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| output code | 0.035 | 0.082 | 0.197 | 0.420 | 0.652 | 0.764 | 0.870 | 0.952 |

Highlights keep 0.112 and 0.106 code per stop of separation at +3 and +4, so a
bright sand patch or a sunlit surface stays readable rather than fusing into
white, and shadows at −4 and −6 stops sit at 0.082 and 0.035 rather than
collapsing into black.

If you want a different amount of punch, the four constants that shape the
curve — `MIDTONE_SLOPE`, `TOE_POWER`, `SHOULDER_STOPS`, `WHITE_STOPS` — are at
the top of `tools/generate_luts.py`, and each profile carries its own
`contrast` value. Raising `MIDTONE_SLOPE` toward 0.14 gives a punchier, more
contrasted render; lowering it toward 0.09 gives a flatter one with even more
latitude to grade into.

The shoulder only rolls off smoothly while its entry slope stays at least 1.5×
its average slope, which lowering `MIDTONE_SLOPE` tightens. The generator
checks this on import and, if you cross the line, tells you the minimum
`WHITE_STOPS` to use rather than quietly producing a curve that speeds up
before it brakes.

## Which log?

The shipped files decode a **generic Cineon-style log** (black at code 0.075,
0.18 grey at 0.420, 100% white at 0.660). That is a reasonable middle ground
for consumer and prosumer flat/log profiles, and it is what you want if you
are not sure what your camera records.

If you know your camera's log, **use the matched set instead** — they are
built and shipped under `luts/<log>/`, same three strengths in each. The
correction is identical; only the decode and the gamut matrix change:

| `--log` | Folder | Decode | Primaries |
| --- | --- | --- | --- |
| `generic` (default) | `luts/` | Cineon-style log | Rec.709 |
| `slog3` | `luts/slog3/` | Sony S-Log3 | S-Gamut3.Cine |
| `logc3` | `luts/logc3/` | ARRI LogC3, EI 800 | ARRI Wide Gamut 3 |
| `dlog` | `luts/dlog/` | DJI D-Log | D-Gamut |
| `protune` | `luts/protune/` | GoPro Protune Flat | Rec.709 |

This matters more than it looks. At code value 1.0 the generic curve assumes
+5.84 stops over mid grey, but S-Log3 holds +7.74 there, LogC3 +8.26, D-Log
+7.87 — and GoPro Protune only +2.47. Feeding Protune footage to the generic
LUT renders it several stops too bright and slams the top end into white.

To rebuild any of them:

```sh
python3 tools/generate_luts.py --log slog3 --outdir luts/slog3
```

Gamut matrices are derived from published primaries at run time, not
hard-coded. `--size` changes the cube resolution (33 by default).

## Checking the result

`tools/generate_luts.py` self-checks as it writes: a grey card seen through
each profile's water column must come back neutral, the recorded grey ramp must
stay monotonic, and black must map to black.

`tools/verify_cube.py` closes the loop on the written files. It parses each
`.cube`, applies it with trilinear interpolation the way a grading application
would, and runs a ColorChecker that has been simulated through the water column
against the same chart rendered with no water in the way:

```
$ python3 tools/verify_cube.py
Light   mean |error|  2.2/255  max 12.0/255   (uncorrected: mean 10.9, max 22.7)
Medium  mean |error|  4.2/255  max 21.2/255   (uncorrected: mean 22.0, max 49.8)
Strong  mean |error|  6.3/255  max 36.2/255   (uncorrected: mean 36.6, max 89.8)
```

Grey patches come back within 2/255 of neutral on all three. The residual sits
on saturated reds, which is where deep water destroys the most information and
where the warm saturation lift is deliberately generous. It also writes
`docs/colorchecker.svg`, a contact sheet of recorded / corrected / reference.

## If the LUT does not look like it should

`docs/test_wedge_log.png` is a neutral log ramp and step wedge.
`docs/test_wedge_light.png`, `_medium.png` and `_strong.png` are exactly what
each LUT turns it into, produced by pushing the wedge through the shipped
`.cube` file itself.

Drop the wedge on a timeline, apply the LUT, and compare against the matching
expected image. They should be indistinguishable. If they are not, the LUT is
not reaching your footage the way you think it is — a cached copy of an older
version, the wrong slot in the pipeline, or a colour management step sitting
in between. Two notes: sample the wedge, not the expected image, and be aware
that some applications colour-manage an incoming PNG, which shifts the input
before the LUT ever sees it.

The same thing as numbers, for checking with a colour picker. Neutral 8-bit in,
Medium profile out:

| in | 0 | 34 | 68 | 102 | 136 | 170 | 204 | 238 | 255 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| R | 0 | 26 | 81 | 128 | 170 | 208 | 239 | 254 | 255 |
| G | 0 | 7 | 37 | 85 | 129 | 170 | 209 | 246 | 254 |
| B | 0 | 3 | 20 | 69 | 116 | 158 | 198 | 242 | 254 |

Regenerate all of it with `python3 tools/make_test_wedge.py`.

## Limitations

- A 3D LUT is a per-pixel function. It cannot do anything spatial, so
  backscatter *particles*, haze that varies with subject distance, and
  dehazing proper are out of reach — a LUT can only lift the global veil.
- The cast is corrected for one depth. In a shot that swims from 5 m to 25 m,
  no single profile is right the whole way through.
- Artificial light changes everything. If you are lighting with strobes or
  video lights, the near field is already red-balanced; the Light profile, or
  none at all, will usually beat the Strong one.

## Requirements

Python 3.9+ and NumPy, for regenerating only. The `.cube` files themselves have
no dependencies.
