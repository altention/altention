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
import json
import os
import time
import traceback

import discord

from altention.connectors.claude_code import btw, screen, transcript
from altention.connectors.claude_code import commands as cc_commands
from altention.connectors.claude_code import hooks as cc_hooks
from altention.connectors.claude_code import launch as cc_launch
from altention.connectors.claude_code import typist as cc_typing

from . import posts as posts_mod
from . import agents, ask, attachments, cc_btw, config, events, history, masking, menus, messages, peers, process, remote, routes, sessions, startup_ask, tables, tmuxio, unsent as unsent_mod
from .claude_bridge import deny
from .posts import Origin
from .util import same_socket, split_message, target

SPOOL = os.path.join(config.STATE, "events")
SETTINGS_FILE = os.path.join(config.STATE, "claude-settings.json")
HOOK = os.path.join(config.ROOT, "tools", "dchook")
HOOK_EVENTS = cc_hooks.EVENTS
START_SECS = 90      # how long a session may take to come up
SUBMIT_DELAY = 0.3   # Enter pressed right after typing can get lost
BTW_SECS = 90        # how long a /btw side question may take
RECEIPT_SECS = 5     # how long to wait for UserPromptSubmit before pressing Enter again
STREAM_MAX_DELAY = 60  # longest wait between a stream's reconnects (one `true` per try; a command getting through
                       # wakes it sooner)
STREAM_MIN_GAP = 5     # shortest time between two reconnects, woken or not (code-review 2026-10-07)
LATE_SECS = 600        # how long a message whose receipt did not come is still matched by a late one
BUSY_MARK = screen.BUSY_MARK  # on screen while Claude works: asked through screen.busy_shown(), never looked for in a whole screen
LONG_TURN = 600                  # a turn this long ends with a mention, so the phone rings even in a muted channel
RETRY = "繼續。（上一輪因為 API 錯誤中斷了，請從中斷的地方接著做。）"
FAILURES = events.FAILURES  # the words for why a request failed (events.failure())
KEPT_SECS, KEPT_MAX = 600, 500  # events of a channel Discord does not hand out just now: how long, how many (keep())
RESEND_FIRST, RESEND_MAX = 30, 600  # a refusal a machine did not take: sent again after this long, doubled each time, up to
WATCH_SECS = 8           # between the looks of watch_busy() at a running turn
WATCH_SAY_SECS = 120     # a turn's machine silent for this long: the channel is told, once
WATCH_SILENT_SECS = 60   # between the looks from then on
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
    MODEL = "model"                # the model set is nothing a command line can hold: {"value", "whose"}

    def __init__(self, why, text, **data):
        BridgeError.__init__(self, text)
        self.why, self.data = why, data


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


CLOSED_KEPT = 5
CLOSED_SECS = 60  # how long a window the bot closed is remembered as closed, when nothing of it or of its number comes


def opened(st, rt):
    """A window is open for the channel (st["pane"]): a window the bot closed lately in the same pane, with the
    conversation the channel has now, is no longer told from it (from_elsewhere()), and is forgotten. Not left to
    the new window's SessionStart alone, which may never come."""
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
            return True
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
        self.outcome = None   # what the checks before the last message typed came to (Typing; may_type())
        self.closed = []      # windows the bot closed for the channel lately (close()), until one of their number starts anew
        self.unanswered = False  # the last opening gave up waiting on the owner's answer to a startup dialog (wait_ready)


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
    settings), else `last`, as it always was."""
    if st.get(key):
        return st[key], "channel"
    value = installation(setting)
    return (value, "installation") if value else (last, None)


def fence(text, limit=1500):
    return "```\n%s\n```" % (text.replace("```", "'''")[-limit:] or "（空白）")


def events_cut(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n - 1] + "…"


class TmuxBridge(object):
    """`host` is the Discord bot: needs chan(channel), save(), audit(text), note(channel, st), get_channel(id)."""

    def __init__(self, host):
        self.host, self.rts = host, {}
        self.asks = ask.Asker(host.audit)
        self.asks.on_owed = self.start_resends  # a result for a hook on another machine: sent by that machine's task
        if getattr(host, "posts", None) is not None:
            self.asks.on_done = host.posts.answered
        self.agents = agents.Agents(host)
        self.menus = menus.Menus(self)
        self.unsent = unsent_mod.Unsent(self)  # C-74: what could not be handed over for now, sent again
        self.startup = startup_ask.StartupAsk(self)
        self.streams = {}  # machine -> the SSH process bringing its events
        self.handling = False  # an event is being posted: a restart waits for it (main.Bot.busy_with_work)
        self.held = set()  # channels whose next event waits in the spool for its row (pump_once)
        self.ticked = 0.0  # when subagents, altention dc post and the chores last had their turn in pump_once
        self.streaming, self.streams_checked = set(), time.time()  # pump() starts the streams; pump_once() rechecks each minute
        self.stream_down = set()  # machines whose stream is not bringing events now (not yet connected, or reconnecting)
        self.stream_wake = {}     # machine -> the Event its stream sleeps on before reconnecting
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
                sent.append(await ch.send(part, silent=True) if silent else await ch.send(part))
            except Exception:
                traceback.print_exc()
        return sent

    # ------------------------------------------------------------------ session
    def argv(self, ch, st, resume, how=None):
        """The command line that opens the channel's conversation (the connector's). On another machine: its own
        claude, and its global hook reports; here: the bot's settings file, whose hooks do. `how`: the model and
        the permission mode (with()); looked up here when not given."""
        there = bool((st.get("home") or {}).get("host"))
        model, permission = how or self.with_(st)
        return cc_launch.command("claude" if there else config.CLAUDE, self.host.note(ch, st), model, permission,
                                 settings=None if there else SETTINGS_FILE, resume=resume)

    def with_(self, st):
        """(model, permission mode) a conversation of this channel is opened with; NotOpened when one of them is
        no value there can be, or when the installation's settings cannot be read and the channel does not say the
        permission mode itself. Nothing is started then, and the channel is told which value, whose it is, and which
        there are. (Never the default instead: that lets everything through, and nobody asked for it. A model that
        is a word but does not exist is Claude Code's own to refuse: its words come back with the screen.)
        Reads the installation's settings file: in the event loop through chosen_for()."""
        try:
            model, whose_model = chosen(st, "model", "dcbot.default_model", config.MODEL)
        except Exception:  # the settings cannot be read: the model as it always was (written to the log)
            traceback.print_exc()
            model, whose_model = config.MODEL, None
        try:
            value, whose = chosen(st, "permission", "dcbot.default_permission", cc_launch.BYPASS)
        except Exception as e:  # what the installation says of permissions is not known, and the last resort lets
            traceback.print_exc()  # everything through. Not opened, and said.
            raise NotOpened(NotOpened.SETTINGS, "讀不到這個安裝的設定（%s），不知道要用哪個權限模式，沒有開對話。"
                            "那是實例資料夾裡的 `plugins.json`；修好之後再傳一則訊息就會開。" % e, error=str(e))
        valid = cc_launch.permissions()
        if not isinstance(value, str) or value not in valid:
            where = "這個頻道設定的權限模式" if whose == "channel" else "這個安裝設定的預設權限模式（plugins.json 的 dcbot.default_permission）"
            raise NotOpened(NotOpened.PERMISSION, "%s「%s」不是可以用的值，沒有開對話。可以用的：%s。"
                            % (where, value, "、".join(valid)), value=value, valid=list(valid), whose=whose)
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

    async def ensure(self, ch, st, rt):
        """Make sure the channel's session is running; resume the saved conversation if there is one."""
        if st.get("home"):  # a window the bot opens in the owner's own tmux (chosen on the setup card)
            return await self.ensure_home(ch, st, rt)
        if st.get("pane"):  # the owner's own terminal: nothing to start, and never anything to restart
            attached = tmuxio.pane(st["pane"])
            how = await attached.standing()
            if how == "alive":
                return
            if how == "unknown":
                raise unreachable(st["pane"])
            raise NotOpened(NotOpened.PANE_GONE, "這個頻道接的 tmux 窗格已經沒有在跑 Claude Code 了（%s）。"
                            "訊息沒有送出。用 `!detach` 卸下這個頻道，或在那個窗格重新開 Claude 後再發一次。"
                            % attached.label, where=attached.label)
        name = st.setdefault("tmux", tmuxio.session_name(getattr(ch, "name", "dm"), ch.id))
        own = tmuxio.pane(name)
        if await own.alive():
            return
        seen = {}

        async def start(resume, how):
            try:
                started = await tmuxio.start(name, st["cwd"], self.argv(ch, st, resume, how), self.env(ch))
            except OSError as e:
                raise NotOpened(NotOpened.NO_TMUX, "無法執行 tmux：%s" % e, error=str(e))
            return own if started else None

        async def kept(pane, fresh):
            if fresh:
                st.pop("bg", None)

        async def failed(pane):
            seen["view"] = await own.screen(15) if await own.alive() else ""
            await own.close()

        if await self.open(ch, st, rt, start, kept, failed):
            return
        view = seen.get("view")
        raise NotOpened(NotOpened.NOT_STARTED, "Claude 沒有啟動成功。%s" % (("最後的畫面：\n" + fence(view)) if view else ""),
                        screen=view or "")

    def env(self, ch):
        env = {"DCBOT_ROOT": config.ROOT, "DCBOT_CHANNEL": str(ch.id)}  # dchook and deploy.sh report here
        if not (os.environ.get("LC_ALL") or os.environ.get("LANG")):
            env["LANG"] = "C.UTF-8"  # started from cron: no locale
        return env

    async def open(self, ch, st, rt, start, kept, failed):
        """Open the channel's conversation: the saved one first, a new one when that does not come up (and the
        channel is told so); True when one is up. What the kinds of channel differ in is in the three it is given:
        `start(resume, how)` begins it, wherever that kind keeps it (how: the model and the permission mode), and gives its pane (None: could not be begun);
        `kept(pane, fresh)` when it came up (fresh: a new conversation in place of the saved one); `failed(pane)`
        clears away one that did not (pane: what start gave, None too)."""
        how = await self.chosen_for(st)
        saved = st.get("session_id")
        for resume in ([saved, None] if saved else [None]):
            rt.ready.clear()
            rt.unanswered = False
            st["busy"] = False
            pane = None
            try:
                pane = await start(resume, how)
                if pane is not None and await self.wait_ready(pane, rt, ask=lambda choice, p=pane: self.startup.post(ch, p, choice)):
                    await kept(pane, bool(saved and not resume))
                    opened(st, rt)
                    if saved and not resume:
                        await self.say(ch, messages.note("claude_code.new_conversation", session=saved))
                    return True
            finally:
                rt.opening = None
            await failed(pane)
            if rt.unanswered:  # the owner did not answer its startup dialog: no new conversation in place of the saved
                break          # one, which would ask the same again (#code-review of 5b4d448); the next message opens it
        return False

    async def ensure_home(self, ch, st, rt):
        """The channel's window in the owner's tmux: there and running Claude, or opened again (in the same
        tmux session, resuming the conversation). Only windows the bot opened are ever reopened or closed."""
        how = await tmuxio.standing(st["pane"]) if st.get("pane") else "gone"
        if how == "alive":
            return
        if how == "unknown":  # its machine did not answer: the window may well be there and running. Another one
            raise unreachable(st["pane"])  # opened now (the line back a moment later) would be a second Claude on the
            # same conversation: not opened, and said
        home, why = st["home"], []
        remote_host = home.get("host")

        async def start(resume, how):
            env = {"DCBOT_CHANNEL": str(ch.id)} if remote_host else self.env(ch)  # there: dcpost's sender only
            rt.opening = True  # what the new window reports is the channel's from before the channel remembers it
            pane = await tmuxio.open_window(home["socket"], home["session"], home.get("window", ""), st["cwd"],
                                            self.argv(ch, st, resume, how), env, host=remote_host, why=why)
            rt.opening = pane
            if pane and remote_host:  # its hooks report through that machine's route table, like an attached pane
                await routes.bind_any(pane, str(ch.id))
            return tmuxio.pane(pane) if pane else None

        async def kept(pane, fresh):
            st["pane"] = pane.t
            self.host.save()

        async def failed(pane):
            if pane is not None:
                await pane.close()

        if await self.open(ch, st, rt, start, kept, failed):
            return
        raise NotOpened(NotOpened.NO_WINDOW, "在 tmux `%s` 裡開不起 Claude。%s" % (home["session"], ("\n-# %s" % why[-1]) if why else ""),
                        session=home["session"], said=why[-1] if why else "")

    async def start_now(self, ch):
        """Open the channel's session right away (after the setup card), so it is there in tmux already."""
        st, rt = self.host.chan(ch), self.rt(ch)
        st["last_active"] = time.time()  # or the idle reaper takes a session nobody has talked to yet
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
            rt.unanswered = token is not None
            return False
        finally:
            if token not in (None, "-"):
                await self.startup.done(token, "" if ok else "等太久了，這次沒有開起來；再發一則訊息會重新開，問題會再出現一次。")

    # ------------------------------------------------------------------ input
    async def submit(self, message, text, out=None, now=False, confirmed=False):
        """Type `text` into the channel's session. `out` (for Discord's "/" menu) replaces posting to the
        channel for whatever answers the submission itself: the screen after a slash command, or a failure.
        `now`: if Claude is busy, the message waits in its queue until the tool call in progress ends; this
        presses send-now (Ctrl+X Ctrl+S), which moves that call to the background (or stops one that cannot
        go there) so Claude reads the message in the same turn."""
        ch = message.channel
        st, rt = self.host.chan(ch), self.rt(ch)
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
                return r
            name = target(st)
            was_busy = bool(st.get("busy"))  # a turn runs: this message will wait in the queue (send_now)
            r = await self.checked(say, st, name, rt, keep)
            if not r:
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
                return await self.told(say, r)
            if note:
                st.pop("note_next", None)
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
                            ch, name, lambda body, view=None: ch.send(body, view=view), text.split()[0]):
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
            await message.add_reaction("⚡")
        except Exception:
            pass
        return True

    async def btw_open(self, ch, question, out=None):
        """/btw: a side question that does not touch the turn in progress. It gets a thread of its own; what
        is typed in that thread later is a follow-up (main.py sends it to btw_ask)."""
        if not question:
            return await (out or (lambda t: self.say(ch, t)))("用法：`/btw <問題>`")
        try:
            starter = await ch.send("💬 **/btw**　%s" % question[:1800])
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
                return await self.told(lambda body: self.say(where, body), not_opened(rt, e))
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
        return what

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
        await message.channel.send("⚠️ `%s` %s。確定要送進這個對話嗎？" % (cmd, CONFIRM[cmd]), view=view)

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

    # ------------------------------------------------------------------ output
    async def pump(self):
        """Forever: turn hook events into Discord messages, and close sessions nobody has used for a while."""
        write_settings()
        await self.read_words()  # the installation's language and own wordings, before the first sentence is said
        await self.remember_pids()
        self.unsent.kick_all()  # posts held up before the bot went down: looked at again (C-74; a task each)
        self.start_streams()
        for cid, st in list(self.host.channels_state.items()):
            if not (st.get("busy") or st.get("bg")):
                continue
            ch = self.host.get_channel(int(cid))
            if remote.problem and ((st.get("pane") or {}).get("host") or (st.get("home") or {}).get("host")):
                continue  # the machine list is unreadable, so that machine cannot be reached by name: do not call it gone
            how = await tmuxio.standing(target(st)) if ch is not None and target(st) else "gone"
            if how != "gone":  # ("unknown": its machine does not answer just now. Not said to be gone, nothing let go
                if st.get("busy"):  # of: watch() finds out once the machine answers again)
                    self.watch(ch, st, self.rt(ch))  # a turn kept running while the bot was down
                continue
            lost = [w for w, on in (("進行中的工作", st.get("busy")), ("背景工作", st.get("bg"))) if on]
            st["busy"] = False
            st.pop("bg", None)
            if ch is not None:  # the machine rebooted, or someone killed the tmux server
                await self.say(ch, "⚠️ 這個頻道的 tmux 對話不見了（機器重開過？），%s已中斷。%s" % ("和".join(lost), (
                    "在 tmux 裡用 `claude --resume` 重開後，再用 `!attach` 接回來。" if st.get("pane") and not st.get("home")
                    else "對話內容還在，下一則訊息會自動接回。")))
        self.host.save()
        self.last_reap = time.time()
        while True:
            try:  # nothing may end this loop: every channel's output and altention dc post go through it
                await self.pump_once()
            except Exception:
                traceback.print_exc()
            await asyncio.sleep(0.1 if self.held else 0.5)  # an event is waiting for its row: look again soon

    def waits(self, ev):
        """Is this event of this machine to be left in the spool for now? A tool call whose own row is not written
        yet (the connector's awaits(): Claude Code writes it a moment after the hook, with what was said ahead of
        the call), for at most cc_hooks.HOLD_SECS after its hook ran; and the end of a turn whose reply's row is
        not written yet, for at most cc_hooks.END_HOLD_SECS (step 2.2f). Only for a channel whose conversation the bot
        is reading (a record of that transcript), and only when the words on the way are posted at all. No waiting
        here: the round goes on with the other channels and comes back."""
        if ev.get("host"):
            return False
        cid = str(ev.get("channel") or "")
        was = self.host.channels_state.get(cid)
        rt = self.rts.get(cid)
        if not isinstance(was, dict) or put_away(was) or unplaced(was, ev) or \
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
        for name in remote.hosts():
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
        t = time.time()
        if time.time() - getattr(self, "streams_checked", 0) > 60:
            try:
                self.start_streams()
                self.read_words()
                await self.say_config_problem()
            except Exception:
                traceback.print_exc()
            self.lap("每分鐘的檢查", t)
        self.held = set()  # channels with an event left in the spool this round (waits())
        for path, ev in events.waiting(SPOOL):
            if ev.get("channel") == "selftest":  # main.Bot.self_test(): the hook works, the spool is read
                if hasattr(self.host, "ready"):
                    self.host.ready(ev)
                events.done(path)
                continue
            cc_hooks.normalize(ev)  # what kind of thing it is: from the event alone, before the wait is decided
            if ev.get("channel") in self.held or self.waits(ev):
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
            if getattr(self.host, "posts", None) is not None:
                t = time.time()
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
            self.stream_down.add(host)
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
                    try:
                        ev = json.loads(line.decode("utf-8", "replace"))
                    except ValueError:
                        continue
                    if ev.get("heartbeat") or not ev.get("channel"):
                        continue
                    ev["host"] = host
                    cc_hooks.normalize(ev)  # a machine whose dcremote is a step behind sends no kind: made out here
                    try:
                        await self.handle(ev)
                    except Exception:
                        traceback.print_exc()
            except ConnectionError as e:
                print("stream %s: %s" % (host, e), flush=True)
            except Exception:
                traceback.print_exc()
            self.stream_down.add(host)
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

    async def remember_pids(self):
        """Panes attached before process ids were recorded get theirs now (while they are surely the same)."""
        changed = False
        for cid, st in self.host.channels_state.items():
            pane = st.get("pane")
            if pane and not pane.get("pid") and await tmuxio.alive(pane):
                pane["pid"] = await tmuxio.pane_pid(pane)
                if not st.get("home") or st["home"].get("host"):
                    await routes.bind_any(pane, cid)
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
        if from_elsewhere(was, getattr(self.rts.get(str(ch.id)), "opening", None), ev, getattr(self.rts.get(str(ch.id)), "closed", None)):
            return await self.let_go(ch, ev, elsewhere=True)
        self.let_go_said.discard(ch.id)
        st, rt = self.host.chan(ch), self.rt(ch)
        self.mark_parked(st, ev, what)
        if what in TURN_MOVED:
            st.pop("own_panel", None)  # a turn moved: a dialog on the screen from here on is the turn's, not the bot's
            self.unsent.moved(ch, st)  # posts held up by a dialog: handed again once the box is back (a task, C-74)
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
                    await ch.send(embed=posts_mod.cards(peers.who(ev.get("dc_peer") or peers.UNNAMED, self.host.channels_state),
                                                        masking.masked(ev.get("dc_text") or "", limit=3850))[0], silent=True)
                except Exception:
                    traceback.print_exc()
            elif not ours and ev.get("dc_from") == cc_hooks.TYPED and prompt.strip() \
                    and not any(events.matches(prompt, t) for t in rt.typed):  # typed at the terminal, not in Discord
                await self.say(ch, "⌨️ **終端機那邊輸入**\n" + events.quote(masking.masked(prompt), 1500), silent=True)
            self.begin_turn(ch, st, rt, prompt, task=ev.get("dc_from") == cc_hooks.TASK, pid=ev.get("prompt_id"))
        elif what in (cc_hooks.PROGRESS, cc_hooks.PEER_SENT):
            turn = self.begin_turn(ch, st, rt, pid=ev.get("prompt_id"))
            name = ev.get("tool_name", "?")
            turn.tools[name] = turn.tools.get(name, 0) + 1
            await self.interim(ch, rt, texts=texts)
            if what == cc_hooks.PEER_SENT and ev.get("send_to") in peers.directory(self.host.channels_state):
                # (a message with words in it, to a conversation that has a channel: not a bare "tell me when idle")
                await self.say(ch, "📤 **傳給 %s**\n%s" % (peers.who(ev["send_to"], self.host.channels_state),
                                                        events.quote(masking.masked(ev.get("send_text") or ""), 1800)), silent=True)
            await self.progress(ch, turn, events.progress_line(ev))
        elif what == cc_hooks.TURN_END:
            turn = rt.turn
            st["bg"] = len(ev.get("background_tasks") or []) + len(ev.get("session_crons") or [])
            st["last_active"] = time.time()
            final = (ev.get("last_assistant_message") or "").strip()
            await self.interim(ch, rt, final, texts=texts)
            rt.last_final = final
            await self.end_turn(st, rt)
            st["context"] = ev.get("context")
            foot = events.footer(turn.tools if turn else {}, time.time() - turn.t0 if turn else None, st["bg"],
                                 st["context"])
            if turn and time.time() - turn.t0 >= LONG_TURN and config.OWNER_ID:
                foot = (foot or "-# ") + " · <@%d>" % config.OWNER_ID
            reply = masking.masked(ev.get("last_assistant_message") or "（沒有文字回覆）")
            if messages.loaded.get("small_tables", True):  # Markdown tables Discord shows as bars: a code block or a list (C-71a)
                reply = tables.rewrite(reply)[0]
            sent = await self.say(ch, reply + ("\n" + foot if foot else ""))
            if sent and getattr(self.host, "files", None) is not None:  # 📂 buttons for the files it mentions
                asyncio.ensure_future(self.host.files.add_links(ch, st, sent[-1], final))
            src = turn.src if turn else rt.where  # no turn begun: a queued message, sent in with no event until its Stop
            if src and src[0]:  # where this turn begins in the transcript, for 📋. Its end is looked for when asked,
                # by the turn's id in the file; a Claude Code that sends no prompt_id: the file's size now, as before
                end = None if ev.get("prompt_id") or ev.get("host") else transcript.size(src[0])
                if not ev.get("host") or ev.get("prompt_id"):  # another machine's file has no size here: by the id only
                    process.remember(st, [m.id for m in sent if hasattr(m, "id")], src[0], src[1],
                                     None if ev.get("host") else end, ev.get("prompt_id"), host=ev.get("host"))
            await self.host.audit("#%s：%s → 完成%s" % (getattr(ch, "name", "DM"), turn.prompt if turn else "…",
                                                     "（%s）" % foot[3:] if foot else ""))
        elif what == cc_hooks.TURN_FAILED:  # the API failed (usage limit, overload, …): the turn is over, say why
            turn = rt.turn
            await self.interim(ch, rt, texts=texts)
            await self.end_turn(st, rt)
            await self.failed(ch, ev, turn)
        elif what == cc_hooks.DIALOG and not ask.waiting(ch.id):  # (Claude Code's notice that it sits idle is no dialog)
            await self.menus.post(ch, st, ev)  # a dialog in the terminal: shown with buttons
        if what in MOVED_ON:
            await self.menus.resolve(ch)  # the session moved on: an old dialog's buttons would mislead
        self.host.save()

    def keep(self, cid, ev, first=False):
        """An event of a channel that is not to be had just now is kept (in memory: a restart drops them, as every
        such event was dropped before step 2.2e), at most KEPT_MAX for a channel (the oldest goes, with a line). Also
        an event that comes while the ones kept are being handled: it goes behind them (`first`: one of those
        themselves, put back because the channel went away again)."""
        rec = self.kept.get(cid)
        if rec is None:
            rec = self.kept[cid] = [time.time(), [], False]
            if cid not in self.kept_said:
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
        if rec is None or rec[2]:
            return  # nothing kept, or another task is at it
        rec[2] = True
        done = 0
        try:
            while rec[1]:
                if self.host.get_channel(int(cid)) is None and cid in self.host.channels_state:
                    return  # away again: the rest stays kept, in its order
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

    async def let_be(self, ev):
        """An event of a background process that is not the channel's conversation (unplaced()): nothing is posted,
        nothing of the channel's state is touched, nothing is said in #audit (the owner's own conversations in the
        agents list are no fault). A question it asks is left to the terminal's own menu, a plan to its dialog. One
        line for each such conversation in state/events-unplaced.log, for whoever asks later why nothing was posted."""
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
                f.write("%s session %s channel %s%s first %s: a background process of Claude Code that is not the channel's conversation "
                        "(its spare process, or a conversation typed into the agents list); its events are not handled\n"
                        % (time.strftime("%Y-%m-%d %H:%M:%S"), sid, ev.get("channel"), " host %s" % ev["host"] if ev.get("host") else "",
                           ev.get("hook_event_name")))
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
        view = discord.ui.View(timeout=None)
        view.add_item(discord.ui.Button(label="再試一次", custom_id="retry:%d" % ch.id, style=discord.ButtonStyle.primary))
        try:
            await ch.send(("⚠️ **這一輪中斷了：%s**%s%s" % (what, "\n" + events.quote(detail, 1500) if detail else "",
                                                      "\n-# 按「再試一次」會請 Claude 從中斷的地方接著做")), view=view)
            view.stop()
        except Exception:
            traceback.print_exc()
        await self.host.audit("#%s：%s → 失敗（%s）" % (getattr(ch, "name", "DM"), turn.prompt if turn else "…", what))

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
        turn, rt.turn = rt.turn, None
        if turn and turn.msg is not None:
            try:
                await turn.msg.delete()
            except Exception:
                pass

    async def interim(self, ch, rt, final="", texts=None):
        """Post, as quotes, what Claude said since the last look (before a tool call), read where the transcript
        is (`texts`; None: nothing was read). `final` is the reply the Stop event is about to post; the previous
        turn's reply can also still be trickling into the file."""
        if not config.INTERIM or texts is None:
            return
        texts = [t for t in texts if t not in (final, rt.last_final)]
        if not texts:
            return
        if rt.turn and rt.turn.msg is not None:  # the "working…" message goes back to the bottom next time
            try:
                await rt.turn.msg.delete()
            except Exception:
                pass
            rt.turn.msg = None
        for t in texts:
            for part in split_message(masking.masked(t), 1890):
                try:
                    await ch.send(">>> " + part, silent=True)
                except Exception:
                    traceback.print_exc()

    async def progress(self, ch, turn, line):
        """Keep one "working…" message up to date once a turn has run for a while."""
        turn.lines = (turn.lines + [line])[-6:]
        now = time.time()
        if now - turn.t0 < 15 or now - turn.last < 5:
            return
        turn.last = now
        body = "處理中…（%d 秒）\n```\n%s\n```" % (now - turn.t0, "\n".join(masking.mask(line, cut=True)[0] for line in turn.lines))  # (cut to 80 already)
        try:
            if turn.msg is None:
                turn.msg = await ch.send(body, silent=True)
            else:
                await turn.msg.edit(content=body)
        except Exception:
            pass

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
        if turn.msg is not None:
            try:
                await turn.msg.delete()
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
            gone = st.pop("pane")
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
            if not name or st.get("pane") or st.get("busy") or st.get("bg"):
                continue
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
        for proc in self.streams.values():  # or an orphaned line would go on taking that machine's events
            try:
                proc.kill()
            except ProcessLookupError:
                pass
        for rt in self.rts.values():
            if rt.typing is not None:
                rt.typing.cancel()
            if rt.turn and rt.turn.msg is not None:
                try:
                    await rt.turn.msg.delete()
                except Exception:
                    pass
