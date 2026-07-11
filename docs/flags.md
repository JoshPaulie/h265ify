# Flags

A walk through every flag, grouped by what they do.

## What to encode

### `paths`

Files or directories to process. Directories are walked recursively, picking up every video file along the way. Files named `*_h265.*` and files already encoded in h265 are skipped automatically, so there's no harm in pointing at a directory you've already run through before.

## Encoding

### `--crf`

Quality control, 0 to 51. Lower numbers mean better quality and bigger files. Default is 23, a sensible starting point for most content. Hardware encoders get an equivalent quality mapping, so the same CRF value works across all encoders (though results won't be pixel-identical).

Lowering CRF too far can backfire. At very low values (roughly below 15), the encoder preserves so much detail — including noise and compression artifacts already in the original — that the output can actually grow *larger* than the input. At CRF 0 the encoder runs in lossless mode, which almost always balloons file size well beyond the original.

h265ify catches this automatically: if an encode produces an output larger than the input, the temp file is deleted and the original is left untouched. The file is reported as skipped in the summary. If you'd rather halt the whole batch when this happens, use [`--halt-on-increase`](#halt-on-increase--h).

Mutually exclusive with [`--vmaf`](#vmaf).

### `--vmaf`

Evaluate video quality at several CRF values and recommend the optimal CRF for each file — without encoding anything.

`--vmaf` is a standalone evaluation mode (mutually exclusive with `--replace`, `--yolo`, and other encoding flags). It runs a quick probe on each file:

1. **Sample** — extracts 60 seconds from the 25% mark of the video (stream copy, no quality loss). Avoids unrepresentative content like studio logos, title cards, and end credits.
2. **Probe** — encodes that sample at CRF values 18, 23, 28, 33 using the same encoder and preset as the main encode would.
3. **Measure** — runs ffmpeg's libvmaf filter (0–100, higher = better) to score each probe encode against the original.
4. **Fit** — fits a curve through the measured points and selects the CRF that hits the target VMAF score.

Probes run in parallel across all files for speed. Results are printed per-file:

```
$ hy --vmaf 95 ~/Videos/
Apple VideoToolbox VMAF target 95.0 preset medium
log: /Users/bex/Library/Logs/h265ify/h265ify.log

found 3 video files

  evaluating 3 file(s) with VMAF (target: 95.0)

  [1/3] show_s01e01.mkv
  probing CRF with VMAF (target: 95.0)...
    testing CRF 18... VMAF 97.5  (6s)
    testing CRF 23... VMAF 97.2  (7s)
    testing CRF 28... VMAF 96.8  (7s)
    testing CRF 33... VMAF 95.5  (7s)
    CRF 18: VMAF 97.5  CRF 23: VMAF 97.2  CRF 28: VMAF 96.8  CRF 33: VMAF 95.5
  selected CRF 38 (target VMAF 95.0)

  [2/3] show_s01e02.mkv
  ...

VMAF evaluation complete.
  CRF range: 36 – 40

  show_s01e01.mkv → CRF 38
  show_s01e02.mkv → CRF 36
  show_s01e03.mkv → CRF 40

  Use --crf <N> to encode with your chosen value.
```

**Default target**: 95 (near-transparent quality). Provide a value to adjust: `--vmaf 93` for smaller files, `--vmaf 97` for higher fidelity.

**Requirements**: ffmpeg must be compiled with `--enable-libvmaf`. Check with `ffmpeg -filters | grep libvmaf`.

Mutually exclusive with `--replace`, `--yolo`, and encoding flags (`--crf`, `--resize`, `--format`, etc.). Respects `--preset` and `--cpu` for probe encode behavior.

### `--preset`

Speed-vs-compression tradeoff. `medium` is the default. Slower presets (`slow`, `veryslow`) squeeze out smaller files; faster ones (`fast`, `ultrafast`) finish quicker but leave files larger. The preset names match libx265's conventions and are translated to each hardware encoder's native equivalents automatically.

### `--tune`

Content-aware optimizations: `animation`, `grain`, `stillimage`, `fastdecode`, `zerolatency`. Only works with software encoding (libx265). Ignored when using a hardware encoder.

### `--cpu`

Bypass hardware acceleration and use libx265 instead. Slower, but you get better compression and `--tune` support. Worth it for archival encodes where file size matters more than speed.

### `--resize` / `-r`

Shrink the output. Accepts friendly presets (`720p`, `1080p`, `4k`) or exact dimensions (`1280x720`). Aspect ratio is always preserved, and dimensions are rounded to even numbers (ffmpeg requires it).

### `--no-upscale`

Paired with `--resize`. If a file is already at or below the target resolution, it's left alone instead of being needlessly enlarged.

### `--format`

Force the output container to `mp4`, `mkv`, or `mov`. By default, mp4/mkv/mov sources keep their container; everything else (webm, avi, etc.) becomes mp4. Use `mkv` if you have multi-track audio or subtitle content you don't want to lose.

### `--reencode-audio`

Re-encode audio tracks instead of stream-copying them. MP4 and MOV get AAC at 192k; MKV gets Opus at 128k. Without this flag, audio is copied as-is, which is almost always what you want.

## Output and safety

### `--yolo` / `-y`

Encode and replace the original file in one shot. A temp file is used during encoding (`video.h265-tmp.mp4`), and the original isn't touched until the encode finishes successfully. If something goes wrong — including Ctrl+C — the temp file is cleaned up and the original stays put.

### `--replace`

A separate, no-encoding mode. After you've encoded a batch of files (producing `*_h265.*` copies) and reviewed them, `--replace` swaps the `_h265` files into place of the originals. Original files go to the system trash by default. Use `--dry-run` with `--replace` to preview the swaps before committing.

### `--dry-run`

Preview mode. Shows what would be encoded or replaced without actually doing anything. Works in both encode and replace modes.

### `--permanent`

Delete replaced originals permanently instead of sending them to the system trash. Applies to both `--yolo` and `--replace`. Combine with `--yolo --permanent` at your own risk — there's no undo.

### `--halt-on-increase` / `-H`

Stop the entire batch if any output file ends up larger than the input. Normally re-encoding shrinks files, but edge cases (already heavily compressed sources, very low CRF values) can produce larger output. This flag catches that early instead of silently bloating your library.

## Meta

### `--version`

Print the version and exit.
