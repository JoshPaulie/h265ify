# Hardware

h265ify detects the best available h265 encoder at startup by scanning `ffmpeg -encoders`.

## Priority

| Priority | Encoder              | Hardware               |
| -------- | -------------------- | ---------------------- |
| 1        | `hevc_videotoolbox`  | Apple Silicon / macOS  |
| 2        | `hevc_nvenc`         | NVIDIA GPU             |
| 3        | `hevc_qsv`           | Intel QuickSync        |
| 4        | `hevc_amf`           | AMD GPU                |
| fallback | `libx265`            | CPU (software)         |

Force software encoding with `--cpu`.

## Preset mapping

`--preset` uses standard x265 names and maps to each encoder's native presets:

| x265 preset   | libx265   | NVENC | QSV      | AMF      |
| ------------- | --------- | ----- | -------- | -------- |
| ultrafast     | ultrafast | p1    | veryfast | speed    |
| superfast     | superfast | p1    | veryfast | speed    |
| veryfast      | veryfast  | p2    | faster   | balanced |
| faster        | faster    | p3    | fast     | balanced |
| fast          | fast      | p4    | medium   | balanced |
| **medium**    | medium    | p4    | slow     | quality  |
| slow          | slow      | p6    | veryslow | quality  |
| slower        | slower    | p7    | veryslow | quality  |
| veryslow      | veryslow  | p7    | veryslow | quality  |

VideoToolbox ignores `--preset` — always runs at max quality.

## CRF mapping

`--crf` uses the standard 0–51 scale. It's mapped to each encoder's native quality parameter:

| Encoder        | Parameter       | Mapping                        |
| -------------- | --------------- | ------------------------------ |
| libx265        | `-crf`          | passed directly (same scale)   |
| VideoToolbox   | `-q:v`          | `85 - (crf / 51) × 65`         |
| NVENC          | `-cq`           | passed directly (same scale)   |
| QSV            | `-global_quality` | passed directly (same scale) |
| AMF            | `-qp_p` / `-qp_i` | CQP mode with crf / crf-2   |

**Note:** CRF is *not* equivalent across encoders. CRF 23 on libx265 ≠ CRF 23 on NVENC. Hardware encoders are tuned for speed, so expect slightly larger files at the same CRF. Use `--cpu` for the best size/quality ratio.
