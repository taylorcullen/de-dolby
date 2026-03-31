# Multi-stage Dockerfile for de-dolby
# Builds a container with ffmpeg, dovi_tool, mkvmerge, and de-dolby

# =============================================================================
# Stage 1: Tool Downloader
# =============================================================================
FROM python:3.11-slim AS downloader

WORKDIR /downloads

# Install download dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    wget \
    curl \
    ca-certificates \
    tar \
    xz-utils \
    && rm -rf /var/lib/apt/lists/*

# Download dovi_tool (latest release)
RUN DOVI_VERSION=$(curl -s https://api.github.com/repos/quietvoid/dovi_tool/releases/latest | grep '"tag_name":' | sed -E 's/.*"([^"]+)".*/\1/') && \
    echo "Downloading dovi_tool ${DOVI_VERSION}" && \
    wget -q "https://github.com/quietvoid/dovi_tool/releases/download/${DOVI_VERSION}/dovi_tool-${DOVI_VERSION}-x86_64-unknown-linux-gnu.tar.gz" -O dovi_tool.tar.gz && \
    tar -xzf dovi_tool.tar.gz && \
    chmod +x dovi_tool && \
    ./dovi_tool --version

# Download MKVToolNix (latest release - static build)
RUN MKV_VERSION=$(curl -s https://mkvtoolnix.download/latest-release.xml.gz | gunzip 2>/dev/null | grep -oP 'version="\K[0-9.]+' | head -1 || echo "84.0") && \
    echo "Downloading MKVToolNix ${MKV_VERSION}" && \
    wget -q "https://mkvtoolnix.download/appimage/MKVToolNix-gui-${MKV_VERSION}-x86_64.AppImage" -O mkvtoolnix.AppImage || \
    wget -q "https://mkvtoolnix.download/appimage/MKVToolNix-gui-84.0-x86_64.AppImage" -O mkvtoolnix.AppImage && \
    chmod +x mkvtoolnix.AppImage && \
    ./mkvtoolnix.AppImage --appimage-extract >/dev/null 2>&1 && \
    mv squashfs-root/usr/bin/mkv* . && \
    mv squashfs-root/usr/bin/mmg . 2>/dev/null || true && \
    ./mkvmerge --version

# =============================================================================
# Stage 2: Runtime Image
# =============================================================================
FROM python:3.11-slim AS runtime

LABEL maintainer="de-dolby"
LABEL description="Convert Dolby Vision MKV files to HDR10"

# Install runtime dependencies
# ffmpeg: video processing
# libexpat1: required by dovi_tool
# libxml2: required for metadata parsing
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    ffprobe \
    libexpat1 \
    libxml2 \
    && rm -rf /var/lib/apt/lists/* \
    && apt-get clean

# Copy tools from downloader stage
COPY --from=downloader /downloads/dovi_tool /usr/local/bin/
COPY --from=downloader /downloads/mkvmerge /usr/local/bin/
COPY --from=downloader /downloads/mkvinfo /usr/local/bin/ 2>/dev/null || true
COPY --from=downloader /downloads/mkvextract /usr/local/bin/ 2>/dev/null || true
COPY --from=downloader /downloads/mkvpropedit /usr/local/bin/ 2>/dev/null || true

# Verify tools are available
RUN ffmpeg -version | head -1 && \
    ffprobe -version | head -1 && \
    dovi_tool --version && \
    mkvmerge --version

# Set up application directory
WORKDIR /app

# Copy and install de-dolby
COPY pyproject.toml ./
COPY de_dolby/ ./de_dolby/

# Install the package
RUN pip install --no-cache-dir -e . && \
    de-dolby --version

# Create working directory for conversions
WORKDIR /videos
VOLUME ["/videos"]

# Set environment variables for tool paths (can be overridden)
ENV FFMPEG_PATH=/usr/bin/ffmpeg
ENV FFPROBE_PATH=/usr/bin/ffprobe
ENV DOVI_TOOL_PATH=/usr/local/bin/dovi_tool
ENV MKVMERGE_PATH=/usr/local/bin/mkvmerge

# Default to CPU encoder since GPU passthrough requires special setup
ENV DEFAULT_ENCODER=libx265

# Entrypoint
ENTRYPOINT ["de-dolby"]
CMD ["--help"]
