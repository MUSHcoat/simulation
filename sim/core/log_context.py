#!/usr/bin/env python3
"""
Thread-local stage context for simulation logging.

Each phase of the simulation calls log_stage() before making LLM calls.
The formatter in main.py reads get_stage_color() to color subsequent lines
with the active stage's color, and renders stage headers in bright white bold.

Usage:
    from .log_context import log_stage, actor_color, YELLOW, GREEN, CYAN

    log_stage(logger, "Actor Proposal — Claude (Anthropic)", actor_color("Claude (Anthropic)"))
    # all logger.* calls until the next log_stage() appear in orange
"""

import threading

_ctx = threading.local()

# ---------------------------------------------------------------------------
# ANSI color constants
# ---------------------------------------------------------------------------

RESET        = "\033[0m"
BRIGHT_WHITE = "\033[1;97m"    # bold bright white — stage headers
DIM_WHITE    = "\033[97m"      # plain white — neutral / fallback INFO
ORANGE       = "\033[38;5;208m"  # Claude / Anthropic
BLUE         = "\033[38;5;75m"   # Gemini / Google
GRAY         = "\033[38;5;245m"  # GPT / OpenAI
MAGENTA      = "\033[38;5;177m"  # DeepSeek
YELLOW       = "\033[38;5;227m"  # JuryOfAlignment
GREEN        = "\033[38;5;83m"   # GrandJury
CYAN         = "\033[38;5;87m"   # MacroJury
RED          = "\033[91m"        # errors / warnings (formatter-only, not a stage)

# ---------------------------------------------------------------------------
# Actor name → stage color
# ---------------------------------------------------------------------------

_ACTOR_COLORS = {
    "claude":    ORANGE,
    "deepseek":  MAGENTA,
    "gemini":    BLUE,
    "gpt":       GRAY,
}


def actor_color(name: str) -> str:
    """Return the stage color for a given actor name (case-insensitive prefix match)."""
    n = name.lower()
    for key, color in _ACTOR_COLORS.items():
        if key in n:
            return color
    return DIM_WHITE


# ---------------------------------------------------------------------------
# Stage context API
# ---------------------------------------------------------------------------

def set_stage(color: str) -> None:
    """Set the stage color for subsequent log lines on this thread."""
    _ctx.color = color


def get_stage_color() -> str:
    """Return the current stage color, or '' if none is set."""
    return getattr(_ctx, "color", "")


def log_stage(log, label: str, color: str = DIM_WHITE) -> None:
    """
    Emit a bright-white stage header line and set the stage color for all
    subsequent log lines until the next log_stage() call.

    Args:
        log:    a logging.Logger instance
        label:  the stage description to display
        color:  ANSI color for lines that follow this header
    """
    set_stage(color)
    log.info(label, extra={"stage_header": True})
