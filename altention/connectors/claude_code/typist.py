"""How a message is typed into Claude Code (named typist, not typing: a module of that name beside a script run
from this folder would stand in for the standard library's) (stage 3b step 2.7: the typing decisions move from the bot into the
connector). This module says what to send and at what pace; the terminal it is sent to is the caller's: the bot's
tmuxio turns the steps into tmux's send-keys, a test's fake pane records them.

What is Claude Code's here, measured on 2.1.295/2.1.296 (docs/gotchas.md "打字的節奏" in the engine):
- a long stretch arriving in one go is taken for a paste: it shows "[Pasted text #1]" in the box, the model is handed
  the words wrapped as pasted content (not written by the user, so instructions in them are not followed), and a long
  line followed by another is even sent off by the typing itself (765 characters typed, 785 pasted; two stretches sent
  one right after the other count as one). So a long message goes in as stretches of TYPE_CHUNK characters with
  TYPE_PAUSE seconds between them. If a new Claude Code moves the threshold, the bot's log and doctor
  (dcbot.typed-as-paste) say that typed text was taken for a paste;
- a new line inside a message is Alt+Enter (NEWLINE): Enter would send it;
- a first character of "!", "#", "&" or "?" switches the prompt into another mode, a trailing backslash escapes Enter,
  a trailing @word leaves the file picker open;
- two Escapes in a row with an empty input box open Rewind (restore the conversation and the code): 0.3 and 0.6 s
  apart did, 0.9 s and more did not (measured through tmux, 2026-10-06). An Escape decided on keeps ESC_GAP from the
  last one sent to the same pane;
- its editor may be vim (setting "editorMode"), and in vim's NORMAL mode, which no screen shows, keys do other things:
  EDITOR_MODE is the shell command that lists what the settings say, says_vim() reads its output.
"""
import re

TYPE_CHUNK = 500  # characters at most in one go (and a terminal command has limits of its own)
TYPE_PAUSE = 0.1  # seconds between two stretches
ESC_GAP = 1.5     # seconds kept between two Escapes the caller decides on, to the same pane (twice what was measured)
NEWLINE = "M-Enter"  # the key that puts a new line into the message instead of sending it


def steps(text):
    """A message as what is to be sent, in order: ("text", s), typed literally, at most TYPE_CHUNK characters each,
    and ("newline",) between its lines. Typed, not pasted: Claude Code wraps long pastes in <pasted_content> and
    treats them as not written by the user. The pace (TYPE_PAUSE between stretches that together would pass
    TYPE_CHUNK) is the sender's to keep: paced(steps) says where the pauses go."""
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    text = "".join(c for c in text if c == "\n" or (c >= " " and c != "\x7f")).strip("\n")
    if text[:1] in ("!", "#", "&", "?"):
        text = " " + text  # these switch the prompt into another mode when typed first
    out = []
    for i, line in enumerate(text.split("\n")):
        if i:
            out.append(("newline",))
        if line.endswith("\\") or re.search(r"(^|\s)@\S*$", line):
            line += " "  # a trailing backslash escapes Enter; a trailing @word leaves the file picker open
        out += [("text", line[j:j + TYPE_CHUNK]) for j in range(0, len(line), TYPE_CHUNK)]
    return out


def paced(steps_):
    """The steps with ("pause",) where the sender waits TYPE_PAUSE: before a stretch of text that, with what was
    typed since the last pause, would pass TYPE_CHUNK (counted across lines: many lines sent one after the other
    are one stretch as well). A message of TYPE_CHUNK characters or fewer goes in as before, in one go."""
    out, burst = [], 0
    for step in steps_:
        if step[0] == "text":
            if burst and burst + len(step[1]) > TYPE_CHUNK:
                out.append(("pause",))
                burst = 0
            burst += len(step[1])
        out.append(step)
    return out


EDITOR_MODE = (  # sh: every editorMode the settings Claude Code reads there name, then DONE; UNREADABLE for a file
    'for f in "$HOME/.claude.json" "$HOME/.claude/settings.json" /etc/claude-code/managed-settings.json '  # not read
    '"/Library/Application Support/ClaudeCode/managed-settings.json" %s; do if [ -e "$f" ]; then '
    'grep -o \'"editorMode"[[:space:]]*:[[:space:]]*"[A-Za-z]*"\' "$f" || [ $? -eq 1 ] || echo UNREADABLE; fi; done; '
    'echo DONE')
FOLDER_SETTINGS = ("settings.json", "settings.local.json")  # in <the folder>/.claude/, read after those of EDITOR_MODE


def says_vim(out):
    """From EDITOR_MODE's output: may the Claude Code there be in vim mode? Yes when any settings file names it,
    when one could not be read, or when the command did not get to its end."""
    lines = out.split("\n")
    return "DONE" not in lines or "UNREADABLE" in lines or any("editorMode" in l and '"vim"' in l.lower() for l in lines)


# What is done to Claude Code's input box around a message, in a pane (3b 2.7a-2). `pane` is whatever the caller
# drives a terminal with: raw_screen(), keys(*names), escape(needed), cursor(), color_screen(), in_mode(),
# vim_mode() (the bot's tmuxio.Pane, the tests' fakepane.Pane). Nothing here keeps anything between calls; what
# the caller writes down (a line in its log, a count for doctor) it is told and writes itself. `others_type`: the
# pane may have someone else typing at it (a terminal of the owner's), as against a session nobody else uses.
CLEAR_PAUSE = 0.15  # between Ctrl-U presses while clearing the input box
PROBE_PAUSE = 0.3   # after the Space or the Backspace of probe(), before the screen is read
PROBE_BACK = 5      # looks, PROBE_PAUSE apart, for the box to be as it was once the Space is taken back
PANEL_PAUSE = 0.2   # between looks for the input box to come back after the Escape that closes a panel


async def enter(pane, typed, again=False, others_type=False):
    """Press Enter on what was just typed, only when, looked at again now, Claude Code's ordinary input box has the
    keyboard (in any pane) and, where `others_type`, holds what was typed and nothing else: a permission question
    that came up since would take the Enter for "Yes" (#code-review 2026-10-09, manager). -> (what, differed):
    what is "pressed"; "empty" (the ordinary box with nothing in it: not pressed, there is nothing to send; a long
    line followed by another is sometimes sent by the typing itself, seen on 2.1.295); None (not pressed: something
    else). `differed`: pressed although the box was not what was typed (a session nobody else types at: a text
    Claude Code draws in a way the comparison does not know must not keep every message from going; the caller
    writes it down)."""
    from . import screen
    view = await pane.raw_screen()
    if screen.pane_state(view) != "box":
        return None, False  # a dialog or ! mode, in whoever's pane
    if screen.input_text(view) == "":
        return "empty", False
    differed = not screen.bot_typed(view, typed, screen.paste_marks(typed))
    if differed and (others_type or again):
        return None, False  # `again` (no receipt came): only an Enter that was lost is pressed again, the message
        # still in the box; the next suggestion standing there was pressed on and counted (#code-review-2)
    await pane.keys("Enter")
    return "pressed", differed


async def clear_input(pane):
    """Empty Claude Code's input box: Ctrl-U removes one line (a long line wraps over several), so press it until
    the box is empty, a bounded number of times. "empty"; "unchanged" (two presses changed nothing and the box is
    not empty: most likely grey suggestion text, which Ctrl-U cannot remove, though possibly text that would not
    go); "stuck" (still changing after every press)."""
    import asyncio
    from . import screen
    last, same = None, 0
    for _ in range(60):
        view = await pane.raw_screen()
        if screen.input_text(view) == "":
            return "empty"
        same = same + 1 if view == last else 0
        if same >= 2:
            return "unchanged"
        last = view
        await pane.keys("C-u")
        await asyncio.sleep(CLEAR_PAUSE)
    return "stuck"


async def probe(pane, view):
    """Is what an input box shows a suggestion? For a screen without colours (NO_COLOR), where grey cannot be seen:
    one Space, which makes a suggestion vanish and leaves typed text as it is, then one Backspace, which brings
    either back. Pressed only where screen.may_probe allows; None when it does not; "scrolling" when the pane is in
    tmux's copy mode, where keys go to tmux. Never when that Claude Code may have vim as its editor mode (its
    settings; not known is yes). "suggestion"; "typed" (someone's text: the Space changed nothing); "changed": no
    further key is pressed. That is when after the Space the box is neither empty nor as it was (someone typing at
    that moment: a Backspace would take a character of theirs; a space may stay), when the box went away, or when
    after the Backspace the text and the cursor are not back as they were. Each press is the caller's to note
    (the result and how long the text was)."""
    import asyncio
    from . import screen
    if await pane.in_mode():
        return "scrolling"
    if await pane.vim_mode():
        return None  # in NORMAL mode, which no screen shows, a Space can finish a command half typed (d, r, c)
    at = await pane.cursor()
    if not screen.may_probe(view, at) or view != await pane.raw_screen():
        return None
    before = screen.input_text(view)
    await pane.keys("Space")
    await asyncio.sleep(PROBE_PAUSE)
    after = screen.input_text(await pane.raw_screen())
    result = "changed"
    if after in ("", before):
        await pane.keys("BSpace")
        for _ in range(PROBE_BACK):
            await asyncio.sleep(PROBE_PAUSE)
            if (screen.input_text(await pane.raw_screen()), await pane.cursor()) == (before, at):
                result = "suggestion" if after == "" else "typed"
                break
    return result


async def close_panel(pane, busy):
    """A panel left open (a slash command's, or the /btw overlay) is closed before typing, but only while Claude is
    idle (`busy()`, asked again before each Escape: a turn may begin while the box comes back): during a turn Escape
    can reach the turn and interrupt it. Typing still works then. After
    each Escape it waits for the input box to come back before looking again (a box not yet redrawn looked like a
    panel, and a second Escape on an empty box opens Rewind); pane.escape() spaces them too (ESC_GAP). True when no
    panel is left."""
    import asyncio
    from . import screen
    for _ in range(3):
        if busy() or not screen.panel_shown(await pane.raw_screen()):
            return True
        if not await pane.escape(screen.panel_shown):
            return True  # gone by the time the gap was over: nothing to close
        for _wait in range(10):
            await asyncio.sleep(PANEL_PAUSE)
            if screen.has_prompt(await pane.raw_screen()):
                break
    return not screen.panel_shown(await pane.raw_screen())


def taken_for_a_paste(text, shown):
    """After `text` was typed: does the box (`shown`, the input box's text) show more of Claude Code's own paste
    marks ("[Pasted text #1]") than the text itself spells out? Only asked of a text with a line at least
    screen.PASTE_AT long: then the pace no longer keeps under Claude Code's threshold (a new version, or pauses that
    ran together). A message about "[Pasted text #1]" was taken for one once (2026-10-09)."""
    from . import screen
    if max((len(line) for line in text.split("\n")), default=0) < screen.PASTE_AT:
        return False
    return len(screen.PASTE_MARK.findall(shown or "")) > len(screen.PASTE_MARK.findall(text))


# The checks before a message is typed, and what they remember between messages (3b 2.7b-1). They say nothing:
# they come to a Typing, whose codes have their words in the bot (dcbot/bot/messages.py).
BOX_PAUSE = 0.2     # between looks at the box: for it to come back, or a message to leave it
GONE_LOOKS = 25     # looks, BOX_PAUSE apart, for the message Enter was just pressed on to leave the box
LIST_LOOKS = 15     # looks, BOX_PAUSE apart, for the conversation's input box after the Escape that leaves the agents list
MOVING_LOOKS = 25   # looks, BOX_PAUSE apart, for a conversation that is about to move to the background to have moved
SEND_NOW_LOOKS = 10  # looks, SEND_NOW_PAUSE apart, for the hint of a message waiting in Claude Code's queue
SEND_NOW_PAUSE = 0.5


class Typing(object):
    """What the checks before a message is typed came to (may_type() and what it calls): true to go on. Else the
    code of why it was not sent and the data that goes with it (the words are the caller's, by the code); and
    either way what there is to say along the way that is no failure (`notes`: "sent again"). `state`: None, or
    "not_sent", "typed_not_entered", "entered" (the protocol's words; what `send` answers from 2.7b-2 on)."""

    def __init__(self):
        self.state, self.code, self.data, self.notes = None, None, {}, []

    @classmethod
    def of(cls, notes):
        """One that only has things to say along the way (and takes them from where they were noted)."""
        r = cls()
        r.notes, notes[:] = list(notes), []
        return r

    def refuse(self, code, **data):
        self.state, self.code, self.data = "not_sent", code, data
        return False

    def note(self, code, **data):
        self.notes.append((code, data))

    def __bool__(self):
        return self.code is None


class Policy(object):
    """What the caller says of the target and of the moment, for the checks (nothing of it is remembered here):
    - others_type: someone else may type at the pane (a terminal of the owner's), as against a session nobody else uses;
    - bot_window(): the pane is the caller's own (a session or a window it opened), where an Escape out of the agents
      list is its to press;
    - parked(): the conversation was moved out of this pane into a background process (the pane may show another);
    - events_down(): what the conversation reports cannot come just now (its machine's stream is down);
    (these three and busy() are asked whenever they matter, not once: the checks wait seconds, and the bot's other
    events change them meanwhile: #code-review on 8278773, as on 44c7069)
    - busy(): a turn runs (asked again whenever it matters);
    - close_panel(): close a panel the caller's own command opened (awaitable; True when none is left);
    - receipt_secs, submit_delay: how long a message's receipt is waited for, and the pause between typing and Enter;
    - differed(): Enter was pressed on a box that was not what was typed (the caller writes it down);
    - probed(result, length): probe() pressed keys (the caller writes it down)."""

    def __init__(self, others_type=False, bot_window=lambda: True, parked=lambda: False, events_down=lambda: False, busy=lambda: False,
                 close_panel=None, receipt_secs=8, submit_delay=0.5, differed=lambda: None, probed=lambda result, length: None):
        self.others_type, self.bot_window, self.parked, self.events_down, self.busy = others_type, bot_window, parked, events_down, busy
        self.close_panel, self.receipt_secs, self.submit_delay = close_panel, receipt_secs, submit_delay
        self.differed, self.probed = differed, probed


async def may_type(pane, memory, policy, r=None):
    """Before anything is typed: is the pane ready for it? Typed into only when Claude Code's ordinary input box has
    the keyboard, in an attached pane (the owner's terminal) and in the caller's own session alike (the owner may sit
    at it through tmux attach; manager 2026-10-09). In its ! mode the message would be run as a shell command; onto a
    permission question the message and its Enter choose "Yes", and an Escape chooses "No". There no key is pressed
    and nothing is sent; only a panel the caller's own command opened (memory.own_panel) is closed first. Then
    clear_leftover(): what an attached pane's box holds may be the owner's and is left; the caller's own session's is
    cleared as before. `memory`: entered, left, typed, own_panel (read and written here; the caller keeps them).
    `r`: the Typing to fill (the caller's, so that what is noted along the way is its even when this raises)."""
    import asyncio
    from . import screen
    r = Typing() if r is None else r
    closed = False
    for _ in range(5):  # a screen between two frames shows no box either
        view = await pane.raw_screen()
        state = screen.pane_state(view)
        if state != "dialog":
            break
        if memory.own_panel and not closed and not policy.events_down():  # its events cannot come: the claim may be
            closed = True                                                 # long over (a turn's dialog)
            await (policy.close_panel() if policy.close_panel else close_panel(pane, policy.busy))
        else:
            await asyncio.sleep(BOX_PAUSE)
    if state == "box" and screen.moving_away(view):  # the left key was pressed while a tool runs: about to move
        for _ in range(MOVING_LOOKS):
            await asyncio.sleep(BOX_PAUSE)
            view = await pane.raw_screen()
            if not screen.moving_away(view):
                break
        else:
            r.refuse("claude_code.moving_away")
            return r
        state = screen.pane_state(view)
    if state == "agents":
        view = await leave_list(pane, view, r, policy)
        if view is None:
            return r
        state = screen.pane_state(view)
    entered = memory.entered  # as it is now: its receipt may clear it while this waits (C-73)
    if state == "box" and entered and screen.bot_typed(view, entered, screen.paste_marks(entered)):
        # the message before this one, its Enter just pressed and not yet taken (two sent one after the other)
        for _ in range(GONE_LOOKS):
            await asyncio.sleep(BOX_PAUSE)
            view = await pane.raw_screen()
            if not memory.entered or not screen.bot_typed(view, entered, screen.paste_marks(entered)):
                break  # gone from the box, or taken (its receipt came)
        else:
            if policy.others_type or not await resend(pane, memory, r, policy):
                r.refuse("claude_code.previous_entered" if policy.others_type else "claude_code.previous_not_resent")
                return r
            view = await pane.raw_screen()
        state = screen.pane_state(view)
    if state == "box" and policy.parked() and screen.list_started(view):
        r.refuse("claude_code.other_conversation")
        return r
    if state == "box":
        memory.own_panel = None
        await clear_leftover(pane, memory, r, policy, view)
        return r
    if state == "agents":  # it came up between the looks
        r.refuse("claude_code.agents_list")
        return r
    r.refuse("claude_code.shell_mode" if state == "shell" else "claude_code.dialog")
    return r


async def leave_list(pane, view, r, policy):
    """The pane shows Claude Code's agents list (screen.agents_list): what is typed there and entered starts a new
    background conversation, which no channel follows (#issues 32: a message from Discord went into one). Nothing is
    typed into it. The caller's own session or window, the list's box empty: one Escape, which goes back to the
    conversation, and on only once the list is gone. An attached pane is the owner's terminal: no key, said. A box
    that holds text (the owner's, half typed): no key in either, said. The screen to go on with, or None."""
    import asyncio
    from . import screen
    if screen.agents_list(view) == "typed":
        r.refuse("claude_code.list_has_text")
        return None
    if not policy.bot_window():
        r.refuse("claude_code.agents_list")
        return None
    await pane.escape(lambda v: screen.agents_list(v) == "empty")
    for _ in range(LIST_LOOKS):
        await asyncio.sleep(BOX_PAUSE)
        view = await pane.raw_screen()
        if screen.pane_state(view) == "box":  # the conversation's own box: a screen between two frames shows
            return view                       # neither the list nor a box, and is not gone on from
    r.refuse("claude_code.list_not_left")
    return None


async def resend(pane, memory, r, policy):
    """The caller's own session: the message before this one is still in the box, its Enter not taken (it used to be
    cleared as a leftover and lost; in an attached pane it is left and said). Cleared, typed again and sent the way
    a message is sent (its Enter, and two more while the message is still in the box: Claude Code does not take the
    first Enter of some texts), and noted ("claude_code.resent"); True when it has left the box."""
    import asyncio
    from . import screen
    again = memory.entered
    await clear_input(pane)
    if screen.bot_typed(await pane.raw_screen(), again, screen.paste_marks(again)):
        return False  # it would not be cleared. Anything else there now (nothing, or the next suggestion, which no key
        # clears and typing takes the place of) is what the next message types over as well
    await pane.type_text(again)
    await asyncio.sleep(policy.submit_delay)
    for attempt in range(3):  # as a message is sent: the Enter, and two pressed again while it is still there
        what, differed = await enter(pane, again, again=bool(attempt), others_type=policy.others_type)
        if differed:
            policy.differed()
        if what is None and not attempt:
            return False
        for _ in range(max(1, int(policy.receipt_secs / BOX_PAUSE))):
            await asyncio.sleep(BOX_PAUSE)
            if not screen.bot_typed(await pane.raw_screen(), again, screen.paste_marks(again)):
                r.note("claude_code.resent")
                return True
    return False


async def clear_leftover(pane, memory, r, policy, view=None):
    """Before typing: whatever the input box still holds would go out in front of the message (an interrupted
    /compact leaves "/compact " there, and the message becomes its instructions). In the caller's own sessions it is
    cleared. Where others type it may be the owner typing at the terminal: cleared only when it is the slash command
    typed last there (memory.typed); else nothing is sent. Grey text there is a suggestion, typed over; on a screen
    without colours (screen.colourless) grey cannot be seen, and probe() tells. True to go on."""
    from . import screen
    view = view if view is not None else await pane.raw_screen()
    text = screen.input_text(view)
    if not text:
        memory.left = memory.entered = None
        return True
    if policy.others_type:
        shown = (await pane.color_screen())[0]
        if screen.input_dim(shown):
            return True  # grey: a placeholder or a suggestion, nothing typed
        last = memory.typed[-1].split()[0] if memory.typed and memory.typed[-1].split() else None
        if not (text.startswith("/") and text.split()[0] == last):
            quoted = text[:80].replace("`", "'")
            if memory.left and screen.bot_typed(view, memory.left, screen.paste_marks(memory.left)):
                return r.refuse("claude_code.previous_left", text=quoted)
            if not screen.colourless(shown):  # a screen with colours: not grey, so typed
                return r.refuse("claude_code.owners_text", text=quoted)
            found = None
            if not screen.busy_shown(view):
                found = await probe(pane, view)
                if found in ("suggestion", "typed", "changed"):  # keys were pressed
                    policy.probed(found, len(screen.input_text(view) or ""))
            if found == "suggestion":
                return True
            return r.refuse({"scrolling": "claude_code.scrolling", "changed": "claude_code.changed_while_probing"}.get(
                found, "claude_code.no_colour"), text=quoted)
    if await clear_input(pane) == "stuck":
        return r.refuse("claude_code.not_cleared", text=text[:80].replace("`", "'"))
    return True


async def send_now(pane, taken):
    """Press send-now (Ctrl+X Ctrl+S) for a message that waits in Claude Code's queue, only when the screen shows
    Claude Code's own hint where it draws it (screen.send_now_shown): with nothing queued the keys send off whatever
    stands in the input box (seen on 2.1.295). `taken()`: the message's receipt has come meanwhile (nothing waits any
    more). Whether one was queued at all is the caller's to know before it asks. True when pressed."""
    import asyncio
    from . import screen
    for _ in range(SEND_NOW_LOOKS):
        await asyncio.sleep(SEND_NOW_PAUSE)
        if taken():
            return False  # taken meanwhile: nothing waits any more
        if screen.send_now_shown(await pane.raw_screen()):
            await pane.keys("C-x", "C-s")
            return True
    return False
