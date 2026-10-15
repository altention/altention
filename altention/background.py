"""The mark of a background Claude: one started by a part of the engine for a job of its own (a progress summary,
asking Claude Code what commands it has), not a conversation of the owner's. Whoever starts one gives it env();
a hook that every Claude runs asks marked() first and leaves such a one alone. One name for every part, so that a
part starting one need not know which others would otherwise act on it. Standard library only."""
import os

NAME = "ALTENTION_BACKGROUND"


def env(base=None):
    """The environment for a background Claude: `base` (or this process's own) with the mark."""
    out = dict(os.environ if base is None else base)
    out[NAME] = "1"
    return out


def marked(environ=None):
    """Is this process (or one with `environ`) a background Claude, or something one started?"""
    return bool((os.environ if environ is None else environ).get(NAME))
