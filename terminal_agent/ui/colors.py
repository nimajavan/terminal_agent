"""
ANSI terminal color and formatting utilities.
Works with zero external dependencies and respects NO_COLOR / dumb terminals.
"""

import os
import sys

def _supports_color() -> bool:
    """Check if the current terminal supports color output."""
    if os.environ.get("NO_COLOR"):
        return False
    if os.environ.get("TERM") == "dumb":
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()

_COLOR_ENABLED = _supports_color()

def set_color_enabled(enabled: bool) -> None:
    global _COLOR_ENABLED
    _COLOR_ENABLED = enabled

def _c(code: str, text: str) -> str:
    if not _COLOR_ENABLED:
        return text
    return f"\033[{code}m{text}\033[0m"

def bold(text: str) -> str:
    return _c("1", text)

def dim(text: str) -> str:
    return _c("2", text)

def italic(text: str) -> str:
    return _c("3", text)

def underline(text: str) -> str:
    return _c("4", text)

def red(text: str) -> str:
    return _c("31", text)

def green(text: str) -> str:
    return _c("32", text)

def yellow(text: str) -> str:
    return _c("33", text)

def blue(text: str) -> str:
    return _c("34", text)

def magenta(text: str) -> str:
    return _c("35", text)

def cyan(text: str) -> str:
    return _c("36", text)

def gray(text: str) -> str:
    return _c("90", text)

def bg_red(text: str) -> str:
    return _c("41;97;1", text)

def bg_yellow(text: str) -> str:
    return _c("43;30;1", text)

def badge_safe() -> str:
    return green("✔ [SAFE]")

def badge_caution() -> str:
    return yellow("⚠ [CAUTION]")

def badge_danger() -> str:
    return red("⚡ [DANGEROUS]")

def badge_critical() -> str:
    return bg_red(" ⛔ [CRITICAL / BLOCKED] ")
