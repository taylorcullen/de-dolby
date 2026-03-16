# de-dolby

Convert Dolby Vision MKV files to HDR10 MKV files. Designed for Windows with an AMD 7900XTX GPU.

## How it works

- **DV Profile 7/8** — Lossless conversion. Strips Dolby Vision RPU metadata and remuxes with HDR10 static metadata. No re-encoding, no quality loss.
- **DV Profile 5** — Re-encodes using AMD hardware encoding (`hevc_amf`) or software (`libx265` fallback). Required because Profile 5 uses a proprietary color space incompatible with standard HDR10 displays.

## Prerequisites

### External tools

**Install ffmpeg and MKVToolNix via winget:**

```powershell
winget install -e --id Gyan.FFmpeg
winget install -e --id MoritzBunkus.MKVToolNix
```

**Install dovi_tool** (not available on winget — manual download required):

1. Download the latest Windows release from [github.com/quietvoid/dovi_tool/releases](https://github.com/quietvoid/dovi_tool/releases) (get `dovi_tool-x86_64-pc-windows-msvc.zip`)
2. Extract `dovi_tool.exe` to a permanent location, e.g. `C:\tools\`

**Add everything to PATH:**

After installing, add the tool directories to your user PATH. Run this in PowerShell:

```powershell
# Find where winget installed ffmpeg
# Typically: C:\Users\<you>\AppData\Local\Microsoft\WinGet\Links\
# The Gyan.FFmpeg package extracts to something like C:\ffmpeg\bin — check with:
where.exe ffmpeg

# MKVToolNix installs to C:\Program Files\MKVToolNix\ by default
# Its installer usually adds itself to PATH — verify with:
where.exe mkvmerge

# Add dovi_tool location (adjust the path to where you extracted it)
$doviPath = "C:\tools"
$currentPath = [Environment]::GetEnvironmentVariable("Path", "User")
if ($currentPath -notlike "*$doviPath*") {
    [Environment]::SetEnvironmentVariable("Path", "$currentPath;$doviPath", "User")
}
```

Restart your terminal after modifying PATH, then verify all tools are accessible:

```powershell
ffmpeg -version
dovi_tool --version
mkvmerge --version
```

> **Alternatively**, skip PATH setup and pass tool locations explicitly:
> `de-dolby convert movie.mkv --ffmpeg "C:\ffmpeg\bin\ffmpeg.exe" --dovi-tool "C:\tools\dovi_tool.exe" --mkvmerge "C:\Program Files\MKVToolNix\mkvmerge.exe"`

### Python setup

Requires Python 3.10 or newer.

**Install Python (if not already installed):**

Download from [python.org/downloads](https://www.python.org/downloads/) — during install, check **"Add python.exe to PATH"**.

Or via winget:

```powershell
winget install Python.Python.3.12
```

Verify:

```powershell
python --version
```

**Create and activate a virtual environment:**

```powershell
# Navigate to the project directory
cd D:\Repos\de-dolby

# Create the virtual environment
python -m venv .venv

# Activate it
.venv\Scripts\Activate.ps1
```

> If you get an execution policy error in PowerShell, run:
> `Set-ExecutionPolicy -ExecutionPolicy RemoteSigned -Scope CurrentUser`
>
> If using Command Prompt instead of PowerShell:
> `.venv\Scripts\activate.bat`

**Install de-dolby:**

```powershell
pip install -e .
```

## Usage

### Convert a file

```powershell
de-dolby convert movie.mkv
```

The tool auto-detects the Dolby Vision profile and chooses the right pipeline. For Profile 7/8 it does a fast lossless strip. For Profile 5 it re-encodes using your AMD GPU.

Output filenames are generated automatically: `.DV.` in the filename is replaced with `.HDR10.`, otherwise `.HDR10` is inserted before the extension. For example:

| Input | Output |
|---|---|
| `2x03 - Secrets.DV.mkv` | `2x03 - Secrets.HDR10.mkv` |
| `2x03 - Secrets.mkv` | `2x03 - Secrets.HDR10.mkv` |

You can override with `-o`:

```powershell
de-dolby convert movie.mkv -o custom-name.mkv
```

### Convert multiple files

Pass multiple files to convert them in one batch:

```powershell
de-dolby convert *.mkv
de-dolby convert episode1.mkv episode2.mkv episode3.mkv
```

If a file fails, the remaining files still process. A summary of failures is printed at the end. Note: `-o` cannot be used with multiple files.

### Quick quality test with sample mode

Convert only the first N seconds to check output quality before committing to a full conversion:

```powershell
# Default: first 30 seconds
de-dolby convert movie.mkv --sample

# Custom duration
de-dolby convert movie.mkv --sample 60
```

### Inspect files

```powershell
de-dolby info movie.mkv
de-dolby info *.mkv
```

Shows Dolby Vision profile, HDR metadata, video/audio/subtitle streams, and bitrate in a neofetch-style display.

### All options

```
de-dolby convert <file> [<file> ...] [options]

  -o, --output PATH         Output file (single input only)
  --encoder {auto,hevc_amf,libx265,copy}
                            Video encoder (default: auto)
  --quality {fast,balanced,quality}
                            Encoder preset (default: balanced)
  --crf INT                 CRF for libx265 (overrides preset default)
  --bitrate STR             Target bitrate for hevc_amf, e.g. "40M"
  --sample [SECONDS]        Convert only first N seconds (default: 30)
  --dry-run                 Show steps without executing
  -v, --verbose             Show detailed output including ffmpeg commands
  --force                   Overwrite existing output file
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary
```

### Examples

```powershell
# Convert a single file (auto-generates output name)
de-dolby convert movie.DV.mkv

# Batch convert all MKVs in a directory
de-dolby convert *.mkv

# GPU encode with high quality preset
de-dolby convert movie.mkv --encoder hevc_amf --quality quality

# Software encode with custom CRF
de-dolby convert movie.mkv --encoder libx265 --crf 16 --quality slow

# Test 60s sample with GPU, then do the full conversion
de-dolby convert movie.mkv --sample 60
de-dolby convert movie.mkv --force

# See what would happen without running anything
de-dolby convert movie.mkv --dry-run

# Specify tool paths explicitly
de-dolby convert movie.mkv --ffmpeg "C:\tools\ffmpeg.exe" --dovi-tool "C:\tools\dovi_tool.exe"
```

## Troubleshooting

**"hevc_amf not available"** — Your ffmpeg build may not include AMF support. Download the full build from [gyan.dev](https://www.gyan.dev/ffmpeg/builds/). The tool will automatically fall back to libx265 (slower but works without GPU).

**"required tools not found on PATH"** — Make sure ffmpeg, dovi_tool, and mkvmerge are accessible. Run `where ffmpeg`, `where dovi_tool`, `where mkvmerge` in your terminal to check.

**"No Dolby Vision metadata detected"** — The input file may not actually contain Dolby Vision. Use `de-dolby info` to inspect the file.

**Large temp files** — 4K HEVC streams can be 50+ GB as raw bitstream. Ensure your system temp directory (`%TEMP%`) has enough free space. The tool cleans up temp files after completion.
