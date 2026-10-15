"""Claude Code's /btw answer, the parts that are Claude Code's own: whether the full answer can be had, and the
line said when it could not (DESIGN 5.1, 2026-10-07). The check itself (does that tmux server keep what Claude Code copies) is the connector's
(altention/connectors/claude_code/btw.py, stage 3b step 1.2); the sentence for the owner is the bot's, here.

The full answer, in its own markdown, comes from pressing "c" in the overlay: Claude Code copies it with OSC 52,
which a tmux server keeps as a buffer only with `set-clipboard on`. The bot's own tmux server has it; a pane the
owner attached (/attach) is on the owner's server, left as the owner set it (the system never changes the owner's
tmux; how to turn it on is told only when the owner asks), and then the answer is read off the screen.
"""

SCREEN_NOTE = "從畫面讀的答案：表格可能走樣，長的答案可能只有畫面上看得到的部分。"


def screen_note():
    """The line posted under an answer read off the screen instead of copied."""
    return SCREEN_NOTE
