"""
Colorblind-friendly color palette for Flamingo Control GUI.

This module provides centralized color constants using IBM Design Language
and Paul Tol's colorblind-safe palettes. These colors are optimized for
accessibility and distinguishability for users with various forms of color
vision deficiency.

Color Palette Sources:
- IBM Design Language: https://www.ibm.com/design/language/color
- Paul Tol's palettes: https://personal.sron.nl/~pault/

Usage:
    from py2flamingo.views.colors import (
        SUCCESS_COLOR, ERROR_COLOR, WARNING_COLOR,
        POSITIVE_JOG_COLOR, NEGATIVE_JOG_COLOR
    )
"""

# ============================================================================
# Primary Semantic Colors (IBM Design Language)
# ============================================================================

# Success/Active/Connected state - Blue (replaces green)
SUCCESS_COLOR = "#0f62fe"  # IBM Blue 60
SUCCESS_COLOR_LIGHT = "#4589ff"  # IBM Blue 50
SUCCESS_COLOR_DARK = "#0043ce"  # IBM Blue 70

# Error/Danger/Critical state - Red-Orange (enhanced red)
ERROR_COLOR = "#da1e28"  # IBM Red 60
ERROR_COLOR_LIGHT = "#ff6f6f"  # IBM Red 50
ERROR_COLOR_DARK = "#a2191f"  # IBM Red 70

# Warning/Moving/In-Progress state - Gold/Amber
WARNING_COLOR = "#f1c21b"  # IBM Yellow 30
WARNING_COLOR_LIGHT = "#fddc69"  # IBM Yellow 20
WARNING_COLOR_DARK = "#d2a106"  # IBM Yellow 40

# Neutral/Inactive/Disconnected state - Gray
NEUTRAL_COLOR = "#8d8d8d"  # IBM Gray 50
NEUTRAL_COLOR_LIGHT = "#c6c6c6"  # IBM Gray 30
NEUTRAL_COLOR_DARK = "#525252"  # IBM Gray 70

# ============================================================================
# Directional/Jog Colors (Tol Bright Palette)
# ============================================================================

# Positive direction (up/right/forward) - Teal/Cyan
POSITIVE_JOG_COLOR = "#bae6ff"  # IBM Cyan 20 (light teal)
POSITIVE_JOG_COLOR_MEDIUM = "#82cfff"  # IBM Cyan 30
POSITIVE_JOG_COLOR_DARK = "#1192e8"  # IBM Cyan 60

# Negative direction (down/left/backward) - Purple/Magenta
NEGATIVE_JOG_COLOR = "#e8daff"  # IBM Purple 20 (light purple)
NEGATIVE_JOG_COLOR_MEDIUM = "#d4bbff"  # IBM Purple 30
NEGATIVE_JOG_COLOR_DARK = "#a56eff"  # IBM Purple 50

# ============================================================================
# Additional UI Colors
# ============================================================================

# Information/Hint state - Teal (distinct from success blue)
INFO_COLOR = "#08bdba"  # IBM Teal 50
INFO_COLOR_LIGHT = "#3ddbd9"  # IBM Teal 40
INFO_COLOR_DARK = "#009d9a"  # IBM Teal 60

# Background colors for states
SUCCESS_BG = "#e5f6ff"  # Very light blue
ERROR_BG = "#fff1f1"  # Very light red
WARNING_BG = "#fcf4d6"  # Very light yellow
NEUTRAL_BG = "#f4f4f4"  # Light gray

# ============================================================================
# Channel/Visualization Colors (for microscopy)
# ============================================================================

# Fluorescence channels - using distinguishable colors
CHANNEL_BLUE = "#0f62fe"  # DAPI/Hoechst - IBM Blue 60
CHANNEL_CYAN = "#1192e8"  # CFP - IBM Cyan 60
CHANNEL_GREEN = "#24a148"  # GFP (kept green for biological accuracy) - IBM Green 60
CHANNEL_YELLOW = "#f1c21b"  # YFP - IBM Yellow 30
CHANNEL_ORANGE = "#ff832b"  # mOrange - IBM Orange 50
CHANNEL_RED = "#da1e28"  # RFP/mCherry - IBM Red 60
CHANNEL_MAGENTA = "#d12771"  # IBM Magenta 60
CHANNEL_PURPLE = "#a56eff"  # IBM Purple 50

# ============================================================================
# Qt Stylesheet Helpers
# ============================================================================

def get_button_stylesheet(bg_color: str, hover_color: str = None,
                          text_color: str = "white") -> str:
    """
    Generate a Qt stylesheet for a button with the given colors.

    Args:
        bg_color: Background color (hex)
        hover_color: Hover state color (hex), defaults to slightly darker
        text_color: Text color (hex or name)

    Returns:
        Qt stylesheet string
    """
    if hover_color is None:
        # Darken by approximately 10% if no hover color provided
        hover_color = bg_color

    return f"""
        QPushButton {{
            background-color: {bg_color};
            color: {text_color};
            border: none;
            padding: 5px;
            border-radius: 3px;
        }}
        QPushButton:hover {{
            background-color: {hover_color};
        }}
        QPushButton:pressed {{
            background-color: {hover_color};
        }}
        QPushButton:disabled {{
            background-color: {NEUTRAL_COLOR};
            color: {NEUTRAL_COLOR_LIGHT};
        }}
    """

def get_label_stylesheet(bg_color: str, text_color: str = "white",
                         border: bool = True) -> str:
    """
    Generate a Qt stylesheet for a label/indicator with the given colors.

    Args:
        bg_color: Background color (hex)
        text_color: Text color (hex or name)
        border: Whether to include a border

    Returns:
        Qt stylesheet string
    """
    border_style = "border: 1px solid #000000;" if border else "border: none;"

    return f"""
        QLabel {{
            background-color: {bg_color};
            color: {text_color};
            {border_style}
            padding: 3px;
            border-radius: 3px;
        }}
    """

def get_status_color(status: str) -> str:
    """
    Get the appropriate color for a given status string.

    Args:
        status: Status string (e.g., "connected", "error", "moving")

    Returns:
        Hex color code
    """
    status_lower = status.lower()

    if status_lower in ["connected", "active", "ready", "success", "on"]:
        return SUCCESS_COLOR
    elif status_lower in ["error", "failed", "critical", "alarm"]:
        return ERROR_COLOR
    elif status_lower in ["moving", "busy", "warning", "pending"]:
        return WARNING_COLOR
    elif status_lower in ["disconnected", "inactive", "idle", "off"]:
        return NEUTRAL_COLOR
    else:
        return NEUTRAL_COLOR

# ============================================================================
# Legacy Color Mappings (for gradual migration)
# ============================================================================

# Map old color names to new ones for backward compatibility
COLOR_MAP = {
    "green": SUCCESS_COLOR,
    "#00ff00": SUCCESS_COLOR,
    "#00FF00": SUCCESS_COLOR,
    "lime": SUCCESS_COLOR,

    "red": ERROR_COLOR,
    "#ff0000": ERROR_COLOR,
    "#FF0000": ERROR_COLOR,

    "yellow": WARNING_COLOR,
    "#ffff00": WARNING_COLOR,
    "#FFFF00": WARNING_COLOR,

    "gray": NEUTRAL_COLOR,
    "grey": NEUTRAL_COLOR,
}

def map_legacy_color(color: str) -> str:
    """
    Map legacy color values to new colorblind-friendly colors.

    Args:
        color: Original color string

    Returns:
        Mapped color string
    """
    return COLOR_MAP.get(color.lower(), color)
