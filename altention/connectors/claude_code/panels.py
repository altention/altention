"""Claude Code's panels read off the screen, as plain data (the third, generic layer of the interaction format in
docs/refactor/interaction-inventory.md section 4): nearly every panel ends with a hint line naming the keys it
takes ("Enter to set as default · s to use this session only · Esc to cancel"), so a panel no one has described
can still be worked from Discord. Pure functions, no Discord and no tmux (the bot's cc_panels.py until stage 3b
step 1.2). The core only renders what parse_panel() and parse_menu() return.

Only keys the hint line itself names become actions. Number keys are never offered here: on some panels a
number confirms at once in a way the hint does not say (/model sets the default), so they stay with the
notification menus (menus.py), where they are known to pick an option.
"""
import re

from . import screen

# A key as the hint line writes it -> tmux key names. Anything else ("Type", "Space bar"…) is not offered.
KEYS = {"enter": "Enter", "return": "Enter", "esc": "Escape", "escape": "Escape", "tab": "Tab", "space": "Space",
        "shift+tab": "BTab", "↑": "Up", "↓": "Down", "←": "Left", "→": "Right", "backspace": "BSpace",
        "delete": "DC", "pgup": "PageUp", "pgdn": "PageDown", "home": "Home", "end": "End"}
PAIRS = {("↑", "↓"), ("↓", "↑"), ("←", "→"), ("→", "←")}  # "←/→ to switch": two buttons, one per direction
SEGMENT = re.compile(r"^(?P<keys>\S+(?:\s*/\s*\S+)*)\s+to\s+(?P<what>\S.*)$")
CTRL = re.compile(r"^ctrl\+([a-z])$", re.I)
# A hint at the end of a row with other text before it: "◐ Medium effort (default) ←/→ to adjust" (/model).
# Only arrow keys there: in running text, "x to y" is too common to be read as a key.
ARROW_TAIL = re.compile(r"(?:^|\s)(?P<keys>[←→↑↓](?:\s*/\s*[←→↑↓])*)\s+to\s+(?P<what>[^·]+?)\s*$")
CURSOR = re.compile(r"^\s+❯\s*\S")  # a list's cursor row: indented (the owner's own messages above show as "❯ …" at column 0)
RULE = re.compile(r"^\s*[─━▔▁═]{10,}")  # a panel starts below the last rule
BUSY = "esc to interrupt"  # Claude at work, not a panel


def key_of(token):
    """tmux's name for one key as written in a hint ("Enter", "s", "Ctrl+A", "↓"), or None if not a key."""
    t = token.strip()
    if t.lower() in KEYS:
        return KEYS[t.lower()]
    if t in KEYS:
        return KEYS[t]
    m = CTRL.match(t)
    if m:
        return "C-" + m.group(1).lower()
    if len(t) == 1 and t.isalpha():
        return t
    return None


def hint_actions(line):
    """[{"label", "keys"}] from a hint line, in its order; [] if it is not one."""
    actions = []
    for part in [p.strip() for p in line.strip().split("·")]:
        m = SEGMENT.match(part)
        if not m:
            continue
        tokens = [t.strip() for t in re.split(r"\s*/\s*", m.group("keys")) if t.strip()]
        if len(tokens) == 1 and len(tokens[0]) == 2 and (tokens[0][0], tokens[0][1]) in PAIRS:
            tokens = list(tokens[0])  # "↑↓"
        what = m.group("what").strip()
        if len(tokens) == 2 and tuple(tokens) in PAIRS:
            for t in tokens:
                actions.append({"label": "%s %s" % (t, what), "keys": [KEYS[t]]})
            continue
        key = next((key_of(t) for t in tokens if key_of(t)), None)  # "Enter/↓ to select": the first is enough
        if key:
            actions.append({"label": "%s %s" % (tokens[0], what), "keys": [key]})
    return actions


def arrow_tail(row):
    """[{"label", "keys"}] for an arrow hint at the end of a row ("… ←/→ to adjust"), else []."""
    m = ARROW_TAIL.search(row)
    if not m or "·" in row:
        return []
    return hint_actions("%s to %s" % (m.group("keys"), m.group("what")))


def parse_panel(view, has_box):
    """What a panel on this raw screen offers, or None when no panel has the keyboard. `has_box`: whether
    Claude Code's input box is on screen (the caller knows how to tell). Returns {"text": the panel's last
    rows, "actions": [{"label", "keys"}]} (actions may be empty: a tall panel can push its hint off screen)."""
    # the input box on screen: no panel has the keyboard (a status line is no hint). Without a box, "at work" is
    # asked of the screen's last rows only, as screen.under_box() does: the words in a reply further up are only words
    if has_box or BUSY in "\n".join([r for r in view.split("\n") if r.strip()][-4:]):
        return None
    rows = [r.rstrip() for r in view.split("\n")]
    while rows and not rows[-1].strip():
        rows.pop()
    hint = []
    for r in reversed(rows[-4:]):  # the hint is the last line, or close to it
        hint = hint_actions(r)
        if hint:
            break
    extra = [a for r in rows[-8:] for a in arrow_tail(r)]  # "←/→ to adjust" on a row of its own above the hint
    keys = {tuple(a["keys"]) for a in hint + extra}
    moves = []
    last_rule = max([i for i, r in enumerate(rows) if RULE.match(r)] or [-1])
    if any(CURSOR.match(r) for r in rows[last_rule + 1:]):  # a list with a cursor, in the panel itself: it moves
        moves = [{"label": label, "move": move, "keys": [k]} for label, move, k in (("↑", "previous", "Up"), ("↓", "next", "Down"))
                 if (k,) not in keys]  # "move": for the caller to word (no sentence of ours in a connector)
    text = [r for r in rows if r.strip()][-24:]
    return {"text": "\n".join(text), "actions": (moves + extra + hint)[:20]}


# ---- a numbered menu (a permission question, a notification's dialog): from the bot's menus.py, as it was


OPTION = re.compile(r"^\s*(?:❯\s*)?(\d{1,2})\.\s+(\S.*)$")
MENU_RULE = re.compile(r"^[\s─━═╌▔▁-]*$")  # a row of a menu that is only a rule (the excerpt leaves them out)

# Claude Code 2.1.302, auto (or bypass) mode: a call its safety check holds (a dangerous rm) is asked about with a
# deadline, a warning row that counts down every second: "⚠ Claude Code will automatically deny this request in 1:59,
# to avoid blocking progress on an unattended session" (its screen-reader mode: "in about 2 minutes"); 80 columns wrap
# it onto two rows, a narrower pane onto more, wrapped anywhere: at 52 columns "…deny this request" / "in 0:56, to avoid
# blocking progress on an" / "unattended session" (#activity-dev 2026-10-27, its buttons never went out). At 0 the dialog
# goes and the call is denied. Only rows inside a dialog count, below its full-width
# top rule and below the command's own dashed rules (a reply above or the command may quote the words; #code-review of
# ac72a14), and only the whole sentence with a time in it.
AUTO_DENY = re.compile(r"⚠ Claude Code will automatically deny this request in (?:(\d+):([0-5]\d)|about (\d+) (minute|second)s?), "
                       r"to avoid blocking progress on an unattended session")
AUTO_DENY_START = re.compile(r"^\s*⚠ Claude Code will\b")  # (its first row: where the rest wraps varies with the width)
AUTO_DENY_ROWS = 5  # rows the sentence may take (128 characters: 5 rows down to about 30 columns)
DASHED = re.compile(r"^\s*╌{8,}\s*$")  # the rule above and below a permission's command
DIALOG_RULE = re.compile(r"^\s*(─{20,}|▔{20,})")  # the full-width rule a dialog is drawn under (bot/menus.py's DIALOG_TOP)


def auto_deny_rows(rows):
    """(first row, last row, seconds left) of the countdown on these screen rows, or None (none inside a dialog)."""
    below = max([i for i, r in enumerate(rows) if DASHED.match(r) or DIALOG_RULE.match(r)], default=-1) + 1
    if not below:
        return None
    for i in range(len(rows) - 1, below - 1, -1):
        if not AUTO_DENY_START.match(rows[i]):
            continue
        for j in range(i, min(i + AUTO_DENY_ROWS, len(rows))):
            m = AUTO_DENY.fullmatch(" ".join(r.strip() for r in rows[i:j + 1]))
            if m:
                mins, secs, n, unit = m.groups()
                return i, j, int(mins) * 60 + int(secs) if mins is not None else int(n) * (60 if unit == "minute" else 1)
        return None
    return None


def auto_deny(view):
    """Seconds until Claude Code denies the dialog on this screen by itself, or None (no such deadline shown)."""
    found = auto_deny_rows(view.split("\n"))
    return found[2] if found else None


def without_auto_deny(rows):
    """These screen rows without that countdown (it changes every second: not part of what a dialog asks)."""
    found = auto_deny_rows(rows)
    return rows[:found[0]] + rows[found[1] + 1:] if found else rows


def parse_menu(view):
    """(excerpt of the dialog, [(number, label)]) from a raw screen. The options are the last run of lines
    numbered 1, 2, 3… near the bottom; the excerpt is the dialog text above and around them, without an auto-deny
    countdown (auto_deny() reads it: shown as it was when read, it would be wrong a second later)."""
    rows = without_auto_deny(view.split("\n"))
    while rows and not rows[-1].strip():
        rows.pop()
    found, want = [], None
    for i in range(len(rows) - 1, max(-1, len(rows) - 30), -1):
        m = OPTION.match(rows[i])
        if not m:
            continue
        n = int(m.group(1))
        if want is None or n == want - 1:
            found.insert(0, (i, n, m.group(2).strip()))
            want = n
        if want == 1:
            break
    options = [(n, label) for _, n, label in found] if found and found[0][1] == 1 else []
    if screen.has_prompt(view):
        options = []  # the input box is back: no dialog, only a numbered list in Claude's own text
    top = found[0][0] - 10 if options else len(rows) - 14
    excerpt = [r.rstrip() for r in rows[max(0, top):] if r.strip() and not MENU_RULE.match(r)]
    return "\n".join(excerpt[-20:]), options
