# h265ify (`hy`)

```
 █████       ████████   ████████  ██████████  ███     ██████            
░░███       ███░░░░███ ███░░░░███░███░░░░░░█ ░░░     ███░░███           
 ░███████  ░░░    ░███░███   ░░░ ░███     ░  ████   ░███ ░░░  █████ ████
 ░███░░███    ███████ ░█████████ ░█████████ ░░███  ███████   ░░███ ░███ 
 ░███ ░███   ███░░░░  ░███░░░░███░░░░░░░░███ ░███ ░░░███░     ░███ ░███ 
 ░███ ░███  ███      █░███   ░███ ███   ░███ ░███   ░███      ░███ ░███ 
 ████ █████░██████████░░████████ ░░████████  █████  █████     ░░███████ 
░░░░ ░░░░░ ░░░░░░░░░░  ░░░░░░░░   ░░░░░░░░  ░░░░░  ░░░░░       ░░░░░███ 
                                                               ███ ░███ 
                                  yet another ffmpeg wrapper   ░░██████  
                                                               ░░░░░░
```

"Zero-fuss" h265/HEVC video re-encoder, powered by ffmpeg <3

> [!warning]
> Written by DSv4. Personal use only :) No support, no responsibility.

## Usage

> [!Note]
> `hy` is an alias for `h265ify`. Both commands work interchangeably.

```bash
hy video.mkv              # → video_h265.mkv (suffixed alongside original)
hy ~/Videos/              # walk directory recursively, re-encode all videos

hy --yolo video.mp4         # re-encode and replace original immediately
hy --replace ~/Videos/      # replace originals with existing _h265 copies (no encoding)
hy --dry-run ~/Movies/      # preview what would happen
hy --crf 20 video.mkv       # higher quality (lower = better, default: 23)
hy --resize 720p video.mkv  # shrink to 720p, preserving aspect ratio

hy --preset fast video.mkv    # faster encoding, slightly larger file
hy --cpu video.mkv            # force CPU encoding for better compression
hy --vmaf video.mkv            # evaluate and recommend optimal CRF using VMAF (no encode)
hy --vmaf 93 video.mkv         # evaluate with custom VMAF target
```

Already-h265 files are skipped automatically.

Output is mp4 by default. Files already in mp4, mkv, or mov keep their original container; everything else (webm, avi, etc.) gets mp4. Use `--format mp4|mkv|mov` to override.

## Flags

### Positional

| Flag     | Short        | Description |
| -------- | ------------ | ----------- |
| `paths`  | (positional) | Video files or directories. Directories are walked recursively. |

### Encoding

| Flag               | Short | Description |
| ------------------ | ----- | ----------- |
| `--crf`            |       | Quality, 0–51. Lower = better. Default 23. Mapped to native scale for hardware encoders. Mutually exclusive with `--vmaf`. |
| `--vmaf`           |       | Evaluate and recommend optimal CRF using VMAF perceptual quality metric (no encoding). Probes short samples at several CRF values to find the one that hits the target VMAF. Mutually exclusive with `--crf`. |
| `--preset`         |       | Speed/efficiency: `ultrafast` … `veryslow`. Default `medium`. Faster = bigger file, slower = smaller. Mapped to hardware equivalents. |
| `--cpu`            |       | Force software encoding (libx265). Slower but better compression than hardware. |
| `--resize`         | `-r`  | Shrink output: `720p`, `1080p`, `4k`, or exact `1280x720`. Maintains aspect ratio. |
| `--no-upscale`     |       | With `--resize`: skip files already ≤ target resolution. |
| `--format`         |       | Force output container: `mp4`, `mkv`, or `mov`. Default: preserve mp4/mkv/mov, convert everything else to mp4. |
| `--reencode-audio` |       | Re-encode audio (AAC for MP4/MOV, Opus for MKV) instead of stream-copy. |

> [!NOTE]
> Lowering `--crf` too far can actually *increase* file size.
> If the output ends up larger than the input, `h265ify` skips it automatically.
> See [docs/flags.md](docs/flags.md#crf) for details.

### Output / safety

| Flag                 | Short | Description |
| -------------------- | ----- | ----------- |
| `--yolo`             | `-y`  | Encode and replace the original immediately. Temp file used — original untouched until encode succeeds. |
| `--replace`          |       | No encoding. Find existing `_h265` files and swap them in place of originals. |
| `--dry-run`          | `--noop` | Preview what would happen. No encoding, no replacing. |
| `--permanent`        | `-P` / `--perm` | Permanently delete replaced originals. Default: move to system trash. |
| `--halt-on-increase` | `-H`  | Stop the entire batch if any output is larger than the original. |

### Meta

| Flag        | Short | Description |
| ----------- | ----- | ----------- |
| `--version` |       | Print version and exit. |
| `--report`  |       | Write a diagnostic report with recent logs to a file, for debugging crashes. |

## Preset mapping

`--preset` uses standard x265 preset names and maps them to each hardware encoder automatically:

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

VideoToolbox does not have a speed preset - it always uses max-quality mode.

## VMAF evaluation (`--vmaf`)

Instead of guessing a CRF value, use `--vmaf` to let the tool measure actual
perceptual quality and recommend the optimal CRF for each file — no encoding
required.

### How it works

1. **Sample** — extracts 60 seconds from the 25% mark of the video (stream
   copy, no quality loss). This avoids studio logos, title sequences, and
   end credits that would skew the quality assessment.
2. **Probe encode** — encodes the sample at CRF 18, 23, 28, 33 using the
   same encoder and preset as a real encode would use.
3. **Measure VMAF** — runs ffmpeg's libvmaf filter to score each probe
   encode against the original.
4. **Fit and select** — fits a linear regression to the (CRF, VMAF) pairs,
   then predicts the CRF that achieves the target VMAF.

All files probe in parallel. Results are printed atomically per-file:

```
$ hy --vmaf 95 ~/Videos/
  [1/3] video1.mkv
  probing CRF with VMAF (target: 95.0)...
    testing CRF 18... VMAF 96.8  (2s)
    testing CRF 23... VMAF 96.1  (2s)
    testing CRF 28... VMAF 94.9  (2s)
    testing CRF 33... VMAF 93.1  (2s)
    CRF 18: VMAF 96.8  CRF 23: VMAF 96.1  CRF 28: VMAF 94.9  CRF 33: VMAF 93.1
  selected CRF 26 (target VMAF 95.0)

  [2/3] video2.mkv
  ...

VMAF evaluation complete.
  CRF range: 24 – 30

  video1.mkv → CRF 26
  video2.mkv → CRF 24
  video3.mkv → CRF 30

  Use --crf <N> to encode with your chosen value.
```

No encoding is performed. Use the recommended CRF with `--crf`:

```bash
hy --crf 26 ~/Videos/
```

### Requirements

`--vmaf` requires ffmpeg compiled with `--enable-libvmaf`. Check with:
```bash
ffmpeg -filters | grep libvmaf
```

### Target VMAF guide

| Target | Quality | Typical CRF | Use case |
| ------ | ------- | ----------- | -------- |
| 97+    | Excellent | 14–18     | Archival, may increase file size |
| 95     | Near-transparent | 20–28 | **Default.** Visually lossless for most content |
| 90–93  | Good | 28–35        | Mobile/portable, noticeable on large screens |
| < 90   | Fair | 35+           | Minimal size, visible artifacts |

> Actual CRF values vary by content. Animation and screen recordings achieve
> higher VMAF at the same CRF compared to live-action.

## Hardware detection

On startup, `h265ify` queries `ffmpeg -encoders` and picks the best available h265 encoder:

| Priority | Encoder             | Platform              |
| -------- | ------------------- | --------------------- |
| 1        | `hevc_videotoolbox` | Apple Silicon / macOS |
| 2        | `hevc_nvenc`        | NVIDIA GPUs           |
| 3        | `hevc_qsv`          | Intel QuickSync       |
| 4        | `hevc_amf`          | AMD GPUs              |
| fallback | `libx265`           | CPU (software)        |

The `--crf` value is mapped to each encoder's native quality parameter automatically.

## Install

>[!Note]
> This tool will remain unpublished for the foreseeable future, as this is for personal use for myself and friends, but you can install it directly from this repo using `uv` (at your own risk).

Requires Python 3.13+ and `ffmpeg` + `ffprobe` on your PATH.

```bash
uv tool install git+https://github.com/JoshPaulie/h265ify.git
```

### Upgrade

```bash
uv tool upgrade h265ify
```
