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

# Show file info as JSON for scripting
de-dolby info movie.mkv --json
de-dolby info movie.mkv --json --pretty

# Estimate conversion without executing
de-dolby estimate movie.mkv
de-dolby estimate movie.mkv --encoder hevc_amf --quality quality

# Extract a tone-mapped SDR frame for visual check
de-dolby preview movie.mkv --time 00:05:00
```

---

## Advanced Features

### Configuration File

Create a config file to set persistent defaults:

```bash
# Create example config file
de-dolby config --init

# View current config
de-dolby config --show
```

**Config file locations:**
- **Windows:** `%APPDATA%\de-dolby\config.toml`
- **Linux/macOS:** `~/.config/de-dolby/config.toml`

**Example config.toml:**

```toml
[defaults]
encoder = "auto"        # auto, hevc_amf, libx265, av1_amf, libsvtav1, copy
quality = "balanced"    # fast, balanced, quality
crf = 18                # CRF for libx265 (0-51, lower is better)
bitrate = "40M"         # Target bitrate for hardware encoders
output_dir = "~/Videos/HDR10"
temp_dir = "/tmp"
workers = 4
verbose = false
force = false

[tool_paths]
# Optional: override tool paths if not in PATH
# ffmpeg = "C:/Tools/ffmpeg.exe"
# dovi_tool = "C:/Tools/dovi_tool.exe"
# mkvmerge = "C:/Tools/mkvmerge.exe"

[tracks]
# Keep only these audio languages (empty = keep all)
audio_languages = ["eng", "jpn"]
# Skip subtitle tracks entirely
skip_subtitles = false
```

### Track Selection

Filter audio and subtitle tracks during conversion:

```bash
# Keep only specific audio languages
de-dolby convert movie.mkv --audio-lang eng,jpn

# Keep only specific subtitle languages
de-dolby convert movie.mkv --subtitle-lang eng

# Strip all audio tracks (video only)
de-dolby convert movie.mkv --no-audio

# Strip all subtitle tracks
de-dolby convert movie.mkv --no-subtitles

# Disable safety defaults (keep-first options)
de-dolby convert movie.mkv --audio-lang eng --no-keep-first-audio
```

**Safety defaults:** By default, the first audio and first subtitle track are always kept regardless of language filters. Use `--no-keep-first-audio` or `--no-keep-first-subtitle` to disable.

### Parallel Batch Processing

Convert multiple files in parallel for faster processing:

```bash
# Use all available CPU cores minus one
de-dolby convert *.mkv --workers auto

# Use specific number of workers
de-dolby convert *.mkv --workers 4

# Continue on errors (process all files even if some fail)
de-dolby convert *.mkv --workers auto --skip-errors
```

When using multiple workers, a progress dashboard displays active conversions and completion status.

### Resume Interrupted Conversions

If a conversion is interrupted, resume from where it left off:

```bash
# Resume previous conversion
de-dolby convert movie.mkv --resume

# Clean up old state files
de-dolby clean-state

# Remove all state files (not just old ones)
de-dolby clean-state --all
```

State files are automatically created in the temp directory and track conversion progress. They expire after 7 days.

### Output Validation

After conversion, de-dolby automatically validates the output file:

```bash
# Skip validation for faster batch processing
de-dolby convert movie.mkv --no-validate
```

**Validation checks:**
- File exists and is readable
- Video stream present
- HDR10 metadata (SMPTE 2084 transfer, BT.2020 primaries)
- Mastering display metadata
- Content light level (MaxCLL/MaxFALL)
- Size ratio compared to input (warnings for significant differences)

### Watch Mode

Monitor a directory and automatically convert new Dolby Vision files:

```bash
# Watch current directory
de-dolby watch .

# Watch with custom output directory
de-dolby watch /mnt/media --output-dir /mnt/converted

# Watch recursively (subdirectories too)
de-dolby watch /mnt/media --recursive

# Customize check interval (default: 5 seconds)
de-dolby watch /mnt/media --interval 10

# Move original to subdirectory after conversion
de-dolby watch /mnt/media --move-original

# Reprocess files already converted
de-dolby watch /mnt/media --reprocess
```

Watch mode waits for files to stabilize (finish copying) before processing and maintains state to avoid reprocessing.

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
ffmpeg -version && dovi_tool --version && mkvmerge --version && de-dolby --version
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
ffmpeg -version && dovi_tool --version && mkvmerge --version && de-dolby --version
```

</details>

### Docker

Use the provided Docker image for a containerized environment:

```bash
# Build the image
docker build -t de-dolby .

# Run a conversion
docker run --rm -v $(pwd)/videos:/videos de-dolby convert /videos/movie.mkv

# Using docker-compose
docker-compose run --rm de-dolby convert /videos/movie.mkv
```

**docker-compose profiles:**

| Profile | Use Case | Command |
|:--------|:---------|:--------|
| (default) | Interactive/single file | `docker-compose run --rm de-dolby ...` |
| `batch` | Batch conversion | `docker-compose --profile batch run de-dolby-batch` |
| `gpu` | GPU-accelerated (VAAPI) | `docker-compose --profile gpu run de-dolby-gpu` |
| `dev` | Development shell | `docker-compose --profile dev run de-dolby-dev` |

**GPU passthrough (AMD/Intel via VAAPI):**

```bash
docker run --rm --device /dev/dri:/dev/dri \
  --group-add video --group-add render \
  -v $(pwd)/videos:/videos \
  de-dolby convert /videos/movie.mkv --encoder hevc_vaapi
```

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
  --no-validate             Skip output validation
  --resume                  Resume from interrupted conversion
  --workers N               Parallel workers for batch: N or 'auto'
  --skip-errors             Continue processing other files if one fails
  --audio-lang LANGS        Audio languages to keep (e.g., 'eng,jpn')
  --subtitle-lang LANGS     Subtitle languages to keep (e.g., 'eng')
  --no-audio                Strip all audio tracks
  --no-subtitles            Strip all subtitle tracks
  --no-keep-first-audio     Don't force keeping first audio track
  --no-keep-first-subtitle  Don't force keeping first subtitle track
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary

de-dolby config [options]
  --init                    Create an example config file
  --show                    Show current config file path and contents

de-dolby clean-state [options]
  --all                     Remove all state files (not just old ones)
  --temp-dir PATH           Directory where state files are stored

de-dolby info <file> [<file> ...] [options]
  --json                    Output in JSON format for scripting
  --pretty                  Pretty-print JSON output (requires --json)
  --ffmpeg PATH             Path to ffprobe binary

de-dolby estimate <file> [options]
  --encoder ENCODER         Video encoder to estimate with
  --quality PRESET          Quality preset for estimation

de-dolby watch <path> [options]
  --output-dir DIR          Directory for converted files
  --recursive               Watch subdirectories too
  --interval N              Check interval in seconds (default: 5)
  --delay N                 Wait N seconds after file appears (default: 10)
  --pattern PATTERN         File pattern to watch (default: *.mkv)
  --move-original           Move original to subdirectory after conversion
  --reprocess               Reprocess files already in state
```

---

<h2 id="troubleshooting">Troubleshooting</h2>

| Error | Solution |
|:------|:---------|
| **"encoder not available"** | Use `--encoder auto` or fall back to `--encoder libx265` |
| **"required tools not found on PATH"** | Check with `which ffmpeg dovi_tool mkvmerge` (Linux) or `where` (Windows) |
| **"No Dolby Vision metadata detected"** | File isn't DV. Run `de-dolby info` to verify |
| **Large temp files** | 4K intermediates can be 50+ GB. Use `--temp-dir /path/with/space` |
| **VAAPI permission denied** | Add user to render/video group: `sudo usermod -aG render,video $USER` |
| **Conversion interrupted** | Use `--resume` to continue from where it left off |
| **Out of disk space during batch** | Use `--workers 1` for sequential processing or set `--temp-dir` to a larger drive |
| **Wrong audio tracks in output** | Use `--audio-lang` to filter and `--keep-first-audio` to ensure primary track |

---

<p align="center">
  <sub>Built with ffmpeg, dovi_tool, and mkvmerge</sub>
</p>
