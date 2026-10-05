# DD-011 — Conversion step ETA

Status: Done  
Priority: 11  
Dependencies: None

## Outcome

Users can see an understandable estimate of how many minutes and seconds remain
in the active FFmpeg conversion step, alongside the existing percentage, frame
rate, and speed.

## Problem and evidence

The conversion progress display currently reports values such as `42.1%`,
`61.7 fps`, and `3.34x`. These are useful diagnostics but force users to
calculate the remaining wall-clock time themselves. The progress parser already
has the input duration, processed media timestamp, and FFmpeg speed required to
derive an estimate.

## Scope

- Parse FFmpeg's numeric speed multiplier without removing the existing display
  string.
- Estimate remaining wall-clock seconds from effective media duration,
  processed media time, and a positive speed multiplier.
- Stabilize normal short-term speed fluctuations so the displayed ETA does not
  jump excessively between adjacent updates.
- Render ETA beside the existing encoding statistics using minutes and seconds,
  adding hours for estimates of at least one hour.
- Use the sample duration as the effective duration when converting a sample.
- Omit ETA cleanly during startup or whenever duration, progress time, or a
  usable speed is unavailable.

Non-goals: estimating probe, extraction, RPU processing, remux, validation, or
the total duration of an entire multi-file batch.

## Acceptance criteria

- [x] A progress update with known duration, processed time, and positive speed
      displays an ETA such as `ETA 12m 34s`.
- [x] Estimates of at least one hour use an unambiguous hours/minutes/seconds
      format, and sub-minute estimates still show minutes and seconds.
- [x] Missing, malformed, zero, or unavailable speed/duration data never raises
      and does not display a misleading ETA.
- [x] ETA approaches zero as processed media time reaches the effective
      duration and never renders a negative value.
- [x] Existing percent, FPS, speed, verbose output, cancellation, and
      non-interactive behavior remain unchanged.
- [x] Unit tests cover calculation, formatting, smoothing/reset behavior,
      sample duration, and unavailable-data cases without invoking FFmpeg.

## Ralph slices

- [x] Add pure ETA calculation/formatting helpers and edge-case tests.
- [x] Carry parsed speed and effective duration through progress reporting,
      including sample conversions.
- [x] Add stable terminal rendering, smoothing/reset tests, and user
      documentation.

## Verification

`python -m pytest tests/test_progress.py tests/test_pipeline.py -q` and
`python -m harness check`.
