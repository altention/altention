"""Claude Code's /btw side question, what is the tool's own about it besides its box on the screen (screen.py):
whether the full answer can be had. The full answer, in its own markdown, comes from pressing "c" in the box:
Claude Code copies it with OSC 52, which a tmux server keeps as a buffer only with `set-clipboard on`. From the
bot's cc_btw.py as it was (stage 3b step 1.2); the line said to the owner when it could not be copied stays there."""


def copies(set_clipboard):
    """Whether a tmux server whose `set-clipboard` option reads this keeps what Claude Code copies. Anything but
    "on" ("external", "off", nothing read): no, the screen is read instead."""
    return (set_clipboard or "").strip() == "on"
