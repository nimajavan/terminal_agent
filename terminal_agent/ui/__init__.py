"""UI package for terminal formatting and interaction."""
from terminal_agent.ui.colors import (
    bold, dim, italic, underline, red, green, yellow, blue,
    magenta, cyan, gray, bg_red, bg_yellow, badge_safe,
    badge_caution, badge_danger, badge_critical, set_color_enabled
)

__all__ = [
    "bold", "dim", "italic", "underline", "red", "green", "yellow", "blue",
    "magenta", "cyan", "gray", "bg_red", "bg_yellow", "badge_safe",
    "badge_caution", "badge_danger", "badge_critical", "set_color_enabled"
]
