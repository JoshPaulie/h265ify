# Changelog

All notable changes to h265ify will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- **`--vmaf-clips` flag** to control the number of sample clips for VMAF
  evaluation (default: 3).
- **`--vmaf-clip-duration` flag** to control the duration of each VMAF sample
  clip in seconds (default: 8).

### Changed

- VMAF evaluation rewritten with multi-clip scene detection (via ffmpeg's
  `scdet` filter) instead of a single segment. Extracts N short clips from
  different scenes and uses the minimum VMAF across clips, ensuring the
  hardest sampled scene drives the CRF recommendation.
- VMAF probing changed from parallel to sequential evaluation, consistent
  with the project's hardware encoder resource philosophy.
- Removed `H265IFY_VMAF_WORKERS` env var (no longer needed with sequential
  probing).
- `--vmaf` no longer supports the `H265IFY_VMAF_WORKERS` env var or the
  legacy `kill_all_vmaf_procs()` SIGINT handler — the simplified threading
  model uses `subprocess.run()` instead of `Popen`.

## [0.7.0] - 2026-07-15

### Added

- **`--sample` flag** for use with `--vmaf`. Accepts an integer (randomly sample N
  files) or a percentage string like `25%` (randomly sample N% of eligible files).
  Sampling happens after h265 filtering, so only non-h265 files are candidates.
- **Trimmed display names on narrow terminals**. When a batch of files shares a
  long common prefix (e.g., TV show episodes), the progress bar, completion
  lines, replace-mode output, and VMAF evaluation all show a shortened name
  with the shared prefix removed — much easier to read on smartphones.
- **Session tag** (`[aB3xYz]`). Each invocation generates a unique 6-character
  alphanumeric tag that appears in every application log line, the ffmpeg log,
  and the error log — making it trivial to grep for a specific run.

### Changed

- VMAF evaluation segment shortened from 60s to 30s, halving CRF probe times.

## [0.6.0] - 2026-07-11

### Added

- **`--vmaf` flag.** Measures perceptual quality (VMAF) at CRF values 18, 23, 28,
  33 on a 60-second sample, fits a linear regression, and recommends the optimal
  CRF for each file — no encoding performed. Probes run in parallel across all
  files.
- `--vmaf` accepts an optional target score (default 95, range 0–100).
- `vmaf.py` module with `determine_crf()`, `_fit_crf()` linear regression,
  `vmaf_available()` libvmaf detection, and `kill_all_vmaf_procs()` for clean
  SIGINT handling.
- `pix_fmt_for_encoder()` helper in `hardware.py` shared between encode and
  VMAF probe paths, eliminating duplicated pix_fmt logic.
- `H265IFY_VMAF_WORKERS` env var to cap VMAF probe parallelism (default:
  `min(cpu_count, 4)`).
- `H265IFY_PROBE_THREADS` env var to override ffprobe thread count.
- Retry loop now uses exponential backoff (1s, 2s) between attempts.
- Per-job CRF override field on `EncodeJob` (`crf: int | None`).

### Changed

- `--crf` default changed from `23` to `None` to distinguish "not passed" from
  "explicitly set to 23". CRF falls back to 23 only in encode mode.
- `--vmaf` is mutually exclusive with `--replace`, `--yolo`, and all encoding
  flags (`--crf`, `--resize`, `--reencode-audio`, `--format`, etc.).
- Probe executor refactored to `try/finally` pattern for cleaner interrupt
  handling.
- Skip messages in encode mode are now a compact summary line when there are
  jobs to encode (e.g. `"  skip 1 file(s) (1 output exists)"`).

### Fixed

- `--report` flag check now correctly detects `--crf` presence using
  `is not None` instead of `!= 23`, which broke when the default changed to
  `None`.

## [0.5.0] - 2026-07-05

### Added

- `--perm` and `-P` as aliases for `--permanent`.
- Failed encodes are now automatically retried up to 2 more times (3 attempts total) before moving on to the next file. Each retry is logged with the attempt count; the final failure denotes how many attempts were exhausted.

### Changed

- Dry-run output is now less verbose: shows filename + size per file, and a single tally line for skipped files instead of listing each one.
- `--report` is now standalone — it errors if combined with other flags or paths.
- `--report` now finds and surfaces the last failed ffmpeg encode session (including the full command and crash stderr) instead of a blind tail of the log, and maps negative return codes to signal names (e.g. `rc=-11` → `"SIGSEGV (segmentation fault)"`).
- `--report` deduplicates consecutive identical lines in both the application and ffmpeg logs, fitting more distinct information into the report window.

### Fixed

- `--permanent` without `--yolo` or `--replace` is now caught with a clear error (it had no effect).
- `--permanent` with `--dry-run` / `--noop` is now caught with a clear error (nothing is changed).

## [0.4.0] - 2026-07-04

### Added

- **Exception logging.** Unhandled exceptions are now written to `h265ify_error.log` in the log directory, so crash details survive even if terminal output scrolls away.
- **`--report` flag.** Writes a timestamped diagnostic report file to the log directory, bundling system info, the last exception(s), and the tail of `h265ify.log` and `h265ify_ffmpeg.log`. Use `hy --report` after a crash to collect everything needed for debugging.

## [0.3.1] - 2026-07-04

### Fixed

- `--replace` no longer crashes with `FileNotFoundError` after successfully replacing files. Space savings are now computed before files are renamed.

## [0.3.0] - 2026-07-03

### Changed

- **Probing is now multi-threaded.** ffprobe runs on a thread pool (`os.cpu_count()` workers by default), giving a near-linear speedup when probing large batches (hundreds or thousands of files).
- Set `H265IFY_PROBE_THREADS=N` to override the thread count. This is intentionally an env var (not a CLI flag) to avoid confusion with encoding parallelism — encoding remains strictly sequential.
- **Ctrl+C during probing is now handled gracefully.** Pending ffprobe calls are cancelled immediately and a clean "probing interrupted" message is shown instead of a raw traceback.

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

[0.7.0]: https://github.com/JoshPaulie/h265ify/compare/v0.6.0...v0.7.0
[0.6.0]: https://github.com/JoshPaulie/h265ify/compare/v0.5.0...v0.6.0
[0.5.0]: https://github.com/JoshPaulie/h265ify/compare/v0.4.0...v0.5.0
[0.4.0]: https://github.com/JoshPaulie/h265ify/compare/v0.3.1...v0.4.0
[0.3.1]: https://github.com/JoshPaulie/h265ify/compare/v0.3.0...v0.3.1
[0.2.2]: https://github.com/JoshPaulie/h265ify/compare/v0.2.1...v0.2.2
[0.2.1]: https://github.com/JoshPaulie/h265ify/compare/v0.2.0...v0.2.1
[0.2.0]: https://github.com/JoshPaulie/h265ify/compare/v0.1.2...v0.2.0
[0.1.2]: https://github.com/JoshPaulie/h265ify/compare/v0.1.1...v0.1.2
[0.1.1]: https://github.com/JoshPaulie/h265ify/compare/v0.1.0...v0.1.1
[0.1.0]: https://github.com/JoshPaulie/h265ify/releases/tag/v0.1.0
