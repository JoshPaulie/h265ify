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
hy --tune animation anime.mkv # optimize for animation (libx265 only)
hy --cpu video.mkv            # force CPU encoding for better compression
```

Already-h265 files are skipped automatically.

Output is mp4 by default. Files already in mp4, mkv, or mov keep their original container; everything else (webm, avi, etc.) gets mp4. Use `--format mp4|mkv|mov` to override.

## Flags

| Flag               | Short        | Description                                                             |
| ------------------ | ------------ | ----------------------------------------------------------------------- |
| `paths`            | (positional) | Video files or directories to process                                   |
| `--crf`            |              | Quality: 0-51, default 23 (lower = better)                              |
| `--preset`         |              | Encoding speed/efficiency: `ultrafast` … `veryslow` (default `medium`)  |
| `--tune`           |              | Tuning profile: `animation`, `grain`, `stillimage`, etc. (libx265 only) |
| `--cpu`            |              | Force CPU encoding (libx265); slower but better compression             |
| `--resize`         | `-r`         | Resize output: `720p`, `1080p`, `4k`, or `1280x720`                     |
| `--no-upscale`     |              | Don't upscale if input is already ≤ target dimensions                   |
| `--format`         |              | Force output container: `mp4` or `mkv`                                  |
| `--reencode-audio` |              | Re-encode audio (AAC/Opus) instead of stream-copy                       |
| `--yolo`           | `-y`         | Replace original immediately after encoding                             |
| `--permanent`      |              | Permanently delete replaced originals instead of sending to trash       |
| `--replace`        |              | Replace originals with existing `_h265` copies (no encoding)            |
| `--dry-run`        |              | Preview without encoding or replacing                                   |
| `--version`        |              | Print version and exit                                                  |

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
> This tool will remain unpublished for the foreseeable future, as this is for personal use for myself and friends, but you can install it directly from this repo using `uv`.

Requires Python 3.13+ and `ffmpeg` + `ffprobe` on your PATH.

```bash
git clone https://github.com/JoshPaulie/h265ify.git
cd h265ify
uv tool install .
```

### Upgrade

```bash
uv tool update h265ify
```
