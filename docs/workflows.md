# Workflows

## Basic encode

```bash
hy video.mkv                # → video_h265.mkv (suffixed, original kept)
hy ~/Videos/                # walk directory recursively
```

Already-h265 files are skipped. Existing `_h265` files are skipped (no double-encoding).

## Review then replace

1. Encode everything:
   ```bash
   hy ~/Videos/
   ```
2. Watch a few `_h265` copies to verify quality.
3. Swap them in:
   ```bash
   hy --replace ~/Videos/
   ```
4. Originals go to trash (or `--permanent` to delete permanently).

Combine with `--dry-run` first to preview:
```bash
hy --dry-run ~/Videos/           # what will be encoded?
hy --replace --dry-run ~/Videos/ # what will be swapped?
```

## In-place (risky)

Replace originals immediately after each encode:
```bash
hy --yolo video.mp4
```

The original is only deleted *after* the encode succeeds. Temp files are cleaned up on failure or Ctrl+C.

## Find the optimal CRF (VMAF evaluation)

Before encoding a batch, find the best CRF for your content:

```bash
hy --vmaf 95 video.mkv              # recommend CRF for target VMAF 95
hy --vmaf 93 ~/Videos/              # evaluate all videos with custom target
hy --vmaf 95 --cpu video.mkv        # evaluate with software encoding
hy --vmaf 95 --preset slow video.mkv # evaluate with slow preset
```

The probe runs in parallel across files and prints live scores:
```
Apple VideoToolbox VMAF target 95.0 preset medium

  evaluating 1 file(s) with VMAF (target: 95.0)

  [1/1] video.mkv
  probing CRF with VMAF (target: 95.0)...
    testing CRF 18... VMAF 96.8  (2s)
    testing CRF 23... VMAF 96.1  (2s)
    testing CRF 28... VMAF 94.9  (2s)
    testing CRF 33... VMAF 93.1  (2s)
    CRF 18: VMAF 96.8  CRF 23: VMAF 96.1  CRF 28: VMAF 94.9  CRF 33: VMAF 93.1
  selected CRF 26 (target VMAF 95.0)

VMAF evaluation complete.

  video.mkv → CRF 26

  Use --crf <N> to encode with your chosen value.
```

Then encode with the recommended value:
```bash
hy --crf 26 ~/Videos/
```

No encoding is performed during evaluation. 

What VMAF target should you use?

| Target | Quality | Typical CRF | Use case |
| ------ | ------- | ----------- | -------- |
| 97+    | Excellent | 14–18     | Archival, may increase file size |
| 95     | Near-transparent | 20–28 | **Default.** Visually lossless for most content |
| 90–93  | Good | 28–35        | Mobile/portable, noticeable on large screens |
| < 90   | Fair | 35+           | Minimal size, visible artifacts |

> Actual CRF values vary by content. The table assumes typical live-action.
> Animation and screen recordings achieve higher VMAF at the same CRF.

## Quality tuning (manual)

Higher quality = slower encode + larger file:
```bash
hy --crf 18 video.mkv           # near-lossless
hy --crf 28 video.mkv           # smaller, some quality loss
```

Faster encode = less compression:
```bash
hy --preset fast video.mkv      # faster, slightly larger
hy --preset slow video.mkv      # slower, smaller
```

## Resize

```bash
hy --resize 720p 4k_video.mp4   # shrink 4K → 720p
hy --resize 1080p ~/Videos/     # batch all to 1080p
hy --resize 720p --no-upscale ~/Videos/  # skip already-≤720p files
```

## Force software encoding

```bash
hy --cpu video.mkv              # libx265, max compression
hy --cpu --preset veryslow video.mkv  # smallest possible file
```

## Full send

```bash
hy --yolo --permanent --resize 1080p --crf 22 ~/Videos/
```

Encodes everything to 1080p, replaces originals, permanently deletes the old files. No undo.
