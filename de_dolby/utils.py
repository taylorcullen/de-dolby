"""Shared utilities: ANSI colors, formatting helpers."""


class Colors:
    """ANSI escape codes for terminal output."""
    RESET = "\033[0m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    GREEN = "\033[32m"
    BRIGHT_GREEN = "\033[92m"
    CYAN = "\033[36m"
    BRIGHT_CYAN = "\033[96m"
    MAGENTA = "\033[35m"
    BRIGHT_MAGENTA = "\033[95m"
    WHITE = "\033[97m"
    YELLOW = "\033[33m"
    BRIGHT_YELLOW = "\033[93m"
    BLUE = "\033[34m"
    BRIGHT_BLUE = "\033[94m"
    RED = "\033[31m"


def format_bytes(n: int | float) -> str:
    """Format a byte count as a human-readable string (e.g. '4.2 GB')."""
    val = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if val < 1024:
            return f"{val:.1f} {unit}"
        val /= 1024
    return f"{val:.1f} PB"


def format_duration(seconds: float | None) -> str:
    """Format seconds as h:mm:ss. Returns 'unknown' if None."""
    if seconds is None:
        return "unknown"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}"
