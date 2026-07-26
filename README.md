<p align="center">
  <pre align="center">
  <b><span style="color: #d55fde">██████╗ ██╗   ██╗</span> <span style="color: white">██████╗</span>  <span style="color: #50fa7b">██╗  ██╗██████╗ ██████╗  ██╗ ██████╗</span></b>
  <b><span style="color: #d55fde">██╔══██╗██║   ██║</span> <span style="color: white">╚════██╗</span> <span style="color: #50fa7b">██║  ██║██╔══██╗██╔══██╗███║██╔═████╗</span></b>
  <b><span style="color: #d55fde">██║  ██║██║   ██║</span>  <span style="color: white">█████╔╝</span> <span style="color: #50fa7b">███████║██║  ██║██████╔╝╚██║██║██╔██║</span></b>
  <b><span style="color: #d55fde">██║  ██║╚██╗ ██╔╝</span> <span style="color: white">██╔═══╝</span>  <span style="color: #50fa7b">██╔══██║██║  ██║██╔══██╗ ██║████╔╝██║</span></b>
  <b><span style="color: #d55fde">██████╔╝ ╚████╔╝</span>  <span style="color: white">███████╗</span> <span style="color: #50fa7b">██║  ██║██████╔╝██║  ██║ ██║╚██████╔╝</span></b>
  <b><span style="color: #d55fde">╚═════╝   ╚═══╝</span>   <span style="color: white">╚══════╝</span> <span style="color: #50fa7b">╚═╝  ╚═╝╚═════╝ ╚═╝  ╚═╝ ╚═╝ ╚═════╝</span></b>
  </pre>
</p>

<p align="center">
  <b>Convert Dolby Vision MKV files to HDR10</b><br>
  <sub>Cross-platform &bull; GPU-accelerated &bull; HEVC &amp; AV1</sub>
</p>

<p align="center">
  <a href="#quick-start">Quick Start</a> &bull;
  <a href="#usage">Usage</a> &bull;
  <a href="#encoders">Encoders</a> &bull;
  <a href="#installation">Installation</a> &bull;
  <a href="#troubleshooting">Troubleshooting</a>
</p>

---

## Development

This repository includes a provider-neutral AI development harness for both
product work and improvements to the harness itself:

```bash
python -m harness context       # discover durable project context
python -m harness doctor        # validate harness configuration
python -m harness check         # run product and harness quality gates
```

Unit tests do not require external media binaries and exclude integration
tests by default. To run the hermetic media suite locally, install `ffmpeg`,
`ffprobe`, and `mkvmerge`, ensure they are on `PATH`, then run:

```bash
ffmpeg -version
ffprobe -version
mkvmerge --version
python -m pytest -m integration -v
```

The suite generates sub-second synthetic fixtures in pytest temporary
directories; it does not download or require sample media. Missing tools
produce an explicit pytest skip reason. CI runs this suite in a separate
five-minute Ubuntu job and records the external tool versions in its log.

See [`AGENTS.md`](AGENTS.md) and [`ai/README.md`](ai/README.md) for the working
contract and extension points.

---

## Quick Start

```bash
pip install -e .
de-dolby convert movie.mkv
```

That's it. de-dolby auto-detects the DV profile, picks the best GPU encoder, and outputs an HDR10 MKV.

---

## How It Works

| DV Profile | Input | Pipeline | Speed |
|:----------:|:-----:|:--------:|:-----:|
| **7 / 8** | HEVC | Lossless RPU strip | Seconds |
| **5** | HEVC | Re-encode (color conversion) | Minutes |
| **10** | AV1 | Re-encode (ffprobe metadata) | Minutes |

- **Profile 7/8** strips DV metadata and remuxes with HDR10 flags. No re-encoding, no quality loss.
- **Profile 5** must re-encode because it uses IPTPQc2, a color space incompatible with standard HDR10 displays.
- **Profile 10** (AV1 DV) re-encodes since dovi_tool can't process AV1 tracks.

---

## Usage

### Convert files

```bash
# Single file (auto-generates output name)
de-dolby convert movie.DV.mkv          # → movie.HDR10.mkv

# Batch convert
de-dolby convert *.mkv

# Custom output
de-dolby convert movie.mkv -o output.mkv
```

Long batches can persist atomic, versioned progress and resume without
reprocessing outputs whose input, conversion plan, validation result, and
output identity still match:

```bash
de-dolby convert *.mkv --manifest batch.json
de-dolby convert *.mkv --manifest batch.json --resume
de-dolby convert *.mkv --manifest batch.json --resume --retry-failed
```

`--resume` requires `--manifest`; failed items remain skipped unless
`--retry-failed` is also supplied. Changed inputs or conversion options, and
missing or modified outputs, are rerun automatically.

### Reusable presets

Configuration is loaded from `%APPDATA%\de-dolby\config.toml` on Windows,
`~/Library/Application Support/de-dolby/config.toml` on macOS, and
`${XDG_CONFIG_HOME:-~/.config}/de-dolby/config.toml` elsewhere. Use
`--config PATH` on `convert`, `plan`, or `config show` for an explicit file.

```toml
schema_version = 1

[defaults]
quality = "balanced"

[presets.cpu]
encoder = "libx265"
crf = 18

[presets.amd]
encoder = "hevc_amf"
bitrate = "40M"

[presets.nvidia]
encoder = "hevc_nvenc"
bitrate = "40M"

[presets.sample]
sample_seconds = 30
```

```bash
de-dolby convert movie.mkv --preset cpu
de-dolby convert movie.mkv --preset amd
de-dolby convert movie.mkv --preset nvidia
de-dolby plan movie.mkv --preset sample --json
de-dolby config show --effective --preset cpu
de-dolby config show --effective --preset cpu --json
```

Settings resolve in this order: built-in defaults, config `[defaults]`, the
named preset, then explicit CLI arguments. Later values win. Effective config
output contains only supported conversion settings; arbitrary environment
variables and command details are never included.

### Output safety

Conversions write the completed MKV to a uniquely named staging file beside
the requested output. The final path becomes visible only after remuxing
succeeds and the staged file is flushed, at which point it is published with
an atomic filesystem operation.

Without `--force`, an existing destination is never replaced—even if it
appears during the conversion. With `--force`, the existing file remains
untouched until the complete staging file is atomically substituted. Ordinary
errors and interruptions remove staging files.

The destination directory must support same-filesystem atomic links or
replacement. de-dolby reports a clear error if the filesystem does not provide
the required operation; it does not fall back to a partial copy.

### Stream preservation

Every plan inventories and assigns an explicit action to video, audio,
subtitle, attachment, chapter, tag, and unknown stream content. The same
policy generates the final `mkvmerge` arguments:

| Element | Full conversion | Sample conversion |
|:--------|:----------------|:------------------|
| Primary video | Replaced by converted HDR10 video | Replaced |
| Additional video | Omitted with a warning | Omitted with a warning |
| Audio tracks | Bit-for-bit copy | Bit-for-bit copy |
| Subtitle tracks | Bit-for-bit copy | Bit-for-bit copy |
| Track language/name | Reapplied explicitly | Preserved by sample extraction |
| Default/forced flags | Reapplied explicitly | Preserved by sample extraction |
| Attachments/fonts | Preserved | Omitted with a warning |
| Chapters | Preserved | Omitted with a warning |
| Container/track tags | Preserved | Omitted with a warning |
| Unknown stream types | Omitted with a warning | Omitted with a warning |

de-dolby does not transcode audio or subtitles and does not edit metadata.
Additional video tracks and unknown stream types are not currently supported.
Review `de-dolby plan FILE` (or its `warnings` JSON field) before conversion;
the converter also prints every planned omission.

### Validate converted output

Every conversion validates its completed staging file before transactional
publication. A validation error removes staging and leaves the requested
destination untouched. Validate an existing pair independently with:

```bash
de-dolby validate movie.DV.mkv movie.HDR10.mkv
de-dolby validate movie.DV.mkv sample.HDR10.mkv --sample 30
de-dolby validate movie.DV.mkv movie.HDR10.mkv --json
```

Validation checks readability, video codec and dimensions, duration, PQ/BT.2020
signalling, HDR10 static metadata, absence of Dolby Vision, and every stream
marked for preservation. Full conversions allow the larger of 1 second or
0.1% of input duration; samples allow 0.5 seconds.

JSON reports use schema version 1. Each issue has a stable `code`, `severity`,
`message`, `expected`, and `actual` field. Codes are:

`output_unreadable`, `video_missing`, `codec_mismatch`,
`dimensions_mismatch`, `duration_mismatch`, `hdr_transfer_missing`,
`hdr_primaries_missing`, `hdr_colorspace_missing`,
`static_metadata_missing`, `dolby_vision_present`, `stream_missing`, and
`stream_metadata_mismatch`.

`--unsafe-skip-validation` is the only validation bypass. It is intended for
diagnosis when a known ffprobe limitation causes a false failure; it can
publish malformed output and should not be used routinely.

### Quality control

```bash
# Quality presets
de-dolby convert movie.mkv --quality fast       # Quick, larger file
de-dolby convert movie.mkv --quality balanced   # Default
de-dolby convert movie.mkv --quality quality    # Slow, best quality

# Fine-tune
de-dolby convert movie.mkv --encoder libx265 --crf 16
de-dolby convert movie.mkv --encoder hevc_nvenc --bitrate 60M

# Test with a sample first
de-dolby convert movie.mkv --sample 30
```

### Inspect and preview

```bash
# Show file info (DV profile, streams, HDR metadata)
de-dolby info movie.mkv

# Extract a tone-mapped SDR frame for visual check
de-dolby preview movie.mkv --time 00:05:00
```

### Inspect a conversion plan

Use `plan` to see the exact route before any output or intermediate media files
are created:

```bash
de-dolby plan movie.DV.mkv
de-dolby plan movie.DV.mkv --encoder libx265
de-dolby plan movie.DV.mkv --sample 30 -o sample.HDR10.mkv
de-dolby plan movie.DV.mkv --json
```

The plan reports the selected pipeline and encoder, automatic fallback reason,
metadata source, stream policy, estimated temporary space, and ordered steps.
Planning fails early for unsupported profile/codec combinations, incompatible
or unavailable explicit encoders, and environments with no compatible
encoder. Profiles 7/8 select the lossless route automatically.

`convert --dry-run` uses the same planner and creates no intermediate
directory. For stable machine-readable output, prefer `plan --json`.

Plan JSON uses schema version 2. Paths are strings preserving the spelling
supplied to the command (or the deterministically derived default output name);
they are not resolved or rewritten. Enum fields use these stable values:

| Field | Values |
|:------|:-------|
| `pipeline` | `lossless_rpu_strip`, `reencode` |
| `metadata_source` | `dovi_rpu`, `ffprobe` |
| `stream_policy.*`, `stream_map[].action` | `copy`, `preserve`, `omit`, `replace` |

Other top-level fields are `schema_version`, `input_path`, `output_path`,
`profile`, `input_codec`, `encoder`, `fallback_reason`,
`estimated_temp_bytes`, `stream_map`, `warnings`, and `steps`. Consumers should reject unsupported
schema versions.

### Check conversion readiness

Run `doctor` before a large conversion to inspect tool versions, FFmpeg
encoders and `libplacebo` support, temp-space availability, and readiness for
each supported Dolby Vision profile:

```bash
de-dolby doctor
de-dolby doctor --profile 5
de-dolby doctor --profile 10 --temp-dir /path/with/space
```

Without `--profile`, the command exits successfully when at least one profile
route is ready. With `--profile`, it exits successfully only when that route is
ready. No input video is required and the checks do not modify the system.

Use custom tool locations when they are not on `PATH`:

```bash
de-dolby doctor \
  --ffmpeg /path/to/ffmpeg \
  --dovi-tool /path/to/dovi_tool \
  --mkvmerge /path/to/mkvmerge
```

For automation, `--json` emits schema version 2. The top-level fields are:

| Field | Meaning |
|:------|:--------|
| `schema_version` | Integer contract version; currently `2` |
| `usable` | Whether any route, or the requested route, is ready |
| `requested_profile` | Requested profile number or `null` |
| `tools` | Configured/resolved paths, versions, availability, and errors |
| `ffmpeg` | Encoders, filters, `libplacebo`, and probe errors |
| `temp_directory` | Path, existence, writability, free bytes, and errors |
| `profiles` | Readiness, applicable encoders, and reasons for profiles 5, 7, 8, and 10 |

Consumers should reject unsupported schema versions rather than assuming
fields from another version.

---

<h2 id="encoders">Encoders</h2>

de-dolby supports 8 encoders across 3 GPU platforms plus CPU fallback:

| Encoder | Platform | GPU | Codec | Type |
|:--------|:---------|:----|:------|:-----|
| `hevc_amf` | Windows | AMD | HEVC | Hardware |
| `av1_amf` | Windows | AMD | AV1 | Hardware |
| `hevc_nvenc` | Windows / Linux | NVIDIA | HEVC | Hardware |
| `av1_nvenc` | Windows / Linux | NVIDIA | AV1 | Hardware |
| `hevc_vaapi` | Linux | AMD / Intel | HEVC | Hardware |
| `av1_vaapi` | Linux | AMD / Intel | AV1 | Hardware |
| `libx265` | All | &mdash; | HEVC | Software |
| `libsvtav1` | All | &mdash; | AV1 | Software |

With `--encoder auto` (default), de-dolby probes your ffmpeg and picks the first available in priority order:

**AMF** &rarr; **NVENC** &rarr; **VAAPI** &rarr; **CPU**

```bash
# Explicit selection
de-dolby convert movie.mkv --encoder hevc_amf       # Windows AMD
de-dolby convert movie.mkv --encoder hevc_vaapi      # Linux AMD/Intel
de-dolby convert movie.mkv --encoder av1_nvenc       # NVIDIA AV1
de-dolby convert movie.mkv --encoder libsvtav1       # CPU AV1 (any platform)
```

---

<h2 id="installation">Installation</h2>

### Requirements

Python 3.10+ and three external tools:

| Tool | Purpose | Download |
|:-----|:--------|:---------|
| **ffmpeg** / **ffprobe** | Video decode, encode, mux | [ffmpeg.org](https://ffmpeg.org/download.html) |
| **dovi_tool** | DV RPU extraction and stripping | [quietvoid/dovi_tool](https://github.com/quietvoid/dovi_tool/releases) |
| **mkvmerge** | MKV remux with HDR10 metadata | [mkvtoolnix.download](https://mkvtoolnix.download/) |

> **Tip:** You can skip PATH setup and pass locations explicitly:
> ```bash
> de-dolby convert movie.mkv --ffmpeg /path/to/ffmpeg --dovi-tool /path/to/dovi_tool --mkvmerge /path/to/mkvmerge
> de-dolby doctor --ffmpeg /path/to/ffmpeg --dovi-tool /path/to/dovi_tool --mkvmerge /path/to/mkvmerge
> ```

### Windows

<details>
<summary><b>Click to expand Windows instructions</b></summary>

**Install tools:**

```powershell
winget install -e --id Gyan.FFmpeg
winget install -e --id MoritzBunkus.MKVToolNix
```

For dovi_tool, download `dovi_tool-x86_64-pc-windows-msvc.zip` from [releases](https://github.com/quietvoid/dovi_tool/releases), extract to `C:\tools\`, and add to PATH:

```powershell
$doviPath = "C:\tools"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$doviPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$doviPath", "User")
}
```

**Install de-dolby:**

```powershell
cd D:\Repos\de-dolby
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

> If you get an execution policy error: `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

**Verify:**

```powershell
de-dolby doctor
```

</details>

### Linux

<details>
<summary><b>Click to expand Linux instructions</b></summary>

**Install tools:**

```bash
# Ubuntu / Debian
sudo apt install ffmpeg mkvtoolnix

# Fedora
sudo dnf install ffmpeg mkvtoolnix

# Arch
sudo pacman -S ffmpeg mkvtoolnix-cli
```

For dovi_tool:

```bash
wget https://github.com/quietvoid/dovi_tool/releases/latest/download/dovi_tool-x86_64-unknown-linux-musl.tar.gz
tar xzf dovi_tool-x86_64-unknown-linux-musl.tar.gz
mv dovi_tool ~/.local/bin/
```

**For VAAPI GPU encoding (AMD/Intel):**

```bash
sudo apt install vainfo libva-dev   # Ubuntu/Debian
vainfo                               # Verify
```

**For NVENC GPU encoding (NVIDIA):**

```bash
ffmpeg -encoders 2>/dev/null | grep nvenc   # Verify NVENC support
```

**Install de-dolby:**

```bash
cd ~/repos/de-dolby
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
```

**Verify:**

```bash
de-dolby doctor
```

</details>

---

## All Options

```
de-dolby convert <file> [<file> ...] [options]

  -o, --output PATH         Output file (single input only)
  --encoder ENCODER         Video encoder (default: auto)
  --quality {fast,balanced,quality}
                            Encoder preset (default: balanced)
  --crf INT                 CRF for CPU encoders (overrides preset)
  --bitrate STR             Target bitrate for GPU encoders, e.g. "40M"
  --sample [SECONDS]        Convert only first N seconds (default: 30)
  --temp-dir PATH           Directory for intermediate files
  --timeout MINUTES         Timeout per subprocess call
  --log-file PATH           Write all commands to a log file
  --dry-run                 Show steps without executing
  -v, --verbose             Show ffmpeg commands
  --force                   Overwrite existing output file
  --unsafe-skip-validation  UNSAFE: publish without output validation
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary

de-dolby doctor [options]

  --json                    Emit the versioned JSON schema
  --profile {5,7,8,10}      Require readiness for one DV profile
  --temp-dir PATH           Inspect a custom intermediate directory
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary

de-dolby plan FILE [options]

  -o, --output PATH         Planned output MKV path
  --encoder ENCODER         Planned encoder (default: auto)
  --sample [SECONDS]        Plan a sample conversion (default: 30)
  --json                    Emit plan JSON schema version 2
  --ffmpeg PATH             Path to ffmpeg/ffprobe

de-dolby validate INPUT OUTPUT [options]

  --sample SECONDS          Validate against a sample duration
  --json                    Emit validation JSON schema version 1
  --ffmpeg PATH             Path to ffmpeg/ffprobe
```

---

<h2 id="troubleshooting">Troubleshooting</h2>

| Error | Solution |
|:------|:---------|
| **Unsure whether a profile can run** | Run `de-dolby doctor --profile PROFILE` for route-specific checks and fixes |
| **Unsure what conversion will do** | Run `de-dolby plan FILE`; use `--json` for automation |
| **"encoder not available"** | Use `--encoder auto` or fall back to `--encoder libx265` |
| **"required tools not found on PATH"** | Run `de-dolby doctor`; install the reported tool or pass its custom path |
| **Profile 5 reports missing `libplacebo`** | Install an FFmpeg build compiled with the `libplacebo` filter |
| **"No Dolby Vision metadata detected"** | File isn't DV. Run `de-dolby info` to verify |
| **"Could not atomically publish"** | Choose an output directory on a local filesystem that supports atomic links/replacement |
| **Stream omitted warning** | Inspect `de-dolby plan FILE`; additional video, unknown streams, and sample container content are intentionally unsupported |
| **Output validation failed** | Run `de-dolby validate INPUT OUTPUT --json` and resolve the reported invariant; bypass only for a confirmed false positive |
| **Large temp files** | Run `de-dolby doctor --temp-dir /path/with/space` to verify free space and writability |
| **VAAPI permission denied** | Add user to render/video group: `sudo usermod -aG render,video $USER` |

---

<p align="center">
  <sub>Built with ffmpeg, dovi_tool, and mkvmerge</sub>
</p>
