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

# Extract a tone-mapped SDR frame for visual check
de-dolby preview movie.mkv --time 00:05:00
```

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
  --ffmpeg PATH             Path to ffmpeg binary
  --dovi-tool PATH          Path to dovi_tool binary
  --mkvmerge PATH           Path to mkvmerge binary
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

---

<p align="center">
  <sub>Built with ffmpeg, dovi_tool, and mkvmerge</sub>
</p>
