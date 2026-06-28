# Flags

Every flag, one line.

## Positional

`paths` — files or directories. Directories are walked recursively.

## Encoding

`--crf N` — quality, 0–51. Lower = better. Default 23. Mapped to native scale for hardware encoders.

`--preset NAME` — speed/efficiency: `ultrafast` … `veryslow`. Default `medium`. Faster = bigger file, slower = smaller file. Mapped to hardware equivalents.

`--tune NAME` — content type: `animation`, `grain`, `stillimage`, `fastdecode`, `zerolatency`. libx265 only (ignored with hardware).

`--cpu` — force software encoding (libx265). Slower but better compression than hardware.

`--resize SPEC` / `-r` — shrink output: `720p`, `1080p`, `4k`, or exact `1280x720`. Maintains aspect ratio. Dimensions rounded to even numbers.

`--no-upscale` — with `--resize`: skip files already ≤ target resolution. Avoids bloating small files.

`--format mp4|mkv|mov` — force output container. Default: preserve mp4/mkv/mov; convert everything else to mp4.

`--reencode-audio` — re-encode audio (AAC for MP4/MOV, Opus for MKV) instead of stream-copy.

## Output / safety

`--yolo` / `-y` — encode and replace the original immediately. Uses a temp file for safety — original isn't touched until the encode succeeds.

`--replace` — no encoding. Find existing `_h265` files and swap them in place of originals. For batch reviewing after encoding.

`--dry-run` — preview what would happen. No encoding, no replacing. Works with both encode and replace modes.

`--permanent` — permanently delete replaced originals. Default: move to system trash.

`--halt-on-increase` / `-H` — stop the entire batch if any output file is larger than the original.

## Meta

`--version` — print version and exit.
