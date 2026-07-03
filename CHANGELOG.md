# Changelog

All notable changes to h265ify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## Unreleased

## [0.3.0] - 2026-07-03

### Changed

- **Probing is now multi-threaded.** ffprobe runs on a thread pool (`os.cpu_count()` workers by default), giving a near-linear speedup when probing large batches (hundreds or thousands of files).
- Set `H265IFY_PROBE_THREADS=N` to override the thread count. This is intentionally an env var (not a CLI flag) to avoid confusion with encoding parallelism — encoding remains strictly sequential.

## [0.2.2] - 2026-07-03

### Added

- `--noop` as an alternative flag for `--dry-run`.
- Space savings shown after `--replace` (both real and dry-run).

## [0.2.1] - 2026-06-28

### Removed

- `--tune` flag removed. x265 tune options like `animation` and `stillimage` were never valid for the h265 encoder (they are x264-only); the actual x265 tune set (`psnr`, `ssim`, `grain`, `fastdecode`, `zerolatency`) adds complexity without clear benefit.

## [0.2.0] - 2026-06-28

### Added

- Skip-on-increase: files that come out larger than the original during encoding are automatically deleted and the original is preserved.
- Early abort: encoding is killed mid-stream when the output file size exceeds the input, saving CPU/GPU cycles.
- `--halt-on-increase` / `-H` flag to stop the entire batch when a file comes out larger than its original.

## [0.1.2] - 2026-06-28

### Changed

- Per-file results now show both input and output sizes (`in_size -> out_size`) instead of only the output size.

## [0.1.1] - 2026-06-21

### Fixed

- Hardware encoders are now validated at runtime with a smoke test (2×2 px null encode), not just at detection time. Prevents silent failure to libx265 when `ffmpeg -encoders` lists an encoder whose runtime libraries are missing.

### Added

- Tests for hardware encoder validation fallback chain.

### Changed

- Simplified install instructions: `uv tool install` / `uv tool upgrade`.
- Toned down README prose.

## [0.1.0] - 2026-06-21

### Added

- CLI tool `h265ify` (alias `hy`): zero-fuss h265/HEVC video re-encoding via ffmpeg.
- Hardware-accelerated encoding for VideoToolbox (macOS), NVENC (NVIDIA), QSV (Intel), and AMF (AMD), with automatic libx265 CPU fallback.
- Encode mode (default): probe video files, re-encode to h265, produce `_h265`-suffixed output.
- Replace mode (`--replace`): batch-replace originals with existing `_h265` files (no encoding).
- In-place replace during encode (`--yolo` / `-y`) using temp files with SIGINT cleanup.
- Dry-run (`--dry-run`) to preview what would be encoded or replaced.
- Quality control (`--crf` 0–51, default 23) mapped appropriately to each hardware encoder.
- Speed/efficiency preset (`--preset`, ultrafast through veryslow) mapped to each hardware encoder's native presets.
- Content tuning (`--tune`) for libx265: animation, grain, stillimage, fastdecode, zerolatency.
- Resize with shorthand presets (`--resize` / `-r`: `720p`, `1080p`, `4k`) or explicit `WxH`.
- No-upscale flag (`--no-upscale`) to skip resizing when input is already within target dimensions.
- Force output container (`--format mp4|mkv|mov`) to override automatic container selection.
- Force CPU encoding (`--cpu`) to bypass hardware acceleration.
- Re-encode audio (`--reencode-audio`) instead of default stream-copy; AAC for MP4, Opus for MKV.
- Subtitle preservation: mov_text for MP4 output, stream-copy for MKV.
- Permanent deletion (`--permanent`) instead of sending replaced files to system trash.
- Version flag (`--version`).
- Rich-based colorful console output with per-file results and final summary.
- Sequential encoding (one file at a time) to avoid splitting hardware encoder throughput.

[0.2.2]: https://github.com/JoshPaulie/h265ify/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/JoshPaulie/h265ify/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/JoshPaulie/h265ify/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/JoshPaulie/h265ify/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/JoshPaulie/h265ify/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/JoshPaulie/h265ify/releases/tag/v0.1.0
