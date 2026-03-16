"""Encoder presets and default settings."""

HEVC_AMF_PRESETS = {
    "fast": {
        "quality": "speed",
        "rc": "vbr_latency",
        "profile": "main10",
    },
    "balanced": {
        "quality": "balanced",
        "rc": "vbr_peak",
        "profile": "main10",
    },
    "quality": {
        "quality": "quality",
        "rc": "vbr_peak",
        "profile": "main10",
    },
}

LIBX265_PRESETS = {
    "fast": {"preset": "fast", "crf": 20},
    "balanced": {"preset": "medium", "crf": 18},
    "quality": {"preset": "slow", "crf": 16},
}

AV1_AMF_PRESETS = {
    "fast": {
        "quality": "speed",
        "rc": "vbr_latency",
    },
    "balanced": {
        "quality": "balanced",
        "rc": "vbr_peak",
    },
    "quality": {
        "quality": "quality",
        "rc": "vbr_peak",
    },
}

LIBSVTAV1_PRESETS = {
    "fast": {"preset": 8, "crf": 30},
    "balanced": {"preset": 6, "crf": 25},
    "quality": {"preset": 4, "crf": 20},
}

# VAAPI (Linux AMD/Intel)
HEVC_VAAPI_PRESETS = {
    "fast": {"compression_level": 1, "rc_mode": "VBR"},
    "balanced": {"compression_level": 16, "rc_mode": "VBR"},
    "quality": {"compression_level": 32, "rc_mode": "VBR"},
}

AV1_VAAPI_PRESETS = {
    "fast": {"compression_level": 1, "rc_mode": "VBR"},
    "balanced": {"compression_level": 16, "rc_mode": "VBR"},
    "quality": {"compression_level": 32, "rc_mode": "VBR"},
}

# NVENC (Linux/Windows NVIDIA)
HEVC_NVENC_PRESETS = {
    "fast": {"preset": "p4", "tune": "hq", "rc": "vbr"},
    "balanced": {"preset": "p5", "tune": "hq", "rc": "vbr"},
    "quality": {"preset": "p7", "tune": "hq", "rc": "vbr"},
}

AV1_NVENC_PRESETS = {
    "fast": {"preset": "p4", "tune": "hq", "rc": "vbr"},
    "balanced": {"preset": "p5", "tune": "hq", "rc": "vbr"},
    "quality": {"preset": "p7", "tune": "hq", "rc": "vbr"},
}

# Standard BT.2020 primaries used for HDR10 mastering display fallback
# Format: G(x,y)B(x,y)R(x,y)WP(x,y)L(max,min) in 1/50000 units for xy, 1/10000 cd/m² for L
DEFAULT_MASTER_DISPLAY = "G(13250,34500)B(7500,3000)R(34000,16000)WP(15635,16450)L(10000000,1)"
DEFAULT_MAX_CLL = 1000
DEFAULT_MAX_FALL = 400
