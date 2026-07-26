import json
import shutil
import subprocess
from dataclasses import replace

import pytest

from .fixture_generator import generate_fixture
from de_dolby.fidelity import build_source_remux_args
from de_dolby.plan import create_conversion_plan
from de_dolby.probe import probe
from de_dolby.validation import ValidationCode, compare_output_to_plan


pytestmark = pytest.mark.integration


def _tool(name):
    path = shutil.which(name)
    if not path:
        pytest.skip(f"integration tool unavailable: {name}")
    return path


def test_generated_fixture_probes_and_remuxes(tmp_path):
    ffmpeg = _tool("ffmpeg")
    ffprobe = _tool("ffprobe")
    mkvmerge = _tool("mkvmerge")
    source = tmp_path / "source.mkv"
    remuxed = tmp_path / "remuxed.mkv"
    generate_fixture(ffmpeg, source)
    subprocess.run([mkvmerge, "-o", str(remuxed), str(source)], check=True)
    result = subprocess.run(
        [ffprobe, "-v", "error", "-show_streams", "-of", "json", str(remuxed)],
        check=True, capture_output=True, text=True,
    )
    streams = json.loads(result.stdout)["streams"]
    assert [stream["codec_type"] for stream in streams] == ["video", "audio"]
    assert remuxed.stat().st_size < 1_000_000


def test_real_probe_drives_fidelity_and_broken_map_is_rejected(tmp_path):
    ffmpeg = _tool("ffmpeg")
    _tool("ffprobe")
    source = tmp_path / "source.mkv"
    generate_fixture(ffmpeg, source)

    input_info = probe(str(source))
    # Synthetic media carries no Dolby Vision bitstream; supply only the route
    # facts needed to exercise the already-probed stream inventory.
    input_info.dv_profile = 7
    input_info.video_streams[0].codec_name = "hevc"
    plan = create_conversion_plan(input_info, str(tmp_path / "output.mkv"))

    audio = input_info.audio_streams[0]
    remux_args = build_source_remux_args(
        plan, str(source), sample_source=False
    )
    assert "--audio-tracks" in remux_args
    assert str(audio.index) in remux_args
    assert f"{audio.index}:eng" in remux_args

    broken_streams = tuple(
        replace(stream, codec_name="aac")
        if stream.source_index == audio.index else stream
        for stream in plan.stream_map
    )
    broken_plan = replace(plan, stream_map=broken_streams)
    output_info = probe(str(source))
    report = compare_output_to_plan(input_info, output_info, broken_plan)
    assert ValidationCode.STREAM_MISSING in {
        issue.code for issue in report.errors
    }
