# Product

`de-dolby` is a Python 3.10+ command-line application that converts Dolby
Vision MKV video to HDR10.

Its important user promises are:

- Profile 7/8 HEVC uses a lossless RPU-strip and remux path.
- Profile 5 uses a colour-converting re-encode path.
- Profile 10 AV1 uses a re-encode path because `dovi_tool` cannot process AV1.
- Hardware encoders are selected when available, with CPU fallbacks.
- Audio, subtitle, attachment, and chapter handling must remain explicit and
  testable.
- Existing outputs are not overwritten unless the user opts in.

The CLI depends on `ffmpeg`/`ffprobe`, `dovi_tool`, and `mkvmerge` for real
conversions. Unit tests must not depend on those binaries being installed.

See `README.md` for supported commands and end-user documentation.

