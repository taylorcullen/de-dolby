# de-dolby

Convert Dolby Vision MKV files to HDR10 MKV files. Cross-platform with GPU-accelerated encoding on Windows (AMD AMF, NVIDIA NVENC) and Linux (VAAPI, NVENC).

## How it works

- **DV Profile 7/8 (HEVC)** — Lossless conversion. Strips Dolby Vision RPU metadata and remuxes with HDR10 static metadata. No re-encoding, no quality loss.
- **DV Profile 5 (HEVC)** — Re-encodes with HDR10 color conversion. Required because Profile 5 uses a proprietary color space incompatible with standard HDR10 displays.
- **DV Profile 10 (AV1)** — Re-encodes AV1 Dolby Vision to HDR10 AV1 or HEVC. Uses ffprobe metadata since dovi_tool doesn't support AV1.

### Supported encoders

| Encoder | Platform | GPU | Type |
|---------|----------|-----|------|
| `hevc_amf` | Windows | AMD | Hardware |
| `av1_amf` | Windows | AMD | Hardware |
| `hevc_nvenc` | Windows/Linux | NVIDIA | Hardware |
| `av1_nvenc` | Windows/Linux | NVIDIA | Hardware |
| `hevc_vaapi` | Linux | AMD/Intel | Hardware |
| `av1_vaapi` | Linux | AMD/Intel | Hardware |
| `libx265` | All | - | Software (CPU) |
| `libsvtav1` | All | - | Software (CPU) |

With `--encoder auto` (the default), de-dolby probes your ffmpeg build and picks the best available encoder in this priority order: AMF → NVENC → VAAPI → CPU fallback.

## Prerequisites

### External tools (all platforms)

Three tools must be on your PATH:

| Tool | Purpose | Download |
|------|---------|----------|
| **ffmpeg** / **ffprobe** | Video decode/encode/mux | [ffmpeg.org](https://ffmpeg.org/download.html) |
| **dovi_tool** | DV RPU extraction and stripping | [github.com/quietvoid/dovi_tool](https://github.com/quietvoid/dovi_tool/releases) |
| **mkvmerge** | Final MKV remux with HDR10 flags | [mkvtoolnix.download](https://mkvtoolnix.download/) |

> You can skip PATH setup and pass tool locations explicitly:
> `de-dolby convert movie.mkv --ffmpeg /path/to/ffmpeg --dovi-tool /path/to/dovi_tool --mkvmerge /path/to/mkvmerge`

---

### Windows setup

**Install ffmpeg and MKVToolNix via winget:**

```powershell
winget install -e --id Gyan.FFmpeg
winget install -e --id MoritzBunkus.MKVToolNix
```

**Install dovi_tool** (manual download):

1. Download the latest Windows release from [dovi_tool releases](https://github.com/quietvoid/dovi_tool/releases) (`dovi_tool-x86_64-pc-windows-msvc.zip`)
2. Extract `dovi_tool.exe` to a permanent location, e.g. `C:\tools\`
3. Add that directory to your PATH:

```powershell
$doviPath = "C:\tools"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$doviPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$doviPath", "User")
}
```

**Verify tools are accessible:**

```powershell
ffmpeg -version
dovi_tool --version
mkvmerge --version
```

**Install de-dolby:**

```powershell
cd D:\Repos\de-dolby
python -m venv .venv
.venv\Scripts\Activate.ps1
pip install -e .
```

> If you get a PowerShell execution policy error:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`

---

### Linux setup

**Install ffmpeg, MKVToolNix, and dovi_tool:**

Ubuntu / Debian:

```bash
sudo apt install ffmpeg mkvtoolnix
```

Fedora:

```bash
sudo dnf install ffmpeg mkvtoolnix
```

Arch:

```bash
sudo pacman -S ffmpeg mkvtoolnix-cli
```

For dovi_tool, download the Linux binary from [dovi_tool releases](https://github.com/quietvoid/dovi_tool/releases) and place it on your PATH:

```bash
# Example: install to ~/.local/bin
wget https://github.com/quietvoid/dovi_tool/releases/latest/download/dovi_tool-x86_64-unknown-linux-musl.tar.gz
tar xzf dovi_tool-x86_64-unknown-linux-musl.tar.gz
mv dovi_tool ~/.local/bin/
```

**For VAAPI GPU encoding (AMD/Intel):**

```bash
# Ubuntu/Debian
sudo apt install vainfo libva-dev

# Verify VAAPI is working
vainfo
```

**For NVENC GPU encoding (NVIDIA):**

Ensure you have the NVIDIA proprietary drivers installed and ffmpeg built with NVENC support. Most distro ffmpeg packages include NVENC; verify with:

```bash
ffmpeg -encoders 2>/dev/null | grep nvenc
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
ffmpeg -version
dovi_tool --version
mkvmerge --version
de-dolby --version
```

---

## Usage

### Convert a file

```bash
de-dolby convert movie.mkv
```

The tool auto-detects the Dolby Vision profile and chooses the right pipeline. For Profile 7/8 it does a fast lossless strip. For Profile 5 and 10 it re-encodes using the best available GPU encoder.

Output filenames are generated automatically: `.DV.` in the filename is replaced with `.HDR10.`, otherwise `.HDR10` is inserted before the extension.

| Input | Output |
|---|---|
| `movie.DV.mkv` | `movie.HDR10.mkv` |
| `movie.mkv` | `movie.HDR10.mkv` |

### Convert multiple files

```bash
de-dolby convert *.mkv
de-dolby convert episode1.mkv episode2.mkv episode3.mkv
```

Shows per-file progress with ETA and a batch summary at the end.

### Choose an encoder

```bash
# Auto-detect best available (default)
de-dolby convert movie.mkv

# Explicit GPU encoder
de-dolby convert movie.mkv --encoder hevc_amf      # Windows AMD
de-dolby convert movie.mkv --encoder hevc_vaapi     # Linux AMD/Intel
de-dolby convert movie.mkv --encoder hevc_nvenc     # NVIDIA
de-dolby convert movie.mkv --encoder av1_nvenc      # NVIDIA AV1

# CPU encoder (works everywhere, slower)
de-dolby convert movie.mkv --encoder libx265
de-dolby convert movie.mkv --encoder libsvtav1
```

### Quality presets

```bash
de-dolby convert movie.mkv --quality fast       # Fastest, larger file
de-dolby convert movie.mkv --quality balanced   # Default
de-dolby convert movie.mkv --quality quality    # Slowest, best quality
```

Fine-tune with `--crf` (CPU encoders) or `--bitrate` (GPU encoders):

```bash
de-dolby convert movie.mkv --encoder libx265 --crf 16
de-dolby convert movie.mkv --encoder hevc_nvenc --bitrate 60M
```

### Quick quality test

Convert only the first N seconds to check output quality:

```bash
de-dolby convert movie.mkv --sample        # First 30 seconds
de-dolby convert movie.mkv --sample 60     # First 60 seconds
```

### Inspect files

```bash
de-dolby info movie.mkv
de-dolby info *.mkv
```

### Preview a frame

Extract a single tone-mapped frame as PNG:

```bash
de-dolby preview movie.mkv --time 00:05:00
```

### All options

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
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary
```

## Troubleshooting

**"encoder not available in your ffmpeg build"** — The requested GPU encoder isn't in your ffmpeg. Use `--encoder auto` to pick the best available, or fall back to `--encoder libx265` (CPU).

**"required tools not found on PATH"** — Run `which ffmpeg dovi_tool mkvmerge` (Linux) or `where ffmpeg dovi_tool mkvmerge` (Windows) to check.

**"No Dolby Vision metadata detected"** — The input file doesn't contain DV. Use `de-dolby info` to inspect.

**Large temp files** — 4K HEVC intermediates can be 50+ GB. Use `--temp-dir /path/with/space` to point at a drive with room.

**VAAPI permission denied** — On Linux, your user may need to be in the `render` or `video` group:

```bash
sudo usermod -aG render $USER
sudo usermod -aG video $USER
# Log out and back in
```
