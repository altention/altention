"""A channel's Claude moved to another place on the same machine (C-201 2c1; design instance
docs/refactor/c201-design.md section 3 points 4-5 and section 4; plan approved 11-18, manager 11-20 on its six points
and three more).

- `/move session <name>` (`!move session <name>`): another session of the same tmux server, by move-window. The pane
  (%N) and its Claude stay; nothing stops. A channel on a session of the bot's own server (st["tmux"], found by the
  session's name) becomes a window of the session it moved to (home + pane, as a window the setup card opens), found
  by its pane from now on: left as it was, the bot would not find it by the old name and open a second Claude on the
  same conversation. The session left behind with no window in it is gone (tmux ends it). A session of the owner's
  server must be there (「先開好再搬」); one of the bot's own is made. Its Claude is told where it is now (note_next).
- `/move tmux bot|user [session]`: the bot's own tmux server <-> the owner's. No pane crosses servers: the old Claude
  is ended FIRST (its turn over, idle at a plain input box; /exit; its process seen gone and not moved to the
  background by a claude daemon), then the same conversation taken up (--resume) in the new place, taken as done only
  at an input box AND with that conversation's id from its SessionStart (or its process record). Anything else: the
  new one closed and the conversation taken up again where it was, its place as it was. Never two Claudes on one
  conversation: a Claude that did not end is left as it is and nothing is opened.

- `/move machine <machine> [folder]`, `/move folder <path>` (C-201 2c2; plan approved by the manager 11-20, with
  his three changes): the same order as a tmux move, with the conversation's record carried in between (copied,
  the old one kept: bot/transcripts.py; through the bot's machine, dcremote transcript-out/-in over there). The old
  Claude is ended on the machine it is on (end_old() asks that machine: its pane's processes, its claude daemon).
  To the bot's machine: the bot's own tmux, as --new-claude opens one; to another: a window in the owner's tmux
  there (session tmuxio.session_name()), started the owner's way (take_up_users()). Its folder over there: the one
  given, else the same path, else the path with the old HOME turned into that machine's; none: refused. Refused also
  when that machine does not answer or has no claude; a login file not seen there is only said on the card (a mac
  keeps it in the keychain, an API key needs none: the manager 11-20; not up: back where it was). /move folder: the
  same place, the folder changed; in the owner's pane in a subshell `( cd -- <folder> && claude … )`, their shell's
  own folder left as it was (manager 11-20). The Claude taken up is told the folder moved (note_next).

Refused, with why: another machine's channel for a session or tmux move, an adapter (pi, Codex: 「這種對話還不能搬」), a channel with no
conversation behind it, background work, subagents or workflows running (they would end: said, not queued), a paused
channel or one run in print; the owner's own pane not at a plain input box (not queued), or running Claude with no
shell under it (/exit would close the owner's pane).
A turn running: the tmux move waits for it to end (st["move_after"], tied to that turn, as --reset's), and a turn
begun after another one drops it. The owner's /move tmux asks first (❓ card, blue 〔搬〕〔取消〕: the conversation is
kept and can be moved back); `altention dc post <channel> --move <kind> <target> --why …` goes at once, its card and
#audit saying which Claude. One move per channel at a time. Places are written by where.put() alone."""
import asyncio
import contextvars
import json
import os
import re
import secrets
import shlex
import shutil
import time
import traceback

import discord

from altention.connectors.claude_code import process as cc_process, screen

from . import attachments, cc_health, config, deliver, kinds, messages, remote, routes, tmuxio, transcripts, where as where_mod
from .util import same_socket

PREFIX = "c201mv"  # c201mv:<channel id>:<token>:go|no (the confirm card), c201mv:<channel id> (/where's 〔搬到…〕)
BACK_SECS = 5  # after Esc on "moved to the background": the old Claude back at its box
EXIT_SECS = 20  # the old Claude given this long to end after /exit
SID_SECS = 30  # after the new one is up, this long for its SessionStart's conversation id
COPY_SECS = 300  # one way of a record's carrying (out of one machine, into another)
BIG = 300 * 1024 * 1024  # a record (and its folder) this big: the bot's machine must have MEM_TIMES its size free,
MEM_TIMES = 4  # the copy being held in the bot's process several times over (manager 11-20, drill B: 213 MiB → 583 MB)
PROBED = contextvars.ContextVar("move_probed", default=None)  # a far move's move-probe answers (bot/moving.py probe())
FAR = ("machine", "folder")  # the moves of 2c2
SHELLS = ("bash", "zsh", "fish", "sh", "dash", "ksh", "tcsh", "csh")
BACKGROUND = re.compile(r"moved to the background", re.I)  # /exit with a claude daemon running (2026-10-21)
CHANNEL_SESSION = re.compile(r"^dc-.*-\d{4}$")  # tmuxio.session_name(): a channel's own session on the bot's server
SESSION_NAME = re.compile(r"^[\w][\w.-]{0,59}$", re.UNICODE)  # a session name tmux takes as a target as it is


# Claude Code's options as its --help gives them (2.1.328): how many words each takes. 1: one; "+": all that follow up
# to the next option; "?": the next one unless it is an option; 0: none. Kept when the owner's Claude is taken up
# again; DROPPED are left out (another conversation, or a new session); an option not here: the line not told.
OPTIONS = dict([(o, 1) for o in (
    "--agent", "--agents", "--append-system-prompt", "--autocompact", "--debug-file", "--effort", "--fallback-model",
    "--model", "-n", "--name", "--permission-mode", "--permission-prompts", "--plugin-dir", "--plugin-url",
    "--remote-control-session-name-prefix", "--setting-sources", "--settings", "--system-prompt",
    "--system-prompt-snapshot")] + [(o, "+") for o in (
    "--add-dir", "--allowedTools", "--allowed-tools", "--disallowedTools", "--disallowed-tools", "--betas",
    "--mcp-config", "--tools")] + [(o, "?") for o in (
    "-d", "--debug", "--prompt-suggestions", "--remote-control")] + [(o, 0) for o in (
    "--allow-dangerously-skip-permissions", "--dangerously-skip-permissions", "--ax-screen-reader", "--bare", "--brief",
    "--chrome", "--no-chrome", "--disable-slash-commands", "--exclude-dynamic-system-prompt-sections", "--ide",
    "--strict-mcp-config", "--verbose", "--restricted", "--safe-mode")])
DROPPED = {"-r": "?", "--resume": "?", "--session-id": 1, "-c": 0, "--continue": 0, "--fork-session": 0}


def plain(text):
    """Card lines for one #audit line: the small-print mark ("-# ") off each, joined (2c1 drill 11-20: "；-# 已經…")."""
    return " ".join(line[3:] if line.startswith("-# ") else line for line in text.splitlines() if line.strip())


def resume_line(argv, sid):
    """The owner's Claude typed again as it was started, on conversation `sid`: its options kept, a prompt it was
    started with left out (else that would be sent again as a new turn: manager 11-20), another resume or continue
    dropped. None when its words are not known or hold an option not known (then the plain line, said)."""
    words = kept_words(argv)
    return None if words is None else " ".join(shlex.quote(w) for w in words + ["--resume", sid])


def kept_words(argv):
    """resume_line()'s words before --resume: the program, then the options kept. None when not known."""
    if not argv:
        return None
    start = 1
    if os.path.basename(argv[0]) in cc_process.INTERPRETERS:  # node …/cli.js: up to the script
        start = next((i + 1 for i, w in enumerate(argv[1:], 1) if not w.startswith("-")), len(argv))
    words, i = list(argv[:start]), start
    while i < len(argv):
        w = argv[i]
        if w == "--":  # (what follows is the prompt)
            break
        if not w.startswith("-"):  # a prompt word: left out
            i += 1
            continue
        flag, given = w.split("=", 1)[0], "=" in w
        takes = OPTIONS.get(flag, DROPPED.get(flag, "unknown"))
        if takes == "unknown":
            return None
        j = i + 1
        if not given:
            if takes == 1:
                j = i + 2
            elif takes in ("+", "?"):
                while j < len(argv) and not argv[j].startswith("-"):
                    j += 1
                    if takes == "?":
                        break
        if flag in OPTIONS:
            words.extend(argv[i:j])
        i = j
    return words


BYPASS = "bypassPermissions"
SKIPPING = ("--dangerously-skip-permissions", "--allow-dangerously-skip-permissions")


def bots_own(words):
    """Is this the bot's own command line (its --settings file in the bot's state folder, or the folders alone as
    JSON: cc_launch.command())? Its options are the bot's, never the owner's way of starting Claude."""
    for i, w in enumerate(words or []):
        value = words[i + 1] if w == "--settings" and i + 1 < len(words) else w[len("--settings="):] if \
            w.startswith("--settings=") else None
        if value is not None and (value.startswith("{") or os.path.realpath(value).startswith(
                os.path.realpath(config.STATE) + os.sep)):
            return True
    return False


def owners_options(words):
    """The options of the owner's own Claude as the bot types them into the owner's terminal (manager 11-20, from the
    2c1 drill): never one that skips the permission questions (--dangerously-skip-permissions, bypassPermissions:
    "default" instead), and always a permission mode said (default when it is not known)."""
    out, i, mode = [], 0, None
    while i < len(words):
        w = words[i]
        if w in SKIPPING:
            i += 1
            continue
        if w == "--permission-mode" or w.startswith("--permission-mode="):
            value = w.split("=", 1)[1] if "=" in w else (words[i + 1] if i + 1 < len(words) else "")
            i += 1 if "=" in w else 2
            if mode is None:  # (where it was; a second one dropped)
                mode = "default" if value in ("", BYPASS) else value
                out += ["--permission-mode", mode]
            continue
        out.append(w)
        i += 1
    return out if mode else out + ["--permission-mode", "default"]


def mode_in(options):
    """The permission mode owners_options() put in."""
    return options[options.index("--permission-mode") + 1] if "--permission-mode" in (options or []) else "default"


def users_options(argv):
    """The options (no program, no prompt, no --resume) the owner's Claude was started with, as owners_options()
    keeps them; None when not known (no words, an option not known, or the bot's own command line)."""
    words = kept_words(argv)
    if words is None or bots_own(words):
        return None
    start = 1
    if os.path.basename(argv[0]) in cc_process.INTERPRETERS:
        start = next((i + 1 for i, w in enumerate(argv[1:], 1) if not w.startswith("-")), len(argv))
    return owners_options(words[start:])


def user_line(argv, sid, known=None, program="claude"):
    """(the line typed into the owner's shell on conversation `sid`, whether it is how that Claude was started).
    From the Claude's own words when they are the owner's (users_options()); else what was kept of the owner's
    way (`known`: st["user_options"], written when the channel left the owner's terminal); else plain, in the
    default permission mode. Never the bot's settings or a mode that skips the questions (manager 11-20)."""
    options = users_options(argv)
    program = shlex.quote(program)
    if options is not None:
        words = kept_words(argv)
        start = 1 if os.path.basename(argv[0]) not in cc_process.INTERPRETERS else \
            next((i + 1 for i, w in enumerate(argv[1:], 1) if not w.startswith("-")), len(argv))
        program = " ".join(shlex.quote(w) for w in words[:start])
    known_ok = isinstance(known, list) and all(isinstance(w, str) for w in known)
    chosen = options if options is not None else owners_options(list(known)) if known_ok else owners_options([])
    line = " ".join([program] + [shlex.quote(w) for w in chosen + ["--resume", sid]])
    return line, options is not None or known_ok



def login_shell():
    """The owner's shell (their login entry; $SHELL; /bin/sh)."""
    try:
        import pwd
        shell = pwd.getpwuid(os.getuid()).pw_shell
    except (ImportError, KeyError):
        shell = None
    return shell if shell and os.path.isfile(shell) else os.environ.get("SHELL") or "/bin/sh"


def say(code, **data):
    return messages.move(code, **data)


def idle(view):
    """Claude Code at its plain input box, nothing running, nothing in the box (what /exit may be typed into)."""
    return bool(view) and screen.pane_state(view) == "box" and not screen.busy_shown(view) and screen.input_text(view) == ""


def owners(st):
    """Is the channel's Claude the owner's own (an attached pane, not a window the bot opened)?"""
    return bool(st.get("pane")) and not st.get("home") and not st.get("tmux")


def host_of(st):
    """The machine the channel's Claude is on, None for the bot's own."""
    t = st.get("home") if isinstance(st.get("home"), dict) else st.get("pane") if isinstance(st.get("pane"), dict) else {}
    return t.get("host") or (st.get("pane") if isinstance(st.get("pane"), dict) else {}).get("host") or None


def subshell(folder, line):
    """The owner's line run in `folder` without moving their shell there (manager 11-20)."""
    return "( cd -- %s && %s )" % (shlex.quote(folder), line)


class Mover:
    def __init__(self, bot):
        self.bot = bot
        self.running = set()  # channel ids with a move going now
        self.asked = {}  # token -> job: a confirm card shown (in memory: after a restart its buttons say so)

    # ------------------------------------------------------------------ where things are
    def paths(self):
        """(the bot's own server's socket, the owner's)."""
        return self.bot.where_context()[1], tmuxio.user_socket()

    def server(self, st):
        """Which tmux server the channel's Claude is on: "bot", "user" or "other" (another machine, another server)."""
        bot_path, user_path = self.paths()
        if st.get("tmux") and not st.get("home") and not st.get("pane"):
            return "bot"
        t = st.get("home") if isinstance(st.get("home"), dict) else st.get("pane") if isinstance(st.get("pane"), dict) else {}
        if t.get("host") or (st.get("pane") or {}).get("host"):
            return "other"
        if same_socket(t.get("socket"), bot_path, here=True):
            return "bot"
        if same_socket(t.get("socket"), user_path, here=True):
            return "user"
        return "other"

    async def pane_of(self, st):
        """The channel's pane as an address ({socket, id, pid}): its own, or the bot's session's (asked of tmux);
        None when there is none running."""
        if isinstance(st.get("pane"), dict):
            return dict(st["pane"])
        if not st.get("tmux"):
            return None
        code, out = await tmuxio.tmux("display-message", "-p", "-t", "=%s:" % st["tmux"], "#{pane_id}\t#{pane_pid}")
        pane, _, pid = out.strip().partition("\t")
        if code != 0 or not pane.startswith("%"):
            return None
        return {"socket": self.paths()[0], "id": pane, "pid": pid}

    async def session_now(self, pane):
        code, out = await tmuxio.tmux("display-message", "-p", "-t", pane["id"], "#{session_name}", socket=pane["socket"])
        return out.strip() if code == 0 else None

    async def shell_under(self, pane):
        """Does the pane run a shell, Claude under it (so /exit leaves the pane)? Told by the pane's own process
        (another machine's: by its dcremote, probe())."""
        if pane.get("host"):
            return bool((await self.probe(pane) or {}).get("shell"))
        try:
            with open("/proc/%s/comm" % int(pane.get("pid")), encoding="utf-8") as f:
                return f.read().strip().lstrip("-") in SHELLS
        except (OSError, ValueError, TypeError):
            return False

    # ------------------------------------------------------------------ refusals
    def refused(self, ch, st, kind, target, session=None):
        """Why this move is not done, or None. What can be told without asking tmux."""
        name = getattr(ch, "name", "?")
        if not st or st.get("archived") or kinds.channel_kind(st) != kinds.CONVERSATION:
            return say("not_claude", channel=name)
        rec = st.get("where") if isinstance(st.get("where"), dict) else {}
        agent = (rec.get("agent") or {}).get("kind") or "claude"
        if agent != "claude" or ((st.get("pane") or {}).get("agent")):
            return say("adapter", channel=name)
        where = self.server(st)
        if where == "other" and not (kind in FAR and host_of(st)):
            return say("other_machine" if host_of(st) else "other_server", channel=name)
        mode = getattr(self.bot, "mode", None)
        if callable(mode) and mode(st) != "tmux":
            return say("not_tmux", channel=name)
        posts = getattr(self.bot, "posts", None)
        pauses = getattr(posts, "pauses", None)
        if pauses is not None and pauses.of(ch.id):
            return say("paused", channel=name)
        if st.get("bg"):
            return say("busy_bg", channel=name, n=st["bg"])
        if st.get("subs"):
            return say("busy_subs", channel=name, n=len(st["subs"]))
        if st.get("move_background"):
            return say("in_background", sid=st["move_background"].get("session") or "?")
        if str(ch.id) in self.running or isinstance(st.get("move_after"), dict):
            return say("already", channel=name)
        if kind == "session":
            if not target or not SESSION_NAME.match(target):
                return say("usage")
            if where == "bot":  # (a channel of st["tmux"] is found by its session's current pane: moved in, another
                for other, ost in (getattr(self.bot, "channels_state", None) or {}).items():  # channel's messages
                    if other != str(ch.id) and isinstance(ost, dict) and ost.get("tmux") == target:  # would reach this
                        return say("channel_session", session=target, other=other)  # Claude: #code-review-2 on 400f5a9d)
                if CHANNEL_SESSION.match(target):
                    return say("channel_session_name", session=target)
        elif kind == "tmux":
            if target not in ("bot", "user"):
                return say("usage")
            if target == where:
                return say("there_already", channel=name, where=say("server_" + target))
            if target == "user" and not (session and SESSION_NAME.match(session)):
                return say("user_session")
            if not st.get("session_id"):
                return say("no_conversation", channel=name)
        elif kind == "machine":
            if not target:
                return say("usage")
            if not st.get("session_id"):
                return say("no_conversation", channel=name)
            host, why = self.machine(target)
            if why:
                return why
            if host == host_of(st):
                return say("same_machine", channel=name, machine=target)
            if session is not None and not session.startswith(("/", "~")):
                return say("folder_path", folder=session)
        elif kind == "folder":
            if not target or not target.startswith(("/", "~")):
                return say("folder_path", folder=target or "")
            if not st.get("session_id"):
                return say("no_conversation", channel=name)
        else:
            return say("usage")
        return None

    def machine(self, name):
        """(the machine `name` is, None for the bot's own; why not): one in the machine list the bot reaches (the
        machines --new-claude --on opens on). Never guessed."""
        me = remote.this_machine()
        if not name or name.lower() == me.lower():
            return None, None
        machines = remote.inventory().get("machines") or {}
        found = next((n for n, m in machines.items() if name.lower() in (n.lower(), ((m or {}).get("hostname") or "").lower())), None)
        if found == me:
            return None, None
        hosts = remote.hosts()
        if found in hosts:
            return found, None
        return None, say("no_machine", machine=name, machines="、".join([me] + sorted(hosts)))

    # ------------------------------------------------------------------ entry points
    async def command(self, ch, st, arg, send):
        """`!move <kind> <target> [session]` (/move): a session move at once; a tmux move asks first."""
        words = (arg or "").split()
        if words == ["clear"]:  # the conversation in the background stopped by hand: the channel opens it again
            if not st.pop("move_background", None):
                return await send(say("clear_nothing"))
            self.bot.save()
            return await send(say("cleared"))
        kind, target = (words + ["", ""])[:2]
        session = words[2] if len(words) > 2 else None
        if kind in FAR:  # (a folder may hold spaces: the rest of the line)
            rest = (arg or "").strip().split(None, 2 if kind == "machine" else 1)
            target = rest[1] if len(rest) > 1 else ""
            session = (rest[2] if len(rest) > 2 else None) if kind == "machine" else None
            if kind == "folder":
                target = (arg or "").strip().split(None, 1)[1].strip() if len(rest) > 1 else ""
        if kind == "tmux" and target not in ("bot", "user") and target and session is None:
            target, session = "user", target  # (/move tmux <session>: the owner's)
        why_not = self.refused(ch, st, kind, target, session)
        if why_not:
            return await send(why_not)
        if kind == "session":
            return await send(await self.move_session(ch, st, target))
        token = secrets.token_hex(4)
        if kind in FAR:  # (what is over there looked at first: the card says where it goes, or why not)
            why, plan = await self.plan(st, kind, target, session)
            if why:
                return await send(say("failed", why=why) + "\n" + say("old_kept"))
            self.asked[token] = {"cid": str(ch.id), "kind": kind, "target": target, "folder": session, "at": time.time()}
            text = self.ask_far(ch, st, kind, target, plan)
            if kind == "machine" and plan["host"] is None and owners(st):
                text += await self.to_bot_words(st)
            return await send(text, view=self.ask_view(ch, token))
        self.asked[token] = {"cid": str(ch.id), "target": target, "session": session, "at": time.time()}
        text = self.ask_text(ch, st, target, session)
        if target == "bot" and owners(st):
            text += await self.to_bot_words(st)
        await send(text, view=self.ask_view(ch, token))

    def ask_far(self, ch, st, kind, target, plan):
        conversation = (st.get("session_id") or "")[:8]
        if kind == "folder":
            return say("ask_folder", channel=getattr(ch, "name", "?"), old=st.get("cwd") or "?", folder=plan["folder"],
                       conversation=conversation) + "\n" + say("trust_note")
        text = say("ask_machine", channel=getattr(ch, "name", "?"), machine=target, old=host_of(st) or remote.this_machine(),
                   place=say("server_bot") if plan["host"] is None else say("server_user"), folder=plan["folder"],
                   conversation=conversation)
        text += "\n" + say("trust_note")
        if plan.get("login") is False:
            text += "\n" + say("no_login_seen", machine=target)
        return text

    def ask_text(self, ch, st, target, session):
        where = say("server_" + target) + (say("in_session", session=session) if session else "")
        return say("ask", channel=getattr(ch, "name", "?"), where=where, conversation=(st.get("session_id") or "")[:8])

    async def to_bot_words(self, st):
        """The owner's pane to the bot's tmux: the card says it runs with the bot's settings and permission mode there,
        and in which mode it comes back (manager 11-20). "" when the bot's mode cannot be told (the move is refused
        then for the same reason)."""
        try:
            bot_mode = (await self.bot.tmux.chosen_for(st))[1]
        except Exception:
            return ""
        pane = await self.pane_of(st)
        options = users_options(self.claude_line(pane)) if pane else None
        if options is None:
            options = st.get("user_options") if isinstance(st.get("user_options"), list) else []
        return say("ask_to_bot", bot_mode=say("skips_questions") if bot_mode == BYPASS else "`%s`" % bot_mode,
                   mode="`%s`" % mode_in(owners_options(list(options))))

    def ask_view(self, ch, token):
        v = discord.ui.View(timeout=None)
        v.add_item(discord.ui.Button(label=say("button_go"), style=discord.ButtonStyle.primary,
                                     custom_id="%s:%s:%s:go" % (PREFIX, ch.id, token)))
        v.add_item(discord.ui.Button(label=say("button_no"), style=discord.ButtonStyle.secondary,
                                     custom_id="%s:%s:%s:no" % (PREFIX, ch.id, token)))
        return v

    def view(self, ch):
        """/where's 〔搬到…〕 button (next to 〔改名…〕)."""
        return discord.ui.Button(label=say("where_button"), style=discord.ButtonStyle.secondary,
                                 custom_id="%s:%s" % (PREFIX, ch.id))

    async def on_interaction(self, interaction):
        """The confirm card's buttons and /where's 〔搬到…〕. True when it was ours."""
        if interaction.type != discord.InteractionType.component:
            return False
        parts = ((interaction.data or {}).get("custom_id") or "").split(":")
        if not parts or parts[0] != PREFIX or len(parts) not in (2, 4):
            return False
        if interaction.user.id != config.OWNER_ID:
            await interaction.response.send_message("只有擁有者能用。", ephemeral=True)
            return True
        ch = interaction.channel
        if ch is None or str(ch.id) != parts[1]:
            await interaction.response.send_message(say("gone"), ephemeral=True)
            return True
        if len(parts) == 2:
            await interaction.response.send_modal(MoveModal(self, ch))
            return True
        job = self.asked.pop(parts[2], None)
        if parts[3] == "no" or job is None:
            await interaction.response.edit_message(content=say("cancelled" if job is not None else "expired"), view=None)
            return True
        await interaction.response.edit_message(content=say("starting"), view=None)
        msg = interaction.message
        st = self.bot.chan(ch)
        why_not = self.refused(ch, st, job.get("kind") or "tmux", job["target"], job.get("folder") if job.get("kind") in FAR else job.get("session"))
        if why_not:
            await self.retext(msg, why_not)
            return True
        asyncio.ensure_future(self.go_logged(ch, dict(job, card=msg)))
        return True

    async def from_modal(self, interaction, ch, kind, target):
        await interaction.response.defer()
        st = self.bot.chan(ch)
        out = []

        async def send(text, view=None):
            out.append(text)
            await interaction.followup.send(text, view=view) if view is not None else await interaction.followup.send(text)
        await self.command(ch, st, "%s %s" % (kind.strip(), target.strip()), send)

    async def by_agent(self, ch, sender_ch, post):
        """`altention dc post <channel> --move <kind> <target> [session] --why …`: no card to press (the Claude asked,
        with why); the card in the channel and #audit say whose. Runs on its own: the asker is told it began."""
        st = self.bot.chan(ch)
        kind, target = post.get("move_place"), post.get("move_target") or ""
        session = post.get("move_folder") if kind in FAR else post.get("move_session")
        by = getattr(sender_ch, "name", None) or post.get("from_name") or "?"
        why_not = self.refused(ch, st, kind, target, session)
        if why_not:
            await self.refused_audit(ch, why_not, by, post.get("why"))
            return False, why_not
        if kind == "session":
            card = await self.move_session(ch, st, target, by=by, why=post.get("why"))
            await self.post(ch, card)
            return not card.startswith("❌"), card
        msg = await self.post(ch, say("starting_by", by=by, why=post.get("why") or ""))
        job = {"cid": str(ch.id), "target": target, "session": session, "by": by, "why": post.get("why") or "", "card": msg}
        if kind in FAR:
            job.update(kind=kind, folder=session, session=None)
        asyncio.ensure_future(self.go_logged(ch, job))
        return True, say("agent_started", channel=ch.name)

    async def post(self, ch, text):
        try:
            return await deliver.deliver(ch, text, intent="status")
        except Exception:
            traceback.print_exc()
            return None

    async def retext(self, msg, text):
        if msg is None:
            return
        try:
            await deliver.deliver(getattr(msg, "channel", None), content=text, view=None, intent="status", edit=msg)
        except Exception:
            traceback.print_exc()

    # ------------------------------------------------------------------ after a turn
    async def go_logged(self, ch, job):
        try:
            await self.go(ch, job)
        except Exception:
            traceback.print_exc()
            self.running.discard(str(ch.id))
            await self.retext(job.get("card"), say("failed", why=say("broke")))

    async def go(self, ch, job):
        """A tmux move pressed (or asked by a Claude): now, or after the turn running."""
        st = self.bot.chan(ch)
        if st.get("busy"):
            rt = getattr(self.bot.tmux, "rts", {}).get(str(ch.id))
            card = job.get("card")
            st["move_after"] = {k: v for k, v in job.items() if k != "card"}
            st["move_after"].update(turn=getattr(getattr(rt, "turn", None), "pid", None),
                                    card=[getattr(getattr(card, "channel", None), "id", None), getattr(card, "id", None)])
            self.bot.save()
            await self.retext(card, say("queued"))
            return
        await (self.move_far if job.get("kind") in FAR else self.move_tmux)(ch, st, job)

    def after_turn(self, ch, st, pid=None):
        """A turn of `ch` ended: a move waiting on it goes now (a task of its own). Another turn's end does nothing."""
        job = st.get("move_after")
        if not isinstance(job, dict) or (job.get("turn") and pid and job["turn"] != pid):
            return None
        st.pop("move_after", None)
        self.bot.save()
        return asyncio.ensure_future(self.run_queued(ch, job))

    async def new_turn(self, ch, st, pid=None):
        """A turn begins (UserPromptSubmit): a move waiting on another turn is dropped (that one ended badly), said."""
        job = st.get("move_after")
        if not isinstance(job, dict) or (job.get("turn") and pid and job["turn"] == pid):
            return
        st.pop("move_after", None)
        self.bot.save()
        await self.retext(await self.card_of(job), say("dropped"))
        await self.bot.audit(say("audit_dropped", channel=getattr(ch, "name", "?"), by=job.get("by") or say("owner")))

    async def card_of(self, job):
        try:
            cid, mid = (job.get("card") or [None, None])[:2]
            c = self.bot.get_channel(int(cid)) if cid else None
            return await c.fetch_message(int(mid)) if c is not None and mid else None
        except Exception:
            return None

    async def run_queued(self, ch, job):
        try:
            job = dict(job, card=await self.card_of(job))
            st = self.bot.chan(ch)
            far = job.get("kind") in FAR
            why_not = self.refused(ch, st, job.get("kind") or "tmux", job["target"], job.get("folder") if far else job.get("session"))
            if why_not:
                if job.get("by"):
                    await self.refused_audit(ch, why_not, job["by"], job.get("why"))
                await self.retext(job["card"], why_not)
                return
            await (self.move_far if far else self.move_tmux)(ch, st, job)
        except Exception:
            traceback.print_exc()
            self.running.discard(str(ch.id))

    # ------------------------------------------------------------------ /move session
    async def move_session(self, ch, st, target, by=None, why=None):
        """move-window into session `target` of the same server; the card."""
        cid, name = str(ch.id), getattr(ch, "name", "?")
        rt = self.bot.tmux.rt(ch)
        self.running.add(cid)
        try:
            async with rt.lock:
                where = self.server(st)
                bot_path = self.paths()[0]
                pane = await self.pane_of(st)
                if pane is None or not await tmuxio.exists(pane):
                    if st.get("tmux") and not st.get("pane"):  # nothing running: the next one opens there
                        where_mod.put(st, name, tmux=None, home={"socket": bot_path, "session": target, "window": ""})
                        self.bot.save()
                        await self.bot.audit(self.audit_line(name, say("server_bot"), target, by, why))
                        return say("session_done_later", channel=name, session=target)
                    return say("failed", why=say("no_pane"))
                now = await self.session_now(pane)
                if now == target:
                    return say("there_already", channel=name, where=say("in_session", session=target))
                placeholder = None
                if not await tmuxio.has_session(pane["socket"], target):
                    if where != "bot":
                        return say("failed", why=say("open_first", session=target))
                    code, out = await tmuxio.tmux("new-session", "-d", "-s", target, "-P", "-F", "#{window_id}",
                                                  socket=pane["socket"])
                    if code != 0:
                        return say("failed", why=say("tmux_refused", what=out.strip()[:120] or code))
                    placeholder = out.strip()
                err = []
                code, _ = await tmuxio.tmux("move-window", "-s", pane["id"], "-t", "=%s:" % target, socket=pane["socket"], err=err)
                if placeholder:
                    await tmuxio.tmux("kill-window", "-t", placeholder, socket=pane["socket"])
                if code != 0:
                    return say("failed", why=say("tmux_refused", what=" ".join(err)[:120] or code))
                if st.get("tmux") and not st.get("pane"):
                    where_mod.put(st, name, tmux=None, home={"socket": bot_path, "session": target, "window": ""}, pane=pane)
                elif st.get("home"):
                    where_mod.put(st, name, home=dict(st["home"], session=target))
                else:
                    where_mod.put(st, name, pane_session=target)
                st["note_next"] = say("note", where=tmuxio.label(pane), session=target)
                self.bot.save()
                await self.bot.audit(self.audit_line(name, now or "?", target, by, why))
                return say("session_done", channel=name, session=target, old=now or "?")
        finally:
            self.running.discard(cid)

    async def stopped(self, ch, card, job, why):
        """A move stopped before anything was sent or opened: the card says why; a Claude's move is in #audit too
        (manager 11-20: every move an agent asked for leaves a line; the owner's own has its card)."""
        if job.get("by"):
            await self.refused_audit(ch, why, job.get("by"), job.get("why"))
        return await self.retext(card, say("failed", why=why) + "\n" + say("old_kept"))

    async def refused_audit(self, ch, why, by, given):
        await self.bot.audit(say("audit_refused", channel=getattr(ch, "name", "?"), why=plain(why),
                                 by=say("audit_by", by=by, why=given or "")))

    def audit_line(self, channel, old, new, by, why):
        return say("audit", channel=channel, old=old, new=new,
                   by=say("audit_by", by=by, why=why or "") if by else say("audit_owner"))

    # ------------------------------------------------------------------ /move tmux
    async def move_tmux(self, ch, st, job):
        """End the old Claude, take the conversation up in the new place, or back where it was. The card edited."""
        cid, name = str(ch.id), getattr(ch, "name", "?")
        card, to, session = job.get("card"), job["target"], job.get("session")
        bridge = self.bot.tmux
        rt = bridge.rt(ch)
        self.running.add(cid)
        try:
            async with rt.lock:
                sid = st.get("session_id")
                old = {k: st.get(k) for k in where_mod.PLACE_FIELDS}
                owner = owners(st)
                frm = self.server(st)
                if to == "user" and not await tmuxio.has_session(self.paths()[1], session):
                    return await self.stopped(ch, card, job, say("open_first", session=session))
                if to == "bot" and await tmuxio.alive(tmuxio.session_name(name, ch.id)):
                    return await self.stopped(ch, card, job, say("bot_session_taken"))
                pane = await self.pane_of(st)
                running = bool(pane) and await tmuxio.alive(pane if st.get("pane") else st.get("tmux"))
                argv = await self.claude_line_at(pane) if running and owner else None  # (read before /exit: to take it up as it was)
                if running:
                    await self.retext(card, say("step_ending"))
                    ended, why = await self.end_old(ch, st, rt, pane, sid, owner)
                    if not ended and why in (say("not_idle"), say("no_shell"), say("daemon")):  # (nothing sent yet)
                        return await self.stopped(ch, card, job, why)
                    if not ended:  # (why says whether the old one is as it was)
                        tail = "" if st.get("move_background") or say("old_kept") in why else "\n" + say("old_kept")
                        if job.get("by"):
                            await self.bot.audit(say("audit_failed", channel=name, why=plain(why), back=plain(tail) or "-"))
                        return await self.retext(card, say("failed", why=why) + tail)
                if owner and users_options(argv) is not None:  # (how the owner starts it: taken back to their tmux so)
                    st["user_options"] = users_options(argv)
                await self.retext(card, say("step_opening"))
                ok, why = await self.open_at(ch, st, rt, to, session, sid)
                if ok:
                    if owner and pane:
                        await routes.unbind_any(cid, pane.get("host"), pane)  # (the owner's pane is theirs again)
                    self.bot.save()
                    await self.bot.audit(self.audit_line(name, say("server_" + frm), say("server_" + to) +
                                                         (say("in_session", session=session) if session else ""),
                                                         job.get("by"), job.get("why")))
                    return await self.retext(card, say("tmux_done", channel=name, where=say("server_" + to) +
                                                     (say("in_session", session=session) if session else "")))
                # the new place did not take it: back where it was
                await self.retext(card, say("step_back"))
                if running:
                    back = await self.reopen(ch, st, rt, old, sid, pane, owner, argv)
                else:  # (nothing ran: its record as it was, the next message opens it there)
                    where_mod.put(st, name, **old)
                    back = None
                self.bot.save()
                tail = say("back_ok") if back is None or back is True else \
                    say("back_failed_owner", why=back, line=user_line(argv, sid, st.get("user_options"))[0]) if owner else \
                    say("back_failed", why=back)  # (the bot opens nothing in the owner's pane: 2c1 drill 11-20)
                if back is True and owner and not user_line(argv, sid, st.get("user_options"))[1]:
                    tail += "\n" + say("back_default")
                await self.bot.audit(say("audit_failed", channel=name, why=why, back=plain(tail)))
                return await self.retext(card, say("failed", why=why) + "\n" + tail)
        finally:
            self.running.discard(cid)

    # ------------------------------------------------------------------ /move machine, /move folder (2c2)
    def here_dir(self, path):
        """A folder on the bot's machine (tests: one file system stands for two machines)."""
        return os.path.isdir(path)

    async def info(self, host, folder=None):
        """dcremote info over there ({"socket", "home", "folder", "claude", "login"}), {} when it did not answer."""
        code, out = await remote.dcremote(host, "info", *([folder] if folder else []), timeout=20)
        try:
            got = json.loads(out) if code == 0 else {}
        except ValueError:
            got = {}
        return got if isinstance(got, dict) else {}

    async def plan(self, st, kind, target, folder=None):
        """(why not, {"host", "folder", "socket", "claude", "login"}): where a far move goes, looked at over there.
        Refused only on what is known (manager 11-20): no answer, no claude, no such folder, the same place."""
        if kind == "folder":
            return await self.plan_folder(st, target)
        host, why = self.machine(target)
        if why:
            return why, None
        if host == host_of(st):
            return say("same_machine", channel="", machine=target), None
        cwd = st.get("cwd") or ""
        src = host_of(st)
        old_home = (await self.info(src)).get("home") if src else os.path.expanduser("~")
        if host is None:  # the bot's machine
            home = os.path.expanduser("~")
            tries = [os.path.abspath(os.path.expanduser(folder))] if folder else \
                [cwd] + ([home + cwd[len(old_home):]] if old_home and (cwd + "/").startswith(old_home.rstrip("/") + "/") else [])
            found = next((t for t in tries if t and self.here_dir(t)), None)
            binary = cc_health.binary()
            if not (os.path.isfile(binary) or shutil.which(binary)):
                return say("no_claude", machine=remote.this_machine()), None
            if not found:
                return say("no_folder", machine=remote.this_machine(), folder=folder or cwd), None
            return None, {"host": None, "folder": found, "socket": None, "claude": binary, "login": None}
        first = await self.info(host, folder or cwd)
        if not first:
            return say("unreachable", machine=host, why=remote.unreachable(host)), None
        found, home = first.get("folder"), first.get("home") or ""
        if not found and not folder and old_home and home and (cwd + "/").startswith(old_home.rstrip("/") + "/"):
            found = (await self.info(host, home.rstrip("/") + cwd[len(old_home.rstrip("/")):])).get("folder")
        if not first.get("claude"):
            return say("no_claude", machine=host), None
        if not found:
            return say("no_folder", machine=host, folder=folder or cwd), None
        return None, {"host": host, "folder": found, "socket": first.get("socket"), "claude": first["claude"],
                      "login": first.get("login")}  # (None from an older dcremote: not said either way)

    async def plan_folder(self, st, target):
        host = host_of(st)
        if host:
            got = await self.info(host, target)
            if not got:
                return say("unreachable", machine=host, why=remote.unreachable(host)), None
            found = got.get("folder")
        else:
            found = os.path.abspath(os.path.expanduser(target))
            found = found if self.here_dir(found) else None
        if not found:
            return say("no_folder", machine=host or remote.this_machine(), folder=target), None
        if found == st.get("cwd"):
            return say("same_folder", folder=found), None
        return None, {"host": host, "folder": found, "socket": None, "claude": None, "login": None}

    async def carry(self, src, src_folder, dst, dst_folder, sid):
        """The conversation's record copied from (src, src_folder) to (dst, dst_folder): (why not, what was written)."""
        loop = asyncio.get_running_loop()
        try:
            if src:
                err = []
                code, out = await remote.dcremote(src, "transcript-out", src_folder, sid, timeout=COPY_SECS, err=err)
                if code != 0:
                    return self.remote_why(src, out, err), None
                data = out.strip()
            else:
                data = await loop.run_in_executor(None, transcripts.pack, src_folder, sid)
            if dst:
                err = []
                code, out = await remote.dcremote(dst, "transcript-in", dst_folder, sid, input=data, timeout=COPY_SECS, err=err)
                if code != 0:
                    return self.remote_why(dst, out, err), None
                return None, json.loads(out)
            return None, await loop.run_in_executor(None, transcripts.unpack, data, dst_folder, sid)
        except transcripts.Refused as e:
            return str(e), None
        except ValueError as e:
            return say("copy_broke", what=type(e).__name__), None

    async def too_big(self, src, folder, sid):
        """Why the record is not carried (its size against the bot machine's free memory), or None. A size not known
        (no record, an older engine there) is left to the copy itself, which says so."""
        if src:
            code, out = await remote.dcremote(src, "transcript-size", folder, sid, timeout=60)
            try:
                n = json.loads(out).get("bytes") if code == 0 else None
            except (ValueError, AttributeError):
                n = None
        else:
            try:
                n = await asyncio.get_running_loop().run_in_executor(None, transcripts.size, folder, sid)
            except transcripts.Refused:
                n = None
        if not isinstance(n, int) or n <= BIG:
            return None
        free = transcripts.mem_available()
        if free is None or free >= MEM_TIMES * n:
            return None
        return say("too_big", mb=n // (1024 * 1024), free=free // (1024 * 1024))

    def remote_why(self, host, out, err):
        try:
            said = json.loads(out).get("error")
        except (ValueError, AttributeError):
            said = None
        if said:
            return said
        if any("不認得" in e for e in err or []):
            return say("old_engine", machine=host)
        return say("unreachable", machine=host, why=" / ".join(err or []) or remote.unreachable(host))

    async def move_far(self, ch, st, job):
        """/move machine, /move folder: end the old Claude, carry the record, take the conversation up in the new
        place (or back where it was, its folder as it was). The card edited."""
        cid, name = str(ch.id), getattr(ch, "name", "?")
        card, kind = job.get("card"), job["kind"]
        rt = self.bot.tmux.rt(ch)
        self.running.add(cid)
        probed = PROBED.set({})  # (this move's own: another channel's move at the same time keeps its own)
        try:
            async with rt.lock:
                sid, old_cwd = st.get("session_id"), st.get("cwd")
                old = {k: st.get(k) for k in where_mod.PLACE_FIELDS}
                owner, src = owners(st), host_of(st)
                why, plan = await self.plan(st, kind, job["target"], job.get("folder"))
                if why:
                    return await self.stopped(ch, card, job, why)
                to = plan["host"]
                why = await self.too_big(src, old_cwd, sid)
                if why:  # (refused before anything is touched; #audit for the owner's own move too: manager 11-20)
                    if not job.get("by"):
                        await self.bot.audit(say("audit_too_big", channel=name, why=plain(why)))
                    return await self.stopped(ch, card, job, why)
                if kind == "machine" and to is None and await tmuxio.alive(tmuxio.session_name(name, ch.id)):
                    return await self.stopped(ch, card, job, say("bot_session_taken"))
                pane = await self.pane_of(st)
                how = await tmuxio.standing(pane if st.get("pane") else st.get("tmux")) if pane else "gone"
                if how == "unknown":  # (not known is not gone: #issues 29; #code-review-2 on af0c86cd)
                    return await self.stopped(ch, card, job, say("no_probe", machine=pane.get("host") or "?"))
                running = how == "alive"
                if running and pane.get("host") and await self.probe(pane) is None:
                    return await self.stopped(ch, card, job, say("no_probe", machine=pane["host"]))
                argv = await self.claude_line_at(pane) if running and owner else None
                if running:
                    await self.retext(card, say("step_far_ending"))
                    ended, why = await self.end_old(ch, st, rt, pane, sid, owner)
                    if not ended and why in (say("not_idle"), say("no_shell"), say("daemon")):
                        return await self.stopped(ch, card, job, why)
                    if not ended:
                        unknown = why in (say("exit_not_sent", machine=pane.get("host") or remote.this_machine()),
                                          say("exit_unknown", machine=pane.get("host"), secs=EXIT_SECS))
                        tail = "" if st.get("move_background") or say("old_kept") in why or unknown else "\n" + say("old_kept")
                        # (not known whether it ended: "舊的沒動，照常在原本的位置" under it said otherwise, drill B)
                        if job.get("by"):
                            await self.bot.audit(say("audit_failed", channel=name, why=plain(why), back=plain(tail) or "-"))
                        return await self.retext(card, say("failed", why=why) + tail)
                if owner and users_options(argv) is not None:
                    st["user_options"] = users_options(argv)
                await self.retext(card, say("step_far_copying"))
                why, wrote = await self.carry(src, old_cwd, to, plan["folder"], sid)
                ok = False
                if why:
                    why = say("not_copied", why=why)
                else:
                    await self.retext(card, say("step_far_opening"))
                    st["cwd"] = plan["folder"]
                    ok, why = await self.open_far(ch, st, rt, kind, plan, sid, owner, pane, argv)
                if ok is None:  # (typed into the owner's pane and not ended: left as it is, nothing more typed)
                    st["cwd"] = old_cwd  # (their shell is still there: the line ran in a subshell. Kept as the new
                    self.bot.save()  # folder, a /move folder back to it was refused as "already there": drill B)
                    await self.bot.audit(say("audit_failed", channel=name, why=plain(why), back="-"))
                    return await self.retext(card, say("failed", why=why))
                if ok:  # (the old pane unbound by end_old already)
                    if plan["folder"] != old_cwd:
                        st["note_next"] = ((st.get("note_next") or "") + say("folder_note", old=old_cwd, new=plan["folder"])).strip()
                    self.bot.save()
                    there = self.far_label(kind, plan)
                    await self.bot.audit(say("audit", channel=name, old=self.far_label(kind, {"host": src, "folder": old_cwd}),
                                             new=there, by=say("audit_by", by=job["by"], why=job.get("why") or "")
                                             if job.get("by") else say("audit_owner")))
                    done = say("far_done", channel=name, where=there)
                    if (wrote or {}).get("kept"):
                        done += "\n" + say("kept_before", name=wrote["kept"])
                    return await self.retext(card, done)
                # not taken up there: back where it was, in its own folder
                st["cwd"] = old_cwd
                await self.retext(card, say("step_back"))
                if running:
                    back = await self.reopen(ch, st, rt, old, sid, pane, owner, argv)
                else:
                    where_mod.put(st, name, **old)
                    back = None
                self.bot.save()
                tail = say("back_ok") if back is None or back is True else \
                    say("back_failed_owner", why=back, line=user_line(argv, sid, st.get("user_options"))[0]) if owner else \
                    say("back_failed", why=back)
                if back is True and owner and not user_line(argv, sid, st.get("user_options"))[1]:
                    tail += "\n" + say("back_default")
                await self.bot.audit(say("audit_failed", channel=name, why=plain(why), back=plain(tail)))
                return await self.retext(card, say("failed", why=why) + "\n" + tail)
        finally:
            self.running.discard(cid)
            PROBED.reset(probed)

    def far_label(self, kind, plan):
        machine = plan.get("host") or remote.this_machine()
        return say("far_where", machine=machine, folder=plan.get("folder") or "?")

    async def open_far(self, ch, st, rt, kind, plan, sid, owner, pane, argv):
        """The conversation taken up where a far move goes (st["cwd"] is the new folder already). (True, None) or
        (False, why) with what was opened closed again."""
        name = getattr(ch, "name", "?")
        if kind == "machine":
            if plan["host"] is None:
                where_mod.put(st, name, tmux=tmuxio.session_name(name, ch.id), home=None, pane=None, pane_session=None)
                return await self.take_up(ch, st, rt, sid)
            return await self.take_up_users(ch, st, rt, plan["socket"], tmuxio.session_name(name, ch.id), sid,
                                            host=plan["host"], program=plan["claude"])
        if owner:  # the same pane: its shell runs the line in the new folder, in a subshell (manager 11-20)
            return await self.take_up_typed(ch, st, rt, pane, subshell(plan["folder"], user_line(argv, sid, st.get("user_options"))[0]), sid)
        if st.get("home"):
            where_mod.put(st, name, pane=None)
        return await self.take_up(ch, st, rt, sid)

    async def take_up_typed(self, ch, st, rt, pane, line, sid):
        """`line` typed into the owner's shell in `pane`; up with conversation `sid`, or Ctrl-C'd back to the shell.
        (True, None) / (False, why)."""
        await routes.bind_any(pane, str(ch.id))
        rt.ready.clear()
        rt.ready_sid = None
        st["busy"] = False
        rt.opening = dict(pane)
        try:
            await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", line, **tmuxio.on(pane))
            await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane))
            ok, why = await self.came_up(ch, st, rt, tmuxio.pane(pane), sid, st.get("pane_session"))
            if ok:
                return True, None
            # Its end is not the conversation's: the pane let go of first (it was said as "the conversation ended in
            # the terminal" right under the card that said it was taken up again: drill B), bound again by reopen().
            await routes.unbind_any(str(ch.id), pane.get("host"), pane)
            if not await self.end_typed(pane):  # still there: nothing typed into it, not taken up back where it was
                await routes.bind_any(pane, str(ch.id))  # (the channel's pane as before, for the owner to look at)
                return None, say("typed_not_ended", why=why, secs=EXIT_SECS)
            return False, why
        finally:
            rt.opening = None

    async def end_typed(self, pane):
        """A Claude typed into the owner's shell that is not the conversation: ended (/exit at its box, else
        Ctrl-C twice), their shell left as it was. True once the pane is back at the shell (asked again each time:
        tmuxio.standing(), nothing kept from before), within EXIT_SECS; False else (#code-review on af0c86cd)."""
        code, out = await tmuxio.tmux("capture-pane", "-p", "-t", pane["id"], **tmuxio.on(pane))
        if code == 0 and idle(out):
            await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", "/exit", **tmuxio.on(pane))
            await asyncio.sleep(0.5)
            await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane))
        else:
            await tmuxio.tmux("send-keys", "-t", pane["id"], "C-c", **tmuxio.on(pane))
            await asyncio.sleep(0.3)
            await tmuxio.tmux("send-keys", "-t", pane["id"], "C-c", **tmuxio.on(pane))
        t0 = time.time()
        while time.time() - t0 < EXIT_SECS:
            await asyncio.sleep(0.5)
            if await tmuxio.standing(pane) == "gone":
                return True
        return False

    async def end_old(self, ch, st, rt, pane, sid, owner):
        """/exit typed into the old Claude, its end waited on. (True, None) when it ended; (False, why) else, and then
        nothing more is done (never a second Claude on the conversation)."""
        view = await tmuxio.tmux("capture-pane", "-p", "-t", pane["id"], **tmuxio.on(pane))
        view = view[1] if view[0] == 0 else ""
        if not idle(view):
            return False, say("not_idle")
        if owner and not await self.shell_under(pane):
            return False, say("no_shell")
        if await self.daemon_at(pane):  # (/exit then only sends it to the background: #code-review on 400f5a9d)
            return False, say("daemon")
        if owner or pane.get("host"):  # (its end is not said as "the conversation ended there"; another machine's
            await routes.unbind_any(str(ch.id), pane.get("host"), pane)  # window of the bot's reports by routes too)
        from .tmux_bridge import CLOSED_KEPT
        closed = list(getattr(rt, "closed", []) or [])
        rt.closed = (closed + [dict(pane, at=time.time(), session=sid)])[-CLOSED_KEPT:]
        sent = (await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", "/exit", **tmuxio.on(pane)))[0] == 0
        if sent:
            await asyncio.sleep(0.5)
            sent = (await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane)))[0] == 0
        if not sent:  # (another machine's line dropped: /exit may not have got there, the old Claude still at its box)
            rt.closed = closed
            if owner or pane.get("host"):
                await routes.bind_any(pane, str(ch.id))
            return False, say("exit_not_sent", machine=pane.get("host") or remote.this_machine())
        t = pane if st.get("pane") else st.get("tmux")
        seen, how = "", None
        t0 = time.time()
        while time.time() - t0 < EXIT_SECS:
            await asyncio.sleep(0.5)
            code, out = await tmuxio.tmux("capture-pane", "-p", "-t", pane["id"], **tmuxio.on(pane))
            if code == 0:
                seen = out
            if BACKGROUND.search(seen or ""):  # (the process may stay on that screen: said at once, Esc brings it back)
                return await self.back_from_background(ch, st, pane, t, sid, owner)
            how = await tmuxio.standing(t)  # (another machine not answering: not known, waited on, never taken for ended)
            if how == "gone" and not self.pid_alive(pane, owner):
                break
        else:
            if owner or pane.get("host"):
                await routes.bind_any(pane, str(ch.id))
            if how == "unknown":
                return False, say("exit_unknown", machine=pane.get("host"), secs=EXIT_SECS)
            return False, say("not_ended", secs=EXIT_SECS)
        if BACKGROUND.search(seen or ""):  # (gone from the pane, the line seen: the conversation runs on elsewhere)
            return await self.back_from_background(ch, st, pane, t, sid, owner)
        return True, None

    async def back_from_background(self, ch, st, pane, t, sid, owner):
        """/exit only sent the conversation to the background: Esc, and the old Claude back at its box is said as
        kept. Not back: the conversation runs on in the background, so the channel opens nothing on it until it is
        stopped (never a second Claude on it); said as it is."""
        await tmuxio.tmux("send-keys", "-t", pane["id"], "Escape", **tmuxio.on(pane))
        t0 = time.time()
        while time.time() - t0 < BACK_SECS:
            await asyncio.sleep(0.5)
            code, out = await tmuxio.tmux("capture-pane", "-p", "-t", pane["id"], **tmuxio.on(pane))
            if code == 0 and idle(out) and await tmuxio.alive(t):
                if owner or pane.get("host"):
                    await routes.bind_any(pane, str(ch.id))
                return False, say("backgrounded") + "\n" + say("old_kept")
        st["move_background"] = {"session": sid, "at": int(time.time())}
        self.bot.save()
        if owner or pane.get("host"):
            await routes.bind_any(pane, str(ch.id))
        return False, say("in_background", sid=sid)

    async def probe(self, pane):
        """Another machine's answer about a pane's processes (dcremote move-probe: bot/transcripts.py probe()), asked
        once per move (kept in PROBED, the move's own); None when that machine did not answer or its engine is too old."""
        key = (pane.get("host"), pane.get("id"), str(pane.get("pid")))
        cache = PROBED.get()
        if cache is not None and key in cache:
            return cache[key]
        code, out = await remote.dcremote(pane["host"], "move-probe", str(pane.get("pid") or ""), timeout=20)
        try:
            got = json.loads(out) if code == 0 else None
        except ValueError:
            got = None
        got = got if isinstance(got, dict) else None
        if cache is not None:
            cache[key] = got
        return got

    async def daemon_at(self, pane):
        """Is a `claude daemon` running on the machine of `pane`?"""
        if pane.get("host"):
            return bool((await self.probe(pane) or {}).get("daemon"))
        return self.daemon_running()

    async def claude_line_at(self, pane):
        """claude_line() for a pane of any machine."""
        if pane and pane.get("host"):
            argv = (await self.probe(pane) or {}).get("argv")
            return list(argv) if isinstance(argv, list) else None
        return self.claude_line(pane)

    def daemon_running(self):
        """Is a `claude daemon` of this user running here (then /exit only sends a conversation to the background)?"""
        me = os.getuid()
        for pid, row in cc_process.table().items():
            words = row[1]
            if cc_process.claude_argv(words) and "daemon" in words[1:4]:
                try:
                    if os.stat("/proc/%d" % pid).st_uid != me:
                        continue
                except OSError:
                    pass
                return True
        return False

    def claude_line(self, pane):
        """The words of the Claude running under the owner's pane (its flags: permission mode, --add-dir, --model…),
        None when not told."""
        try:
            procs = cc_process.table()
            root = int(pane.get("pid"))
        except (TypeError, ValueError):
            return None
        todo, seen = [root], set()
        while todo:
            pid = todo.pop()
            if pid in seen:
                continue
            seen.add(pid)
            row = procs.get(pid)
            if row and pid != root and cc_process.claude_argv(row[1]):
                return list(row[1])
            todo.extend(p for p, r in procs.items() if r[0] == pid)
        return None

    def pid_alive(self, pane, owner):
        """The bot's own window: its process is Claude's; still there is not ended. The owner's: the shell stays.
        Another machine's: tmuxio.alive() has asked that machine already."""
        if owner or pane.get("host"):
            return False
        try:
            os.kill(int(pane.get("pid")), 0)
            return True
        except (OSError, ValueError, TypeError):
            return False

    async def open_at(self, ch, st, rt, to, session, sid):
        """The conversation taken up in the new place (its fields written first, so the note Claude gets says it).
        (True, None), or (False, why) with whatever was opened closed again."""
        name = getattr(ch, "name", "?")
        bot_path, user_path = self.paths()
        if to == "bot":
            where_mod.put(st, name, tmux=tmuxio.session_name(name, ch.id), home=None, pane=None, pane_session=None)
            return await self.take_up(ch, st, rt, sid)
        return await self.take_up_users(ch, st, rt, user_path, session, sid)

    async def take_up_users(self, ch, st, rt, socket, session, sid, host=None, program=None):
        """In the owner's tmux the conversation is started as the owner starts Claude (manager 11-20, from the 2c1
        drill: the bot's command line there was a Claude with the bot's settings that skipped every permission
        question, in the owner's own terminal): a window running the owner's shell, which runs the line user_line()
        makes (the owner's options kept when the channel left their tmux, else the default permission mode; never
        the bot's --settings, never bypassPermissions) and then stays as their shell when Claude ends. Run by the
        shell, not typed into one: a new shell may ask something first (zsh's first-run menu ate the typed line in
        the drill; an update prompt would too). The channel then has it as an attached pane (the owner's, reported
        by the route table), as /attach makes one. (True, None) / (False, why), that window closed again."""
        name = getattr(ch, "name", None)
        why = []
        line = user_line(None, sid, st.get("user_options"), program=program or cc_health.binary())[0]
        if host:  # (that machine's shell is not known here: sh runs the line, then the owner's login shell there)
            run = ["/bin/sh", "-c", '%s; exec "${SHELL:-/bin/sh}" -l' % line]
        else:
            shell = login_shell()
            run = [shell, "-c", "%s; exec %s -l" % (line, shlex.quote(shell))]
        addr = await tmuxio.open_window(socket, session, "", st["cwd"], run, host=host, why=why)
        if not addr:
            return False, say("not_opened", why=(why or ["?"])[-1])
        where_mod.put(st, name, tmux=None, home=None, pane=addr, pane_session=session)
        await routes.bind_any(addr, str(ch.id))
        rt.ready.clear()
        rt.ready_sid = None
        st["busy"] = False
        rt.opening = addr
        pane = tmuxio.pane(addr)
        try:
            ok, why = await self.came_up(ch, st, rt, pane, sid, session)
            if ok:
                return True, None
            await routes.unbind_any(str(ch.id), host, addr)
            await pane.close()
            return False, why
        finally:
            rt.opening = None

    async def take_up(self, ch, st, rt, sid):
        """`claude --resume <sid>` where the channel's fields say (the bot's own session, or a window in `home`); up at
        an input box and with that id, or closed again. (True, None) / (False, why)."""
        bridge = self.bot.tmux
        how = await bridge.chosen_for(st)
        host = None if st.get("tmux") else (st.get("home") or {}).get("host")
        dirs = await attachments.folders(host, ch.id)
        argv = bridge.argv(ch, st, sid, dirs, how, claude=await remote.claude_on(host) if host else None)
        rt.ready.clear()
        rt.ready_sid = None
        st["busy"] = False
        why, pane = [], None
        rt.opening = True
        try:
            if st.get("tmux"):
                if not await tmuxio.start(st["tmux"], st["cwd"], argv, bridge.env(ch)):
                    return False, say("not_opened", why="tmux new-session")
                pane = tmuxio.pane(st["tmux"])
            else:
                home = st["home"]
                from .tmux_bridge import RESUME_ENV
                env = dict(RESUME_ENV, DCBOT_CHANNEL=str(ch.id)) if host else bridge.env(ch)  # (as pane_again() opens one)
                addr = await tmuxio.open_window(home["socket"], home["session"], home.get("window", ""), st["cwd"], argv,
                                                env, host=host, why=why)
                if not addr:
                    return False, say("not_opened", why=(why or ["?"])[-1])
                rt.opening = addr
                if host:  # (its hooks report through that machine's route table)
                    await routes.bind_any(addr, str(ch.id))
                pane = tmuxio.pane(addr)
            ok, words = await self.came_up(ch, st, rt, pane, sid, (st.get("home") or {}).get("session"))
            if not ok:
                if host and isinstance(rt.opening, dict):
                    await routes.unbind_any(str(ch.id), host, rt.opening)
                await pane.close()
            return ok, words
        finally:
            rt.opening = None

    async def came_up(self, ch, st, rt, pane, sid, session):
        """The Claude just started in `pane` is up at its input box with conversation `sid` (from its SessionStart,
        or its process record): its place written, (True, None). Else (False, why); closing it is the caller's."""
        bridge = self.bot.tmux
        up = await bridge.wait_ready(pane, rt, ask=lambda choice, p=pane: bridge.startup.post(ch, p, choice))
        got = None
        if up:
            t0 = time.time()
            while rt.ready_sid is None and time.time() - t0 < SID_SECS:
                await asyncio.sleep(0.5)
            got = rt.ready_sid
            if got is None and not st.get("tmux"):  # (no SessionStart yet: its process record says)
                try:
                    got = await self.bot.pane_session_id(dict(rt.opening, session=session, cwd=st["cwd"]))
                except Exception:
                    traceback.print_exc()
        if up and got == sid:
            if st.get("tmux"):
                await tmuxio.settled(st["tmux"])
            else:
                where_mod.put(st, getattr(ch, "name", None), pane=rt.opening)
            from .tmux_bridge import opened
            opened(st, rt)
            return True, None
        return False, (say("wrong_conversation", got=(got or "?")[:8]) if up and got else
                       say("no_id", secs=SID_SECS) if up else (plain(getattr(rt, "ready_why", None) or "").rstrip("。") or say("not_up")))
        # (wait_ready()'s why is small print, a line of its own: "❌ 沒搬成：-# 等了 90 秒…。；…" in drill B)

    async def reopen(self, ch, st, rt, old, sid, pane, owner, argv=None):
        """Back where it was: its fields as they were, the conversation taken up there again. True, or why not."""
        where_mod.put(st, getattr(ch, "name", None), **old)
        if owner:  # the owner's shell: the resume line typed into it
            await routes.bind_any(pane, str(ch.id))
            await tmuxio.tmux("send-keys", "-t", pane["id"], "-l", user_line(argv, sid, st.get("user_options"))[0], **tmuxio.on(pane))
            await tmuxio.tmux("send-keys", "-t", pane["id"], "Enter", **tmuxio.on(pane))
            rt.ready.clear()
            ok = await self.bot.tmux.wait_ready(tmuxio.pane(pane), rt)
            return True if ok else say("not_up")
        if old.get("home"):
            where_mod.put(st, getattr(ch, "name", None), pane=None)
        ok, why = await self.take_up(ch, st, rt, sid)
        if not ok and old.get("home"):
            where_mod.put(st, getattr(ch, "name", None), **old)  # (its record as before: the next message opens it)
        return True if ok else why


class MoveModal(discord.ui.Modal):
    """〔搬到…〕: the kind and where (as /move takes them)."""
    def __init__(self, mover, ch):
        super().__init__(title=say("modal_title", channel=ch.name)[:45], timeout=900)
        self.mover, self.ch = mover, ch
        self.kind = discord.ui.TextInput(label=say("modal_kind"), default="tmux", max_length=10)
        self.target = discord.ui.TextInput(label=say("modal_target"), placeholder=say("modal_target_hint"), max_length=80)
        self.add_item(self.kind)
        self.add_item(self.target)

    async def on_submit(self, interaction):
        await self.mover.from_modal(interaction, self.ch, str(self.kind.value), str(self.target.value))
