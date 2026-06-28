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
