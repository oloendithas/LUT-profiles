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
4. **Display rendering** — a filmic tone map (ACES fit, exposed so 0.18 scene
   grey lands at code 0.419), then the gamma 2.4 encode.
5. **Contrast** — an endpoint-preserving S-curve pivoted on mid grey, putting
   back the contrast the scattering veil took out.
6. **Hue-targeted saturation** — reds, oranges and yellows lifted; leftover
   cyan pulled toward blue and eased down so the water reads as water instead
   of neon turquoise. Both fade out in the shadows and as a colour approaches
   full saturation, where pushing further only breaks gradients.

Every stage is monotonic and smooth, so gradients through the LUT stay clean.

## Which log?

The shipped files decode a **generic Cineon-style log** (black at code 0.075,
0.18 grey at 0.420, 100% white at 0.660). That is a reasonable middle ground
for consumer and prosumer flat/log profiles, and it is what you want if you
are not sure what your camera records.

If you know your camera's log, rebuild for it — the correction is identical,
only the decode and the gamut matrix change:

```sh
python3 tools/generate_luts.py --log slog3    --outdir luts/slog3
python3 tools/generate_luts.py --log logc3    --outdir luts/logc3
python3 tools/generate_luts.py --log dlog     --outdir luts/dlog
python3 tools/generate_luts.py --log protune  --outdir luts/protune
```

| `--log` | Decode | Primaries |
| --- | --- | --- |
| `generic` (default) | Cineon-style log | Rec.709 |
| `slog3` | Sony S-Log3 | S-Gamut3.Cine |
| `logc3` | ARRI LogC3, EI 800 | ARRI Wide Gamut 3 |
| `dlog` | DJI D-Log | D-Gamut |
| `protune` | GoPro Protune Flat | Rec.709 |

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

Grey patches come back within 3/255 of neutral on all three. The residual sits
on saturated reds, which is where deep water destroys the most information and
where the warm saturation lift is deliberately generous. It also writes
`docs/colorchecker.svg`, a contact sheet of recorded / corrected / reference.

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
