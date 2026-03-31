"""ConvertOptions dataclass for conversion configuration."""

from dataclasses import dataclass, field

from de_dolby.tracks import TrackSelection


@dataclass
class ConvertOptions:
    """Configuration options for conversion."""

    encoder: str = "auto"  # auto, hevc_amf, libx265, av1_amf, libsvtav1, copy
    quality: str = "balanced"  # fast, balanced, quality
    crf: int | None = None
    bitrate: str | None = None
    sample_seconds: int | None = None  # convert only first N seconds
    temp_dir: str | None = None  # custom temp directory for intermediate files
    dry_run: bool = False
    verbose: bool = False
    force: bool = False
    skip_validation: bool = False  # skip output validation after conversion
    resume: bool = False  # resume from interrupted conversion
    track_selection: TrackSelection = field(default_factory=lambda: TrackSelection())
