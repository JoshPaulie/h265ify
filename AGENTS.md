# AGENTS.md - h265ify

## Project

Zero-fuss h265/HEVC video re-encoder wrapper for ffmpeg. CLI tool `h265ify` re-encodes videos with near-identical visual quality at a fraction of the size, using hardware acceleration when available.

## Stack

- **Python 3.13+** (`.python-version`)
- **uv** package manager (`pyproject.toml`, `uv.lock`)
- **Hatchling** build backend
- **mypy** (type checking), **ruff** (format + lint)
- **Runtime deps**: `ffmpeg` + `ffprobe` on PATH

## Structure

```
src/h265ify/
  __init__.py     entry point, argparse CLI, main()
  encoder.py      ffmpeg command builder, subprocess runner, size/duration formatting
  hardware.py     hardware encoder detection (VT/NVENC/QSV/AMF → libx265 fallback)
  pipeline.py     file discovery, job preparation, sequential encoding, summary
  probe.py        ffprobe wrapper, codec/HDR/stream metadata extraction
pyproject.toml
justfile         aliases: check, format, lint, mypy
```

## Conventions

- Every module uses `from __future__ import annotations`
- Dataclasses for all structured data (no dicts); `field(default_factory=...)` for mutable defaults
- `pathlib.Path` exclusively - no `os.path` usage
- Type hints on every function signature; `mypy` strictness is enforced
- External tools (ffmpeg, ffprobe) are called via `subprocess.run` / `subprocess.Popen`, never `os.system`
- Encoding logic is kept out of `__init__.py` - it handles only CLI + orchestration, each concern gets its own module

---

## Design Decisions

### Modes of operation

h265ify has two distinct modes of operation:

1. **Encode mode** (default) - probe files, re-encode to h265, produce output files.
2. **Replace mode** (`--replace`) - find existing `_h265` files and swap them in place of originals. No encoding happens.

These modes are mutually exclusive. Passing both `--replace` and `--yolo` is an error.

### Output naming

- Suffixed copies use `_h265`.
  - `video1.mp4` → `video1_h265.mp4`
  - `video1.mkv` → `video1_h265.mkv`

### Container format

- **Default**: mp4 is the default output container. Most source formats (webm, avi, etc.) produce mp4 output.
  - **Exception**: files already in mp4, mkv, or mov retain their original container.
    - `input.mp4` → `output_h265.mp4`
    - `input.mkv` → `output_h265.mkv` (preserves multi-track audio/subtitle data)
    - `input.mov` → `output_h265.mov` (QuickTime supports h265)
    - `input.webm` → `output_h265.mp4` (webm does not support h265)
  - Rationale: webm only supports VP8/VP9/AV1, and many other containers are incompatible with h265. mp4 is the safest universal fallback, while mkv is preserved to prevent data loss for content with multiple audio/subtitle tracks (anime, foreign films), and mov is preserved because QuickTime natively supports h265.
- `--format mp4|mkv|mov` overrides to force a specific output container.
- When output is MP4 or MOV: apply `-tag:v hvc1` and `-movflags +faststart`.
- When output is MKV: no hvc1 tag, no faststart. Stream-copy all subtitles without conversion.

### `--yolo` / `-y` (in-place replace during encoding)

- Replaces the original file immediately after encoding completes.
- Uses a temp file during encoding (e.g., `video.h265-tmp.mp4`), then `os.replace(tmp, output)` on success.
- If the original had a different extension than the output, the original is moved to trash (or permanently deleted with `--permanent`).
- Temp files are cleaned up on failure or interrupt (SIGINT handler).

### `--replace` (batch post-review replacement)

- A **separate, non-encoding** mode. Accepts files and/or directories.
- Finds all `*_h265.*` files. For each, strips the `_h265` suffix and looks for the original file (any video extension).
- If original found: move original to trash (or permanently delete with `--permanent`), rename `_h265` file to `<stem>.<extension>` (keeping the `_h265` file's extension).
  - `video1_h265.mp4` + `video1.mkv` → trash `video1.mkv`, rename → `video1.mp4`
- If original not found: warn and skip.
- Supports `--dry-run` to preview replacements.

### `--permanent`

- By default, replaced originals are sent to the system trash (via `send2trash`).
- `--permanent` permanently deletes them instead.
- Applies to both `--yolo` and `--replace` modes.

### `--resize` / `-r`

- Accepts shorthand presets or explicit dimensions:
  - `720p` → width 1280, height auto (maintain aspect ratio)
  - `1080p` → width 1920, height auto
  - `4k` → width 3840, height auto
  - `1280x720` → explicit width × height
- Dimensions are rounded to even numbers (ffmpeg requirement).
- `--no-upscale` flag: skip resize if the input is already ≤ target dimensions.
- Uses ffmpeg `scale` filter: `-vf scale=w:h:force_original_aspect_ratio=decrease`.

### `--crf` quality

- User-facing CRF scale: 0–51 (matching libx265).
- Default: 23.
- Hardware encoder mappings:
  - **VideoToolbox**: linear mapping `q:v = 85 - (crf / 51) × 65` (0–100 scale, higher = better quality), `-realtime 0`, `-allow_sw 1`.
  - **NVENC**: `-cq <crf>` (same 1–51 scale as x265 CRF).
  - **QSV**: `-global_quality <crf>` (same 1–51 scale), `-look_ahead 1` for better rate control.
  - **AMF**: CQP mode with `-qp_p <crf> -qp_i <max(0, crf-2)>`, `-quality quality`.

### `--preset` speed/efficiency

- Accepts standard x265 preset names (`ultrafast` through `veryslow`, default `medium`).
- Mapped to each hardware encoder's native presets:

| x265 preset          | libx265   | NVENC | QSV      | AMF      |
| -------------------- | --------- | ----- | -------- | -------- |
| ultrafast            | ultrafast | p1    | veryfast | speed    |
| superfast            | superfast | p1    | veryfast | speed    |
| veryfast             | veryfast  | p2    | faster   | balanced |
| faster               | faster    | p3    | fast     | balanced |
| fast                 | fast      | p4    | medium   | balanced |
| **medium** (default) | medium    | p4    | slow     | quality  |
| slow                 | slow      | p6    | veryslow | quality  |
| slower               | slower    | p7    | veryslow | quality  |
| veryslow             | veryslow  | p7    | veryslow | quality  |

- VideoToolbox ignores `--preset` (always max quality).

### `--tune` content tuning

- Accepts x265 tune values: `animation`, `grain`, `stillimage`, `fastdecode`, `zerolatency`.
- Only applied for libx265. A warning is printed if used with a hardware encoder.

### Audio handling

- **Default**: stream-copy all audio tracks (`-c:a copy`).
- `--reencode-audio` flag re-encodes audio:
  - MP4 output → AAC @ 192k
  - MKV output → Opus @ 128k

### Subtitle handling

- **Default**: preserve all subtitles.
  - MP4 output: convert text-based subs to `mov_text`, warn about dropped bitmap subs.
  - MKV output: stream-copy all subs as-is (no conversion, no loss).
- Dropped bitmap subtitles in MP4 mode produce warnings per file.

### File discovery

- Skip files that already have `_h265` in their stem (prevents double-encoding of already-converted files).
- Canonical video extensions: `.mp4`, `.mkv`, `.mov`, `.avi`, `.webm`, `.wmv`, `.flv`, `.m4v`, `.mts`, `.m2ts`, `.ts`.

### Error handling

- **Stop on first failure**: if any encode fails, halt immediately. Clean up temp files.
- SIGINT handler cleans temp files on Ctrl+C.

### Encoding strategy

- All encodes run sequentially. Hardware encoder ASICs are a fixed resource — parallel encodes split throughput with no net time savings, and produce noisy interleaved output.

### Dry-run

- `--dry-run` shows what would happen without encoding or replacing.
- Does not estimate file size savings.
- Works for both encode mode and `--replace` mode.

### Summary output

- Final summary shows: files encoded, skipped, failed, total time, aggregate size reduction.
- Per-file results printed inline as each job completes.

### CLI flags reference

| Flag               | Short        | Description                                                             |
| ------------------ | ------------ | ----------------------------------------------------------------------- |
| `paths`            | (positional) | Video files or directories to process                                   |
| `--crf`            |              | Quality: 0–51, default 23                                               |
| `--resize`         | `-r`         | Resize output: `720p`, `1080p`, `4k`, `WxH`                             |
| `--no-upscale`     |              | Don't upscale if input ≤ target                                         |
| `--yolo`           | `-y`         | Replace original during encode (risky)                                  |
| `--permanent`      |              | Permanently delete replaced originals instead of sending to trash       |
| `--replace`        |              | Batch-replace originals with existing `_h265` files (no encoding)       |
| `--format`         |              | Force output container: `mp4` or `mkv`                                  |
| `--reencode-audio` |              | Re-encode audio instead of stream-copy                                  |
| `--preset`         |              | Encoding speed/efficiency: `ultrafast` … `veryslow` (default `medium`)  |
| `--tune`           |              | Tuning profile: `animation`, `grain`, `stillimage`, etc. (libx265 only) |
| `--cpu`            |              | Force CPU encoding (libx265) instead of hardware acceleration            |
| `--dry-run`        |              | Preview without encoding/replacing                                      |
| `--version`        |              | Print version and exit                                                  |

### Deferred to v2

- `--output-dir` / `-o`: redirect encoded files to a different directory
- Config file (`~/.config/h265ify/config.toml`)

### Verbosity

- Single verbosity level (current behavior). No `--quiet` or `--verbose` flags.

### Testing

- Unit tests for: probe JSON parsing, encoder detection mock, CRF mapping functions, output path generation, resize dimension calculation, `_h265` file matching for `--replace`.
- Integration smoke tests: require ffmpeg on PATH, skip gracefully if absent.

### Version

- `--version` reads from `importlib.metadata`.
