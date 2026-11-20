"""Tmux mode: each channel's Claude Code runs interactively in tmux: in a session the bot starts on its own
tmux server, or in a pane of the owner's own tmux that was attached to the channel (bot/routes.py).

Messages are typed into the session (tmuxio). Replies come back through Claude Code hooks: tools/dchook
drops one file per event into state/events/, and pump() reads them and posts to Discord. Because Claude
lives in tmux and not under the bot, a bot restart or deploy does not interrupt it, background work keeps
running between turns, and a message sent while Claude is working is delivered into the running turn.

There is no one-to-one pairing of message and reply: two messages can share one turn, and a finished
background task starts a turn by itself. So the rule is simply "every Stop event is posted".
"""
import asyncio
import collections
import json
import os
import re
import time
import traceback

import discord

from altention.connectors.claude_code import btw, panels, screen, transcript
from altention.connectors.claude_code import commands as cc_commands
from altention.connectors.claude_code import hooks as cc_hooks
from altention.connectors.claude_code import launch as cc_launch
from altention.connectors.claude_code import typist as cc_typing

from . import posts as posts_mod
from . import agents, ask, attachments, awaiting, calls, cc_btw, cc_health, cc_probe, cc_watch, config, deliver, events, frames, history, lanes, ledger, masking, menus, messages, blocked, peers, process, quota, remote, routes, sessions, rewrite, sources, startup_ask, tables, tmuxio, unsent as unsent_mod
from .claude_bridge import deny
from .posts import Origin
from .util import same_socket, split_message, target
from . import where as where_mod

SPOOL = os.path.join(config.STATE, "events")
SETTINGS_FILE = os.path.join(config.STATE, "claude-settings.json")
HOOK = os.path.join(config.ROOT, "tools", "dchook")
HOOK_EVENTS = cc_hooks.EVENTS
START_SECS = 90      # how long a session may take to come up
# C-161: Claude Code 2.1.318 may ask, when a conversation older than 70 minutes and over 100k tokens is resumed,
# whether to go on from a summary. The bot always resumes the whole conversation: never asked, in the conversations
# it opens only (the owner's own settings and terminals are left as they are).
RESUME_ENV = {"CLAUDE_CODE_RESUME_THRESHOLD_MINUTES": "1000000000", "CLAUDE_CODE_RESUME_TOKEN_THRESHOLD": "1000000000000"}
SUBMIT_DELAY = 0.3   # Enter pressed right after typing can get lost
BTW_SECS = 90        # how long a /btw side question may take
RECEIPT_SECS = 5     # how long to wait for UserPromptSubmit before pressing Enter again
STREAM_MAX_DELAY = 60  # longest wait between a stream's reconnects (one `true` per try; a command getting through
                       # wakes it sooner)
STREAM_MIN_GAP = 5     # shortest time between two reconnects, woken or not (code-review 2026-10-07)
LATE_SECS = 600        # how long a message whose receipt did not come is still matched by a late one
BUSY_MARK = screen.BUSY_MARK  # on screen while Claude works: asked through screen.busy_shown(), never looked for in a whole screen
MACHINE_DOWN_SECS = 300         # another machine's stream down this long, with channels on it: the owner is told (C-103)
RETRY = "繼續。（上一輪因為 API 錯誤中斷了，請從中斷的地方接著做。）"
FAILURES = events.FAILURES  # the words for why a request failed (events.failure())
NOW_OWNER = "#%s：你要求立刻送出，已經按了 Ctrl+X Ctrl+S"  # (C-176; words #release)
NOW_CLAUDE = "#%s：%s要求立刻送出，已經按了 Ctrl+X Ctrl+S"


def now_said(message):
    """The #audit line for a send-now pressed (C-176): the owner's !!, or a Claude's `altention dc post --now` (a
    post's stand-in message says who sent it: "#st-1" is "#st-1 的 Claude", a name off a channel is said as it is)."""
    where = getattr(getattr(message, "channel", None), "name", None) or "DM"
    if not getattr(message, "auto", False):
        return NOW_OWNER % where
    who = getattr(message, "sender_name", None) or "別的地方的 Claude"
    return NOW_CLAUDE % (where, who + " 的 Claude " if who.startswith("#") else who)


KEPT_SECS, KEPT_MAX = 600, 500  # events of a channel Discord does not hand out just now: how long, how many (keep())
RESEND_FIRST, RESEND_MAX = 30, 600  # a refusal a machine did not take: sent again after this long, doubled each time, up to
WATCH_SECS = 8           # between the looks of watch_busy() at a running turn
WATCH_SAY_SECS = 120     # a turn's machine silent for this long: the channel is told, once
WATCH_SILENT_SECS = 60   # between the looks from then on
LANE_KINDS = (cc_hooks.PROGRESS, cc_hooks.PEER_SENT)  # what only adds to the channel's lane: never held for it
SLOW_ROUND = 2           # seconds: a pump round longer than this is said in supervisor.out, with its slow parts (C-73)
# Slash commands that do harm when typed from Discord: refused outright, or sent only after a button press. Which
# commands, and what kind of harm, the connector says (commands.REFUSED, commands.CONFIRM: a code for each); the words
# for each harm are here. A code with no words here (a connector ahead of the bot) is shown as it is.
HARM = {cc_commands.LOGS_OUT: "會登出這台機器上所有的 Claude Code 對話",
        cc_commands.ENDS: "會結束這個對話（頻道會對著一個空的終端機）",
        cc_commands.LEAVES_PANE: "對話會離開這個 tmux 窗格，頻道會對著一個空的終端機",
        cc_commands.RESTORE_PANEL: "會打開「還原對話和程式碼」的面板（選錯會蓋掉檔案）"}


def harm_tables():
    """(the commands refused, the commands asked about first): each with the words for its harm."""
    worded = lambda table: dict((cmd, HARM.get(code, "（%s）" % code)) for cmd, code in table.items())  # noqa: E731
    return worded(cc_commands.REFUSED), worded(cc_commands.CONFIRM)


BLOCKED, CONFIRM = harm_tables()


# The kinds of event (the connector's hooks.normalize) with which a turn has moved: a dialog still on the screen is
# then the turn's own and not one the bot opened; and those with which the session has moved on, so that the buttons
# of a dialog shown earlier would mislead. (Until stage 3b step 2.2b these were lists of hook names; a tool call from
# inside a subagent counted for the first, and does.)
LANE_KEPT_RESTART = "重啟時，別台傳來的事件還有沒處理完的，丟掉了：%s"  # (C-174; words #release)
LANE_KEPT_REPLY_GONE = "；含回覆的頻道少貼了那則回覆，可以在那個頻道用 /screen 看 Claude 的終端機畫面（已經捲走的話，要到那台的 tmux 看）"  # (!recap reads this
# machine's files only; these channels are another machine's)
LANE_KEPT_HANDOVER = "交出機器人時，別台傳來的事件還有沒處理完的：%s；回覆由接手的機器人補上"
LANE_KEPT_SAY_SECS = 5
TURN_MOVED = (cc_hooks.INPUT, cc_hooks.PROGRESS, cc_hooks.PEER_SENT, cc_hooks.AGENT_PROGRESS, cc_hooks.TURN_END, cc_hooks.READY,
              cc_hooks.SESSION_CHANGED, cc_hooks.COMPACTED, cc_hooks.SESSION_ENDED, cc_hooks.SESSION_LEAVING)
MOVED_ON = (cc_hooks.INPUT, cc_hooks.PROGRESS, cc_hooks.PEER_SENT, cc_hooks.TURN_END, cc_hooks.SESSION_ENDED, cc_hooks.SESSION_LEAVING)


class BridgeError(Exception):
    pass


Typing = cc_typing.Typing  # what the checks before typing came to (the connector's since 3b 2.7b-1)


class Kept(object):
    """What the typing remembers of a channel between messages, where the bot keeps it (cc_typing.may_type()'s
    `memory`): entered, left and typed on the channel's runtime, own_panel in its state (2.7c: kept across restarts)."""

    def __init__(self, st, rt):
        self.st, self.rt = st, rt

    entered = property(lambda self: self.rt.entered, lambda self, v: setattr(self.rt, "entered", v))
    left = property(lambda self: self.rt.left, lambda self, v: setattr(self.rt, "left", v))
    typed = property(lambda self: self.rt.typed, lambda self, v: setattr(self.rt, "typed", v))

    @property
    def own_panel(self):
        return self.st.get("own_panel")

    @own_panel.setter
    def own_panel(self, value):
        if value:
            self.st["own_panel"] = value
        else:
            self.st.pop("own_panel", None)


class NotOpened(BridgeError):
    """A conversation could not be opened. `why`: which of the reasons (a fixed word, for whoever shows it otherwise
    than by these words: step 2.6), `data`: what the reason is about. The message is what the channel is told."""
    NO_TMUX = "no-tmux"            # tmux itself could not be run: {"error"}
    NOT_STARTED = "not-started"    # the bot's own session did not come up: {"screen"}
    NO_WINDOW = "no-window"        # no window could be opened in the owner's tmux: {"session", "said"}
    PANE_GONE = "pane-gone"        # the pane the channel is attached to runs no agent any more: {"where"}
    UNREACHABLE = "unreachable"    # the machine of the attached pane cannot be reached: {"host", "where", "said"}
    PERMISSION = "permission"      # the permission mode set is not one there is: {"value", "valid", "whose"}
    SETTINGS = "settings"          # the installation's settings could not be read: {"error"}
    PERMISSION_UNSET = "permission-unset"  # neither the channel nor the installation says the permission mode: {}
    MODEL = "model"                # the model set is nothing a command line can hold: {"value", "whose"}
    BACKGROUND = "background"      # a move's /exit left the conversation running in the background (C-201 2c1): {"session"}

    def __init__(self, why, text, **data):
        BridgeError.__init__(self, text)
        self.why, self.data = why, data


def resume_command(st):
    """The line that takes up the channel's conversation again, in the owner's own tmux."""
    return "claude --resume %s" % st["session_id"][:8] if st.get("session_id") else "claude --resume"


def unreachable(pane):
    """The NotOpened for a pane on another machine that was asked and did not answer (tmuxio.standing(): "unknown").
    What was tried is remote's to say (remote.unreachable(): the asking itself went every way it had)."""
    where, said = tmuxio.label(pane), remote.unreachable(pane.get("host"))
    return NotOpened(NotOpened.UNREACHABLE, messages.not_sent("machine_unreachable", said=said, where=where),
                     host=pane.get("host"), where=where, said=said)


def not_opened(rt, e, message=True):
    """What it comes to when the channel's conversation could not be opened (a BridgeError of ensure()): by the code
    of why. A machine that cannot be reached is a reason of its own, and one that passes; every other is said as
    it was, the error's own words. `message`: it was to be opened for a message, which was then not sent; False
    when the channel is only being given its conversation (the setup card, an archived channel taken back, altention dc post
    --new-claude): there is no message to send again, the next one opens it."""
    rt.outcome = r = Typing()
    if getattr(e, "why", None) == NotOpened.UNREACHABLE:
        r.refuse("machine_unreachable" if message else "machine_unreachable_unopened", said=e.data["said"], where=e.data["where"])
    else:
        r.refuse("not_started", error=e)
    return r


UNFOLLOWED = ("This conversation is no longer followed from Discord (its channel was detached). "
              "Ask in plain text here in the terminal instead.")


def sessions_named(channels_state):
    """Every tmux session name a channel's state holds, in each of the ways one can: the channel's own session
    on the bot's server (st["tmux"]); the session a window or a terminal was opened in (st["home"]["session"]:
    the setup card can put either in a session of the bot's server); the session of an attached pane
    (st["pane_session"]); and what an archived channel remembers for being taken back (st["archived"]:
    pane_session, home.session). tests/test_reap.py holds this against every place that writes one."""
    out = set()
    for st in channels_state.values():
        if not isinstance(st, dict):
            continue
        rec = st.get("archived") if isinstance(st.get("archived"), dict) else {}
        for name in (st.get("tmux"), (st.get("home") or {}).get("session"), st.get("pane_session"),
                     rec.get("pane_session"), (rec.get("home") or {}).get("session")):
            if name:
                out.add(str(name))
    return out


WAIT = "wait"  # ownerless(): not to be closed now, and nothing for the owner to decide either
LATE_ENDS = "turn-ends-late.json"  # in config.STATE: how many turns' ends waited out the limit for their row (note_late())
UNREACHABLE = "channels-unreachable.json"  # in config.STATE: channels Discord has not handed out for long (note_away())
UNPLACED_LOG, UNPLACED_MAX = "events-unplaced.log", 200 * 1024  # in config.STATE: let_be()
UNPLACED_WHY = ("a background process of Claude Code that is not the channel's conversation (its spare process, or a "
                "conversation typed into the agents list)")
RESPAWNED_WHY = ("another process in the attached pane than the route has, with another conversation than the channel's "
                 "(a tmux server started again, or a new conversation in the pane)")
PARKED = "panes-parked.json"  # in config.STATE: channels whose pane's conversation was moved to a background process (note_parked())
OWNERLESS = "ownerless-sessions.json"  # in config.STATE: the sessions reap_ownerless() did not close, and why


def note_ownerless(kept):
    """For doctor: the bot's sessions whose channel is gone and that were not closed (each with why), as of the
    last look. An empty list when there is none."""
    path = os.path.join(config.STATE, OWNERLESS)
    try:
        os.makedirs(config.STATE, exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"at": time.time(), "kept": kept}, f, ensure_ascii=False)
        os.replace(path + ".tmp", path)
    except OSError:
        traceback.print_exc()


def put_away(st):
    """Is this the state of a channel !detach put away, and nothing has taken it up again? ("接回原本的對話",
    /attach of its pane and the setup card each clear the record or give the channel a session.)"""
    return bool(st.get("archived")) and not (st.get("pane") or st.get("tmux") or st.get("home"))


def window_of(st):
    """The window a channel has, as a value that can be kept and compared: an attached pane or a window the bot opened
    in the owner's tmux by its pane, a session on the bot's own tmux server by its name; None when it has none."""
    pane = st.get("pane")
    if isinstance(pane, dict):
        return [pane.get("host"), pane.get("socket"), pane.get("id")]
    return ["tmux", st["tmux"]] if st.get("tmux") else None


def parked(st):
    """What the channel remembers of its window's conversation having been moved to a background process (the left key
    on an empty input box in the terminal; the agents list): {"session", "since", "pane", …}, or None. Told by the
    hook, event by event (tools/dchook: "dc_parked"), for this machine's panes and another's alike; it holds for
    the window it was said of, and no longer once that window's own Claude Code says anything. From then on the
    pane may show that conversation, another background one, or the list: nothing Claude Code writes says which
    (measured 2026-10-13), so what is typed into the pane may not reach the channel's conversation."""
    rec, where = st.get("parked"), window_of(st)
    if not isinstance(rec, dict) or where is None or rec.get("pane") != where:
        return None
    return rec


def unplaced(st, ev):
    """Is this an event of a background process of Claude Code's that names the channel by its variable, and that is
    not the channel's conversation? The hook marks what it could place with no pane ("dc_unplaced", tools/dchook):
    every background process started from a window the bot opened has that window's hooks and DCBOT_CHANNEL, the
    spare one and every conversation typed into the agents list too. The one that is the channel's is the
    conversation moved out of the window, and it is known here by its id: while the window's Claude Code was there
    its events came placed (the first, its SessionStart, made its id the channel's); once that process has ended
    nothing on its machine ties the conversation to a pane any more, and it still ends the turn it was in (measured:
    its reply was posted before this step, and not posting it would be a step back). Any other id: not the
    channel's, whatever the event is; its start must not become the channel's session."""
    return bool(ev.get("dc_unplaced")) and (not st.get("session_id") or ev.get("session_id") != st.get("session_id"))


def stranger(st, ev):
    """Is this an event the route table's hook sent for a pane whose process is no longer the one the route has
    ("dc_respawned": the pane's process now, tools/dchook routed()), and that is not the channel's conversation?
    Claude Code started again in the same pane (respawn-pane; a pane that is Claude itself, with no shell around it)
    is the channel's when it goes on with the channel's conversation: `--resume <id>` and `-c` keep the id (measured,
    2.1.306), and the route then follows it (repin()). Any other id is another Claude Code that got the pane's
    number (a tmux server started again) or a new conversation: as before C-79, nothing of it is the channel's."""
    return bool(ev.get("dc_respawned")) and (not st.get("session_id") or ev.get("session_id") != st.get("session_id"))


STALE_SAY_SECS = 5
AGAIN_SECS = 2  # C-198: a new process in an attached pane whose record could not be read: asked again this much later
STALE_WAIT_SECS = 300  # C-162: how long a window to be reopened is let finish the turn it runs
PANE_SAID_SECS = 6 * 3600  # an attached pane whose screen is not known: said at most this often (C-162)
CLOSED_KEPT = 5
CLOSED_END = "closed"  # from_elsewhere(): the end of a window the bot closed itself (truthy, like any not the channel's)
CLOSED_SECS = 60  # how long a window the bot closed is remembered as closed, when nothing of it or of its number comes


def leading(host):
    """host.leader_ok(), where it has one that says False; True otherwise (no standby, a test's host)."""
    way = getattr(host, "leader_ok", None)
    return not callable(way) or way() is not False


def place(t):
    """What tells one window from another for target(): a pane's machine, socket and id (not its pid, agent and the
    like, written into the record as they are learnt), or the bot's session name."""
    if isinstance(t, dict):
        return (t.get("host") or None, t.get("socket"), str(t.get("id")))
    return t


def opened(st, rt):
    """A window is open for the channel (st["pane"], or its session on the bot's own tmux): one closed for print
    turns (cc_stale, C-162) is no longer in the way. A window the bot closed lately in the same pane, with the
    conversation the channel has now, is no longer told from it (from_elsewhere()), and is forgotten. Not left to
    the new window's SessionStart alone, which may never come."""
    st.pop("cc_stale", None)  # C-162: the window is a new one, opened with --resume
    st.pop("cc_told", None)  # (and the next time it is held down, its first reply says what is missing again)
    now = st.get("pane")
    if not isinstance(now, dict) or not getattr(rt, "closed", None):
        return
    alike = lambda p: (p.get("host") or None) == (now.get("host") or None) and str(p.get("id")) == str(now.get("id")) and \
        same_socket(p.get("socket"), now.get("socket"), here=not now.get("host"))  # noqa: E731
    rt.closed[:] = [p for p in rt.closed if not (alike(p) and p.get("session") == st.get("session_id"))]


def from_elsewhere(st, opening, ev, closed=None):
    """Does this event, which names the channel by the DCBOT_CHANNEL of the window it comes from, come from a
    pane that is not the channel's now? (Manager 2026-10-11: whether an event is the channel's is not told by
    that variable, nor by the channel being put away alone, but by the window the channel has.)

    A window the bot opened keeps DCBOT_CHANNEL as long as it lives. Detached, with the channel then given a new
    conversation ("在這裡開新對話", the setup card), every turn the old window ran was posted there again, and its
    session id written over the new conversation's: the channel's next start would have resumed the old one,
    two processes on one conversation (#code-review 2026-10-11).

    Told by the pane (tools/dchook's came_from), not by the session id: /clear, /resume and a restart with
    --resume change the session in the same pane, and are the channel's own. The channel's pane is the window
    it has (st["pane"]); while one is being opened for it (`opening`: True from before the window exists, then
    its pane) that one counts too, its SessionStart comes before the channel remembers it. A channel on the
    bot's own tmux server (st["tmux"]) takes what comes from that server: its session is closed and started
    again under one name, and an old and a new process there are both the channel's. A channel with neither
    has no window: whatever names it comes from elsewhere. Events that say nothing of where they come from (not
    in tmux, a hook of before this) are not judged here; nor are the route table's for a pane that was attached
    (the table says whose they are), nor another machine's that did not come by its route table. The route
    table's for a window the bot opened ("dc_routed", and the channel has a home) are judged like any: a window
    the bot opened on another machine reports that way, and when the bot closes it (!resume, !reset, !cwd, !mode,
    an idle one put down) its end comes after and was said as "the window was closed?" (#issues 35). The pane is in the
    event's "dc_pane"; "source" is a field of Claude Code's own (how a session started: the history of a
    conversation switched to with /resume is posted by it)."""
    src = ev.get("dc_pane")
    if not isinstance(src, dict) or not src.get("pane") or not src.get("socket"):
        return False
    far = ev.get("host") or None
    if ev.get("dc_routed") and not st.get("home"):
        return False  # by the route table (tools/dchook --global), for a pane that was attached: the table says whose
        # it is. (The same event of a window the bot opened on another machine, which reports by the table too, is
        # judged as below: #issues 35.)
    if far and not ev.get("dc_routed"):
        return False  # another machine's, and not by its route table: as it always was, not judged
    same = lambda pane: isinstance(pane, dict) and (pane.get("host") or None) == far and str(pane.get("id")) == str(src["pane"]) and \
        same_socket(pane.get("socket"), src["socket"], here=not far)  # noqa: E731
    if closed:
        # A window the bot closed a moment ago (`closed`: Runtime.closed, kept by close() with the conversation the
        # channel had in it). Its end comes after, and may come while the next window is being opened, when every
        # pane's events count as the channel's (below): it was then said as an end nobody asked for (#code-review-2,
        # a message 0.2 s after the close). By the pane's number alone it cannot be told from the next window:
        # closing the last window of a tmux server ends the server, and the new one's first pane has the same
        # number again. Nor by waiting for the new window's SessionStart: on another machine that one may be sent
        # before the route is written and never come (wait_ready() says so), and a whole turn was then taken for
        # the closed window's (#code-review on ddf02aa). So by the conversation, and only for the one event a closed
        # window is known to send after it: a SessionEnd from that pane, with the id the closed window had, is
        # the closed window's. (Measured, #code-review-2 2026-10-14: 48 windows closed in the middle of a turn, on
        # a shared server and on one of their own; after close() each sent its SessionEnd and nothing else.) Every
        # other kind of event is the channel's as it always was, whatever its id: a new window that took the same
        # conversation up in the same pane, its SessionStart lost, has its first turn posted even when it comes
        # before the window is known open (2.6c; 14b8c85 swallowed that turn).
        # Anything else is handled as it always was, rather an end said once too often than a turn swallowed:
        #  - another id: the new window's (or the closed one's after a /clear the bot never saw: said, as before).
        #    The record stays, it only ever matches the old id;
        #  - no id in the event: not told, so not judged here;
        #  - a SessionStart with the old id: the new window took that conversation up again in that pane; from
        #    here on the two cannot be told apart, the record goes. (opened() drops it likewise when the window
        #    is known to be open, for the SessionStart that never came.)
        closed[:] = [p for p in closed if time.time() - p.get("at", 0) < CLOSED_SECS]
        sid = ev.get("session_id")
        hit = [p for p in closed if same(p) and sid and p.get("session") == sid]
        if hit and ev.get("hook_event_name") == "SessionStart":
            closed[:] = [p for p in closed if p not in hit]
        elif hit and ev.get("hook_event_name") == "SessionEnd":
            return CLOSED_END  # (not the channel's, and nothing to say of it: the bot closed it and said so)
    if opening is True:
        return False
    if same(opening) or same(st.get("pane")):
        return False
    if st.get("pane") or st.get("home"):
        return True  # it has a window, and this is not it
    if st.get("tmux"):
        return bool(far) or os.path.basename(str(src["socket"])) != config.TMUX_SOCKET
    return True


class Pending(object):
    """A Discord message typed into the session, waiting for Claude to confirm it arrived."""
    def __init__(self, message, text):
        self.message, self.text, self.got = message, text, asyncio.Event()
        self.late = None  # when it went into Runtime.late


class Turn(object):
    def __init__(self, prompt="", src=None):
        self.t0, self.prompt, self.tools, self.lines = time.time(), prompt, {}, []
        self.src = src  # (transcript path, byte where the turn starts): for 📋 on its reply
        self.msg, self.last = None, 0.0  # the "working…" message and when it was last edited
        self.gen = 0          # one more each time the "working…" message goes (or goes to the bottom): one being sent
        self.closed = False   # meanwhile is deleted when it comes (lanes.py); closed: the turn is over
        self.sending = None   # a "working…" message being sent (its lane's request, maybe timed out and still going)
        self.pid = None     # the prompt_id of its events (Claude Code's own id of the turn), once one brought it
        self.quiet = False  # the screen showed no work for a while (watch_busy): waiting in a dialog, or cut short


class Runtime(object):
    """What the bot keeps in memory about one channel (rebuilt from events after a restart)."""
    def __init__(self):
        self.lock = asyncio.Lock()    # one message is typed at a time
        self.ready = asyncio.Event()  # set when the session reports SessionStart
        self.pending, self.turn, self.typing = [], None, None
        self.typed = []  # slash commands typed lately: their UserPromptSubmit is ours, not the terminal's
        self.late = []   # messages typed while the machine's stream was down, receipt not in time (the last 5,
                         # for LATE_SECS): a late receipt is still ours, not the terminal's
        self.btw_from = None  # the thread the last /btw question came from
        self.entered = None     # the last message Enter was pressed on in an attached pane, until its box is seen empty
        self.left = None        # a message typed into an attached pane and left there: its Enter was not pressed
        self.last_final = ""  # the last turn's reply: it can still be trickling into the file when the next turn is read
        self.pos = {}  # what the connector's enrich keeps for each transcript of this channel (hooks.place)
        self.where = None  # (transcript, byte where the running turn begins), as the last event told: for 📋
        self.opening = None   # a window being opened for the channel (ensure_home): True, then its pane, until it is kept
        self.opens = 0        # one more each time open() begins: look_again() tells a window opened meanwhile by it
        self.ready_sid = None  # the conversation id of the last SessionStart (bot/moving.py checks a move by it)
        self.outcome = None   # what the checks before the last message typed came to (Typing; may_type())
        self.closed = []      # windows the bot closed for the channel lately (close()), until one of their number starts anew
        self.unanswered = False  # the last opening gave up waiting on the owner's answer to a startup dialog (wait_ready)
        self.lane = None      # what its turns show along the way, posted in order outside the round of events (lanes.py)
        self.ready_why = None  # why the last wait_ready() gave up, when it ran out of time


def write_settings():
    """Settings for bot sessions only: the secrets deny rules, plus hooks that report back to the bot. (The
    connector words the file, and leaves one that says the same already as it is.)"""
    os.makedirs(SPOOL, exist_ok=True)
    return cc_launch.write_settings(SETTINGS_FILE, deny(), "'%s'" % HOOK)


def installation(setting):
    """One setting of this installation (its settings file); None when it has none of that name."""
    return remote.alt().setting(setting)


def settings_read_well():
    """Can the installation's settings be read at once: no instance, no settings file, or one that is a JSON object
    as it stands? (Then the engine's reading does not wait.) Anything else, the instance not found too: no."""
    try:
        alt = remote.alt()
        inst = alt.instance_dir()
        path = os.path.join(inst, "plugins.json") if inst else None
        return not path or not os.path.exists(path) or isinstance(alt.read_json(path), dict)
    except Exception:
        return False


def chosen(st, key, setting, last):
    """What a channel's conversations are opened with (`key`: "model" or "permission"), and whose word it is:
    (value, "channel" | "installation" | None). The channel's own, else the installation's (`setting` in its
    settings), else `last` (None for the permission mode: then nothing is opened, permission_of())."""
    if st.get(key):
        return st[key], "channel"
    value = installation(setting)
    return (value, "installation") if value else (last, None)


UNSET = ("這個安裝還沒選權限模式，所以沒有開對話。到 #setup 回答「權限」那一題；沒有這一題的話，到 #chat 跟 Claude 說"
         "「權限模式設成 auto」（auto、manual、bypassPermissions 三選一）。選好再傳一則訊息就會開。"
         "（設定在 plugins.json 的 dcbot.default_permission）")


def permission_of(st):
    """The permission mode a conversation of this channel is opened with: the channel's own, else the
    installation's dcbot.default_permission. NotOpened when neither says one (C-121: never a mode nobody chose;
    the last resort was bypassPermissions, which lets everything through), when it is no mode there is, or when
    the installation's settings cannot be read. Both ways of opening a conversation ask this (tmux: with_(); print
    mode: main.turn())."""
    try:
        value, whose = chosen(st, "permission", "dcbot.default_permission", None)
    except Exception as e:  # what the installation says of permissions is not known: not opened, and said
        traceback.print_exc()
        raise NotOpened(NotOpened.SETTINGS, "讀不到這個安裝的設定（%s），不知道要用哪個權限模式，沒有開對話。"
                        "那是實例資料夾裡的 `plugins.json`；修好之後再傳一則訊息就會開。" % e, error=str(e))
    if whose is None:
        raise NotOpened(NotOpened.PERMISSION_UNSET, UNSET)
    valid = cc_launch.permissions()
    if not isinstance(value, str) or value not in valid:
        where = "這個頻道設定的權限模式" if whose == "channel" else "這個安裝設定的預設權限模式（plugins.json 的 dcbot.default_permission）"
        raise NotOpened(NotOpened.PERMISSION, "%s「%s」不是可以用的值，沒有開對話。可以用的：%s。"
                        % (where, value, "、".join(valid)), value=value, valid=list(valid), whose=whose)
    return value


async def box_shown(pane):
    """Is the prompt box on the pane's screen now (wait_ready()'s last look)? No when it cannot be read."""
    try:
        return await pane.standing() not in ("gone", "unknown") and screen.has_prompt(await pane.raw_screen())
    except Exception:
        traceback.print_exc()
        return False


def ended_words(end, limit=1200):
    """What tmuxio.ended() read, as words for the channel (keys masked first); None for nothing read."""
    if not end:
        return None
    how = ("結束碼 %s" % end["status"]) if end["status"] else ("被訊號 %s 結束" % end["signal"]) if end["signal"] else "結束了"
    rows = "\n".join(r.rstrip() for r in end["screen"].split("\n") if r.strip())  # what it wrote, without the blank rows
    return "\n" + messages.note("claude_code.resume_ended", how=how) + "\n" + fence(rows, limit)


def fence(text, limit=1500):
    """A screen in a code block, its last `limit` characters, keys masked first (C-127)."""
    shown, said = masking.screen(text)
    return "```\n%s\n```%s" % (shown.replace("```", "'''")[-limit:] or "（空白）", said)


def events_cut(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


def gist(words, most=160):
    """The first sentence of a not-sent notice, as plain words (C-202: what the sender's channel is told)."""
    text = re.sub(r"[*`]", "", words).strip()
    text = re.sub(r"^[^\w（(/]+", "", text)  # (its mark in front)
    first = re.split(r"(?<=[。！？])", text.split("\n")[0], maxsplit=1)[0].strip()
    first = re.sub(r"[，,、]?\s*(?:這則|所以|因此|就)?\s*(?:沒有|沒)送出[。.]?$", "。", first)  # (#discord-lab: said already)
    first = first if first.endswith(("。", "！", "？", "…")) else first + "。"
    return first if len(first) <= most else first[:most - 1] + "…"


UNENTERED_KEPT = 50  # screens kept of the times Enter was not pressed (state/samples/not-entered/)


def unentered_dir():
    """state/samples/not-entered/ (looked up at each call; DCBOT_UNENTERED: the tests' own, tests/tmuxguard.py)."""
    return os.environ.get("DCBOT_UNENTERED") or os.path.join(config.STATE, "samples", "not-entered")


def keep_unentered(name, typed, view, coloured, state, now=None):
    """The whole screen as it was when Enter was not pressed, masked (masking.screen(): a key broken over rows too),
    as one file of state/samples/not-entered/ (only this user's: 600), the last UNENTERED_KEPT kept; with it how
    the bot read it and how long the message was. The message itself is not a field of it, but it is there as far as
    the screen shows it (the rows in the box, and the conversation above it), only keys masked (#code-review on
    cf23197b: say so when telling the owner). The coloured screen only when nothing on the
    plain one was masked (colour codes in a key's middle hide its end from the patterns: screens.post() does the
    same). The file's path, or None when it could not be written (never raises)."""
    try:
        now = time.time() if now is None else now
        folder = unentered_dir()
        os.makedirs(folder, exist_ok=True)
        plain, note = masking.screen(view or "")
        pane_id = name.get("id") if isinstance(name, dict) else str(name)
        path = os.path.join(folder, "%s.%06d-%s.json" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)),
                                                        int(now % 1 * 1e6), re.sub(r"[^A-Za-z0-9_.-]", "_", pane_id)[:40]))
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump({"at": now, "target": pane_id, "host": name.get("host") if isinstance(name, dict) else None,
                       "read_as": state, "typed_chars": len(typed or ""), "typed_rows": (typed or "").count("\n") + 1,
                       "screen": plain, "colour": "" if note else (coloured or "")}, f, ensure_ascii=False, indent=1)
        for old in sorted(n for n in os.listdir(folder) if n.endswith(".json"))[:-UNENTERED_KEPT]:
            os.remove(os.path.join(folder, old))
        return path
    except Exception as e:
        print("[enter] the screen was not kept (%s)" % type(e).__name__, flush=True)
        return None


class TmuxBridge(object):
    """`host` is the Discord bot: needs chan(channel), save(), audit(text), note(channel, st), get_channel(id)."""

    def __init__(self, host):
        self.host, self.rts = host, {}
        self.not_known_yet = set()  # channel ids whose pane pane_again() last found running a Claude Code whose
        # conversation could not be read yet (just restarted): ensure() says that, not "runs no Claude Code"
        self.asks = ask.Asker(host.audit)
        self.asks.on_owed = self.start_resends  # a result for a hook on another machine: sent by that machine's task
        if getattr(host, "posts", None) is not None:
            self.asks.on_done = host.posts.answered
        self.asks.on_asked, self.asks.on_closed = self.question_asked, self.question_closed  # C-103: the owner's mention
        self.down_since = {}  # machine -> when its stream went down (machine_watch())
        self.agents = agents.Agents(host)
        self.menus = menus.Menus(self)
        self.unsent = unsent_mod.Unsent(self)  # C-74: what could not be handed over for now, sent again
        self.quota = quota.Quota(self)  # C-99a: a usage limit, and Claude Code's own wait for its reset
        self.cc = cc_watch.Watch(self, probe=cc_probe.Probe(SETTINGS_FILE, permission_of))  # C-162 (inert in tests)
        self.rewriting = set()  # channel ids whose final reply a plugin is rewriting (C-71b): their events wait
        self.startup = startup_ask.StartupAsk(self)
        self.streams = {}  # machine -> the SSH process bringing its events
        self.handling = False  # an event is being posted: a restart waits for it (main.Bot.busy_with_work)
        self.held = set()  # channels whose next event waits in the spool for its row (pump_once)
        self.ticked = 0.0  # when subagents, altention dc post and the chores last had their turn in pump_once
        self.streaming, self.streams_checked = set(), time.time()  # pump() starts the streams; pump_once() rechecks each minute
        self.stream_down = set()  # machines whose stream is not bringing events now (not yet connected, or reconnecting)
        self.stream_wake = {}     # machine -> the Event its stream sleeps on before reconnecting
        self.lane_held = {}       # channel id -> its events from another machine's stream that wait on its lane (C-174)
        self.lane_tasks = set()   # the tasks handing those on, one per channel while it has any
        self.tell_tasks = set()   # telling a sender's Claude what became of its post (held: a task nobody holds may go)
        self.let_go_said = set()  # channels put away whose events were dropped, and #audit told once (this run)
        self.kept = {}  # channel id -> [when the first was kept, events, being released?]: of channels Discord does
        #                 not hand out just now
        self.kept_said = set()   # channels a line was written about (kept; dropped), once each while the bot runs
        self.away = {}           # channel id -> [since when, events dropped]: not to be had for longer than KEPT_SECS,
        #                          state still here
        self.resending = {}      # machine -> the Event its refuse_again() sleeps on (one task for a machine)
        self.unknown_said = set()  # what a line has been written about: sorts of event of a kind not known, and
        # pairs (the kind an event came with, the kind it is here)
        remote.on_good = self.reached

    def reached(self, host):
        """A command or probe got through to `host`: a stream waiting to reconnect to it tries now."""
        for ev in (self.stream_wake.get(host), getattr(self, "resending", {}).get(host)):  # (and a refusal owed to it is sent now)
            if ev is not None:
                ev.set()

    def stream_lost(self, st):
        """The channel's machine, when it is another one whose stream is down now; else None."""
        host = (st.get("pane") or {}).get("host") or (st.get("home") or {}).get("host")
        return host if host and host in self.stream_down else None

    def rt(self, ch):
        return self.rts.setdefault(str(ch.id), Runtime())

    async def say(self, ch, text, silent=False):
        """Post text (split as needed); returns the messages sent. `silent`: no push notification (what
        Claude says along the way); replies, questions and failures do ring."""
        sent = []
        for part in split_message(text):
            try:
                sent.append(await deliver.deliver(ch, part, silent=True, intent="status") if silent
                            else await deliver.deliver(ch, part, intent="status"))
            except Exception:
                traceback.print_exc()
        return sent

    # ------------------------------------------------------------------ session
    async def foreign_window(self, name):
        """Is the bot's window `name` one the other bot of a standby pair opened here (C-163: a window opened from
        another machine carries no settings file: no --settings, or one with only the folders it reads, as JSON;
        one this bot opens always names a file)? Only with a standby: without one
        nothing changes and no window is ever taken for a foreign one (#code-review on 3223faf, manager). False when
        its command line cannot be read: nothing is closed on a guess."""
        if getattr(self.host, "standby_on", False) is not True:
            return False
        cmd = await tmuxio.session_command(name)
        words = (cmd or "").split()  # (--settings <path>, or --settings=<path>: #code-review-2 on 676d4d4)
        given = [words[i + 1] if w == "--settings" and i + 1 < len(words) else w[len("--settings="):]
                 for i, w in enumerate(words) if w == "--settings" or w.startswith("--settings=")]
        return cmd is not None and not any(g and not g.startswith("{") for g in given)  # (JSON: cc_launch.folders_only)

    def argv(self, ch, st, resume, dirs, how=None, claude=None):
        """The command line that opens the channel's conversation (the connector's). On another machine: its own
        claude, and its global hook reports; here: the bot's settings file, whose hooks do. `dirs`: what it reads
        without asking besides its working directory, put in its settings (cc_launch.command(): a copy of the file
        here, JSON there; never --add-dir, which Claude Code 2.1.326 asks about; attachments.folders(): every way a
        conversation is opened comes here, and none may leave them out, or what the owner attaches is read now
        without a question, now with one; C-151). `how`: the model and the permission mode (with()); looked up here when not given."""
        there = bool((st.get("home") or {}).get("host"))
        model, permission = how or self.with_(st)
        return cc_launch.command((claude or "claude") if there else cc_health.binary(), self.host.note(ch, st), model, permission,
                                 settings=None if there else SETTINGS_FILE, resume=resume, dirs=dirs)

    def with_(self, st):
        """(model, permission mode) a conversation of this channel is opened with; NotOpened when one of them is
        no value there can be, when neither the channel nor the installation says the permission mode, or when the
        installation's settings cannot be read and the channel does not say the permission mode itself. Nothing is
        started then, and the channel is told which value, whose it is, and which there are (permission_of()). (A
        model that is a word but does not exist is Claude Code's own to refuse: its words come back with the screen.)
        Reads the installation's settings file: in the event loop through chosen_for()."""
        try:
            model, whose_model = chosen(st, "model", "dcbot.default_model", config.MODEL)
        except Exception:  # the settings cannot be read: the model as it always was (written to the log)
            traceback.print_exc()
            model, whose_model = config.MODEL, None
        value = permission_of(st)
        if not isinstance(model, str):  # a number, a list (a file edited by hand): nothing a command line can hold
            where = "這個頻道設定的模型" if whose_model == "channel" else "這個安裝設定的預設模型（plugins.json 的 dcbot.default_model）"
            raise NotOpened(NotOpened.MODEL, "%s「%s」不是可以用的值（要是一個名稱，例如 opus），沒有開對話。" % (where, model),
                            value=model, whose=whose_model)
        return model, value

    async def chosen_for(self, st):
        """with_(), without the event loop standing still: the engine's reading of a settings file that is broken
        waits (0.4 s the first time, for a file caught while git swaps it). A file that reads well is read here and
        now (as every state file is; and nothing else is handled between a message and the opening of its window:
        what an old window says last would be taken for this conversation's); one that does not, in a thread."""
        if settings_read_well():
            return self.with_(st)
        return await asyncio.get_running_loop().run_in_executor(None, self.with_, dict(st))

    async def pane_again(self, ch, st):
        """An attached pane whose pid is not the one recorded, the same pane number running Claude Code (or an agent
        passing for it) again: the record follows it (pid), said in #audit; True then (C-163 drill problem A, manager
        11-09 (a): a pane attached again whose new pid never reached the standby). Its conversation is the proof when
        it can be read: the channel's own, followed; another, not (C-201 2a's rules for another conversation).
        An adapter (pi, Codex: no Claude Code process record) is followed only by facts it left itself (C-198,
        manager 11-18): the pane tagged for this channel (the tag stays through respawn-pane) and a valid mark of
        its own (tmuxio.marked_agent(): "<agent>|<pid>", the pid the pane's process or under it); its conversation
        id, when it gives one, must be the channel's (2a's rules). Followed so, it is said in the channel too,
        quietly, so an agent the owner changed there is seen. Followed either way, the route table takes the new
        process too, as repin() does (#code-review-2 on e30ad2b3: its events were dropped as a stranger's). None
        when it cannot be told now (the Claude Code record could not be read): asked again later. Anything else (no
        agent there, no mark, no tag of its own, another conversation): False, the pane gone as before."""
        pane = st["pane"]
        now = await tmuxio.moved_on(pane)
        if now is None:
            return False
        sid = None
        if now.get("agent"):
            if now.get("tag") != str(ch.id):
                return False
            if now.get("agent_session") and st.get("session_id") and now["agent_session"] != st["session_id"]:
                return False
        else:
            try:
                sid = await self.host.pane_session_id(dict(pane, id=pane["id"], session=now["session"], cwd=now["cwd"] or st.get("cwd")))
            except Exception:
                traceback.print_exc()
                return None
            if not sid or sid != st.get("session_id"):
                if not sid:  # (still False, as before; only what the channel is told differs: manager 11-20)
                    self.not_known_yet.add(str(ch.id))
                return False
        was = pane.get("pid")
        pane = dict({k: v for k, v in pane.items() if k != "agent"}, pid=int(now["pid"]) if str(now["pid"]).isdigit() else now["pid"])
        if now.get("agent"):  # (the adapter's name in the record: where.record()'s kind, rename types nothing; C-198)
            pane["agent"] = now["agent"]
        where_mod.put(st, pane=pane)
        self.host.save()
        if not st.get("home") or st["home"].get("host"):  # (a window this bot opened here reports through its
            if pane.get("host"):                          # --settings, no route: remember_pids()); awaited:
                await self.bind_far(pane, ch.id)          # moved_on() above asked that machine already
            else:
                routes.bind(pane, ch.id)
        name, where = getattr(ch, "name", "?"), tmuxio.label(pane)
        try:
            if sid:
                await self.host.audit(messages.lease("audit_pane_followed", channel=name, where=where, was=was,
                                                     pid=now["pid"], session=str(sid)[:8]))
            else:
                await self.host.audit(messages.lease("audit_pane_followed_tag", channel=name, where=where, was=was,
                                                     pid=now["pid"], agent=now["agent"]))
                await deliver.deliver(ch, messages.lease("pane_followed_tag"), silent=True, intent="status")
        except Exception:
            traceback.print_exc()
        return True

    async def ensure(self, ch, st, rt):
        """Make sure the channel's session is running; resume the saved conversation if there is one."""
        if (st.get("took_from") or {}).get("pending"):  # C-163: the old bot machine's channel, that machine not
            raise unreachable({"host": st["took_from"]["machine"], "socket": "", "id": "?"})  # reached yet: never
            # opened here (section 4.1)
        if st.get("move_background"):  # (C-201 2c1: the conversation still runs, in the background; another
            sid = st["move_background"].get("session") or "?"  # opened on it would be a second Claude)
            raise NotOpened(NotOpened.BACKGROUND, messages.move("in_background", sid=sid), session=sid)
        if st.get("home"):  # a window the bot opens in the owner's own tmux (chosen on the setup card)
            return await self.ensure_home(ch, st, rt)
        if st.get("pane"):  # the owner's own terminal: nothing to start, and never anything to restart
            attached = tmuxio.pane(st["pane"])
            how = await attached.standing()
            if how == "alive":
                return
            if how == "unknown":
                raise unreachable(st["pane"])
            self.not_known_yet.discard(str(ch.id))
            again = await self.pane_again(ch, st)
            if again is None:  # its Claude Code record could not be read: asked once more, then gone as before
                await asyncio.sleep(AGAIN_SECS)  # (C-198)
                again = await self.pane_again(ch, st)
            if again:
                return
            if str(ch.id) in self.not_known_yet:  # (a new Claude Code there, not yet known to be this channel's)
                self.not_known_yet.discard(str(ch.id))
                raise NotOpened(NotOpened.PANE_GONE, "這個頻道的窗格剛重開（%s），還在確認是不是同一個對話，訊息沒有送出，"
                                "等一下再試。" % attached.label, where=attached.label)
            if st.pop("bg", None):  # what ran under it ended with it
                self.host.save()
            resume = resume_command(st)
            raise NotOpened(NotOpened.PANE_GONE, "這個頻道接的 tmux 窗格已經沒有在跑 Claude Code 了（%s）。"
                            "訊息沒有送出。窗格還在的話，在那裡跑 `%s` 就會自動接回。窗格不在了（例如機器重開過）："
                            "在 tmux 開一個視窗，到 `%s` 跑同一行，再用 `/attach` 選它，會接回這個頻道。不要了就用 `!detach` 卸下。"
                            % (attached.label, resume, st.get("cwd")), where=attached.label)
        if "tmux" not in st:
            where_mod.put(st, getattr(ch, "name", None), tmux=tmuxio.session_name(getattr(ch, "name", "dm"), ch.id))
        name = st["tmux"]
        own = tmuxio.pane(name)
        alive = await own.alive()
        if not alive:  # renamed in tmux? (C-201 6.3) its Claude is taken again, not a second one opened on the same conversation
            renamed = await self.renamed_session(ch)
            if renamed:
                was, name = name, renamed
                where_mod.put(st, tmux=renamed)
                own = tmuxio.pane(name)
                self.host.save()
                await self.host.audit("#%s 的 tmux 對話 `%s` 在 tmux 被改名為 `%s`：照用改名後的，沒有另開"
                                      % (getattr(ch, "name", ch.id), was, renamed))
                alive = await own.alive()
        if alive:
            if not st.get("cc_stale") and await self.foreign_window(name):
                st["cc_stale"] = True  # a window of this name the other bot opened here while it was the bot (C-163:
                # no --settings, its hooks went by a route that is gone now): closed and opened again with --resume,
                # the conversation kept (drill 11-08, manager)
            if not st.get("cc_stale"):
                return
            await self.print_done(ch)
            await self.stale_turn_done(ch, st, own)
            await self.close(ch)  # C-162: turns ran in print since this window was opened: its Claude Code does not
            # know them; reopened with --resume, which reads them from the session file (#code-review-2)
        seen = {}
        await self.print_done(ch)  # (C-162) a print turn of the channel still running or queued: it ends first

        async def start(resume, how, dirs):
            try:
                started = await tmuxio.start(name, st["cwd"], self.argv(ch, st, resume, dirs, how), self.env(ch))
            except OSError as e:
                raise NotOpened(NotOpened.NO_TMUX, "無法執行 tmux：%s" % e, error=str(e))
            return own if started else None

        async def kept(pane, fresh):
            await tmuxio.settled(name)
            if fresh:
                st.pop("bg", None)

        async def failed(pane):
            end = await tmuxio.ended(name)  # its Claude ended: why, read once and the dead session closed (C-161)
            seen["view"] = end["screen"] if end else (await own.screen(15) if await own.alive() else "")
            await own.close()
            return ended_words(end)

        if await self.open(ch, st, rt, start, kept, failed, None):  # this machine's (the bot's own tmux)
            return
        view = seen.get("view")
        raise NotOpened(NotOpened.NOT_STARTED, "Claude 沒有啟動成功。%s" % (("最後的畫面：\n" + fence(view)) if view else ""),
                        screen=view or "")

    def reopen_off(self, version):
        """A pin to Claude Code `version` (C-162 section 2.0 step 1): every window of this machine's own conversations
        opened with another version is marked to be reopened (cc_stale) at its next message, with --resume, on the
        pinned file; else "fell back" would be false, the broken windows still in use (#code-review-2 on 0c0b58d)."""
        states = getattr(self.host, "channels_state", None) or {}
        marked = False
        for st in states.values():
            if isinstance(st, dict) and cc_health.applies(st) and st.get("cc_version") and st["cc_version"] != version:
                st["cc_stale"] = True
                marked = True
        if marked:
            self.host.save()

    def held_channels(self):
        """The channels run otherwise than they would (C-162): conversations the bot opened here whose way of running
        is broken now (cc_health.effective() differs from their own mode)."""
        states = getattr(self.host, "channels_state", None) or {}
        return [k for k, st in states.items() if isinstance(st, dict) and posts_mod.listens(st) and
                cc_health.effective(st.get("mode") or config.BRIDGE, st) != (st.get("mode") or config.BRIDGE)]

    async def box_unknown(self, ch, st, name, r):
        """A message not sent because the pane shows no box the bot knows, and nothing else it knows either (no
        menu, no panel, no startup dialog): (c) of C-162 section 2.1, Claude Code's screen changed under it. Counted
        for this machine's own conversations; an attached pane here (the owner's own Claude Code, never held down)
        is told in its channel once in a while, with what the owner can do."""
        if getattr(r, "code", None) != "claude_code.dialog" or not cc_watch.ACTIVE:
            return
        try:
            view = await tmuxio.raw_screen(name)
        except Exception:
            return
        menu, panel = panels.parse_menu(view), panels.parse_panel(view, False)
        if (menu and menu[1]) or (panel and panel.get("actions")) or screen.startup_key(view) or \
                screen.startup_choice(view) is not None:  # numbered options, a panel's keys, a startup dialog: known
            return
        if cc_health.applies(st):
            await self.cc.failed("tmux", ch.id, st.get("cc_version"), why="畫面上找不到輸入框", text=view)
        elif st.get("pane") and not st.get("home") and not st["pane"].get("host"):
            if time.time() - (st.get("cc_pane_said") or 0) > PANE_SAID_SECS:
                st["cc_pane_said"] = time.time()
                self.host.save()
                version = cc_health.version_of("/proc/%s/exe" % st["pane"].get("pid")) if st["pane"].get("pid") else None
                await self.say(ch, messages.cc("attached_pane", version=version or "").replace("Claude Code  ", "Claude Code "))

    async def stale_turn_done(self, ch, st, pane):
        """A window marked to be reopened (cc_stale) that still runs a turn: that turn ends first, up to
        STALE_WAIT_SECS, while the window is there (a pin moved the channel to the kept version: #code-review-2)."""
        t0, said = time.time(), False
        while st.get("busy") and time.time() - t0 < STALE_WAIT_SECS and await pane.alive():
            if not said and time.time() - t0 > STALE_SAY_SECS:  # (said, as leave_window does: #code-review-2 on 7780fb3)
                said = True
                await self.say(ch, messages.cc("wait_window", mode=messages.cc("mode_tmux")))
            await asyncio.sleep(2)

    async def print_done(self, ch):
        """Wait for the channel's print turns, running or queued (Bot.locks, the channel's queue of them), before a
        window is opened on its conversation: two Claudes on one conversation otherwise (the window's would read
        it halfway through that turn, and both write the session file; #code-review-2 on 7c0ac52). A turn queued
        meanwhile that finds the channel back at tmux is typed into the window instead (Bot.print_turn)."""
        lock = (getattr(self.host, "locks", None) or {}).get(str(ch.id)) if isinstance(getattr(self.host, "locks", None), dict) else None
        if isinstance(lock, asyncio.Lock) and lock.locked():
            async with lock:
                pass

    def env(self, ch):
        env = dict(RESUME_ENV, DCBOT_ROOT=config.ROOT, DCBOT_CHANNEL=str(ch.id), **cc_health.pinned_env())  # dchook
        # and deploy.sh report here; a pinned older Claude Code does not update itself (C-162 section 3.1)
        if not (os.environ.get("LC_ALL") or os.environ.get("LANG")):
            env["LANG"] = "C.UTF-8"  # started from cron: no locale
        return env

    async def open(self, ch, st, rt, start, kept, failed, host):
        """Open the channel's conversation: the saved one first, a new one when that does not come up (and the
        channel is told so); True when one is up. What the kinds of channel differ in is in the three it is given:
        `start(resume, how, dirs)` begins it, wherever that kind keeps it (how: the model and the permission mode;
        dirs: the channel's attachments folder, made on `host`, the machine it works on: None for this one; C-151),
        and gives its pane (None: could not be begun);
        `kept(pane, fresh)` when it came up (fresh: a new conversation in place of the saved one); `failed(pane)`
        clears away one that did not (pane: what start gave, None too), and gives what is known of why (words to
        add when the saved one could not be taken up; None: nothing known). The folder is made once the conversation is
        to be opened at all (not when the permission mode says no), once for both tries."""
        rt.opens += 1  # (before anything is awaited: a look_again() asking meanwhile leaves this channel alone)
        how = await self.chosen_for(st)
        dirs = await attachments.folders(host, ch.id)
        saved = st.get("session_id")
        why = None
        local = host is None and cc_health.applies(st) and getattr(self, "cc", None) is not None  # C-162: failures of
        # this machine's own Claude Code counted (a bridge made without one, as some tests make it: none)
        version = cc_health.version_of(cc_health.binary()) if local else None  # (the file as it is started: section 2.1)
        for resume in ([saved, None] if saved else [None]):
            rt.ready.clear()
            rt.unanswered = False
            st["busy"] = False
            pane = None
            try:
                pane = await start(resume, how, dirs)
                if pane is not None and await self.wait_ready(pane, rt, ask=lambda choice, p=pane: self.startup.post(ch, p, choice)):
                    await kept(pane, bool(saved and not resume))
                    opened(st, rt)
                    if local:
                        st["cc_version"] = version
                        self.cc.worked(ch.id, version=version)
                        if saved and not resume:  # the saved one did not come up, a new one did (C-161's): counted apart
                            await self.cc.failed("resume", ch.id, version, why=why or "", text=why or "")
                    if st.pop("start_failed", None) is not None:  # (C-103: its mention gets its ✅)
                        await self.call_done(ch, prefix="not_started:")
                    if saved and not resume:
                        await self.say(ch, messages.note("claude_code.new_conversation", session=saved) + (why or ""))
                    return True
            finally:
                rt.opening = None
            said = await failed(pane)
            if resume:
                why = said or ("\n" + rt.ready_why if rt.ready_why else None)
            if rt.unanswered:  # the owner did not answer its startup dialog: no new conversation in place of the saved
                break          # one, which would ask the same again (#code-review of 5b4d448); the next message opens it
        if local and not rt.unanswered:  # (a or b of section 2.1: it did not come up, or its claude ended at once)
            words = said or why or ""
            await self.cc.failed("tmux", ch.id, version, why="開不起來", text=words,
                                    signal="9" if "被訊號 9" in words else "")
        return False

    async def renamed_session(self, ch):
        """The session of the bot's server whose Claude Code this bot started for this channel (DCBOT_ROOT and
        DCBOT_CHANNEL in the process' own environment, as ownerless() reads them), when its name is no longer the
        one the channel's state holds (the owner renamed it in tmux: C-201 6.3). None when there is none, or more
        than one (not guessed), or one another channel's state names."""
        named = sessions_named(self.host.channels_state)
        found = []
        for s in await tmuxio.own_sessions():
            if s["name"] in named:
                continue
            for pid, command in s["panes"]:
                fact = tmuxio.started_for(pid) if screen.is_claude(command) else None
                if fact and os.path.realpath(fact[0]) == os.path.realpath(config.ROOT) and fact[1] == str(ch.id):
                    found.append(s["name"])
                    break
        return found[0] if len(found) == 1 else None

    async def ensure_home(self, ch, st, rt):
        """The channel's window in the owner's tmux: there and running Claude, or opened again (in the same
        tmux session, resuming the conversation). Only windows the bot opened are ever reopened or closed."""
        how = await tmuxio.standing(st["pane"]) if st.get("pane") else "gone"
        if how == "alive" and st.get("cc_stale") and not st["home"].get("host"):  # C-162, as in ensure()
            await self.print_done(ch)
            await self.stale_turn_done(ch, st, tmuxio.pane(st["pane"]))
            await self.close(ch)  # (its end is not said: rt.closed)
            how = "gone"
        if how == "alive":
            return
        if how == "unknown":  # its machine did not answer: the window may well be there and running. Another one
            raise unreachable(st["pane"])  # opened now (the line back a moment later) would be a second Claude on the
            # same conversation: not opened, and said
        st.pop("bg", None)  # what ran under the old window ended with it
        home, why = st["home"], []
        if not home.get("host"):
            await self.print_done(ch)  # (C-162, as in ensure())
        remote_host = home.get("host")

        async def start(resume, how, dirs):
            env = dict(RESUME_ENV, DCBOT_CHANNEL=str(ch.id)) if remote_host else self.env(ch)  # there: dcpost's sender only
            rt.opening = True  # what the new window reports is the channel's from before the channel remembers it
            claude = await remote.claude_on(remote_host) if remote_host else None  # (that machine's own path)
            pane = await tmuxio.open_window(home["socket"], home["session"], home.get("window", ""), st["cwd"],
                                            self.argv(ch, st, resume, dirs, how, claude=claude), env, host=remote_host, why=why)
            rt.opening = pane
            if pane and remote_host:  # its hooks report through that machine's route table, like an attached pane
                await routes.bind_any(pane, str(ch.id))
            return tmuxio.pane(pane) if pane else None

        async def kept(pane, fresh):
            where_mod.put(st, pane=pane.t)
            self.host.save()

        async def failed(pane):
            if pane is not None:
                if not why:  # (a window that opened and whose claude did not come up: its last lines say why; the
                    try:     # message said nothing at all before: drill 11-08)
                        shown = masking.masked(await pane.screen() or "")  # (the whole screen masked first, then cut:
                        # a secret cut in two would not look like one; #code-review-2 on 60a8796, manager)
                        tail = [l for l in shown.splitlines() if l.strip()][-3:]
                        if tail:
                            why.append("最後的畫面：" + " / ".join(t.strip()[:120] for t in tail))
                    except Exception:
                        pass
                await pane.close()

        if await self.open(ch, st, rt, start, kept, failed, remote_host):
            return
        if remote_host:  # (that machine's claude asked again next time: it may have moved; #code-review-2 on 7cc6236)
            remote.CLAUDE_AT.pop(remote_host, None)
        raise NotOpened(NotOpened.NO_WINDOW, "在 tmux `%s` 裡開不起 Claude。%s" % (home["session"], ("\n-# %s" % why[-1]) if why else ""),
                        session=home["session"], said=why[-1] if why else "")

    async def start_now(self, ch):
        """Open the channel's session right away (after the setup card), so it is there in tmux already."""
        st, rt = self.host.chan(ch), self.rt(ch)
        st["last_active"] = time.time()  # or the idle reaper takes a session nobody has talked to yet
        way = getattr(self.host, "mode", None)
        if callable(way) and way(st) != "tmux":  # (C-162) run in print or held down: nothing to open beforehand
            return True
        async with rt.lock:
            try:
                await self.ensure(ch, st, rt)
                return True
            except BridgeError as e:
                await self.told(lambda body: self.say(ch, body), not_opened(rt, e, message=False))
                return False

    async def wait_ready(self, where, rt, ask=None):
        """Wait for SessionStart, answering the startup dialogs (trust this folder, bypass mode) with Yes.
        `where`: the pane the conversation was opened in (tmuxio.Pane, or what one is made from). A startup dialog
        the bot does not answer for the owner (screen.startup_choice: 2.1.303's external imports) is handed to
        `ask(choice)` once (startup_ask posts it with buttons and gives its token) and waited on without the
        START_SECS limit, up to startup_ask.ASK_SECS; its buttons go when the wait ends."""
        pane = tmuxio.pane(where)
        t0 = first = time.time()
        token, ok = None, False
        rt.ready_why = None
        try:
            while time.time() - t0 < START_SECS and time.time() - first < startup_ask.ASK_SECS:
                if rt.ready.is_set():
                    await asyncio.sleep(1)  # the prompt box appears just after the hook fires
                    ok = True
                    return True
                how = await pane.standing()
                if how == "gone":
                    return False
                if how == "unknown":  # asked and not answered (the line to that machine): it may be starting all the same.
                    await asyncio.sleep(0.5)  # Taken for gone, the window was closed and a new conversation begun in its place
                    continue
                view = await pane.raw_screen()
                if pane.remote and screen.has_prompt(view):
                    ok = True
                    return True  # another machine: its SessionStart may have come before the route was written
                key = screen.startup_key(view)
                if key:
                    await pane.keys(key)
                elif ask is not None and screen.startup_choice(view) is not None:
                    if token is None:
                        token = await ask(screen.startup_choice(view)) or "-"
                    t0 = time.time()  # the owner is deciding: not a start that hangs
                await asyncio.sleep(0.5)
            if rt.ready.is_set() or await box_shown(pane):
                # Its SessionStart can wait in the spool behind other events (11-10: a round of 136 s; the window of
                # #英文 had come up, and was closed as not come up). The prompt box on its screen says it is up.
                if not rt.ready.is_set():
                    print("bridge: no SessionStart in %d s, but the prompt box is on the screen: taken as come up" % START_SECS, flush=True)
                ok = True
                return True
            rt.unanswered = token is not None
            if token is None:
                rt.ready_why = messages.note("claude_code.no_start", secs=START_SECS)
            return False
        finally:
            if token not in (None, "-"):
                await self.startup.done(token, "" if ok else "等太久了，這次沒有開起來；再發一則訊息會重新開，問題會再出現一次。")

    # ------------------------------------------------------------------ input
    async def submit(self, message, text, out=None, now=False, confirmed=False, typed=None):
        """Type `text` into the channel's session. `out` (for Discord's "/" menu) replaces posting to the
        channel for whatever answers the submission itself: the screen after a slash command, or a failure.
        `now`: if Claude is busy, the message waits in its queue until the tool call in progress ends; this
        presses send-now (Ctrl+X Ctrl+S), which moves that call to the background (or stops one that cannot
        go there) so Claude reads the message in the same turn. `typed()`: called once a plain message (not "/…")
        went in, Enter pressed on it; never for one not sent, refused, waiting on a confirmation, /btw (C-199)."""
        ch = message.channel
        st, rt = self.host.chan(ch), self.rt(ch)
        way = getattr(self.host, "mode", None)
        way = way(st) if callable(way) else None
        if isinstance(way, str) and way != "tmux":  # a print channel, or one held down (C-162): no window to type
            # into (one would be opened). A turn of its own, queued behind the channel's others; handed over now, as
            # typing into a window is (a post's spool file goes once this returns: waiting for the turn, up to
            # TURN_TIMEOUT, a restart meanwhile posted and ran it again, #code-review on 7c0ac52)
            self.host.queue_turn(message, text)
            return None
        say = out or (lambda body: self.say(ch, body))
        original = text  # what is kept to be sent again (C-74), without the note put in front of it

        async def keep(words, r):  # not sent for now (a temporary code): the notice with a 「再送一次」 button, the words kept
            if not getattr(message, "quiet", False):  # a post handed again by itself: back in its place, nothing said
                await self.unsent.offer(ch, st, original, words, message, r.code)
        keep = None if out else keep  # the "/" menu answers where it was asked, with no button
        if text == "/btw" or text.startswith("/btw "):
            return await self.btw_open(ch, text[4:].strip(), out)
        cmd = cc_commands.command_of(text)  # " /exit" too
        if cmd in BLOCKED:
            return await say(messages.not_sent("claude_code.blocked_command", command=cmd, why=BLOCKED[cmd]))
        if cmd in CONFIRM and not confirmed:
            if out:  # the "/" menu waits for an answer of its own
                await out("⚠️ `%s` 要先確認，按鈕在頻道裡。" % cmd)
            return await self.confirm(message, text)
        note = st.get("note_next") if not text.startswith("/") else None  # something Claude should know, said with
        if note:                                                            # the next message (dropped once it went)
            text = "%s\n%s" % (note, text)
        p = Pending(message, text)
        if not isinstance(message, posts_mod.Origin):  # the owner answered whatever Claude asked to discuss (C-103)
            await self.call_done(ch, prefix="discuss:")
        slash = text.startswith("/")  # handled by Claude Code itself; most of them do not start a turn
        # One message at a time per channel, typing to receipt (C-73): the lock is the channel's queue for whatever
        # is typed into it (the owner's messages, posts handed over, the retry button). It used to be let go once
        # Enter was pressed: the next one typed while this one's receipt came, which cleared rt.entered under
        # it (the AttributeError in may_type, #code-review-2 on C-73: that message was lost without a word).
        async with rt.lock:
            if isinstance(getattr(message, "timing", None), dict):  # a post's (posts.Origin): when its turn came (C-75)
                message.timing["locked"] = time.time()
            try:
                await self.ensure(ch, st, rt)
            except BridgeError as e:
                self.host.save()
                r = not_opened(rt, e)
                await self.told(say, r, keep)
                await self.tell_sender(message, r)
                await self.start_failed(ch, st, e)  # (C-103)
                return r
            name = target(st)
            was_busy = bool(st.get("busy"))  # a turn runs: this message will wait in the queue (send_now)
            r = await self.checked(say, st, name, rt, keep)
            if not r:
                await self.box_unknown(ch, st, name, r)  # (C-162)
                await self.tell_sender(message, r)
                return r  # not sent: note_next stays for the next one; the caller sees why (posts, unsent)
            if not slash:
                rt.pending.append(p)
            else:
                rt.typed = (rt.typed + [text])[-5:]
            await tmuxio.type_text(name, text)
            await asyncio.sleep(SUBMIT_DELAY)
            await self.note_paste(name, text)
            pressed = await self.enter(name, text)
            r.state = "entered" if pressed else r.state
            if pressed == "pressed":
                rt.entered = text
            if not pressed:
                if p in rt.pending:
                    rt.pending.remove(p)
                rt.left = text
                r.refuse("claude_code.box_differs")
                r.state = "typed_not_entered"
                said = await self.told(say, r)
                await self.tell_sender(message, r)
                return said
            if note:
                st.pop("note_next", None)
            if typed is not None and not slash:  # (C-199: what a paused channel held goes in after it)
                typed()
            lost = self.stream_lost(st)  # its stream down as it went in: a receipt can only come late
            st["last_active"] = time.time()
            self.host.save()
            if slash:
                await asyncio.sleep(3)
                if not st.get("busy"):
                    # the panel goes to the channel itself, buttons and later edits alike; the "/" menu only hears where
                    st["own_panel"] = await self.panel_open(name)  # kept with the channel: a restart of the bot keeps the claim
                    self.host.save()
                    if st["own_panel"] and await self.menus.show_panel(
                            ch, name, lambda body, view=None: deliver.deliver(ch, body, view=view, intent="card"), text.split()[0]):
                        if out:
                            await out("▶️ `%s` 的面板貼在頻道裡，用按鈕操作。" % text.split()[0])
                        return
                    hint = "\n-# 面板還開著：用 `!key` 操作，或直接發下一則訊息（會自動關掉）" if await self.panel_open(name) else ""
                    await say(fence(await tmuxio.screen(name, 45), 3600) + hint)
                elif out:
                    await out("▶️ `%s` 已交給 Claude，結果會貼在這個頻道。" % text[:300])
                elif hasattr(message, "add_reaction"):  # a skill typed as a message: it started a turn
                    try:
                        await message.add_reaction("👀")
                    except Exception:
                        pass
                return
            if now:
                asyncio.ensure_future(self.send_now(message, name, p, was_busy))
            for _ in range(2):
                if await self.received(p) or st.get("busy"):
                    return  # busy without a receipt: queued behind a long tool call, the receipt comes later
                if (lost and isinstance(name, dict)) or await self.enter(name, text, again=True) != "pressed":
                    break  # an attached pane: no Enter while its events cannot come, nor onto anything but the message
            if await self.received(p) or st.get("busy"):
                return
            if p in rt.pending:
                rt.pending.remove(p)
            if lost:  # typed in; only the way back is down (2026-10-07: "may not have gone in" was wrong there)
                p.late = time.time()  # its receipt may still come: then it is ours, not the terminal's (manager)
                rt.late = (rt.late + [p])[-5:]
                return await say(messages.unconfirmed("claude_code.stream_down", host=lost))
            moved = parked(st)
            if moved:  # typed into the pane, and no word of it from the conversation that was moved out of it
                moved["unanswered"] = time.time()
                self.host.save()
                self.note_parked()
                return await say(messages.unconfirmed("claude_code.maybe_not_received_moved", screen=fence(await tmuxio.screen(name, 15))))
            await say(messages.unconfirmed("claude_code.maybe_not_received", screen=fence(await tmuxio.screen(name, 15))))

    async def send_now(self, message, name, p=None, was_busy=True):
        """Press send-now (Ctrl+X Ctrl+S) for a message of the bot's that waits in Claude Code's queue. Two things
        must both hold (manager 2026-10-09): the bot knows it has one waiting there (`was_busy`: a turn ran when
        it was sent; `p`: its receipt has not come), and the screen shows Claude Code's own hint where it draws it
        (screen.send_now_shown). With nothing queued the keys send off whatever stands in the input box (seen on
        2.1.295): a reply that ended in the hint's very words had the owner's half-typed text sent. Nothing queued
        within a few seconds: Claude was idle and took it."""
        if not was_busy:
            return False
        if not await cc_typing.send_now(tmuxio.pane(name), lambda: p is not None and p.got.is_set()):
            return False
        try:
            await (getattr(message, "card", None) or message).add_reaction("⚡")  # a post's ⚡ on its card (C-176)
        except Exception:
            pass
        try:
            await self.host.audit(now_said(message))
        except Exception:
            traceback.print_exc()
        return True

    async def btw_open(self, ch, question, out=None):
        """/btw: a side question that does not touch the turn in progress. It gets a thread of its own; what
        is typed in that thread later is a follow-up (main.py sends it to btw_ask)."""
        if not question:
            return await (out or (lambda t: self.say(ch, t)))("用法：`/btw <問題>`")
        try:
            starter = await deliver.deliver(ch, "💬 **/btw**　%s" % question[:1800], intent="status")
            thread = await starter.create_thread(name=events_cut("💬 " + question, 95), auto_archive_duration=60)
        except Exception:
            traceback.print_exc()
            thread = None
        if out:
            await out("💬 答案在討論串裡：<#%d>，在那裡可以繼續追問。" % thread.id if thread else "💬 開不了討論串，答案會貼在這裡。")
        await self.btw_ask(ch, thread, question)

    async def btw_ask(self, ch, thread, question):
        """One question in a btw thread (`thread` None: answer in the channel). Earlier exchanges in the
        thread go along, because /btw itself remembers nothing."""
        st, rt = self.host.chan(ch), self.rt(ch)
        where = thread or ch
        log = st.setdefault("btw", {}).setdefault(str(where.id), {"pairs": [], "t0": time.time()})
        text = "/btw " + events.btw_prompt(log["pairs"], question, fresh=rt.btw_from not in (None, where.id))
        if len(text) > len("/btw ") + screen.BTW_MAX:  # the question alone: typed, it would be taken for a paste
            rt.outcome = r = Typing()
            r.refuse("claude_code.btw_too_long", chars=len(text) - 5, limit=screen.BTW_MAX)
            return await self.told(lambda body: self.say(where, body), r)
        rt.btw_from = where.id
        async with rt.lock:  # the next message typed would close the answer before it is read
            try:
                await self.ensure(ch, st, rt)
            except BridgeError as e:
                said = await self.told(lambda body: self.say(where, body), not_opened(rt, e))
                await self.start_failed(ch, st, e)  # (C-103)
                return said
            name = target(st)
            r = await self.checked(lambda body: self.say(where, body), st, name, rt)
            if not r:
                return
            rt.typed = (rt.typed + [text])[-5:]
            await tmuxio.type_text(name, text)
            await asyncio.sleep(SUBMIT_DELAY)
            view = await tmuxio.raw_screen(name)
            state = screen.btw_input_state(view)
            if state == "ok" and isinstance(name, dict) and not (
                    screen.pane_state(view) == "box" and screen.bot_typed(view, text, screen.paste_marks(text))):
                state = "other"  # an attached pane: Enter only on the question alone (the owner typed after it)
            if state != "ok":  # never Enter on what is not a /btw line: it would go to the main conversation
                if isinstance(name, dict) and state != "empty" and not screen.bot_typed(view, text, screen.paste_marks(text)):
                    # an attached pane: what is there now is not only the bot's, so it is not the bot's to clear
                    r.refuse("claude_code.btw_not_alone")
                    r.state = "typed_not_entered"
                    return await self.told(lambda body: self.say(where, body), r)
                cleared = await self.clear_input(name)
                r.refuse("claude_code.btw_pasted" if state == "pasted" else "claude_code.btw_not_command", cleared=cleared)
                return await self.told(lambda body: self.say(where, body), r)
            await tmuxio.keys(name, "Enter")
            answer, note = None, ""
            for _ in range(BTW_SECS):
                await asyncio.sleep(1)
                answer = screen.btw_answer(await tmuxio.raw_screen(name), tmuxio.WIDTH)
                if answer:
                    break
            if answer:
                copied = await self.btw_copy(name)  # the whole answer, not just what fits on screen
                answer, note = (copied, "") if copied else (answer, cc_btw.screen_note())
            if answer:  # only a box that has answered is closed: one still on its way is left as it is
                if "x to clear history" in screen.btw_hint(await tmuxio.raw_screen(name)):  # the box's own hint only
                    await tmuxio.keys(name, "x")  # /btw keeps its own memory across questions, and with it
                    await asyncio.sleep(0.5)      # the threads would leak into each other
                # close the overlay, and wait until it is gone: an Escape still in flight plus a second one
                # from the next question would reach the running turn and interrupt it
                await tmuxio.escape(name, lambda view: screen.btw_answer(view, tmuxio.WIDTH) is not None)
                for _ in range(10):
                    await asyncio.sleep(0.3)
                    if screen.btw_answer(await tmuxio.raw_screen(name), tmuxio.WIDTH) is None:
                        break
        if answer:
            log["pairs"] = (log["pairs"] + [[question, answer[:1500]]])[-20:]
            log["t0"] = time.time()
            self.host.save()
            await self.say(where, masking.masked(answer) + ("\n-# " + note if note else ""))  # (B-12: read off the screen, Claude's words)
        else:
            st["own_panel"] = True  # its answer may still come up: an overlay the bot's own question opened
            self.host.save()
            if answer == "":  # the box is there, with no answer yet (still answering, or retrying a failed request)
                await self.say(where, messages.unconfirmed("claude_code.btw_still_answering", seconds=BTW_SECS,
                                                           screen=fence(await tmuxio.screen(name, 30), 3000)))
            else:
                await self.say(where, messages.unconfirmed("claude_code.btw_no_answer", seconds=BTW_SECS,
                                                           screen=fence(await tmuxio.screen(name, 30), 3000)))

    async def may_type(self, st, name, rt):
        """cc_typing.may_type() on the channel's pane, with what the bot knows of it (policy()) and remembers of it
        (Kept). What it came to (Typing: true to go on; else its code and data, for told() to say)."""
        rt.outcome = r = Typing()  # (kept: what `send` will answer once the typing is the connector's, 2.7)
        return await cc_typing.may_type(tmuxio.pane(name), Kept(st, rt), self.policy(st, name, rt), r)

    def policy(self, st, name, rt):
        """What the bot knows of a channel's pane and of the moment, for the connector's checks (cc_typing.Policy)."""
        others = isinstance(name, dict)  # an attached pane, or a window in the owner's tmux: the owner may type there

        def probed(result, length):
            print("[probe] %s: %s (%d characters in the box)" % (name.get("id") if others else name, result, length), flush=True)

        def differed():
            print("[enter] %s: the box is not what was typed (%d characters typed); Enter pressed" % (
                name, len(rt.entered or "")), flush=True)  # (resend(): the message before, typed again)
            self.note_differs()
        return cc_typing.Policy(others_type=others, bot_window=lambda: not others or bool(st.get("home")), parked=lambda: bool(parked(st)),
                                events_down=lambda: bool(self.stream_lost(st)), busy=lambda: bool(st.get("busy")),
                                close_panel=lambda: self.close_panel(st, name), receipt_secs=RECEIPT_SECS,
                                submit_delay=SUBMIT_DELAY, differed=differed, probed=probed)

    async def checked(self, say, st, name, rt, keep=None):
        """may_type(), and what it came to told. What it noted along the way is said even when it then fails with
        an error (tmux gone in the middle): "sent again" was said the moment the sending again had worked, and a
        message that did go must not go unsaid (manager, on 3f8e783)."""
        try:
            r = await self.may_type(st, name, rt)
        except Exception:
            if isinstance(rt.outcome, Typing) and rt.outcome.notes:
                try:
                    await self.told(say, Typing.of(rt.outcome.notes))
                except Exception:
                    traceback.print_exc()
            raise
        await self.told(say, r, keep)
        return r

    async def tell_sender(self, message, r):
        """A post (another channel's Claude's) not handed over for good, or typed but not entered: said in the
        channel it came from too (C-202; manager 11-18: those two, a post held up for now is kept and handed again as
        C-74 has it), and told to the Claude there, which knows nothing of it otherwise. That Claude is told as the
        owner's messages are typed (Posts.tell: no sender of its own), so what becomes of it is said in that channel
        only, never sent back again: no two channels telling each other over and over. Never raises."""
        try:
            sender = str(getattr(message, "sender", "") or "")
            if not getattr(message, "auto", False) or not sender.isdigit() or r:
                return
            entered = getattr(r, "state", None) == "typed_not_entered"
            if not entered and messages.retry(r.code) != messages.PERMANENT:
                return
            to = getattr(message.channel, "name", "?")
            text = messages.note("post.not_entered", to=to) if entered else \
                messages.note("post.refused", to=to, why=gist(messages.not_sent(r.code, **r.data)))
            ch = self.host.get_channel(int(sender))
            if ch is None:
                return await self.host.audit(text)
            await deliver.deliver(ch, text, silent=True, intent="status")
            posts = getattr(self.host, "posts", None)
            if posts is not None and posts_mod.listens(self.host.channels_state.get(sender) or {}):
                task = asyncio.ensure_future(posts.tell(ch, "（機器人通知）" + text))  # (its own queue: not waited on here)
                self.tell_tasks.add(task)  # (#code-review on b8fbb3fd)
                task.add_done_callback(self.tell_tasks.discard)
        except Exception:
            traceback.print_exc()

    async def told(self, say, r, keep=None):
        """Say what a typing check came to (Typing): what it noted along the way, and, when the message was not
        sent, why, in the words messages.py has for its code. True to go on. A note is said once: told() is called
        again for the same Typing when the message fails after the typing (the box was not what was typed, a /btw
        taken for a paste), and "sent again" was then posted twice (#code-review on 3f8e783)."""
        notes, r.notes = r.notes, []
        for code, data in notes:
            await say(messages.note(code, **data))
        if not r:
            words = messages.not_sent(r.code, **r.data)
            if keep is not None and messages.retry(r.code) == messages.TEMPORARY:
                await keep(words, r)  # C-74: said with a 「再送一次」 button, and kept (unsent.py)
            else:
                await say(words)
        return bool(r)

    async def note_paste(self, name, text):  # screen.PASTE_MARK: Claude Code's own mark, numbered
        """After a long message is typed: was it taken for a paste all the same ("[Pasted text" in the box)? Then
        the pacing of tmuxio.type_text no longer keeps under Claude Code's threshold (a new version, or pauses
        that ran together): one line in the bot's log, and state/typed-as-paste.json for doctor to show
        (dcbot.typed-as-paste). Nothing else changes: the message goes on as it would have. Never raises."""
        try:
            if max((len(line) for line in text.split("\n")), default=0) < screen.PASTE_AT:
                return  # (not even looked at: cc_typing.taken_for_a_paste() asks only of a long line)
            if not cc_typing.taken_for_a_paste(text, screen.input_text(await tmuxio.raw_screen(name))):
                return
            print("[paste] typed text taken for a paste (%d characters, longest line %d; TYPE_CHUNK %d, TYPE_PAUSE %s)" % (
                len(text), max(len(line) for line in text.split("\n")), cc_typing.TYPE_CHUNK, cc_typing.TYPE_PAUSE), flush=True)
            path = os.path.join(config.STATE, "typed-as-paste.json")
            try:
                with open(path, encoding="utf-8") as f:
                    seen = json.load(f)
                count = int(seen.get("count") or 0) if isinstance(seen, dict) else 0
            except (OSError, ValueError, TypeError):
                count = 0
            os.makedirs(config.STATE, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"at": int(time.time()), "count": count + 1, "chars": len(text)}, f)
            os.replace(path + ".tmp", path)
        except Exception:
            traceback.print_exc()

    def note_differs(self):
        """The times, of the last weeks, the bot's own session's box was not what had been typed when Enter was
        pressed all the same: state/enter-differs.json, for doctor to count (dcbot.enter-differs; many in a week
        means the comparison does not know some way Claude Code draws text, or someone types there). Never raises."""
        try:
            path = os.path.join(config.STATE, "enter-differs.json")
            try:
                with open(path, encoding="utf-8") as f:
                    seen = json.load(f)
                times = [t for t in seen.get("times", []) if isinstance(t, (int, float))] if isinstance(seen, dict) else []
            except (OSError, ValueError, AttributeError):
                times = []
            times = (times + [int(time.time())])[-200:]
            os.makedirs(config.STATE, exist_ok=True)
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                json.dump({"times": times}, f)
            os.replace(path + ".tmp", path)
        except Exception:
            traceback.print_exc()

    async def enter(self, name, typed, again=False):
        """cc_typing.enter() on the channel's pane (someone else may type at an attached one): "pressed", "empty"
        or None. Pressed although the box was not what was typed: a line in the log, and counted for doctor."""
        what, differed = await cc_typing.enter(tmuxio.pane(name), typed, again, others_type=isinstance(name, dict))
        if differed:
            print("[enter] %s: the box is not what was typed (%d characters typed); Enter pressed" % (name, len(typed)), flush=True)
            self.note_differs()
        if what is None and not again:  # (again: no receipt, the message most likely went: nothing to say)
            await self.why_not_entered(name, typed)
        return what

    async def why_not_entered(self, name, typed):
        """Enter was not pressed: one line in the log with what the screen shows now, the box masked and cut (11-10
        #psro: a message typed right was left unsent, and nothing said what the screen was), and the whole screen
        kept (keep_unentered(): C-202, a box the reading did not know was taken for a dialog). Never raises."""
        try:
            view = await tmuxio.raw_screen(name)
            try:
                coloured, _ = await tmuxio.color_screen(name)
            except Exception:
                coloured = ""
            box = screen.input_text(view)
            same = box is not None and screen.same_typed(box) == screen.same_typed(typed)
            state = screen.pane_state(view)
            kept = keep_unentered(name, typed, view, coloured, state)
            print("[enter] %s: not pressed; now: %s, box %s characters%s: %s%s" % (
                name.get("id") if isinstance(name, dict) else name, state,
                len(box) if box is not None else "none", " (now the message, as typed)" if same else "",
                masking.masked(box or "")[:160].replace("\n", " "), "; the screen kept in %s" % kept if kept else ""),
                flush=True)
        except Exception:
            traceback.print_exc()

    async def clear_input(self, name):
        """cc_typing.clear_input() on the channel's pane: "empty", "unchanged" or "stuck"."""
        return await cc_typing.clear_input(tmuxio.pane(name))

    async def probe(self, name, view):
        """cc_typing.probe() on an attached pane, each time a line in the bot's log."""
        result = await cc_typing.probe(tmuxio.pane(name), view)
        if result in ("suggestion", "typed", "changed"):  # keys were pressed
            print("[probe] %s: %s (%d characters in the box)" % (
                name.get("id") if isinstance(name, dict) else name, result, len(screen.input_text(view) or "")), flush=True)
        return result

    async def confirm(self, message, text):
        """A command from CONFIRM: ask first, with buttons; it is typed only after "送出" is pressed."""
        cmd = cc_commands.command_of(text)
        view = discord.ui.View(timeout=600)

        def answer(go):
            async def press(interaction):
                if interaction.user.id != config.OWNER_ID:
                    return await interaction.response.send_message("只有擁有者可以按。", ephemeral=True)
                await interaction.response.edit_message(
                    content="%s `%s`" % ("▶️ 已送出" if go else "已取消，沒有送出", text[:300]), view=None)
                if go:
                    await self.submit(message, text, confirmed=True)
            return press
        for label, style, go in (("送出", discord.ButtonStyle.danger, True), ("取消", discord.ButtonStyle.secondary, False)):
            button = discord.ui.Button(label=label, style=style)
            button.callback = answer(go)
            view.add_item(button)
        await deliver.deliver(message.channel, "⚠️ `%s` %s。確定要送進這個對話嗎？" % (cmd, CONFIRM[cmd]), view=view, intent="card")

    async def btw_copy(self, name):
        """Press "c" in the /btw answer and read what Claude Code copied: the full answer, in its own
        markdown. Needs the tmux server to keep copies (set on the bot's own server; an attached pane's
        server is left as the owner set it, and then the screen is read instead: cc_btw)."""
        if not isinstance(name, dict):  # the bot's own server: sessions started before this setting existed
            await tmuxio.tmux("set-option", "-s", "set-clipboard", "on")
        if not btw.copies(await tmuxio.clipboard(name)):
            return None
        before = set(await tmuxio.buffers(name))
        await tmuxio.keys(name, "c")
        for _ in range(10):
            await asyncio.sleep(0.3)
            new = [b for b in await tmuxio.buffers(name) if b not in before]
            if new:
                return (await tmuxio.take_buffer(name, new[0])).strip() or None
        return None

    async def close_panel(self, st, name):
        """cc_typing.close_panel() on the channel's pane, while Claude is idle. True when no panel is left."""
        return await cc_typing.close_panel(tmuxio.pane(name), lambda: bool(st.get("busy")))

    async def panel_open(self, name):
        return screen.panel_shown(await tmuxio.raw_screen(name))

    async def received(self, p):
        try:
            await asyncio.wait_for(p.got.wait(), RECEIPT_SECS)
        except asyncio.TimeoutError:
            pass
        return p.got.is_set()

    def menus_again(self):
        """C-189: every channel whose state says a dialog held it up (blocked "dialog", kept in channels.json across a
        restart): its screen read again on a task of its own (Menus.look_again(): the menu taken in, or its old card
        closed when it was answered meanwhile). Never awaited here: it may ask another machine."""
        for cid, st in list(self.host.channels_state.items()):
            if isinstance(st, dict) and str(cid).isdigit() and blocked.of(st, "dialog"):
                ch = self.host.get_channel(int(cid))
                if ch is not None:
                    asyncio.ensure_future(self.menus.look_again(ch, st))

    # ------------------------------------------------------------------ output
    async def pump(self):
        """Forever: turn hook events into Discord messages, and close sessions nobody has used for a while."""
        write_settings()
        await self.read_words()  # the installation's language and own wordings, before the first sentence is said
        asyncio.ensure_future(self.remember_pids())  # (asks other machines: never waited for here, manager 11-19)
        while getattr(self.host, "lease", None) is not None and not leading(self.host):  # C-163: what reaches other
            await asyncio.sleep(1)  # machines or says things waits until this machine is the bot (#code-review on
            # abc07b3: before the first lease round no epoch went with a stream, and it cut the bot's own)
        self.unsent.kick_all()  # posts held up before the bot went down: looked at again (C-74; a task each)
        self.menus_again()  # C-189: dialogs still open on the screen taken in again (a task each)
        await self.read_rewriter()  # which plugin lays replies out (C-71b), before the first reply
        await self.posted_left()  # a reply that was being laid out when the bot went down: posted as before
        self.start_streams()
        for cid, st in list(self.host.channels_state.items()):  # a task each: a machine that does not answer keeps
            asyncio.ensure_future(self.look_again_saved(cid, st))  # only its own channels waiting, never this loop
        self.host.save()  # (the 11-18 rollback: ssh timeouts in a row here kept the self test unread past the trial)
        self.last_reap = time.time()
        while True:
            try:  # nothing may end this loop: every channel's output and altention dc post go through it
                await self.pump_once()
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(0.1 if self.held else 0.5)  # an event is waiting for its row: look again soon

    async def look_again_saved(self, cid, st):
        """look_again() on a task of its own at start, the state saved after it."""
        try:
            await self.look_again(cid, st)
            self.host.save()
        except Exception:
            traceback.print_exc()

    async def look_again(self, cid, st):
        """A channel busy (or with background work) when the bot went down: its window looked at. Still there: a
        turn kept running, watched; gone: let go of, and said. At start (pump()), and after a takeover for the old bot
        machine's channels once they are taken over (C-163; #code-review-2 on 65ee18f)."""
        if not (st.get("busy") or st.get("bg")) or (st.get("took_from") or {}).get("pending"):
            return  # (C-163: the old bot machine's, not taken over yet: not looked for here, never "gone")
        ch = self.host.get_channel(int(cid))
        if remote.problem and ((st.get("pane") or {}).get("host") or (st.get("home") or {}).get("host")):
            return  # the machine list is unreadable, so that machine cannot be reached by name: do not call it gone
        asked = target(st)
        rt = self.rts.setdefault(str(cid), Runtime())
        opens = rt.opens
        how = await tmuxio.standing(asked) if ch is not None and asked else "gone"
        if asked and place(target(st)) != place(asked) or rt.opens != opens:
            # the channel moved to another window while its machine was asked (a message meanwhile), or a window was
            # (being) opened again for it, in the same place for a session of the bot's (#code-review-2 on fbe1cfa6):
            # not ours. Only the place counts: a pid or agent written into the record meanwhile (remember_pids(),
            # /where) is the same window (#code-review-2 on cafb7031)
            return
        if how != "gone":  # ("unknown": its machine does not answer just now. Not said to be gone, nothing let go
            if st.get("busy"):  # of: watch() finds out once the machine answers again)
                self.watch(ch, st, self.rt(ch))  # a turn kept running while the bot was down
            return
        lost = [w for w, on in (("進行中的工作", st.get("busy")), ("背景工作", st.get("bg"))) if on]
        st["busy"] = False
        st.pop("bg", None)
        if ch is not None:  # the machine rebooted, or someone killed the tmux server
            await self.say(ch, "⚠️ 這個頻道的 tmux 對話不見了（機器重開過？），%s已中斷。%s" % ("和".join(lost), (
                "對話內容還在：在 tmux 開一個視窗，到 `%s` 跑 `%s`，再用 `/attach` 選它，會接回這個頻道。" % (
                    st.get("cwd"), resume_command(st)) if st.get("pane") and not st.get("home")
                else "對話內容還在，下一則訊息會自動接回。")))

    def waits(self, ev):
        """Is this event of this machine to be left in the spool for now? A tool call whose own row is not written
        yet (the connector's awaits(): Claude Code writes it a moment after the hook, with what was said ahead of
        the call), for at most cc_hooks.HOLD_SECS after its hook ran; and the end of a turn whose reply's row is
        not written yet, for at most cc_hooks.END_HOLD_SECS (step 2.2f). Only for a channel whose conversation the bot
        is reading (a record of that transcript), and only when the words on the way are posted at all. No waiting
        here: the round goes on with the other channels and comes back. A channel whose final reply a plugin is
        laying out (C-71b): every event of it waits until that is posted."""
        if str(ev.get("channel") or "") in self.rewriting:
            return not self.receipt_meanwhile(ev)
        if ev.get("host"):
            return False
        cid = str(ev.get("channel") or "")
        was = self.host.channels_state.get(cid)
        rt = self.rts.get(cid)
        if not isinstance(was, dict) or put_away(was) or unplaced(was, ev) or stranger(was, ev) or \
                from_elsewhere(was, getattr(rt, "opening", None), ev, getattr(rt, "closed", None)):
            # Not the channel's own, by the same rules handle() goes by: a channel the bot does not know, one put
            # away, a window that is not the channel's now. It is let go there, with nothing read from its file:
            # no row to wait for, and the channel's own events are not kept behind it. (#code-review-2 on bc7582d:
            # another window's reply of 24 tool calls held this channel's events for 2 s each; and, before that
            # commit too, whenever the bot had read that window's file.) No Runtime is made for it either.
            return False
        if rt is None:  # the channel's first event since the bot started: waited for like any other (step 2.2g;
            rt = self.rts.setdefault(cid, Runtime())  # rt() makes the same)
        try:
            # (a tool call's row is waited for only when the words on the way are posted at all; a turn's end
            # always: the context its footer shows comes from the reply's row)
            wait = bool(rt is not None and cc_hooks.awaits(ev, rt.pos, calls=config.INTERIM))
            if rt is not None and not wait:
                self.note_late(rt)
            return wait
        except Exception:
            traceback.print_exc()
            return False  # whatever went wrong looking: the event is handled, not kept

    def note_late(self, rt):
        """A turn's end that waited out the connector's limit without its reply's row (hooks.ends_late()): counted
        while the bot runs, said once aloud, and written down (state/turn-ends-late.json: {"at", "count", "since"};
        nothing reads the file yet: a doctor item for it comes with the next change in ops/)."""
        late = cc_hooks.ends_late(rt.pos)
        more = late - getattr(rt, "late_seen", 0)
        rt.late_seen = late
        if more <= 0:
            return
        if not getattr(self, "late_ends", 0):
            self.late_since = time.time()
            print("bridge: a turn's end was handled without its reply's row, %.1f s after its hook (the footer's context may be the "
                  "turn before's, or none); said once, counted from here on (state/%s)" % (cc_hooks.END_HOLD_SECS, LATE_ENDS), flush=True)
        self.late_ends = getattr(self, "late_ends", 0) + more
        try:
            os.makedirs(config.STATE, exist_ok=True)
            path = os.path.join(config.STATE, LATE_ENDS)
            with open(path + ".tmp", "w") as f:
                json.dump({"at": time.time(), "count": self.late_ends, "since": self.late_since}, f)
            os.replace(path + ".tmp", path)
        except OSError:
            traceback.print_exc()

    def read_words(self):
        """What the installation says of the bot's words (messages.load(): its language, its own wordings) is read
        into memory in a thread: at start, and once a minute from pump_once() for a settings file that changed.
        Never in the event loop: the file is on disk, and a broken one is waited for. One reading at a time; the
        task, for whoever wants to wait for it (the start does, the round of events does not)."""
        task = getattr(self, "words_read", None)
        if task is None or task.done():
            async def read():
                try:
                    await asyncio.get_running_loop().run_in_executor(None, messages.load)
                except Exception:
                    traceback.print_exc()
            self.words_read = task = asyncio.ensure_future(read())
        return task

    def start_streams(self):
        """A stream for each other machine that has none yet. Run at start and every minute from pump_once(), so
        machines added to the list, or a list that was broken and has been fixed, come in without a restart."""
        for name in remote.hosts(self.host.channels_state):
            if name not in self.streaming:
                self.streaming.add(name)
                asyncio.ensure_future(self.stream(name))
        self.streams_checked = time.time()

    async def say_config_problem(self):
        """#audit learns that the machine list is broken (once per problem, again every 10 minutes)."""
        p = remote.problem
        if p and (getattr(self, "problem_said", None) != p[1] or time.time() - getattr(self, "problem_said_at", 0) > 600):
            self.problem_said, self.problem_said_at = p[1], time.time()
            await self.host.audit("⚠️ 機器清單讀不出來，機器人先當成只有這台在跑（別台的頻道暫時收不到東西）：%s" % p[1])

    async def pump_once(self):
        """One round of pump(): the waiting hook events, then subagents, dcpost and the hourly chores. A round
        longer than SLOW_ROUND is said in supervisor.out with the parts that took the time (C-73): nothing in it
        may wait on another machine, and this shows it if something does."""
        began, self.laps = time.time(), []
        try:
            await self.pump_round()
        finally:
            took = time.time() - began
            if took > SLOW_ROUND:
                print("[pump] 一圈 %.1f 秒：%s" % (took, "、".join("%s %.1f" % lap for lap in self.laps) or "（沒有單一段超過 0.2 秒）"),
                      flush=True)

    def lap(self, what, since):
        """A part of this round that took 0.2 seconds or more, for pump_once()'s line."""
        took = time.time() - since
        if took >= 0.2:
            self.laps.append((what, took))

    async def pump_round(self):
        if not leading(self.host):  # C-163: not the bot right now: the spool waits (only with a standby)
            return
        t = time.time()
        if time.time() - getattr(self, "streams_checked", 0) > 60:
            try:
                self.start_streams()
                self.read_words()
                self.read_rewriter()
                await self.say_config_problem()
                pauses = getattr(getattr(self.host, "posts", None), "pauses", None)
                if pauses is not None:
                    pauses.chores()  # C-184: a pause whose time has come (in a task of its own; also at the first round)
            except Exception:
                traceback.print_exc()
            self.lap("每分鐘的檢查", t)
        self.held = set()  # channels with an event left in the spool this round (waits())
        for path, ev in events.waiting(SPOOL):
            if ev.get("channel") == cc_probe.PROBE:  # the probe's own conversation (C-162): its, and nowhere else's
                self.cc.probe.event(ev)
                events.done(path)
                continue
            if ev.get("channel") == "selftest":  # main.Bot.self_test(): the hook works, the spool is read
                if hasattr(self.host, "ready"):
                    self.host.ready(ev)
                events.done(path)
                continue
            cc_hooks.normalize(ev)  # what kind of thing it is: from the event alone, before the wait is decided
            if (ev.get("channel") in self.held and not self.receipt_meanwhile(ev)) or self.waits(ev) or self.lane_waits(ev):
                self.held.add(ev.get("channel"))  # left where it is, and every later event of its channel behind it:
                continue                          # the other channels' go on
            self.handling, t = True, time.time()
            try:
                await self.handle(ev)
                events.done(path)  # only now: a restart halfway through a batch handles the rest again
            except Exception as e:
                traceback.print_exc()
                # out of the way, kept for a look: one bad event never blocks the rest
                events.set_aside(path, SPOOL, net=events.transient(e))
                try:
                    await self.bad_event(ev)
                except Exception:  # saying so must not end the loop (Discord may be what is failing)
                    traceback.print_exc()
            finally:
                self.handling = False
                self.lap("事件 %s %s" % (ev.get("channel"), ev.get("dc_kind") or ev.get("hook_event_name")), t)
        if self.held and time.time() - self.ticked < 0.45:
            return  # a round come early because an event waits for its row: the rest keeps its own pace
        self.ticked = time.time()
        try:
            t = time.time()
            await self.release_kept()
            self.lap("release_kept", t)
            if time.time() - getattr(self, "again_at", 0) > 30:
                self.again_at = time.time()
                self.start_resends()  # (only started here: nothing in this round waits on another machine)
            t = time.time()
            await self.agents.tick()
            self.lap("子代理", t)
            if time.time() - getattr(self, "machines_at", 0) > 60:
                self.machines_at = time.time()
                await self.machine_watch()  # another machine down for long, with channels on it (C-103)
            if getattr(self.host, "posts", None) is not None and not getattr(self.host, "adopting", False):
                t = time.time()  # (C-163: posts wait while the old bot machine's channels are being taken over)
                await self.host.posts.tick()  # altention dc post: posts to channels, and Claude talking to Claude
                self.lap("posts", t)
        except Exception:
            traceback.print_exc()
        if time.time() - self.last_reap > 600:
            self.last_reap = time.time()
            t = time.time()
            try:
                await self.reap()
                attachments.prune()
                ask.prune()
                self.agents.prune()
                asyncio.ensure_future(self.unsent.expire())  # (says some where they came from: not waited on here)
                rewrite.prune()
            except Exception:
                traceback.print_exc()
            self.lap("閒置回收", t)

    async def stream(self, host):
        """Forever: the hook events of another machine, over one SSH line, handled like the local ones. The
        line also brings that machine's engine up to date and its hooks with it."""
        delay = 5
        cmd = remote.stream_command(host)
        # A command getting through wakes the wait (the machine is back), but never around the backoff: tries at
        # least STREAM_MIN_GAP apart, and after a woken try that brought nothing, no waking until a line comes
        # (a stream that dies at once on that machine would otherwise restart at every command; code-review)
        wakeable, woken, last_try = True, False, 0.0
        while True:
            self.went_down(host)
            gap = STREAM_MIN_GAP - (time.time() - last_try)
            if gap > 0:
                await asyncio.sleep(gap)
            last_try, got = time.time(), False
            try:
                alias = await remote.reach(host)  # every (re)connect: the alias that answers now, last good first
                if alias is None:
                    raise ConnectionError(remote.unreachable(host))
                proc = await asyncio.create_subprocess_exec(*remote.argv(host, cmd, alias), stdin=asyncio.subprocess.DEVNULL,
                                                            stdout=asyncio.subprocess.PIPE,
                                                            stderr=asyncio.subprocess.DEVNULL, limit=32 * 1024 * 1024)
                self.streams[host] = proc
                while True:
                    line = await proc.stdout.readline()
                    if not line:
                        break
                    delay, got, wakeable = 5, True, True
                    if host in self.stream_down and host in getattr(self, "resending", {}):
                        self.resending[host].set()  # its stream is back: what is owed to it is sent now
                    self.stream_down.discard(host)
                    if host in getattr(self, "down_since", {}):  # (C-103: said "back" where its going was said)
                        await self.machine_back(host)
                    try:
                        ev = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if ev.get("heartbeat") or not ev.get("channel"):
                        continue
                    ev["host"] = host
                    cc_hooks.normalize(ev)  # a machine whose dcremote is a step behind sends no kind: made out here
                    try:
                        await self.from_stream(ev)  # (only that channel waits on its lane: C-174)
                    except Exception:
                        traceback.print_exc()
                if await proc.wait() == remote.OLD and remote.EPOCH is not None and remote.on_old is not None:
                    remote.on_old(host)  # C-163: that machine has seen a newer bot: this one is old
            except ConnectionError as e:
                print("stream %s: %s" % (host, e), flush=True)
            except Exception:
                traceback.print_exc()
            self.went_down(host)
            if woken and not got:
                wakeable = False
            woken = False
            if not wakeable:
                await asyncio.sleep(delay)
                delay = min(delay * 2, STREAM_MAX_DELAY)
                continue
            wake = self.stream_wake[host] = asyncio.Event()  # fresh: only a success from here on wakes it
            try:
                await asyncio.wait_for(wake.wait(), delay)
                woken, delay = True, 5  # a command just got through: that machine is back
            except asyncio.TimeoutError:
                delay = min(delay * 2, STREAM_MAX_DELAY)
            finally:
                self.stream_wake.pop(host, None)

    def repin(self, ch, st, ev, what):
        """The channel's conversation goes on in a new process in the same pane (stranger() said it is the channel's):
        the channel and the route table take the pane's process now, so its later events and what the owner types
        reach it again (C-79; until then the owner had to !detach and !attach). Not on the end of a session: the old
        process's end may come after the new one's start. The route table on another machine is written by its
        dcremote, in a task of its own (the round of events waits on no other machine)."""
        pane, pid = st.get("pane"), str(ev.get("dc_respawned") or "")
        if not ev.get("dc_respawned") or not isinstance(pane, dict) or str(pane.get("pid") or "") == pid or \
                what in (cc_hooks.SESSION_ENDED, cc_hooks.SESSION_LEAVING):
            return
        print("bridge: channel %s: its conversation goes on in a new process in pane %s (%s → %s); the route follows it"
              % (ch.id, pane.get("id"), pane.get("pid"), pid), flush=True)
        pane = dict(pane, pid=pid)
        where_mod.put(st, pane=pane)
        self.host.save()
        if pane.get("host"):
            asyncio.ensure_future(self.bind_far(pane, ch.id))
        else:
            routes.bind(pane, ch.id)

    async def bind_far(self, pane, cid):
        try:
            await routes.bind_any(pane, cid, tag=False)  # (its process came back where it was: the look tags it)
        except Exception:
            traceback.print_exc()

    async def remember_pids(self):
        """Panes attached before process ids were recorded get theirs now (while they are surely the same)."""
        changed = False
        for cid, st in list(self.host.channels_state.items()):
            pane = st.get("pane")
            if pane and not pane.get("pid") and await tmuxio.alive(pane):
                pid = await tmuxio.pane_pid(pane)
                if st.get("pane") is not pane:  # (a task now: the channel took another pane while this one was asked)
                    continue
                pane = dict(pane, pid=pid)
                where_mod.put(st, pane=pane)
                if not st.get("home") or st["home"].get("host"):
                    await routes.bind_any(pane, cid, tag=False)  # (its pid learnt: bound again where it was)
                changed = True
        if changed:
            self.host.save()

    async def handle(self, ev, kept=False):
        # The kind is worked out by the side that handles the event: here, by this version. What the event brought
        # along (worked out where it came in; on another machine by a dcremote that may be of another version) is
        # for reference: where it differs, this version's is taken and a line says so, once for each such pair.
        came = ev.get("dc_kind") if isinstance(ev.get("dc_kind"), str) else None  # (anything else is no kind at all)
        cc_hooks.normalize(ev, anew=True)
        what = ev.get("dc_kind")
        if came and came != what and (came, what) not in self.unknown_said:
            self.unknown_said.add((came, what))
            print("bridge: an event came as kind %s and is %s here (said once): %s (channel %s)"
                  % (came, what, ev.get("hook_event_name"), ev.get("channel")), flush=True)
        if what == cc_hooks.UNKNOWN:  # nothing this version knows what to do with: left alone, and written down
            sort = (str(ev.get("hook_event_name")), str(ev.get("dc_why") or ""))
            if sort not in self.unknown_said:  # once for each sort while the bot runs, not once for each event
                self.unknown_said.add(sort)
                print("bridge: an event of a kind not known is left alone (said once): %s %s (channel %s)%s"
                      % (sort[0], sort[1], ev.get("channel"), "; it came as kind %s" % came if came and came != what else ""), flush=True)
            return
        cid = str(ev["channel"])
        ch = self.host.get_channel(int(cid))
        if ch is None and cid in self.host.channels_state:
            # Not to be had from Discord just now, and not deleted (its state is here): Discord is reconnecting, or
            # the channel is not in the cache yet. Nothing is known to be gone: the event is kept, with every later
            # one of its channel behind it, and handled when the channel is there again (release_kept()).
            return self.keep(cid, ev, first=kept)
        if cid in self.rewriting and not kept and not self.receipt_meanwhile(ev):  # its final reply is being laid out
            return self.keep(cid, ev, quiet=True)  # (C-71b): this waits behind it
        if cid in self.kept and not kept:
            self.keep(cid, ev)  # behind the ones kept before it: they go first, in the order they came, and no two
            return await self.release(cid)  # of a channel at once (a release under way takes this one too)
        if ch is None:
            # Deleted (on_guild_channel_delete took its state away; its Claude is left running): nothing can be
            # shown. A question or a plan it asks is handed straight back, as for a channel put away: the hook would
            # wait for an answer nobody can give (#issues 21).
            if what in (cc_hooks.ASK, cc_hooks.PLAN) and ev.get("ask_id"):
                await ask.deliver_to(ev.get("host"), ev["ask_id"], ask.gone_result(plan=what == cc_hooks.PLAN))
            return
        was = self.host.channels_state.get(str(ch.id)) or {}
        if put_away(was):
            return await self.let_go(ch, ev)
        if unplaced(was, ev):
            return await self.let_be(ev)
        if stranger(was, ev):
            return await self.let_be(ev, RESPAWNED_WHY)
        gone = from_elsewhere(was, getattr(self.rts.get(str(ch.id)), "opening", None), ev, getattr(self.rts.get(str(ch.id)), "closed", None))
        if gone == CLOSED_END:
            return  # a window the bot closed (!resume, /move's /exit): its end is no news, not even in #audit. (Let go as
            # "another window, it runs on in the terminal" it was said wrongly there after every /move: 2c1 drill 11-20)
        if gone:
            return await self.let_go(ch, ev, elsewhere=True)
        self.let_go_said.discard(ch.id)
        st, rt = self.host.chan(ch), self.rt(ch)
        self.repin(ch, st, ev, what)
        self.mark_parked(st, ev, what)
        if what in TURN_MOVED:
            st.pop("own_panel", None)  # a turn moved: a dialog on the screen from here on is the turn's, not the bot's
            self.unsent.moved(ch, st)  # posts held up by a dialog: handed again once the box is back (a task, C-74)
        if what == cc_hooks.ASK_CANCELLED:  # cancelled in its terminal: its buttons close (C-152)
            return await self.asks.cancelled(ch, ev)
        if what == cc_hooks.ASK_ELSEWHERE:  # not the conversation's own question: no card, said in #audit (C-167)
            return await self.asks.elsewhere(ch, ev)
        if what in (cc_hooks.ASK, cc_hooks.PLAN):
            self.fill(rt, ev)
            await self.interim(ch, rt, texts=ev.get("texts"))  # what Claude said before asking goes above the question
            shown = await (self.asks.post(ch, ev) if what == cc_hooks.ASK else self.asks.post_plan(ch, ev))
            posts = getattr(self.host, "posts", None)
            if shown and posts is not None:  # working on another channel's post: that channel waits on the owner too
                first = ((ask.load(ev["ask_id"]) or {}).get("messages") or {}).get("0")
                if first:
                    await posts.asking(ch, ev["ask_id"], first, plan=what == cc_hooks.PLAN)
            return
        if await self.agents.handle(ch, st, ev):  # a subagent's or workflow's own events: shown in its thread
            return self.host.save()
        if what in (cc_hooks.SESSION_ENDED, cc_hooks.SESSION_LEAVING, cc_hooks.TURN_END, cc_hooks.TURN_FAILED):
            await self.asks.cancel(ch, "The turn ended before the user answered.")
        if what in (cc_hooks.SESSION_ENDED, cc_hooks.SESSION_LEAVING):
            await self.end_turn(st, rt)
            # Only an end is said as one. A conversation left for another (/clear, /resume: the same Claude Code goes
            # on, and says so with the SessionStart that follows) is not: "Claude 結束了（視窗被關掉了？）" came up in
            # a window the bot opened whenever /resume was used there (#issues 20; step 2.3).
            ended = what == cc_hooks.SESSION_ENDED
            if ended and ev.get("dc_parked"):
                # The conversation ended where it had been moved to; the Claude Code in the window is still there, at
                # its agents list: "the window was closed" or "start Claude again in the same pane" would be wrong.
                attached = st.get("pane") and not st.get("home")
                await self.say(ch, "ℹ️ 這個頻道的對話先前被搬到背景（終端機按了 ←），現在它結束了。%s裡的 Claude Code 還開著，"
                                   "停在背景對話的清單：在那裡開的新對話這個頻道收不到。要繼續用這個頻道：%s" % (
                                       ("窗格", "在終端機離開 Claude Code，再在同一個窗格重新開；不再需要這個頻道的話用 `!detach`。") if attached
                                       else ("視窗", "用 `!reset` 重開這個頻道的對話。")))
            elif st.get("home") and ended:
                await self.say(ch, "ℹ️ 這個頻道的 Claude 結束了（tmux 視窗被關掉了？）。下一則訊息會在 tmux `%s` "
                                   "重新開一個視窗，接回同一個對話。" % st["home"]["session"])
            elif st.get("pane") and ended:
                await self.say(ch, "ℹ️ 這個對話在終端機那邊結束了。在同一個窗格重新開 Claude 就會自動接回這個頻道；"
                                   "不再需要這個頻道的話用 `!detach`。")
        elif ev.get("session_id"):
            switched = st.get("session_id") != ev["session_id"]
            if switched:
                sessions.remember(st, st.get("session_id"))  # /clear starts a new session; !resume can go back
            held = list(st.get("history") or [])  # the channel's own conversations before this one (history.seen())
            st["session_id"] = ev["session_id"]
            # the conversation goes on in this channel, so it is the channel's own from now: taken up again later
            # (!resume, dcpost --resume), only its last turns are posted (owner's report 2026-10-20: a channel had all
            # of it posted again: a conversation begun in the channel was noted only when it left it by !reset or
            # !resume, and !cwd noted it not at all)
            sessions.remember(st, ev["session_id"])
            if switched and what == cc_hooks.SESSION_CHANGED and ev.get("dc_why") == "resume":
                # the history of the conversation taken up, in a task of its own: on this machine read from its
                # file, on another by that machine's dcremote (nothing of that machine's waits behind it)
                asyncio.ensure_future(
                    history.show_remote(self.host, ch, st, ev["host"], ev["session_id"], ev.get("transcript_path"), held=held)
                    if ev.get("host") else history.show(self.host, ch, st, ev["session_id"], ev.get("transcript_path"), held=held))
        self.fill(rt, ev)  # a turn starts: only what is said from here on is relayed
        if what in (cc_hooks.INPUT, cc_hooks.PROGRESS, cc_hooks.PEER_SENT, cc_hooks.TURN_END, cc_hooks.TURN_FAILED) and self.another_turn(rt, ev, what):
            await self.end_turn(st, rt)  # the turn still open here ended with no event (cut short): this is not it
        texts = ev.get("texts")  # read where the transcript is: here by fill(), on another machine by tools/dcremote
        if what in (cc_hooks.READY, cc_hooks.SESSION_CHANGED, cc_hooks.COMPACTED):  # a Claude Code says its session is there
            rt.ready_sid = ev.get("session_id")  # (the conversation it says it is: a move checks it, C-201 2c1)
            rt.ready.set()
        elif what == cc_hooks.INPUT:
            prompt = ev.get("prompt") or ""
            if rt.entered and events.matches(prompt, rt.entered):  # this one, not an earlier one's late receipt
                rt.entered = None  # taken: the same words in the box later (called back with the up key) are the owner's
            rt.late = [p for p in rt.late if time.time() - p.late < LATE_SECS]
            ours = [p for p in rt.pending + rt.late if events.matches(prompt, p.text)]
            for p in ours:
                (rt.pending if p in rt.pending else rt.late).remove(p)
                p.got.set()
                try:
                    await p.message.add_reaction("👀")
                except Exception:
                    pass
            # Whose words these are. First of all: are they what the bot typed in for somebody (`ours`, above; the 👀)?
            # Only for what is not is the connector's "from" asked: it goes by how the prompt begins, and a message
            # sent from Discord may begin like a notice of Claude Code's ("[TODO] …", "<div> …").
            if ev.get("dc_from") == cc_hooks.PEER:  # another Claude Code session wrote to this one
                try:
                    await deliver.deliver(ch, embed=posts_mod.cards(peers.who(ev.get("dc_peer") or peers.UNNAMED, self.host.channels_state),
                                                                    masking.masked(ev.get("dc_text") or "", limit=3850))[0],
                                          silent=True, intent="talk", framed=("card",))
                except Exception:
                    traceback.print_exc()
            elif not ours and ev.get("dc_from") == cc_hooks.TYPED and prompt.strip() \
                    and not any(events.matches(prompt, t) for t in rt.typed):  # typed at the terminal, not in Discord
                await self.say(ch, "⌨️ **終端機那邊輸入**\n" + events.quote(masking.masked(prompt), 1500), silent=True)
            if st.get("reset_after"):  # (dc post --reset asked in a turn that did not end well: dropped)
                await self.host.posts.resets.new_turn(ch, st, ev.get("prompt_id"))
            if st.get("move_after"):  # (a tmux move waiting on a turn that did not end well: dropped; C-201 2c1)
                await self.host.mover.new_turn(ch, st, ev.get("prompt_id"))
            self.begin_turn(ch, st, rt, prompt, task=ev.get("dc_from") == cc_hooks.TASK, pid=ev.get("prompt_id"))
        elif what in (cc_hooks.PROGRESS, cc_hooks.PEER_SENT):
            turn = self.begin_turn(ch, st, rt, pid=ev.get("prompt_id"))
            name = ev.get("tool_name", "?")
            turn.tools[name] = turn.tools.get(name, 0) + 1
            turn.lines = (turn.lines + [events.progress_line(ev)])[-6:]
            # Posted from the channel's lane, in this order, never awaited here (lanes.py: one channel's Discord
            # request held every channel's events, 11-10)
            lane = self.lane(ch, rt)
            if config.INTERIM and texts:
                lane.put("送引言", lambda texts=texts: self.interim(ch, rt, texts=texts, lane=lane))
            if what == cc_hooks.PEER_SENT and ev.get("send_to") in peers.directory(self.host.channels_state):
                # (a message with words in it, to a conversation that has a channel: not a bare "tell me when idle")
                words = "📤 **傳給 %s**\n%s" % (peers.who(ev["send_to"], self.host.channels_state),
                                              events.quote(masking.masked(ev.get("send_text") or ""), 1800))
                lane.put("送📤", lambda words=words: self.lane_say(lane, ch, words))
            lane.put("處理中", lambda turn=turn: self.progress(ch, turn, lane), progress=True)
        elif what == cc_hooks.TURN_END:
            turn = rt.turn
            st["bg"] = len(ev.get("background_tasks") or []) + len(ev.get("session_crons") or [])
            st["last_active"] = time.time()
            final = (ev.get("last_assistant_message") or "").strip()
            await self.interim(ch, rt, final, texts=texts)
            self.said(ch, final)
            rt.last_final = final
            await self.end_turn(st, rt)
            st["context"] = ev.get("context")
            ran_on = ev.get("host") or remote.this_machine()  # C-180: the machine the channel's Claude runs on (its
            # events' host; this one when they come from this machine's own spool), not the bot's as such
            foot = events.footer(turn.tools if turn else {}, time.time() - turn.t0 if turn else None, st["bg"],
                                 st["context"], tail=events.footer_tail(ran_on, ev.get("prompt_id"))
                                 if getattr(self.host, "lease", None) is not None else "")  # (C-163 1.4: with a standby)
            if turn is None and ev.get("host") and ev.get("host") == remote.old_bot_machine():  # a turn begun under the
                # bot before the takeover, its Stop read from that machine's spool: marked as a replay is (drill 11-08)
                foot = messages.lease("replayed_footer", tail=events.footer_tail(ran_on, ev.get("prompt_id")))
            reply = masking.masked(ev.get("last_assistant_message") or "（沒有文字回覆）")
            src = turn.src if turn else rt.where  # no turn begun: a queued message, sent in with no event until its Stop
            # where this turn begins in the transcript, for 📋. Its end is looked for when asked, by the turn's id in
            # the file; a Claude Code that sends no prompt_id: the file's size now, as before (another machine's
            # file has no size here: by the id only)
            job = {"final": final, "foot": foot, "src": list(src) if src and src[0] else None, "pid": ev.get("prompt_id"),
                   "host": ev.get("host"), "prompt": turn.prompt if turn else "…",
                   "end": None if ev.get("prompt_id") or ev.get("host") or not (src and src[0]) else transcript.size(src[0])}
            spec = rewrite.wanted(reply)
            seed = getattr(self.host, "seed_turns", None) if getattr(self.host, "lease", None) is not None else None
            if seed is not None and ev.get("prompt_id"):
                await seed(ch)  # (what the bot before this process posted there counts too: drill 11-08)
            if getattr(self.host, "lease", None) is not None and ev.get("prompt_id") and \
                    not self.host.claim_turn(str(ch.id), ev["prompt_id"]):
                pass  # C-163: posted already (by the replay after a takeover): not twice (#code-review-2 on 43bfe53)
            elif spec is not None:  # a plugin lays it out (C-71b): in a task of its own; the channel's events wait for it
                st["rewriting"] = dict(job, reply=reply, at=time.time())
                self.rewriting.add(str(ch.id))
                asyncio.ensure_future(self.rewrite_and_post(ch, st, spec))
            else:
                await self.post_reply(ch, st, self.plain_reply(reply), job)
            gist = calls.discussed(reply)  # (C-103: Claude says it needs the owner; after the reply, which may wait)
            if gist:
                await calls.discuss(ch, self.host.chan(ch), "discuss:%s" % (ev.get("prompt_id") or int(time.time() * 1000)),
                                    gist, save=self.host.save)  # (one mention while the owner has not answered: v0.6.1)
            elif (ev.get("last_assistant_message") or "").strip():  # a reply that needs nobody: what was discussed is
                # over (manager 11-20); the next mark rings. Claude's own words: `reply` says （沒有文字回覆） for none
                # (#code-review on 314211bf, low)
                await self.call_done(ch, prefix="discuss:")
            await self.login_back(st)  # a turn of that machine ended well: a lost login is back
            if st.get("reset_after"):  # its Claude asked for a new conversation during this turn (dc post --reset)
                self.host.posts.resets.after_turn(ch, st, ev.get("prompt_id"))  # (a task: its receipt is an event)
            if st.get("move_after"):  # a tmux move waiting on this turn (C-201 2c1: bot/moving.py)
                self.host.mover.after_turn(ch, st, ev.get("prompt_id"))
        elif what == cc_hooks.TURN_FAILED:  # the API failed (usage limit, overload, …): the turn is over, say why
            turn = rt.turn
            await self.interim(ch, rt, texts=texts)
            await self.end_turn(st, rt)
            if not self.quota.handle(ch, st, ev, turn):  # a usage limit: Claude Code may wait for the reset itself
                await self.failed(ch, ev, turn)
        elif what in (cc_hooks.QUOTA_RESUMED, cc_hooks.QUOTA_STALE, cc_hooks.QUOTA_STOPPED):
            self.quota.handle(ch, st, ev, None)  # how its wait ended (C-99a): said, never a dialog
        elif what == cc_hooks.DIALOG and not ask.waiting(ch.id):  # (Claude Code's notice that it sits idle is no dialog)
            await self.menus.post(ch, st, ev)  # a dialog in the terminal: shown with buttons
        if what in MOVED_ON:
            await self.menus.resolve(ch)  # the session moved on: an old dialog's buttons would mislead
            blocked.clear(st)  # it moves again: whatever held it up is over (C-101)
            await self.call_done(ch, prefix="failed:")  # (C-103: and its mention)
            await self.usage_maybe_back()
            self.quota.moved_on(ch, st, ev)
        self.host.save()

    def receipt_meanwhile(self, ev):
        """While a channel's reply is being laid out (C-71b), is this the receipt of a message the bot typed into
        it (its UserPromptSubmit, matching rt.pending or rt.late)? Handled at once, not held: submit() waits only
        RECEIPT_SECS for it, and said "maybe not received", then took the late one for the owner's at the terminal
        (#code-review-2 on f2ee5d3). Handling it posts nothing (👀 on the message); input not the bot's would post
        "⌨️ 終端機那邊輸入": that waits, with everything else of the channel."""
        cid = str(ev.get("channel") or "")
        if cid not in self.rewriting or ev.get("dc_kind") != cc_hooks.INPUT:
            return False
        rt = self.rts.get(cid)
        prompt = ev.get("prompt") or ""
        return rt is not None and any(events.matches(prompt, p.text) for p in rt.pending + rt.late)

    def plain_reply(self, reply):
        """The final reply as it goes without a plugin: one message, its small tables rewritten (C-71a)."""
        if messages.loaded.get("small_tables", True):  # Markdown tables Discord shows as bars: a code block or a list
            reply = tables.rewrite(reply)[0]
        return [(reply, [])]

    async def post_reply(self, ch, st, msgs, job):
        """The final reply posted: `msgs` [(text, [picture paths])] in order, the footer on the last; then its 📂
        buttons, its record for 📋 and its line in #audit. `job`: what the Stop left for it (final, foot, src, …).
        A message may have a third item, what each picture was made from (rewrite.read()): kept for 📝 (sources.py)."""
        foot, sent = job.get("foot"), []
        if msgs and msgs[0][0]:  # (P0-6: a reply beginning like another channel's post, a notification… is not read so)
            msgs = [(frames.defused(msgs[0][0]),) + tuple(msgs[0][1:])] + list(msgs[1:])
        for i, (text, pics, *more) in enumerate(msgs):
            if i == len(msgs) - 1 and foot:
                text = text + "\n" + foot if text else foot
            parts = split_message(text) if text else []
            if not pics:
                sent += await self.say(ch, "\n".join(parts)) if parts else []
                continue
            for part in parts[:-1]:
                sent += await self.say(ch, part)
            files = []
            try:
                files = [discord.File(p) for p in pics]
                sent.append(await deliver.deliver(ch, parts[-1] if parts else None, files=files, intent="reply"))
                if more:
                    sources.remember(sent[-1], ch.id, more[0], len(pics))
            except Exception:
                traceback.print_exc()
                if parts:
                    sent += await self.say(ch, parts[-1])
            finally:
                for f in files:  # (the pictures are dropped next: nothing left open on them)
                    f.close()
        for m in sent:  # (C-172a P1a: who and what; the footer is on the last only, and only that comes off when read)
            ledger.note(getattr(ch, "id", None), getattr(m, "id", None), "reply",
                        framed=("status",) if foot and m is sent[-1] else ())
        if sent and getattr(self.host, "files", None) is not None:  # 📂 buttons for the files it mentions
            asyncio.ensure_future(self.host.files.add_links(ch, st, sent[-1], job.get("final") or ""))
        src = job.get("src")
        if src and (not job.get("host") or job.get("pid")):
            process.remember(st, [m.id for m in sent if hasattr(m, "id")], src[0], src[1],
                             None if job.get("host") else job.get("end"), job.get("pid"), host=job.get("host"))
        await self.host.audit("#%s：%s → 完成%s" % (getattr(ch, "name", "DM"), job.get("prompt") or "…",
                                                 "（%s）" % foot[3:] if foot else ""))
        return sent

    async def rewrite_and_post(self, ch, st, spec):
        """A plugin lays the final reply out (C-71b, rewrite.py), in a task of its own: the round of events does not
        wait for it. The channel's later events wait meanwhile (waits() leaves this machine's in the spool, handle()
        keeps another machine's in memory), so nothing of the next turn comes before this reply; other channels go
        on. Past SHOW_AFTER the channel is told it is being laid out. A failure, a timeout or nothing drawn: the
        reply goes as before (C-71a). The reply is in the channel's state (`rewriting`) until posted: a restart in
        the middle posts it as before (posted_left())."""
        cid, job, shown, token = str(ch.id), st.get("rewriting") or {}, [], None

        async def tell():
            await asyncio.sleep(rewrite.SHOW_AFTER)
            try:
                shown.append(await deliver.deliver(ch, "-# ⏳ 整理排版中…", silent=True, intent="status", keep=False))  # (deleted soon: not kept)
            except Exception:
                traceback.print_exc()
        teller = asyncio.ensure_future(tell())
        try:
            def started(pid):  # its process group, kept with the reply: a restart in the middle stops it (posted_left)
                job.update(pgid=pid, pgid_start=rewrite.started_at(pid))
                self.host.save()
            msgs, why, token = await rewrite.run(spec, job.get("reply") or "", started)
            if msgs is None:
                print("[rewrite] %s，#%s：照原本的貼法（%s）" % (spec.get("plugin"), getattr(ch, "name", cid), why), flush=True)
            if msgs is None or rewrite.unchanged(msgs):
                msgs = self.plain_reply(job.get("reply") or "")
            teller.cancel()
            await self.post_reply(ch, st, msgs, job)
        except Exception:
            traceback.print_exc()
            try:
                await self.post_reply(ch, st, self.plain_reply(job.get("reply") or ""), job)
            except Exception:
                traceback.print_exc()
        finally:
            teller.cancel()
            for m in shown:
                try:
                    await deliver.remove(ch, m)
                except Exception:
                    pass
            if token:
                rewrite.drop(token)
            st.pop("rewriting", None)
            self.host.save()
            turn = getattr(self.rts.get(cid), "turn", None)
            if turn is not None:  # the next turn's tool calls held meanwhile come all at once now: no "working…"
                turn.last = time.time()  # message posted for them only to go with its Stop (#code-review-2 on f0f950b)
            self.rewriting.discard(cid)
            if cid in self.kept:  # another machine's events kept meanwhile: now, in their order
                asyncio.ensure_future(self.release(cid))

    async def posted_left(self):
        """A final reply a plugin was laying out when the bot went down (`rewriting` in a channel's state): posted as
        it goes without a plugin, at the start (C-71b)."""
        for cid, st in list(self.host.channels_state.items()):
            job = st.pop("rewriting", None) if isinstance(st, dict) else None
            if isinstance(job, dict):
                rewrite.stop_left(job.get("pgid"), job.get("pgid_start"))  # the plugin left running when the bot died
            ch = self.host.get_channel(int(cid)) if isinstance(job, dict) and str(cid).isdigit() else None
            if ch is not None and job.get("reply"):
                try:
                    await self.post_reply(ch, st, self.plain_reply(job["reply"]), job)
                except Exception:
                    traceback.print_exc()
        self.host.save()

    def read_rewriter(self):
        """Which plugin rewrites replies (rewrite.choose()), read in a thread: at start and once a minute."""
        task = getattr(self, "rewriter_read", None)
        if task is None or task.done():
            async def read():
                try:
                    await asyncio.get_running_loop().run_in_executor(None, rewrite.choose)
                except Exception:
                    traceback.print_exc()
            self.rewriter_read = task = asyncio.ensure_future(read())
        return task

    def keep(self, cid, ev, first=False, quiet=False):
        """An event of a channel that is not to be had just now is kept (in memory: a restart drops them, as every
        such event was dropped before step 2.2e), at most KEPT_MAX for a channel (the oldest goes, with a line). Also
        an event that comes while the ones kept are being handled: it goes behind them (`first`: one of those
        themselves, put back because the channel went away again)."""
        rec = self.kept.get(cid)
        if rec is None:
            rec = self.kept[cid] = [time.time(), [], False]
            if cid not in self.kept_said and not quiet:
                self.kept_said.add(cid)
                print("bridge: channel %s is not to be had from Discord just now (not deleted): its events are kept, for %d s at most "
                      "(said once for a channel)" % (cid, KEPT_SECS), flush=True)
        rec[1].insert(0, ev) if first else rec[1].append(ev)
        if len(rec[1]) > KEPT_MAX:
            gone = rec[1].pop(0)
            print("bridge: more than %d events kept for channel %s: the oldest is dropped (%s)" % (KEPT_MAX, cid, gone.get("hook_event_name")), flush=True)

    async def release(self, cid):
        """The events kept for a channel are handled now, in the order they came. The channel stays among the kept
        until the last one is done: an event that comes meanwhile (from this machine's round, or from another
        machine's stream, which is another task) goes behind them (handle() sees the channel is being released), so
        nothing overtakes and no two of a channel are handled at once."""
        rec = self.kept.get(cid)
        if rec is None or rec[2] or cid in self.rewriting:
            return  # nothing kept, another task is at it, or its reply is being laid out (released once posted)
        rec[2] = True
        done = 0
        try:
            while rec[1]:
                if self.host.get_channel(int(cid)) is None and cid in self.host.channels_state:
                    return  # away again: the rest stays kept, in its order
                if cid in self.rewriting:
                    return  # a Stop among them started a layout: the rest waits for it, in its order
                ev = rec[1].pop(0)
                done += 1
                try:
                    await self.handle(ev, kept=True)
                except Exception:
                    traceback.print_exc()
            self.kept.pop(cid, None)
            self.away.pop(cid, None)
        finally:
            rec[2] = False
            if done:
                print("bridge: channel %s: %d kept events were handled (first kept %.0f s ago)" % (cid, done, time.time() - rec[0]), flush=True)

    async def release_kept(self):
        """Each round: for a channel that is there again (or whose state is gone: deleted meanwhile) a task of its own
        handles what was kept (handling may reach another machine; this round waits on none); what was kept longer
        than KEPT_SECS is given up, with a line, once for a channel (a question among them stays with its hook, as
        it did before: nothing is known to be gone, so nothing is refused). Such a channel is written down
        (note_away()): its state says it is there and Discord has not handed it out for that long; deleted while the
        bot was not running is one way to get there, Discord being away is another."""
        for cid, rec in list(self.kept.items()):
            if rec[2]:
                continue
            if self.host.get_channel(int(cid)) is not None or cid not in self.host.channels_state:
                asyncio.ensure_future(self.release(cid))
            elif time.time() - rec[0] > KEPT_SECS:
                self.kept.pop(cid, None)
                was = self.away.get(cid) or [rec[0], 0]
                self.away[cid] = [was[0], was[1] + len(rec[1])]
                if ("dropped", cid) not in self.kept_said:
                    self.kept_said.add(("dropped", cid))
                    print("bridge: channel %s was not to be had for %d s: its %d kept events are dropped (%s); said once for a channel"
                          % (cid, KEPT_SECS, len(rec[1]), ", ".join(sorted(set(str(e.get("hook_event_name")) for e in rec[1])))), flush=True)
        for cid in [c for c in self.away if self.host.get_channel(int(c)) is not None or c not in self.host.channels_state]:
            self.away.pop(cid, None)  # there again, or known to be deleted now
        if self.away or getattr(self, "away_noted", None):
            self.note_away()

    def away_long(self):
        """The channels not to be had for longer than KEPT_SECS whose state is still here: for each, since when, how
        many events were kept for it in all, and why it is listed."""
        return [{"channel": cid, "since": rec[0], "events": rec[1],
                 "why": "Discord 拿不到這個頻道超過 %d 秒，頻道狀態還在（Discord 連不上時正在用的頻道也會這樣；機器人不在時被刪的頻道也是）；"
                        "它的事件沒有地方貼，留過的都丟了" % KEPT_SECS}
                for cid, rec in sorted(self.away.items())]

    def note_away(self):
        """state/channels-unreachable.json: what away_long() says, as of now (an empty list when there is none). A
        file of its own, and not among the sessions without a channel (state/ownerless-sessions.json): what doctor
        says of those is that they may be closed, and a channel listed here may be in use (manager, 2026-10-13: not
        sure is not "may be closed"). Nothing reads this file yet; a doctor item for it is to come with the next
        change in ops/."""
        listed = self.away_long()
        if listed == getattr(self, "away_noted", None):
            return
        path = os.path.join(config.STATE, UNREACHABLE)
        try:
            os.makedirs(config.STATE, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"at": time.time(), "channels": listed}, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)
            self.away_noted = listed
        except OSError:
            traceback.print_exc()

    def mark_parked(self, st, ev, what):
        """Remember, with the channel, that its pane's conversation was moved to a background process (parked()):
        from the first event that says so, until the pane's own Claude Code starts a conversation or ends."""
        where = window_of(st)
        if ev.get("dc_parked") and where is not None:
            rec = parked(st)
            if rec is None or rec.get("session") != ev.get("session_id"):
                st["parked"] = {"session": ev.get("session_id"), "since": time.time(), "pane": where}
            elif what == cc_hooks.INPUT and rec.get("unanswered"):
                rec.pop("unanswered")  # what is typed reaches it again
            else:
                return
        elif "parked" in st and not ev.get("dc_parked") and what in (cc_hooks.READY, cc_hooks.SESSION_CHANGED, cc_hooks.SESSION_ENDED):
            st.pop("parked")
        else:
            return
        self.note_parked()

    def note_parked(self):
        """state/panes-parked.json: the channels whose pane's conversation was moved to a background process, as of
        now (an empty list when there is none): {"channel", "session", "since", "unanswered"}; "unanswered" is when a
        message typed into the pane last got no word from that conversation, None if that has not happened since
        it last took one. For the list of things that want the owner's eye; nothing reads the file yet."""
        listed = []
        for cid, st in sorted(self.host.channels_state.items()):
            rec = parked(st) if isinstance(st, dict) else None
            if rec:
                listed.append({"channel": cid, "session": rec.get("session"), "since": rec.get("since"),
                               "unanswered": rec.get("unanswered")})
        if listed == getattr(self, "parked_noted", None):
            return
        path = os.path.join(config.STATE, PARKED)
        try:
            os.makedirs(config.STATE, exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"at": time.time(), "channels": listed}, f, ensure_ascii=False)
            os.replace(path + ".tmp", path)
            self.parked_noted = listed
        except OSError:
            traceback.print_exc()

    def start_resends(self):
        """A task for each machine that is owed a result (ask.undelivered()) and has none running. Called by the round
        every 30 s and by the asker the moment something is owed (hand()); it reads records and starts tasks, and
        waits on nothing. (Outside a running loop, as in a test that closes a question by hand: nothing is started;
        the round starts it.)"""
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return
        for host in ask.undelivered():
            if host not in self.resending:
                self.resending[host] = asyncio.Event()
                asyncio.ensure_future(self.refuse_again(host))

    async def refuse_again(self, host):
        """A refusal that `host` did not take (its question's channel was deleted while the machine did not answer) is
        sent again until it gets there: after RESEND_FIRST seconds, then twice as long each time, RESEND_MAX at most;
        at once when a command gets through to the machine or its stream is back. A task of its own, one for a
        machine: it waits on that machine, and the bot's round of events must not."""
        delay = RESEND_FIRST
        try:
            while True:
                wake = self.resending[host] = asyncio.Event()
                try:
                    if not await self.asks.again(host):
                        return
                except Exception:
                    traceback.print_exc()
                try:
                    await asyncio.wait_for(wake.wait(), delay)
                    delay = RESEND_FIRST  # the machine answered something: begin anew
                except asyncio.TimeoutError:
                    delay = min(delay * 2, RESEND_MAX)
        finally:
            self.resending.pop(host, None)

    async def let_go(self, ch, ev, elsewhere=False):
        """An event of a channel that !detach put away: nothing is posted, nothing is written to the channel's
        state (what "接回原本的對話" goes by is the record made at the detach), no line per turn in #audit; #audit
        is told once that its events are dropped. The event still counts as handled, so none pile up.

        A window the bot opened on this machine keeps DCBOT_CHANNEL in its environment and the bot's hooks in
        its --settings: detaching does not stop it reporting (a pane attached, or a window on another machine,
        reports through the route table, which the detach cleared). Until 2026-10-11 every turn it ran
        afterwards was posted into the archived channel and its state grew a session, a turn, a context again
        (#issues 11). A question or a plan it asks is handed straight back: the hook would otherwise wait half a
        day for an answer nobody can give."""
        what = cc_hooks.normalize(ev, anew=True).get("dc_kind")  # (let go before handle() made its kind out)
        if what == cc_hooks.ASK and ev.get("ask_id"):
            await ask.deliver_to(ev.get("host"), ev["ask_id"], {"reason": UNFOLLOWED})
        elif what == cc_hooks.PLAN and ev.get("ask_id"):
            await ask.deliver_to(ev.get("host"), ev["ask_id"], {})  # the terminal's own dialog takes it
        said = (ch.id, (ev.get("dc_pane") or {}).get("pane")) if elsewhere else ch.id
        if said not in self.let_go_said:
            self.let_go_said.add(said)
            await self.host.audit(
                "#%s 收到別的視窗（%s）的事件：那個視窗是這個頻道以前的對話，現在不是，之後它的事件不再處理（它在終端機照常跑）"
                % (getattr(ch, "name", "DM"), said[1]) if elsewhere else
                "#%s 已卸下，之後它的事件不再處理（那個對話在終端機照常跑；接回或重新 attach 就恢復）" % getattr(ch, "name", "DM"))

    async def let_be(self, ev, why=None):
        """An event of a background process that is not the channel's conversation (unplaced()): nothing is posted,
        nothing of the channel's state is touched, nothing is said in #audit (the owner's own conversations in the
        agents list are no fault). A question it asks is left to the terminal's own menu, a plan to its dialog. One
        line for each such conversation in state/events-unplaced.log, for whoever asks later why nothing was posted.
        `why`: what the line says it is, when it is not that (stranger(): another Claude Code in an attached pane)."""
        what = cc_hooks.normalize(ev, anew=True).get("dc_kind")
        if what in (cc_hooks.ASK, cc_hooks.PLAN) and ev.get("ask_id"):
            await ask.deliver_to(ev.get("host"), ev["ask_id"], {} if what == cc_hooks.PLAN else {"pass": True})
        sid = str(ev.get("session_id"))
        noted = self.__dict__.setdefault("unplaced_noted", set())
        if sid in noted:
            return
        noted.add(sid)
        path = os.path.join(config.STATE, UNPLACED_LOG)
        try:
            os.makedirs(config.STATE, exist_ok=True)
            if os.path.exists(path) and os.path.getsize(path) > UNPLACED_MAX:
                os.replace(path, path + ".1")
            with open(path, "a", encoding="utf-8") as f:
                f.write("%s session %s channel %s%s first %s: %s; its events are not handled\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), sid, ev.get("channel"), " host %s" % ev["host"] if ev.get("host") else "",
                           ev.get("hook_event_name"), why or UNPLACED_WHY))
        except OSError:
            traceback.print_exc()

    async def bad_event(self, ev):
        """Say in #audit that an event could not be handled (at most once a minute): a new version that fails on
        every Stop would otherwise silence all channels without a word (the self test does not go through
        handle(), so the supervisor would not roll it back)."""
        now = time.time()
        self.bad_count = getattr(self, "bad_count", 0) + 1
        if now - getattr(self, "bad_said", 0) >= 60:
            self.bad_said = now
            await self.host.audit("⚠️ 有事件處理失敗（%s，頻道 %s），已移到 `state/events-bad/`；這次啟動以來共 %d 個。詳情在 supervisor.out。"
                                  % (ev.get("hook_event_name"), ev.get("channel"), self.bad_count))

    async def failed(self, ch, ev, turn):
        """A StopFailure: what went wrong, with a button that asks Claude to carry on."""
        what, detail = events.failure(ev)  # (by the API's error code, as the connector passes it on)
        st = self.host.channels_state.get(str(ch.id))
        b = blocked.put(st, "api_error", self.machine_of(st), detail=what, why=ev.get("dc_why")) if st is not None else None
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="再試一次", custom_id="retry:%d" % ch.id, style=discord.ButtonStyle.primary))
        msg = None
        try:
            msg = await deliver.deliver(ch, ("⚠️ **這一輪中斷了：%s**%s%s" % (what, "\n" + events.quote(detail, 1500) if detail else "",
                                                                        "\n-# 按「再試一次」會請 Claude 從中斷的地方接著做")),
                                        view=view, intent="card")
            view.stop()
        except Exception:
            traceback.print_exc()
        try:  # (C-103) the owner's mention: once for the machine or the installation when they all stop, else here
            if st is None:
                pass
            elif ev.get("dc_why") == "authentication_failed":
                await self.login_lost(ch, st)
            elif ev.get("dc_why") == "rate_limit":  # a usage limit Claude Code does not wait out
                await self.usage_limited(ch)
            else:
                await self.call(ch, "failed:%d" % int(b["since"] * 1000), "turn_failed", what, about=getattr(msg, "id", None))
        except Exception:
            traceback.print_exc()
        await self.host.audit("#%s：%s → 失敗（%s）" % (getattr(ch, "name", "DM"), turn.prompt if turn else "…", what))

    @staticmethod
    def machine_of(st):
        """The machine of the channel's conversation when it is not the bot's (for `blocked`), else None."""
        t = st.get("pane") or (st.get("home") if isinstance(st.get("home"), dict) else None)
        return t.get("host") if isinstance(t, dict) else None

    # ------------------------------------------------------------------ mentions (C-103: bot/calls.py)
    async def call(self, ch, key, code, gist=None, about=None, **data):
        """The owner mentioned once for `key` in this channel (calls.call())."""
        return await calls.call(ch, self.host.chan(ch), key, code, gist, about, save=self.host.save, **data)

    async def call_done(self, ch, key=None, prefix=None):
        """What `key` (or every open key starting with `prefix`) asked for is over: its line ends with ✅."""
        st = self.host.channels_state.get(str(ch.id))
        if st is None:
            return False
        if prefix is not None:
            return await calls.done_all(ch, st, prefix, self.host.save)
        return await calls.done(ch, st, key, self.host.save)

    async def question_asked(self, ch, ask_id, code, gist, mid, n=1):
        await self.call(ch, "ask:" + ask_id, code, masking.masked(gist or ""), about=mid, n=n)

    async def question_closed(self, ch, ask_id):
        await self.call_done(ch, "ask:" + ask_id)

    async def start_failed(self, ch, st, e):
        """A message not sent because the conversation could not be opened: mentioned once until one opens (open()
        marks it ✅). A machine that cannot be reached is said once for the whole machine instead (machine_watch());
        an attached pane no longer running Claude Code is the owner's own terminal, nothing the bot failed to start."""
        if getattr(e, "why", None) in (NotOpened.UNREACHABLE, NotOpened.PANE_GONE):
            return
        since = st.setdefault("start_failed", int(time.time()))
        await self.call(ch, "not_started:%d" % since, "not_started")

    async def notify_channel(self):
        """The default notification channel (altention dc notify's), for what stops the whole installation."""
        posts = getattr(self.host, "posts", None)
        return await posts.channel("@notify") if posts is not None else None

    @staticmethod
    def single_machine():
        try:
            return len(remote.inventory().get("machines") or {}) <= 1
        except Exception:
            return True

    async def login_lost(self, ch, st):
        """The conversation's machine needs Claude Code logged in again: said once for that machine, in the notification
        channel, its channels listed (C-103). Today it is known by a turn that failed with authentication_failed;
        C-100's own look at the screen calls this too, unchanged."""
        machine = self.machine_of(st) or remote.this_machine()
        one = self.single_machine()
        head = calls.line("login_one") if one else calls.line("login", machine=machine)
        how = messages.call("login_how_one") if one else messages.call("login_how", machine=machine)
        await calls.system(self.notify_channel, "login:" + machine, "lost", head, [how], channel=ch.id)

    async def login_back(self, st):
        """A turn of that machine ended well: its login is back (said where the loss was, mentioning nobody)."""
        machine = self.machine_of(st) or remote.this_machine()
        if calls.open_episode("login:" + machine) is None:
            return
        text = messages.call("login_back_one") if self.single_machine() else messages.call("login_back", machine=machine)
        await calls.over(self.notify_channel, "login:" + machine, text)

    async def usage_limited(self, ch, when=None):
        """A usage limit stops this channel: said once for the installation while any channel is held by one (C-103),
        this channel added to the list. `when`: the reset as the channel shows it."""
        head = calls.line("usage_limit", when=when) if when else calls.line("usage_limit_unknown")
        await calls.system(self.notify_channel, "usage_limit", "on", head, channel=ch.id)

    async def usage_maybe_back(self):
        """No channel is held by a usage limit any more: the installation's line says so, mentioning nobody."""
        if calls.open_episode("usage_limit") is None:
            return
        if any(blocked.usage(st) for st in self.host.channels_state.values() if isinstance(st, dict)):
            return  # (one Claude Code does not wait out counts too: #code-review on 3c7ad8f)
        await calls.over(self.notify_channel, "usage_limit", messages.call("usage_back"))

    def channels_on(self, host):
        return [cid for cid, st in self.host.channels_state.items() if isinstance(st, dict) and self.machine_of(st) == host]

    def working_on(self, host):
        """The channels of `host` with work under way: a turn running, or messages waiting to be handed to it (typed
        and not taken yet, or kept to be sent again: unsent)."""
        out = []
        for cid in self.channels_on(host):
            st, rt = self.host.channels_state[cid], self.rts.get(cid)
            if st.get("busy") or st.get("unsent") or (rt is not None and (rt.pending or rt.turn is not None)):
                out.append(cid)
        return out

    def went_down(self, host):
        """`host`'s stream is down (again): the moment is noted the first time, with the channels whose work it cut
        off then (machine_watch())."""
        self.stream_down.add(host)
        down = self.__dict__.setdefault("down_since", {})
        if host not in down:
            down[host] = time.time()
            try:  # (in the stream's own loop: what it cut off is worth noting, never worth stopping the stream for)
                self.__dict__.setdefault("cut_off", {})[host] = self.working_on(host)
            except Exception:
                traceback.print_exc()

    async def machine_watch(self, now=None):
        """Another machine whose stream has been down MACHINE_DOWN_SECS: the owner is told once, in the notification
        channel, only when work was cut off (a turn running, messages waiting for it, when it went down or since),
        those channels listed; a machine whose channels were all idle (one the owner turned off, as the mac is when
        not in use) gets one line in #audit and no mention: the channel says it cannot reach it when the owner next
        writes there (manager 2026-11-03). Its line "back" only after a mention (stream(): machine_back())."""
        now = time.time() if now is None else now
        down, cut = self.__dict__.setdefault("down_since", {}), self.__dict__.setdefault("cut_off", {})
        said = self.__dict__.setdefault("down_said", set())
        for host in list(self.stream_down):
            since = down.setdefault(host, now)
            if now - since < MACHINE_DOWN_SECS or host in said or not self.channels_on(host):
                continue
            if calls.open_episode("machine:" + host) is not None:  # said before a restart, not over yet: not again
                said.add(host)  # (#code-review-2 on d988a6b; state/calls-system.json outlives the bot)
                continue
            said.add(host)
            work = sorted(set(cut.get(host) or []) | set(self.working_on(host)))
            if not work:
                await self.host.audit(messages.call("machine_idle", machine=host))
                continue
            await calls.system(self.notify_channel, "machine:" + host, str(int(since)),
                               calls.line("machine_down", machine=host, n=len(work)), [calls.channels_text(work)])

    async def machine_back(self, host):
        for name in ("down_since", "cut_off"):
            self.__dict__.setdefault(name, {}).pop(host, None)
        self.__dict__.setdefault("down_said", set()).discard(host)
        if calls.open_episode("machine:" + host) is not None:  # (only after a mention)
            await calls.over(self.notify_channel, "machine:" + host, messages.call("machine_back", machine=host))

    async def on_interaction(self, interaction):
        """The "再試一次" button under a failure. Returns True if the interaction was ours."""
        if await self.unsent.on_interaction(interaction):
            return True
        cid = ((interaction.data or {}).get("custom_id") or "") if interaction.type == discord.InteractionType.component else ""
        if not cid.startswith("retry:"):
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        await interaction.response.edit_message(view=None)
        await self.submit(Origin(interaction.channel), RETRY)
        return True

    def fill(self, rt, ev):
        """Add to an event of this machine what tools/dcremote adds to one of another: the same function of the
        connector, with the same rule for turns (the event's prompt_id). A subagent's files are not looked for
        here: that waits, asleep, and this is the event loop (agents.py reads them). Reading can fail (the file,
        a line of it): the event is handled all the same, with `relay_error`, as an event of another machine is,
        and never set aside for it. Where the running turn begins is kept as the last event tells it: its own
        first row is written after its first hook, so the first word on it is not the last."""
        path = ev.get("transcript_path")
        if not path:
            return
        if not ev.get("host"):  # another machine's event was filled there (tools/dcremote), its file is not here
            try:
                cc_hooks.enrich(ev, rt.pos, texts=config.INTERIM)
            except Exception as e:
                traceback.print_exc()
                ev["relay_error"] = "%s: %s" % (type(e).__name__, e)
        elif "dc_turn_start" not in ev:
            rt.where = None  # a machine whose engine does not say where the turn begins: no 📋 for it
        if "dc_turn_start" in ev:  # None: a turn that was running when the bot came to this file, its beginning not
            rt.where = (path, ev["dc_turn_start"]) if ev["dc_turn_start"] is not None else None  # found: no 📋 for it
            if rt.turn is not None:
                rt.turn.src = rt.where or (None, 0)

    @staticmethod
    def another_turn(rt, ev, what):
        """Is this event of another turn than the one still open here? Claude Code sends no event when a turn is cut
        short (Esc), so the turn stayed open, and the next one's reply bore its tools and its time (#issues 27). Turns
        are told apart as the connector tells them: by the prompt_id of their events. When either has none (a
        version that sends none): what the owner says next, after the screen showed no work for a while, is
        another turn."""
        turn, pid = rt.turn, ev.get("prompt_id")
        if turn is None:
            return False
        if turn.pid and pid:
            return str(turn.pid) != str(pid)
        return turn.quiet and what == cc_hooks.INPUT

    def begin_turn(self, ch, st, rt, prompt="", task=False, pid=None):
        """`task`: what begins the turn is Claude Code's own notice that a background task is done. `pid`: the
        prompt_id of the event (the turn's own id)."""
        if rt.turn is None:
            first = prompt.strip().splitlines()[0][:80] if prompt.strip() else "…"
            rt.turn = Turn("（背景工作完成）" if task else first, rt.where or (None, 0))
        rt.turn.pid = rt.turn.pid or pid
        rt.turn.quiet = False
        st["busy"] = True
        self.watch(ch, st, rt)
        return rt.turn

    async def end_turn(self, st, rt):
        st["busy"] = False
        st.pop("doing", None)  # (C-88) what a busy channel is doing: not busy, nothing
        turn, rt.turn = rt.turn, None
        if turn:
            turn.closed = True  # (a "working…" message still being sent is deleted when it comes)
        if turn and turn.msg is not None:
            try:
                await deliver.remove(None, turn.msg)
            except Exception:
                pass

    async def interim(self, ch, rt, final="", texts=None, lane=None):
        """Post, as quotes, what Claude said since the last look (before a tool call), read where the transcript
        is (`texts`; None: nothing was read). `final` is the reply the Stop event is about to post; the previous
        turn's reply can also still be trickling into the file. `lane`: posted from the channel's lane (a tool
        call's), each request waited for lanes.LANE_SECS at most; else at once (what ends a turn, its lane done)."""
        if not config.INTERIM or texts is None:
            return
        texts = [t for t in texts if t not in (final, rt.last_final)]
        if not texts:
            return
        self.said(ch, texts[-1], doing=True)
        if rt.turn:  # the "working…" message goes back to the bottom next time (one still being sent: deleted)
            rt.turn.gen += 1
        if rt.turn and rt.turn.msg is not None:
            msg, rt.turn.msg = rt.turn.msg, None
            try:
                await (lane.call("刪掉處理中", lambda: deliver.remove(None, msg)) if lane is not None else deliver.remove(None, msg))
            except Exception:
                pass
        for t in texts:
            for part in split_message(masking.masked(t), 1890):
                try:
                    if lane is not None:
                        await lane.call("送引言", lambda part=part: deliver.deliver(ch, ">>> " + part, silent=True, intent="status",
                                                                                framed=("interim",)), new=True)
                    else:
                        await deliver.deliver(ch, ">>> " + part, silent=True, intent="status", framed=("interim",))
                except lanes.TimedOut:
                    pass
                except Exception:
                    traceback.print_exc()

    async def lane_say(self, lane, ch, text):
        """say(), silent, from the channel's lane."""
        for part in split_message(text):
            try:
                await lane.call("送訊息", lambda part=part: deliver.deliver(ch, part, silent=True, intent="status"), new=True)
            except lanes.TimedOut:
                pass
            except Exception:
                traceback.print_exc()

    def lane(self, ch, rt=None):
        """The channel's lane (lanes.py), made the first time."""
        rt = rt or self.rt(ch)
        if rt.lane is None:
            rt.lane = lanes.Lane(ch.id)
        return rt.lane

    def lane_waits(self, ev):
        """Does this event stay in the spool because its channel's lane still posts (lanes.py)? What only adds to
        the lane goes on (a tool call); so does the receipt of a message the bot typed (submit() waits for it only
        RECEIPT_SECS). Anything else, a turn's end above all, waits: it comes after the lane's quotes, and the
        "working…" updates not sent yet are dropped."""
        lane = getattr((getattr(self, "rts", None) or {}).get(str(ev.get("channel"))), "lane", None)
        if lane is None or ev.get("dc_kind") in LANE_KINDS or not lane.busy() or self.receipt_meanwhile(ev):
            return False
        lane.drop_progress()
        return True

    async def lanes_done(self):
        """Wait until every lane has posted what it holds (tests, which then look at what was posted)."""
        for rt in list(getattr(self, "rts", {}).values()):
            task = getattr(getattr(rt, "lane", None), "task", None)
            if task is not None:
                await asyncio.gather(task, return_exceptions=True)

    async def from_stream(self, ev):
        """An event from another machine's stream: handled now, or, when lane_waits() says it waits, kept for its
        channel only (C-174), as the pump keeps it in the spool on this machine: every later event of that channel
        stays behind it (a receipt of a message the bot typed excepted), and that machine's other channels go on.
        Until C-174 the whole stream waited (one task for the machine)."""
        if not hasattr(self, "lane_held"):  # (a bridge made without __init__: tests)
            self.lane_held, self.lane_tasks = {}, set()
        key, held = str(ev.get("channel")), self.lane_held
        queue = held.get(key)
        if (queue is not None and not self.receipt_meanwhile(ev)) or self.lane_waits(ev):
            if queue is None:
                queue = held[key] = collections.deque()
                task = asyncio.ensure_future(self.hand_on(key, queue))
                self.lane_tasks.add(task)
                task.add_done_callback(self.lane_tasks.discard)
            queue.append(ev)
            return
        await self.handle(ev)

    def lane_kept(self):
        """What from_stream() still keeps: [(channel id, how many, a turn's end among them)], for #audit when the bot
        restarts or hands over before it got to them (C-174)."""
        ends = (cc_hooks.TURN_END, cc_hooks.TURN_FAILED)
        return [(key, len(queue), any(ev.get("dc_kind") in ends for ev in queue))
                for key, queue in sorted((getattr(self, "lane_held", None) or {}).items()) if queue]

    async def say_lane_kept(self, handover=False):
        """One #audit line for what is still kept (lane_kept()), if anything (#code-review on d46a429, manager): a
        restart drops it; at a handover the bot that takes over posts the replies again (replay_all). Never raises,
        and never waits on Discord more than LANE_KEPT_SAY_SECS."""
        rows = self.lane_kept()
        if not rows:
            return
        def name(key):
            try:
                return getattr(self.host.get_channel(int(key)), "name", None) or key
            except Exception:
                return key
        listed = "、".join("#%s %d 則%s" % (name(key), n, "（含回覆）" if end else "") for key, n, end in rows)
        try:
            text = LANE_KEPT_HANDOVER % listed if handover else \
                LANE_KEPT_RESTART % listed + (LANE_KEPT_REPLY_GONE if any(end for _, _, end in rows) else "")
            await asyncio.wait_for(self.host.audit(text), LANE_KEPT_SAY_SECS)
        except Exception:
            traceback.print_exc()

    def still_waits(self, ev):
        """lane_waits() for hand_on(); should it raise, the event goes on (its traceback printed) rather than every
        event kept behind it being lost with the task (#code-review-2 on d46a429)."""
        try:
            return self.lane_waits(ev)
        except Exception:
            traceback.print_exc()
            return False

    async def hand_on(self, key, queue):
        """from_stream()'s kept events of one channel, handled in the order they came, each once lane_waits() lets
        it go (at most the lane's requests left, LANE_SECS each, or LANE_HOLD)."""
        try:
            while queue:
                while self.still_waits(queue[0]):
                    await asyncio.sleep(0.2)
                ev = queue.popleft()
                try:
                    await self.handle(ev)
                except Exception:
                    traceback.print_exc()
        finally:
            if self.lane_held.get(key) is queue:
                self.lane_held.pop(key)

    def said(self, ch, text, doing=False):
        """Claude said `text` in the channel (C-88): one line of it (awaiting.line(): masked, first sentence, at most
        80 characters) is what a menu or a plan there waits on (awaiting.said) and, for what it says along the way
        (`doing`), what the channel is doing (its state's "doing", written with the rest; end_turn() drops it). These
        are for readers of the bot's state files, a plugin among them, which may take them off this machine."""
        one = awaiting.line(text)
        if one is None:
            return
        awaiting.said[str(ch.id)] = one
        st = (getattr(self.host, "channels_state", None) or {}).get(str(ch.id))
        if doing and isinstance(st, dict):
            st["doing"] = one

    async def progress(self, ch, turn, lane):
        """Keep one "working…" message up to date once a turn has run for a while (from the channel's lane: the
        lines are the turn's latest when it runs)."""
        now = time.time()
        if turn.closed or turn.sending is not None or now - turn.t0 < 15 or now - turn.last < 5:
            return
        turn.last = now
        body = "處理中…（%d 秒）\n```\n%s\n```" % (now - turn.t0, "\n".join(masking.mask(line, cut=True)[0] for line in turn.lines))  # (cut to 80 already)
        if turn.msg is not None:
            msg = turn.msg
            try:
                await lane.call("更新處理中", lambda: deliver.deliver(ch, content=body, intent="status", edit=msg, keep=False))  # (not kept)
            except discord.NotFound:  # (taken away meanwhile, C-205's --delete or by hand: posted anew next round)
                if turn.msg is msg:
                    turn.msg = None
            except Exception:
                pass
            return
        gen = turn.gen
        turn.sending = True

        def came(msg):  # it came after all, after the lane stopped waiting (or failed: None)
            turn.sending = None
            self.working_came(turn, gen, msg)
        try:
            msg = await lane.call("送處理中", lambda: deliver.deliver(ch, body, silent=True, intent="status", keep=False), new=True, on_late=came)
        except lanes.TimedOut:
            return  # (turn.sending stays until it comes: no second "working…" message meanwhile)
        except Exception:
            turn.sending = None
            return
        turn.sending = None
        self.working_came(turn, gen, msg)

    @staticmethod
    def working_came(turn, gen, msg):
        """A "working…" message was made: the turn's, or, when the turn moved on while it was on its way (ended, the
        message went to the bottom, another one there), deleted at once: none is left behind."""
        if msg is None:
            return
        if turn.closed or turn.gen != gen or turn.msg is not None:
            async def drop():
                try:
                    await deliver.remove(None, msg)
                except Exception:
                    pass
            asyncio.ensure_future(drop())
        else:
            turn.msg = msg

    def watch(self, ch, st, rt):
        if rt.typing is None or rt.typing.done():
            rt.typing = asyncio.ensure_future(self.watch_busy(ch, st, rt))

    async def watch_busy(self, ch, st, rt):
        """While a turn runs: show "typing…", and notice if the turn ended without a Stop event
        (an interrupt from an attached terminal, or Claude Code exiting)."""
        idle, silent, told = 0, None, False  # `silent`: since when its machine has not answered; `told`: the channel knows
        while st.get("busy"):
            try:
                await ch.typing()
            except Exception:
                pass
            await asyncio.sleep(WATCH_SILENT_SECS if told else WATCH_SECS)
            if not st.get("busy"):
                return
            how = await tmuxio.standing(target(st))
            if how == "unknown":
                # Its machine does not answer: whether Claude still runs is not known. Nothing is ended and the
                # channel stays busy. Said once when it has lasted (not for a line that dropped for a moment), asked
                # less often from then on, and one line in the log when it begins and one when it is over, not one
                # for every asking (manager on 25caa93: it went on every 8 seconds without end or word).
                host = (target(st) or {}).get("host") if isinstance(target(st), dict) else None
                if silent is None:
                    silent = time.time()
                    print("bridge: %s does not answer; the turn running in channel %s is left as it is" % (host, ch.id), flush=True)
                if not told and time.time() - silent >= WATCH_SAY_SECS:
                    told = True
                    await self.say(ch, messages.far("turn", said=remote.unreachable(host)))
                continue
            if silent is not None:
                print("bridge: %s answers again (channel %s, after %d s)" % (
                    (target(st) or {}).get("host") if isinstance(target(st), dict) else None, ch.id, time.time() - silent), flush=True)
                silent, told = None, False
            if how == "gone":
                await self.end_turn(st, rt)
                st.pop("bg", None)  # what ran under it ended with it
                self.host.save()
                return await self.say(ch, "⚠️ Claude 的行程結束了，這一輪沒有完成。" + (
                    "" if st.get("pane") and not st.get("home") else "下一則訊息會自動接回同一個對話。"))
            idle = 0 if screen.busy_shown(await tmuxio.raw_screen(target(st))) else idle + 1
            if idle >= 3:
                await self.rest(st, rt)
                self.host.save()

    async def rest(self, st, rt):
        """The screen has shown no work for a while: the channel is no longer shown as busy, and the "working…"
        message goes. The turn itself is kept: it may be waiting in a dialog (a permission asked: minutes can pass)
        and go on, and its reply then says what it used and how long it took (#issues 27: it was forgotten here, and
        the reply's footer had neither). If it was cut short instead, the next turn's first event says so
        (another_turn())."""
        st["busy"] = False
        turn = rt.turn
        if turn is None:
            return
        turn.quiet = True
        turn.gen += 1  # (a "working…" message on its way is deleted when it comes)
        if turn.msg is not None:
            try:
                await deliver.remove(None, turn.msg)
            except Exception:
                pass
            turn.msg = None

    # ------------------------------------------------------------------ commands
    async def stop(self, ch):
        """Interrupt the running turn (Escape). Claude Code fires no Stop hook for an interrupt."""
        st, rt = self.host.chan(ch), self.rt(ch)
        name = target(st)
        if not name or not await tmuxio.alive(name):
            return False
        if not st.get("busy") and not screen.busy_shown(await tmuxio.raw_screen(name)):
            return False
        await tmuxio.escape(name, lambda view: st.get("busy") or screen.busy_shown(view))
        await self.asks.cancel(ch)
        await asyncio.sleep(1)  # let the screen catch up, so a second !stop does not see the old one
        await self.end_turn(st, rt)
        self.host.save()
        return True

    async def close(self, ch, kill=True):
        """End the channel's session (for !reset, !cwd, !mode). Background work in it ends too.
        An attached pane is the owner's own terminal: it is let go of, never killed. `kill=False` (for
        !detach): only stop following it, whoever opened it."""
        st, rt = self.host.chan(ch), self.rt(ch)
        if kill and st.get("home") and st.get("pane"):  # a window the bot opened itself: closed, reopened on demand
            gone = st["pane"]
            where_mod.put(st, pane=None)
            if st.get("session_id"):  # (its end comes after: from_elsewhere(). Without an id it could not be told: not kept)
                rt.closed = (rt.closed + [dict(gone, at=time.time(), session=st["session_id"])])[-CLOSED_KEPT:]
            await tmuxio.kill_pane(gone)
        elif kill and st.get("tmux") and not st.get("pane"):
            await tmuxio.kill(st["tmux"])
        await self.asks.cancel(ch, "The conversation was closed before the user answered." if kill else UNFOLLOWED)
        await self.menus.resolve(ch)  # a dialog's buttons still shown would press keys in a session let go of
        await self.end_turn(st, rt)
        for p in rt.pending:
            p.got.set()
        rt.pending = []
        st.pop("bg", None)

    def unanswered(self, ch):
        """Was the channel's pane just asked and its machine did not answer? (After stop(), live_target(), screen()
        or key() said no: "nothing runs here" is then not known, and not what the owner is told.) What was tried,
        as a sentence, or None. Nothing is asked here."""
        t = target(self.host.chan(ch))
        return remote.unreachable(t.get("host")) if tmuxio.unanswered(t) else None

    async def live_target(self, ch):
        """Where this channel's Claude runs (session name or pane address), if it is running."""
        st = self.host.chan(ch)
        return target(st) if target(st) and await tmuxio.alive(target(st)) else None

    async def screen(self, ch, lines=30):
        st = self.host.chan(ch)
        if not target(st) or not await tmuxio.alive(target(st)):
            return None
        return await tmuxio.screen(target(st), lines)

    async def key(self, ch, names):
        st = self.host.chan(ch)
        if not names or not target(st) or not await tmuxio.alive(target(st)):
            return None
        await tmuxio.keys(target(st), *names)  # the owner's own keys: not held back, even two Escapes
        await asyncio.sleep(1)
        shown = await tmuxio.screen(target(st), 20)
        if any(tmuxio.key_name(k) == "Escape" for k in names):
            shown += "\n（提醒：輸入框是空的時候，連按兩次 Esc（一秒內）會打開「還原對話和程式碼」的面板）"
        return shown

    async def reap(self):
        """Close sessions idle for IDLE_HOURS with nothing running in them; they resume on the next message."""
        for cid, st in list(self.host.channels_state.items()):
            name = st.get("tmux")
            if not name or st.get("pane") or st.get("busy") or st.get("bg") or quota.holding(st):
                continue  # (a usage-limit wait: closing Claude Code ends it for good, C-99a)
            if time.time() - st.get("last_active", 0) > config.IDLE_HOURS * 3600 and await tmuxio.alive(name):
                await tmuxio.kill(name)
                await self.host.audit("關閉閒置的 tmux 對話 `%s`（下一則訊息會自動接回）" % name)
        await self.reap_ownerless()

    async def reap_ownerless(self):
        """The sessions the bot started for a channel that is gone: closed, or listed for the owner.

        A channel deleted leaves its Claude running (on_guild_channel_delete: it may hold work in progress), and
        nothing looked at it again: reap() goes by the channels' state, which no longer names it (2026-10-11:
        three of them idle for 107 to 129 hours, #issues 10).

        Stopping something goes only by facts that can be read and are plain; where one cannot be, the session
        is not closed but written down (state/ownerless-sessions.json, shown by doctor) for the owner to decide
        (manager 2026-10-06, again 2026-10-11 after the first version went by the session's name and by an idle
        screen: it would have closed a terminal channel's session, and one with a training run in the
        background). A session is closed when all of this holds:
          - every pane of it runs Claude Code, started by this bot for a channel: DCBOT_ROOT is this bot's and
            DCBOT_CHANNEL a channel's id, read from the process' own environment (not from its name). A session
            with such a pane and others beside it (a shell someone split off) is kept and listed;
          - that channel has no state and Discord has no such channel; and no channel's state names the session,
            in any of the ways a state can (sessions_named);
          - nothing was drawn in it for IDLE_HOURS (tmux), nobody is attached, no turn shows as running;
          - the Claude Code process has no child process: a background command is a shell under it, and nothing
            else hangs there while it sits idle (seen on 2.1.296). When that cannot be read, it is not known.
        With no channel's state at all (the file could not be read: the bot starts with none), or while Discord's
        own list of channels is not there (none of the state's channels can be had from it), nothing is closed.
        #audit is told first; the conversation itself stays on disk (claude --resume); a question it still
        waits on, asked for that very channel, is closed with it."""
        states = self.host.channels_state
        kept = []
        # Discord's list of channels must be there too: until it is (the bot just started, the connection is down)
        # every channel looks gone. It is, when at least one channel the state knows can be had from it.
        listed = any(str(cid).isdigit() and self.host.get_channel(int(cid)) is not None for cid in states)
        if states and listed:
            named = sessions_named(states)
            for s in await tmuxio.own_sessions():
                why, cid = await self.ownerless(s, named)
                if why is None or why is WAIT:
                    continue  # a channel's, or nothing of the bot's; or its time has not come
                idle = (time.time() - s["at"]) / 3600
                if why:
                    kept.append({"name": s["name"], "channel": cid, "idle_hours": round(idle, 1), "why": why})
                    continue
                await self.host.audit("關閉沒有頻道的 tmux 對話 `%s`（它的頻道 %s 已經不在，閒置 %d 小時、沒有背景工作；對話內容還在，"
                                      "要接回：在它的資料夾 `claude --resume`）" % (s["name"], cid, idle))
                for ask_id, asked in ask.waiting(cid):
                    await self.asks.hand(ask_id, asked, {"reason": "The conversation was closed: its Discord channel no longer exists."})
                await tmuxio.kill(s["name"])
            note_ownerless(kept)  # only what was looked at: a round that could not look leaves the last list

    async def ownerless(self, s, named):
        """For one session of the bot's server: (why, the channel it was started for). `why` is None when it is a
        channel's or not the bot's at all; "" when it is the bot's, its channel is gone and every fact for closing
        it holds; WAIT when it is only too early; else why it is kept, for the owner to read. The channel's id is
        read once, here: the process may be gone a moment later."""
        if s["name"] in named or not s["panes"]:
            return None, None
        mine = []  # the panes running Claude Code that this bot started for a channel
        for pid, command in s["panes"]:
            fact = tmuxio.started_for(pid) if screen.is_claude(command) else None
            if fact and os.path.realpath(fact[0]) == os.path.realpath(config.ROOT) and fact[1].isdigit():
                mine.append((pid, fact[1]))
        if not mine:
            return None, None  # not started by this bot for a channel (someone's own session here): not the bot's to judge
        cids = sorted(set(cid for _, cid in mine))
        if any(cid in self.host.channels_state or self.host.get_channel(int(cid)) is not None for cid in cids):
            return None, None  # a channel of it is there (its state names another session now, or none yet)
        cid = "、".join(cids)
        if s["attached"] or time.time() - s["at"] <= config.IDLE_HOURS * 3600:
            return WAIT, cid  # someone is at it, or it was used not long ago: nothing to decide yet
        if len(cids) > 1:
            return "裡面的 Claude 是為不同的頻道開的（%s）" % cid, cid
        others = len(s["panes"]) - len(mine)
        if others:  # a pane someone added beside it (a shell, an editor): theirs, so the session is not closed
            return "它還有 %d 個別的窗格（不是機器人為這個頻道開的 Claude）" % others, cid
        if screen.busy_shown(await tmuxio.raw_screen(s["name"])):
            return "有一輪在跑", cid
        for pid, _ in mine:
            under = tmuxio.children(pid)
            if under is None:
                return "讀不到它底下有沒有行程在跑（背景工作）", cid
            if under:
                return "它底下還有行程在跑（背景工作？pid %s）" % "、".join(str(p) for p in under[:5]), cid
        return "", cid

    async def shutdown(self):
        """The bot is about to restart: drop the "working…" messages; the sessions themselves keep running."""
        await self.say_lane_kept()  # (main.busy_with_work() waited for them, at most RESTART_WAIT: what is left is said)
        for task in list(getattr(self, "lane_tasks", ())):  # events kept for a lane go as they went when the whole
            task.cancel()                                     # stream waited on them (its task ended with the bot)
        for proc in self.streams.values():  # or an orphaned line would go on taking that machine's events
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        dropped = {}
        for cid, rt in self.rts.items():
            if rt.lane is not None:
                n = rt.lane.stop()  # (only progress: lanes.py)
                if n:
                    dropped[cid] = n
            if rt.turn:
                rt.turn.closed = True
        if dropped:
            print("[lane] 重啟：%d 則還沒送的進度丟掉了（%s）" % (sum(dropped.values()), "、".join(
                "#%s %d" % kv for kv in sorted(dropped.items()))), flush=True)
        for rt in self.rts.values():
            if rt.typing is not None:
                rt.typing.cancel()
            if rt.turn and rt.turn.msg is not None:
                try:
                    await deliver.remove(None, rt.turn.msg)
                except Exception:
                    pass
