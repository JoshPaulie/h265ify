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

## Quality tuning

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

For animation:
```bash
hy --tune animation anime.mkv   # optimizes for flat areas, grain retention
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
