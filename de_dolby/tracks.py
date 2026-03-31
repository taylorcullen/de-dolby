"""Audio and subtitle track selection for conversions."""

from dataclasses import dataclass

from de_dolby.probe import FileInfo, StreamInfo


@dataclass
class TrackSelection:
    """Configuration for audio and subtitle track selection."""

    audio_langs: list[str] | None = None  # None = all languages
    subtitle_langs: list[str] | None = None  # None = all languages
    no_audio: bool = False
    no_subtitles: bool = False
    keep_first_audio: bool = True  # safety default: always keep first audio
    keep_first_subtitle: bool = True  # safety default: always keep first subtitle


def parse_lang_string(lang_str: str | None) -> list[str] | None:
    """Parse a language string like 'eng,jpn' into a list of languages.

    Returns None for 'all' or empty/None input (meaning keep all languages).
    """
    if lang_str is None or lang_str.lower() == "all":
        return None
    return [lang.strip().lower() for lang in lang_str.split(",") if lang.strip()]


def select_streams(
    streams: list[StreamInfo],
    selected_langs: list[str] | None,
    keep_first: bool,
    disabled: bool = False,
) -> list[StreamInfo]:
    """Select streams based on language filter and safety flags.

    Args:
        streams: List of streams to filter (audio or subtitle)
        selected_langs: List of language codes to keep, or None for all
        keep_first: Always keep the first stream regardless of language filter
        disabled: If True, return empty list (strip all streams)

    Returns:
        List of selected streams
    """
    if disabled:
        return []

    if selected_langs is None:
        return streams

    selected = []
    for i, stream in enumerate(streams):
        if i == 0 and keep_first or stream.language and stream.language.lower() in selected_langs:
            selected.append(stream)
        elif stream.language is None and "und" in selected_langs:
            # Handle undefined language tracks
            selected.append(stream)

    return selected


def get_audio_tracks(info: FileInfo, selection: TrackSelection) -> list[StreamInfo]:
    """Get the selected audio tracks from a FileInfo."""
    return select_streams(
        info.audio_streams,
        selection.audio_langs,
        selection.keep_first_audio,
        selection.no_audio,
    )


def get_subtitle_tracks(info: FileInfo, selection: TrackSelection) -> list[StreamInfo]:
    """Get the selected subtitle tracks from a FileInfo."""
    return select_streams(
        info.subtitle_streams,
        selection.subtitle_langs,
        selection.keep_first_subtitle,
        selection.no_subtitles,
    )


def build_ffmpeg_audio_maps(
    info: FileInfo,
    selection: TrackSelection,
) -> list[str]:
    """Build ffmpeg -map arguments for audio tracks.

    Returns a list of -map arguments for the selected audio tracks.
    """
    if selection.no_audio:
        return []

    selected = get_audio_tracks(info, selection)
    maps = []
    for stream in selected:
        # Find the input stream index within audio streams
        audio_index = info.audio_streams.index(stream)
        maps.extend(["-map", f"0:a:{audio_index}"])

    return maps


def build_ffmpeg_subtitle_maps(
    info: FileInfo,
    selection: TrackSelection,
) -> list[str]:
    """Build ffmpeg -map arguments for subtitle tracks.

    Returns a list of -map arguments for the selected subtitle tracks.
    """
    if selection.no_subtitles:
        return []

    selected = get_subtitle_tracks(info, selection)
    maps = []
    for stream in selected:
        # Find the input stream index within subtitle streams
        subtitle_index = info.subtitle_streams.index(stream)
        maps.extend(["-map", f"0:s:{subtitle_index}"])

    return maps


def build_mkvmerge_audio_args(
    info: FileInfo,
    selection: TrackSelection,
) -> list[str]:
    """Build mkvmerge --audio-tracks argument.

    Returns a list containing the --audio-tracks option if filtering is needed.
    """
    if selection.no_audio:
        return ["--no-audio"]

    selected = get_audio_tracks(info, selection)
    if len(selected) == len(info.audio_streams):
        # Keeping all tracks, no filter needed
        return []

    if not selected:
        return ["--no-audio"]

    # Build track list (mkvmerge uses 0-based indices)
    track_indices = [str(info.audio_streams.index(s)) for s in selected]
    return ["--audio-tracks", ",".join(track_indices)]


def build_mkvmerge_subtitle_args(
    info: FileInfo,
    selection: TrackSelection,
) -> list[str]:
    """Build mkvmerge --subtitle-tracks argument.

    Returns a list containing the --subtitle-tracks option if filtering is needed.
    """
    if selection.no_subtitles:
        return ["--no-subtitles"]

    selected = get_subtitle_tracks(info, selection)
    if len(selected) == len(info.subtitle_streams):
        # Keeping all tracks, no filter needed
        return []

    if not selected:
        return ["--no-subtitles"]

    # Build track list (mkvmerge uses 0-based indices)
    track_indices = [str(info.subtitle_streams.index(s)) for s in selected]
    return ["--subtitle-tracks", ",".join(track_indices)]


def build_mkvmerge_track_args(
    info: FileInfo,
    selection: TrackSelection,
) -> list[str]:
    """Build all mkvmerge track selection arguments.

    Combines audio and subtitle track selection into a single argument list.
    """
    args = []
    args.extend(build_mkvmerge_audio_args(info, selection))
    args.extend(build_mkvmerge_subtitle_args(info, selection))
    return args
