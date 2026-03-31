"""Tests for track selection module."""

from de_dolby.probe import FileInfo, StreamInfo
from de_dolby.tracks import (
    TrackSelection,
    build_ffmpeg_audio_maps,
    build_ffmpeg_subtitle_maps,
    build_mkvmerge_audio_args,
    build_mkvmerge_subtitle_args,
    build_mkvmerge_track_args,
    get_audio_tracks,
    get_subtitle_tracks,
    parse_lang_string,
    select_streams,
)


class TestParseLangString:
    """Tests for parse_lang_string function."""

    def test_none_returns_none(self):
        assert parse_lang_string(None) is None

    def test_all_returns_none(self):
        assert parse_lang_string("all") is None

    def test_ALL_returns_none(self):
        assert parse_lang_string("ALL") is None

    def test_single_language(self):
        assert parse_lang_string("eng") == ["eng"]

    def test_multiple_languages(self):
        assert parse_lang_string("eng,jpn") == ["eng", "jpn"]

    def test_languages_with_spaces(self):
        assert parse_lang_string("eng, jpn, fre") == ["eng", "jpn", "fre"]

    def test_languages_lowercased(self):
        assert parse_lang_string("ENG,JPN") == ["eng", "jpn"]


class TestSelectStreams:
    """Tests for select_streams function."""

    def create_streams(self, languages: list[str | None]) -> list[StreamInfo]:
        """Helper to create a list of StreamInfo with given languages."""
        return [
            StreamInfo(index=i, codec_type="audio", codec_name="aac", language=lang)
            for i, lang in enumerate(languages)
        ]

    def test_disabled_returns_empty(self):
        streams = self.create_streams(["eng", "jpn"])
        result = select_streams(streams, None, True, disabled=True)
        assert result == []

    def test_none_langs_returns_all(self):
        streams = self.create_streams(["eng", "jpn"])
        result = select_streams(streams, None, True, disabled=False)
        assert result == streams

    def test_single_lang_filter(self):
        streams = self.create_streams(["eng", "jpn", "fre"])
        result = select_streams(streams, ["eng"], False, disabled=False)
        assert len(result) == 1
        assert result[0].language == "eng"

    def test_multi_lang_filter(self):
        streams = self.create_streams(["eng", "jpn", "fre"])
        result = select_streams(streams, ["eng", "jpn"], False, disabled=False)
        assert len(result) == 2
        assert result[0].language == "eng"
        assert result[1].language == "jpn"

    def test_keep_first_overrides_filter(self):
        streams = self.create_streams(["fre", "eng", "jpn"])
        result = select_streams(streams, ["eng"], True, disabled=False)
        assert len(result) == 2
        assert result[0].language == "fre"  # First kept due to keep_first
        assert result[1].language == "eng"  # Second matches filter

    def test_undefined_language_matching(self):
        streams = self.create_streams([None, "eng"])
        result = select_streams(streams, ["und"], False, disabled=False)
        assert len(result) == 1
        assert result[0].language is None


class TestGetAudioTracks:
    """Tests for get_audio_tracks function."""

    def create_file_info(self, audio_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with audio streams."""
        return FileInfo(
            path="test.mkv",
            audio_streams=[
                StreamInfo(index=i, codec_type="audio", codec_name="aac", language=lang)
                for i, lang in enumerate(audio_langs)
            ],
        )

    def test_no_audio_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_audio=True)
        result = get_audio_tracks(info, selection)
        assert result == []

    def test_filter_by_language(self):
        info = self.create_file_info(["eng", "jpn", "fre"])
        selection = TrackSelection(audio_langs=["eng"], keep_first_audio=False)
        result = get_audio_tracks(info, selection)
        assert len(result) == 1
        assert result[0].language == "eng"

    def test_keep_first_audio_safety(self):
        info = self.create_file_info(["fre", "eng"])
        selection = TrackSelection(audio_langs=["eng"], keep_first_audio=True)
        result = get_audio_tracks(info, selection)
        assert len(result) == 2
        assert result[0].language == "fre"  # First kept for safety
        assert result[1].language == "eng"  # Matches filter


class TestGetSubtitleTracks:
    """Tests for get_subtitle_tracks function."""

    def create_file_info(self, sub_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with subtitle streams."""
        return FileInfo(
            path="test.mkv",
            subtitle_streams=[
                StreamInfo(index=i, codec_type="subtitle", codec_name="subrip", language=lang)
                for i, lang in enumerate(sub_langs)
            ],
        )

    def test_no_subtitles_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_subtitles=True)
        result = get_subtitle_tracks(info, selection)
        assert result == []

    def test_filter_by_language(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(subtitle_langs=["eng"], keep_first_subtitle=False)
        result = get_subtitle_tracks(info, selection)
        assert len(result) == 1
        assert result[0].language == "eng"


class TestBuildFFmpegAudioMaps:
    """Tests for build_ffmpeg_audio_maps function."""

    def create_file_info(self, audio_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with audio streams."""
        return FileInfo(
            path="test.mkv",
            audio_streams=[
                StreamInfo(index=i, codec_type="audio", codec_name="aac", language=lang)
                for i, lang in enumerate(audio_langs)
            ],
        )

    def test_no_audio_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_audio=True)
        result = build_ffmpeg_audio_maps(info, selection)
        assert result == []

    def test_single_track_map(self):
        info = self.create_file_info(["eng"])
        selection = TrackSelection(audio_langs=["eng"])
        result = build_ffmpeg_audio_maps(info, selection)
        assert result == ["-map", "0:a:0"]

    def test_multiple_track_maps(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(audio_langs=["eng", "jpn"])
        result = build_ffmpeg_audio_maps(info, selection)
        assert result == ["-map", "0:a:0", "-map", "0:a:1"]

    def test_filtered_tracks_map_correctly(self):
        info = self.create_file_info(["eng", "jpn", "fre"])
        selection = TrackSelection(audio_langs=["eng", "fre"])
        result = build_ffmpeg_audio_maps(info, selection)
        # Maps to audio tracks 0 and 2 (skipping jpn at index 1)
        assert result == ["-map", "0:a:0", "-map", "0:a:2"]


class TestBuildFFmpegSubtitleMaps:
    """Tests for build_ffmpeg_subtitle_maps function."""

    def create_file_info(self, sub_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with subtitle streams."""
        return FileInfo(
            path="test.mkv",
            subtitle_streams=[
                StreamInfo(index=i, codec_type="subtitle", codec_name="subrip", language=lang)
                for i, lang in enumerate(sub_langs)
            ],
        )

    def test_no_subtitles_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_subtitles=True)
        result = build_ffmpeg_subtitle_maps(info, selection)
        assert result == []

    def test_single_subtitle_map(self):
        info = self.create_file_info(["eng"])
        selection = TrackSelection(subtitle_langs=["eng"])
        result = build_ffmpeg_subtitle_maps(info, selection)
        assert result == ["-map", "0:s:0"]

    def test_multiple_subtitle_maps(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(subtitle_langs=["eng", "jpn"])
        result = build_ffmpeg_subtitle_maps(info, selection)
        assert result == ["-map", "0:s:0", "-map", "0:s:1"]


class TestBuildMkvmergeAudioArgs:
    """Tests for build_mkvmerge_audio_args function."""

    def create_file_info(self, audio_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with audio streams."""
        return FileInfo(
            path="test.mkv",
            audio_streams=[
                StreamInfo(index=i, codec_type="audio", codec_name="aac", language=lang)
                for i, lang in enumerate(audio_langs)
            ],
        )

    def test_no_audio_flag(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_audio=True)
        result = build_mkvmerge_audio_args(info, selection)
        assert result == ["--no-audio"]

    def test_keep_all_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(audio_langs=None)
        result = build_mkvmerge_audio_args(info, selection)
        assert result == []

    def test_filtered_tracks(self):
        info = self.create_file_info(["eng", "jpn", "fre"])
        selection = TrackSelection(audio_langs=["eng", "fre"], keep_first_audio=False)
        result = build_mkvmerge_audio_args(info, selection)
        assert result == ["--audio-tracks", "0,2"]

    def test_empty_selection_returns_no_audio(self):
        info = self.create_file_info(["jpn"])
        selection = TrackSelection(audio_langs=["eng"], keep_first_audio=False)
        result = build_mkvmerge_audio_args(info, selection)
        assert result == ["--no-audio"]


class TestBuildMkvmergeSubtitleArgs:
    """Tests for build_mkvmerge_subtitle_args function."""

    def create_file_info(self, sub_langs: list[str | None]) -> FileInfo:
        """Helper to create FileInfo with subtitle streams."""
        return FileInfo(
            path="test.mkv",
            subtitle_streams=[
                StreamInfo(index=i, codec_type="subtitle", codec_name="subrip", language=lang)
                for i, lang in enumerate(sub_langs)
            ],
        )

    def test_no_subtitles_flag(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(no_subtitles=True)
        result = build_mkvmerge_subtitle_args(info, selection)
        assert result == ["--no-subtitles"]

    def test_keep_all_returns_empty(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(subtitle_langs=None)
        result = build_mkvmerge_subtitle_args(info, selection)
        assert result == []

    def test_filtered_tracks(self):
        info = self.create_file_info(["eng", "jpn"])
        selection = TrackSelection(subtitle_langs=["jpn"], keep_first_subtitle=False)
        result = build_mkvmerge_subtitle_args(info, selection)
        assert result == ["--subtitle-tracks", "1"]


class TestBuildMkvmergeTrackArgs:
    """Tests for build_mkvmerge_track_args function."""

    def create_file_info(
        self, audio_langs: list[str | None], sub_langs: list[str | None]
    ) -> FileInfo:
        """Helper to create FileInfo with both audio and subtitle streams."""
        return FileInfo(
            path="test.mkv",
            audio_streams=[
                StreamInfo(index=i, codec_type="audio", codec_name="aac", language=lang)
                for i, lang in enumerate(audio_langs)
            ],
            subtitle_streams=[
                StreamInfo(index=i, codec_type="subtitle", codec_name="subrip", language=lang)
                for i, lang in enumerate(sub_langs)
            ],
        )

    def test_combined_audio_and_subtitle_args(self):
        info = self.create_file_info(["eng", "jpn"], ["eng"])
        selection = TrackSelection(
            audio_langs=["eng"],
            subtitle_langs=None,
            keep_first_audio=False,
        )
        result = build_mkvmerge_track_args(info, selection)
        assert "--audio-tracks" in result
        assert "0" in result  # English audio track
        assert "--subtitle-tracks" not in result  # All subtitles kept

    def test_strip_all_tracks(self):
        info = self.create_file_info(["eng"], ["eng"])
        selection = TrackSelection(no_audio=True, no_subtitles=True)
        result = build_mkvmerge_track_args(info, selection)
        assert "--no-audio" in result
        assert "--no-subtitles" in result


class TestTrackSelectionDefaults:
    """Tests for TrackSelection dataclass defaults."""

    def test_default_keep_first_audio(self):
        selection = TrackSelection()
        assert selection.keep_first_audio is True

    def test_default_keep_first_subtitle(self):
        selection = TrackSelection()
        assert selection.keep_first_subtitle is True

    def test_default_no_flags_false(self):
        selection = TrackSelection()
        assert selection.no_audio is False
        assert selection.no_subtitles is False

    def test_default_langs_none(self):
        selection = TrackSelection()
        assert selection.audio_langs is None
        assert selection.subtitle_langs is None
