"""A screen of Claude Code -> facts. Pure functions of the text tmux captured (and, where colour matters, of the
same screen with its SGR sequences): where the input box is and what stands in it, whether that is what was
typed, where the /btw answer box is and what it holds.

The fixed words Claude Code draws are looked for only in the region it draws them in (under the box, in the box,
the answer box), never anywhere on the screen: the conversation shown above quotes them all the time (the rule of
2026-10-09). Moved here from the bot's events.py as they were (stage 3b step 1.1); standard library only, Python
3.8, nothing of the bot imported."""
import re
import unicodedata


def shown_width(text):
    """Columns a string takes on a terminal: CJK characters are two wide."""
    return sum(2 if unicodedata.east_asian_width(c) in ("W", "F") else 1 for c in text)


BTW_EDGE = "▔▔▔"  # how the row over Claude Code's /btw answer box starts, at the screen's left edge
BTW_HINT = "Esc to close"
BTW_DONE = "c to copy"  # in the box's hint once there is an answer to copy, and only then


def btw_box(view):
    """Where Claude Code draws its /btw answer, and what stands there: (the question rows, the rows under them,
    the hint row), or None when no answer box is on this raw screen.

    The box is drawn where the input box was (so there is none while it shows), at the bottom of the screen: a
    row of "▔" from the left edge (with the effort setting at its right end while the answer is on its way),
    the questions asked so far at an indent (each on one row: a long one is cut with "…"), an empty row, the
    answer deeper under them, an empty row, and as the last of the screen the box's own hint, which ends in
    "Esc to close" (returned as one row; on a narrow pane it is drawn on two). A tall answer scrolls inside the
    box: its edge and its hint stay. While the answer is on its way the one row under the questions is a
    spinner's glyph and "Answering…" (or, the request failing and tried again, "API error · retrying in …",
    "Rate limited · retrying in …"), and the hint does not offer "c to copy" yet; nothing is drawn under the
    hint, in bypass mode either (measured on 2.1.296, 2026-10-11 and 10-12).

    Only that region is read (manager 2026-10-11, after #code-review-2 ran the real thing): the words were
    looked for on the whole screen, and a conversation that quoted them, an answer that said "Answering…" or
    "Esc to close", or a message of the owner's with a line starting "/btw" was taken for the box or for its
    end: an answer never posted and its box closed, an answer cut short, "（沒有內容）" posted for one still
    on its way."""
    rows = view.split("\n")
    if box_span(view) is not None:
        return None
    edges = [i for i, r in enumerate(rows) if r.startswith(BTW_EDGE)]
    if not edges:
        return None
    below = rows[edges[-1] + 1:]
    filled = [i for i, r in enumerate(below) if r.strip()]
    if len(filled) < 2:
        return None
    first, last = filled[0], filled[-1]
    top = last
    while top - 1 > first and below[top - 1].strip():  # the hint: the last rows of the screen, down from an empty one.
        top -= 1                                        # On a pane narrower than it (78 columns with its indent) it wraps
    hint = " ".join(r.strip() for r in below[top:last + 1])  # onto a second row, anywhere between two words (2.1.296)
    indent = lambda r: len(r) - len(r.lstrip())  # noqa: E731
    if not below[first].strip().startswith("/btw") or not indent(below[first]) or BTW_HINT not in hint \
            or indent(below[top]) != indent(below[first]):
        return None
    end = first
    while end < top and below[end].strip():  # the questions: the rows down to the first empty one
        end += 1
    if end == top:
        return None  # no empty row between the questions and the hint: not the box as Claude Code draws it
    return below[first:end], below[end:top], " " * indent(below[top]) + hint


def btw_hint(view):
    """The answer box's own hint row (what its keys do now), "" when no answer box shows."""
    found = btw_box(view)
    return found[2] if found else ""


def btw_answer(view, width=120):
    """Read Claude Code's /btw answer off the (raw) screen: None if no answer box is shown, "" while it is
    still on its way, else the answer. Only what stands in the box itself is read (btw_box). The terminal
    wraps long lines; they are put back together.

    There is an answer when the box's own hint offers "c to copy", and only then. Not told by what the row
    under the question says: while it waits that row reads "· Answering…", but when the request fails and is
    tried again it reads "· API error · retrying in 2s · attempt 3/10" or "✶ Rate limited · retrying in 18s ·
    attempt 6/10" (2.1.296), which was taken for the answer: posted a second after sending, and the box, still
    retrying, closed (#code-review-2 2026-10-12). In every state seen the hint has "c to copy" exactly when
    there is something to copy: an answer, or Claude Code's own last word on a request that failed for good
    ("(API error: …)"), which is posted as the answer it is."""
    found = btw_box(view)
    if found is None:
        return None
    body = [r.rstrip() for r in found[1]]
    while body and not body[0].strip():
        body = body[1:]
    while body and not body[-1].strip():
        body = body[:-1]
    if BTW_DONE not in found[2]:
        return ""
    pad = min((len(r) - len(r.lstrip()) for r in body if r.strip()), default=0)
    out = []
    raw_prev = ""
    for raw in body:
        row = raw[pad:]
        prev = out[-1] if out else ""
        if out and prev.strip() and row.strip() and not row.startswith((" ", "-", "*", "#", "`", "|")) \
                and shown_width(raw_prev) >= width - 22:  # the line above ran to the edge: this one continues it
            glue = " " if prev[-1:].isascii() and row[:1].isascii() else ""
            out[-1] = prev + glue + row
        else:
            out.append(row)
        raw_prev = raw
    return "\n".join(out).strip() or "（沒有內容）"


BTW_MAX = 550  # characters after "/btw ": Claude Code takes a line of about 780 or more typed at once for a
               # paste (measured 2026-10-06: 765 typed, 785 pasted), and a pasted line no longer starts with /btw


QUEUED_HINT = "Press up to edit queued messages"  # what the empty box shows, grey, while a message waits in the queue.
# Taken by its words, not by its colour: on a screen without colours it was held to be the owner's half-typed text
# and the third of three messages sent together was not sent (#code-review-2 2026-10-09, manager)


def box_span(view, shell=False):
    """(first row, closing rule) of Claude Code's input box on this raw screen, as indexes of its rows: the last
    row that starts with "❯" (with `shell`, or "!", the box in its shell mode) right under a rule, and the rule
    that closes it; None when there is no box (a panel has the keyboard). The one place that says where the box
    is: what stands in it (input_rows) and what stands under it (under_box) are read from here.
    The closing rule is the last rule on the screen: under it stand only the status line and a hint, which have
    none, while a message may hold rows of "─" of its own (a pasted screen, a divider: read up to its first rule,
    such a message was never the bot's own text and got no Enter in an attached pane; #code-review-2 2026-10-09).
    No rule at all after the first row: the end of the box is not on the screen, (first row, None). (However many
    rows stand under the last rule, it is the end: the box scrolls and never runs off the screen, while a guard
    "too many rows under it" would have taken a long menu under the box for the message; manager 2026-10-09.)"""
    rows = view.split("\n")
    marks = ("❯", "!") if shell else ("❯",)
    # at the row's very start: the box's own mark; the rows of a message in the box are indented, also one that
    # quotes a screen with a "❯" under a rule of its own
    start = next((i for i in range(len(rows) - 1, 0, -1) if rows[i].startswith(marks) and is_box_rule(rows[i - 1])), None)
    if start is None:
        return None
    return start, next((i for i in range(len(rows) - 1, start, -1) if is_box_rule(rows[i])), None)


def input_rows(view):
    """(index of the first row, the rows) of Claude Code's input box on this raw screen; None when there is no
    box (a panel has the keyboard)."""
    span = box_span(view)
    if span is None:
        return None
    rows = view.split("\n")
    return span[0], rows[span[0]:span[1]] if span[1] is not None else rows[span[0]:]


def input_text(view):
    """The text in Claude Code's input box on this raw screen: None when there is no box (a panel has the
    keyboard), "" when it is empty or shows only its grey "Try …" placeholder or its hint of queued messages."""
    found = input_rows(view)
    if found is None:
        return None
    text = " ".join(found[1]).replace("\u00a0", " ").strip()
    text = text[1:].strip() if text.startswith("❯") else text
    return "" if not text or text.startswith("Try \"") or text == QUEUED_HINT else text


VIM_MODE = re.compile(r"-- (INSERT|NORMAL|VISUAL)[A-Z ]* --")


def may_probe(view, cursor):
    """May one Space be pressed into this input box to see whether what it shows is a suggestion (see
    TmuxBridge.probe)? Only on a screen where a press cannot do anything else: the box is there and one row
    long, nothing says vim mode (a mode line is drawn in INSERT only: NORMAL cannot be
    told from no vim at all, and there a Space only moves the cursor), and the cursor, (column, row) from tmux,
    stands where the text starts. A suggestion is drawn with the cursor there; after typed text it stands
    elsewhere unless the owner moved it back."""
    found = input_rows(view)
    if found is None or cursor is None or VIM_MODE.search(view):
        return False
    start, box = found
    row = box[0]
    return (len([r for r in box if r.strip()]) == 1 and row.startswith("❯")
            and tuple(cursor) == (2, start) and len(row) > 2 and row[1] in " \u00a0" and row[2] not in "!#")


SGR = re.compile(r"\x1b\[([0-9;]*)m")


def grey(params):
    """SGR parameters that draw grey: dim (2) or bright black (90) as attributes, or a foreground colour that is
    grey (256-colour 240…250, or true colour with red, green and blue within 16 of each other and not near white
    or black). The numbers inside a colour (38;5;N, 38;2;R;G;B, and the same for 48, the background) are colour
    values, not attributes: a 2 or a 90 there means nothing about dimness."""
    params = [p for p in params if p != ""] or ["0"]
    dim, grey_fg, i = False, False, 0  # an attribute and a colour: each set and reset on its own
    while i < len(params):
        p = params[i]
        if p in ("38", "48") and i + 1 < len(params):
            mode = params[i + 1]
            if mode == "5" and i + 2 < len(params):
                n = int(params[i + 2]) if params[i + 2].isdigit() else -1
                if p == "38":
                    grey_fg = 240 <= n <= 250
                i += 3
                continue
            if mode == "2" and i + 4 < len(params):
                rgb = [int(x) if x.isdigit() else -1 for x in params[i + 2:i + 5]]
                if p == "38":
                    grey_fg = min(rgb) >= 64 and max(rgb) <= 200 and max(rgb) - min(rgb) <= 16
                i += 5
                continue
            i += 2
            continue
        if p == "2":
            dim = True
        elif p == "22":
            dim = False
        elif p == "0":
            dim = grey_fg = False
        elif p == "90":
            grey_fg = True
        elif p == "39" or (len(p) == 2 and p[0] in "39"):  # the default colour, or another foreground colour
            grey_fg = False
        i += 1
    return dim or grey_fg


PLAIN_SGR = {"", "0", "1", "3", "4", "7", "9", "21", "22", "23", "24", "27", "29"}  # reset, bold, italic, underline,
# inverse, struck out and their ends: what a screen keeps when colours are off. Dim (2) is how grey is drawn there.


def colourless(color_view):
    """Is this screen (with its SGR sequences) drawn without colours, so that grey cannot be seen on it? No
    sequence sets a colour or dim. Claude Code under NO_COLOR still draws the cell under the cursor in inverse
    while the terminal has the focus (#code-review-2 2026-10-09)."""
    return all(p in PLAIN_SGR for m in SGR.finditer(color_view) for p in m.group(1).split(";"))


def inverse(params):
    """Do these SGR parameters, in order, leave inverse video on? (Colour values inside 38/48 are skipped.)"""
    on, i = False, 0
    while i < len(params):
        p = params[i]
        if p in ("38", "48") and i + 1 < len(params):
            i += {"5": 3, "2": 5}.get(params[i + 1], 2)
            continue
        on = True if p == "7" else False if p in ("27", "0", "") else on
        i += 1
    return on


def input_dim(color_view):
    """Is what the input box shows grey (dim) text, a placeholder or a suggestion, rather than typed text?
    From the screen with its colours (tmuxio.color_screen): the style in force (every sequence of the row up to
    there, in order) at the first character after the "❯" of the last input row that is not the cell under the
    cursor. That cell Claude Code draws in inverse and not grey while the terminal has the focus, and with a
    suggestion showing it holds the suggestion's first character (#code-review-2 2026-10-09)."""
    for row in reversed(color_view.split("\n")):
        plain = SGR.sub("", row)
        if not plain.lstrip().startswith("❯"):
            continue
        style, i, skipped = [], row.index("❯") + 1, False
        while i < len(row):
            m = SGR.match(row, i)
            if m:
                style, i = style + (m.group(1).split(";") if m.group(1) else ["0"]), m.end()
                continue
            if row[i] not in " \u00a0":
                if inverse(style) and not grey(style) and not skipped:
                    skipped = True  # the cursor's cell: what comes after it tells
                else:
                    return grey(style)
            i += 1
        return False
    return False


def btw_input_state(view):
    """What Claude Code's input box holds on this raw screen, before Enter: "ok" (a line starting with /btw),
    "pasted" (taken for pasted text: "[Pasted text …]"), "empty", or "other" (anything else, /btw gone)."""
    text = input_text(view)
    if text is None:
        return "other"
    if "Pasted text" in text:
        return "pasted"
    if not text:
        return "empty"
    return "ok" if text.startswith("/btw ") else "other"


PASTED = re.compile(r"\[Pastedtext[^\]]*\]")  # "[Pasted text #1 +3 lines]" with its blanks taken out
BOX_MAX, MARKS_MAX = 6000, 8  # more characters or marks in the box than this: not compared, not the bot's


def bot_typed(view, typed, marks=0):
    """Is what the input box holds on this raw screen all of what the bot just typed there (`typed`), nothing
    missing and nothing more? Then Enter sends just that, and it is the bot's to clear. Anything else in the box
    is someone else's. Blanks and what Claude Code draws differently do not count (same_typed). Claude Code
    shows a long stretch typed fast as "[Pasted text #1]", one or several of them, perhaps with typed text
    around: each stands for some part, at least a character; `marks` is how many the bot's typing can have made
    at most (paste_marks), more are not its, nor is one that counts lines ("[Pasted text #2 +29 lines]");
    marks the message itself spells out are its words, not counted. A box of three rows or more may be the end of a text taller
    than the box shows (it scrolls, the cursor at the end): then the end of what the bot typed counts as the
    whole (seen on Claude Code 2.1.295, 2026-10-09). Known limits (manager 2026-10-09): what the owner pastes
    while the bot types a text long enough for marks, within their number, and what the owner types into the
    part scrolled out of sight, cannot be seen. Compared piece by piece from the left, never by a pattern (one
    with several marks that did not match ran for minutes, in the bot's own loop: #code-review 2026-10-09)."""
    found = input_rows(view)
    text = input_text(view)
    if not text or found is None or len(text) > BOX_MAX:
        return False
    box, ours = same_typed(text), same_typed(typed)
    tall = len([r for r in found[1] if r.strip()]) >= 3
    if ours.endswith(box) if tall else ours == box:
        return True  # as it was typed, whatever it spells out: a message about "[Pasted text #1]" is no paste
    parts = PASTED.split(box)
    shown, spelled = PASTED.findall(box), PASTED.findall(ours)
    # Only the marks beyond those the message itself spells out are Claude Code's (the same rule as note_paste's:
    # a question about "[Pasted text #1]" was never sent into an attached pane, #code-review-2 2026-10-09). And
    # "+29 lines" is a paste with its line ends, never the bot's (it types a line end as a key of its own), unless
    # the message spells that very mark out.
    left, others = list(spelled), 0
    for m in shown:
        if m in left:
            left.remove(m)
        elif "+" in m:
            return False
        else:
            others += 1
    if len(shown) > MARKS_MAX + len(spelled) or others > min(marks, MARKS_MAX):
        return False
    if len(parts) == 1:
        return False
    first, last = parts[0], parts[-1]
    if tall:
        at = ours.find(first)
        if at < 0:
            return False
    elif not ours.startswith(first):
        return False
    else:
        at = 0
    at += len(first) + 1  # each mark stands for at least one character
    for part in parts[1:-1]:
        at = ours.find(part, at)
        if at < 0:
            return False
        at += len(part) + 1
    return ours.endswith(last) and len(ours) - len(last) >= at


DROPPED = dict.fromkeys([0x200b, 0x200c, 0x200d, 0x2060, 0xfeff, 0xfe0e, 0xfe0f, 0x20e3, 0xa0, 0x7f])


def same_typed(text):
    """Text as it is compared between what was typed and what the input box shows: composed (NFC: an e and its
    accent are drawn as one é), without blanks of any kind (a full-width one at either end is cut off the box
    as it is read) and control characters, and without what Claude Code drops or
    moves when it draws (zero-width characters, variation selectors, the keycap mark; seen on 2.1.295)."""
    return "".join(c for c in unicodedata.normalize("NFC", text).translate(DROPPED) if c > " " and not c.isspace())


def is_box_rule(row):
    row = row.strip()
    return row.startswith("─") and row.endswith("─") and row.count("─") >= 10


# ---- who has the keyboard, what the status line and the rows by the box say (from the bot's tmuxio.py and
# tmux_bridge.py, as they were: stage 3b step 1.2)


CLAUDE_COMMANDS = ("claude", "node")  # what tmux reports as the running command of a Claude Code pane


CLAUDE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")  # on macOS: the name of the versioned binary claude links to


def is_claude(command):
    return command in CLAUDE_COMMANDS or bool(CLAUDE_VERSION.match(command or ""))


def has_prompt(view):
    """Is Claude Code's input box on this (raw) screen? It is a "❯" row right under a horizontal rule.
    A panel (/usage, /model, …) replaces the box, and a tall panel pushes its own "Esc to cancel" off screen,
    so a missing box is the dependable sign that something else has the keyboard."""
    return box_span(view) is not None  # the one place that says where the box is


BUSY_MARK = "esc to interrupt"  # in the status line under the input box while Claude works


def under_box(view):
    """The rows of this raw screen under Claude Code's input box (past the rule that closes it, the last on the
    screen: the status line, a panel's hint), or the last few rows when there is no box. What a panel or the status line says is said there.
    The conversation above the box is only text, and so is what stands in the box: a reply that quoted "Esc to
    cancel" was taken for an open panel and every message to that idle channel was held; then a message that
    itself said "Esc to cancel", once typed into the box, got no Enter (#code-review 2026-10-09, manager)."""
    rows = view.split("\n")
    span = box_span(view, shell=True)  # the one place that says where the box is
    if span is None:  # a panel has taken the box's place: its own last rows (its hint, or the status line)
        return "\n".join([r for r in rows if r.strip()][-4:])
    return "\n".join(rows[span[1] + 1:]) if span[1] is not None else ""  # no closing rule: the box runs off the screen


SEND_NOW_HINT = "ctrl+x ctrl+s to send now"  # the row Claude Code draws under the messages waiting in its queue


def send_now_shown(view):
    """Is a message waiting in Claude Code's queue, by this raw screen? Its hint, a row of its own, stands in the
    few rows right over the input box's upper rule (the queued messages, the hint, the spinner, the effort mark;
    seen on 2.1.295). Only there, and only the row that is the hint and nothing else: the conversation further
    up, and a queued message that happens to say "to send now", are text (manager 2026-10-09)."""
    rows = view.split("\n")
    span = box_span(view, shell=True)
    if span is None:
        return False
    over = [r for r in rows[:span[0] - 1] if r.strip()][-QUEUE_ROWS:]  # blank rows stand between them and do not count
    return any(r.strip() == SEND_NOW_HINT and not r.startswith("❯") for r in over)


QUEUE_ROWS = 6  # how far over the box's rule the hint is looked for (it was the third row up)


BUSY_CUT = 8  # how much of BUSY_MARK a status line cut short must still show to be read: "esc to i" (and "esc to
# cancel", a panel's hint, never is); cut shorter, it is not told from other words, and not read as busy


def busy_shown(view):
    """Is Claude at work, by this raw screen? Its status line says so, under the input box (under_box): the
    one place every "is it busy" on a screen asks, so that words in the conversation never count.

    The status line is cut to the pane's width with an ellipsis. A conversation that was moved to the background
    and back has "← for agents" in front of the mark, and at 80 columns it reads "… · ← for agents · esc to inter…"
    (#issues 34, both versions' samples): the turn was taken for over after three looks. So the last segment of a
    row there that ends in "…" counts too, when what is left of it is the start of BUSY_MARK and at least BUSY_CUT
    long. Words in the conversation or in the box are above the status line and are never looked at here."""
    under = under_box(view)
    if BUSY_MARK in under:
        return True
    for row in under.split("\n"):
        row = row.rstrip()
        if not row.endswith("…"):
            continue
        last = row[:-1].rsplit(" · ", 1)[-1].strip()
        if len(last) >= BUSY_CUT and BUSY_MARK.startswith(last):
            return True
    return False


PASTE_AT = 600  # a stretch arriving in one go at least this long may show as "[Pasted text]" (700 did not, 900 did)


def paste_marks(text):
    """How many "[Pasted text]" marks typing `text` can leave in the input box at most. With the pacing of
    type_text none is expected; should Claude Code take the stretches for pastes all the same (its threshold
    moved, or the pauses ran together), each PASTE_AT characters of a line can make one, and that many are still
    the bot's own (screen.bot_typed)."""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    return sum(len(line) // PASTE_AT for line in text.split("\n"))


DIALOG = re.compile(r"Esc to (cancel|close|exit|go back)|Enter to confirm")  # a panel (/usage, /model…) is open


def panel_shown(view):
    """Does a panel have the keyboard on this raw screen? Its hint line by the box, or no input box at all: a
    panel taller than the screen (/usage in a session that has run for a while) loses its "Esc to cancel"."""
    if busy_shown(view):
        return False  # Escape would interrupt the turn
    return bool(DIALOG.search(under_box(view))) or not has_prompt(view)


STARTUP_NO = "No, exit"  # the answer every dialog Claude Code opens with offers (trust this folder, bypass mode)


def startup_key(view):
    """The key that answers a dialog Claude Code opens with, by Yes: "Enter" when Yes is the one picked, "Down"
    when it is not yet; None when no such dialog has the keyboard on this raw screen. Told only where a dialog
    is drawn: there is no input box, and the rows after the last rule (the dialog's own) end in its two answers,
    one of them exactly "No, exit", the other a Yes, next to each other, with nothing after them but the dialog's
    hint. The words were looked for on the whole screen: a conversation resumed with them in its text got a Down
    every half second until its SessionStart was handled (#code-review-2 2026-10-11: 30 of them when that took
    15 seconds)."""
    if pane_state(view) != "dialog":
        return None
    rows = view.split("\n")
    rules = [i for i, r in enumerate(rows) if is_box_rule(r)]
    own = [r.strip() for r in rows[rules[-1] + 1 if rules else 0:] if r.strip()]
    if own and DIALOG.search(own[-1]):
        own = own[:-1]  # its hint: "Enter to confirm · Esc to cancel"
    option = lambda r: r.lstrip("❯ ").lstrip("0123456789. ")  # noqa: E731
    last = [option(r) for r in own[-2:]]
    if len(last) != 2 or STARTUP_NO not in last or not any(o.startswith("Yes") for o in last):
        return None
    picked = [r for r in own[-2:] if r.startswith("❯")]
    return "Enter" if picked and option(picked[-1]).startswith("Yes") else "Down"


# Claude Code's agents list (the left key on an empty input box): the pane's conversation has moved to a background
# process and the pane shows the list of background conversations, with an input box of its own. What is typed there
# and entered starts a NEW background conversation; Enter on an empty box opens the row under the cursor. Real screens
# of 2.1.288 and 2.1.296, two sizes: tests/connectors/claude_code/screens/<version>/agents/.
AGENTS_FOOT = re.compile(r"\benter to (return|open|create)\b")  # the list's status line: what Enter does there
AGENTS_TYPED = "enter to create"                                # … once its box holds text
AGENTS_HEAD = re.compile(r"^\s*▝▝\s+▝▝\s+\d+ awaiting input · \d+ working · \d+ completed\s*$")  # third row of the list's
# header, with the foot of Claude Code's mark before it: the same words as a row of a conversation (after "● ", or
# alone) are text. An ordinary box that holds text has a status line with neither of a conversation's marks, and
# such a row at the top made it the list (#code-review 2026-10-14).
ORDINARY_FOOT = ("(shift+tab to cycle)", "for agents")  # in a conversation's status line, never in the list's
AGENTS_EMPTY = "describe a task for a new session"  # what the list's empty box shows
HEAD_ROWS = 6  # the header stands in the screen's first rows (measured: its third row is the screen's fourth) until it scrolls off
MOVING = "● Backgrounding after the current tool finishes…"  # the row Claude Code adds between the left key and the move
LIST_STARTED = re.compile(r"^\s*▝▝\s+▝▝\s+@claude · ")  # third row of the header of a conversation started from the list


def agents_list(view):
    """Is this raw screen Claude Code's agents list? "typed" when its box holds text, "empty" when it does not,
    None for any other screen. Told by what Claude Code draws itself: the status line under the box says what Enter
    does in the list; or, that line being cut short or worded otherwise, the list's header stands at the top and
    the status line has none of a conversation's own marks. Never by what stands in the box or in the rows of
    the list (a row there shows the words of a conversation)."""
    if box_span(view) is None:
        return None
    foot = under_box(view)
    if not AGENTS_FOOT.search(foot):
        head = view.split("\n")[:HEAD_ROWS]
        if any(mark in foot for mark in ORDINARY_FOOT) or not any(AGENTS_HEAD.match(r) for r in head):
            return None
    text = input_text(view)
    return "typed" if AGENTS_TYPED in foot or text not in ("", AGENTS_EMPTY) else "empty"


def moving_away(view):
    """Was the left key pressed while a tool runs? The conversation moves to the background once the tool is done,
    and the pane then shows the agents list: until then the screen is an ordinary busy one with this one row more.
    The row stands among the conversation's (Claude Code has no other place for it): told only as a whole row, on
    a screen that shows a turn running."""
    return busy_shown(view) and any(r.strip() == MOVING for r in view.split("\n"))


def list_started(view):
    """Does the header on this raw screen say the conversation shown was started from the agents list (its third
    row begins "@claude · ")? A conversation moved out of a pane shows the folder alone there. The one thing on the
    screen that tells the two apart, and only while the header has not scrolled off; conversations started from
    the list cannot be told from each other."""
    return any(LIST_STARTED.match(r) for r in view.split("\n")[:HEAD_ROWS])


PASTE_MARK = re.compile(r"\[Pasted text #\d+")  # how Claude Code shows a paste in the input box


def pane_state(view):
    """What has the keyboard on this raw screen: "box" (Claude Code's ordinary input box), "shell" (the box in
    its ! mode: what is typed there is run as a shell command), "agents" (the agents list: what is typed there
    starts a new background conversation, see agents_list()) or "dialog" (anything else: a panel, a menu, a
    permission question, where typed characters and Enter choose an answer)."""
    span = box_span(view, shell=True)
    if span is not None and view.split("\n")[span[0]].startswith("!"):
        return "shell"
    if agents_list(view):
        return "agents"
    if not has_prompt(view) or (not busy_shown(view) and DIALOG.search(under_box(view))):
        return "dialog"
    return "box"
