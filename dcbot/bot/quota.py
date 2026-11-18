"""A claude.ai usage limit, and Claude Code's own wait for its reset (C-99a).

Since 2.1.234 Claude Code waits in the open session when a usage limit stops a turn and continues on its own at the
reset (its setting autoContinueAtUsageLimit, on by default). The bot says so instead of 「這一輪中斷了」 with a
「再試一次」 button: pressing that typed a prompt, and a prompt ends the wait (and mostly hits the limit again).

- A turn that fails with `rate_limit`: the pane's screen is read (a task of its own: the pump waits on no machine)
  at LOOKS (2, 5, 10, 15 s) until Claude Code's lines under the box tell that it waits (connector screen.quota_wait()). Waiting: one message
  「⏸️ 用量達到上限…」, no button, kept in the channel's state as `blocked` (bot/blocked.py, kind usage_limit). Not waiting (its
  setting off, a reset more than 24 hours out, the screen not read): the failure as before (C-99b: the bot's timer),
  unless the session moved on while the screen was read (said then, it would come after newer replies).
- QUOTA_RESUMED: that message becomes 「▶️ 用量重置了…」. QUOTA_STALE: the owner is told to press Enter (C-99b
  presses it). QUOTA_STOPPED: said with 「再試一次」. Each said in #audit in one line.
- While that holds (WAIT_HOLD from `since`: Claude Code waits 24 hours at most), reap() leaves the window alone:
  closing Claude Code ends its wait for good.
- Known limit (manager 2026-10-26): somebody types between the failure and the look that sees the wait; the typing
  ended the wait, yet 「⏸️」 is posted and `blocked` written again, until the next event of the turn clears it.
- Every such event saves the rows under the input box (the screen's own lines, no conversation), and the event
  without what Claude said, masked, for samples
  (state/samples/usage-limit/, the last SAMPLES_KEPT).
- "when" is the time on the screen, in the clock of that machine: on this machine it is shown as Discord's time
  mark (<t:…:t>, each reader's own time zone); a pane on another machine shows it as written.
"""
import asyncio
import datetime
import json
import os
import re
import time
import traceback

import discord

from altention.connectors.claude_code import hooks as cc_hooks, screen
from . import blocked, config, deliver, masking, messages, tmuxio
from .util import target

WAIT_HOLD = 25 * 3600            # how long after the limit a channel counts as waiting (Claude Code: 24 h at most)
LOOKS = (2, 5, 10, 15)           # seconds after the failed turn the screen is read, until the wait shows (manager
# 2026-10-26: when its lines are drawn after the failure was not measured; at 15 s with none, it does not wait)
SAMPLES_KEPT = 50
CLOCK = re.compile(r"^(?:at\s+)?(\d{1,2})(?::(\d{2}))?\s*(am|pm)$", re.I)



def say(key, /, **data):
    return messages.words("quota", key, data)


def clock_at(text, now=None):
    """The Unix time of a clock time as Claude Code writes it ("3:45pm", "at 5am"), the next one after `now` in this
    machine's time zone; None for anything else (a date, "when it resets")."""
    m = CLOCK.match((text or "").strip())
    if not m:
        return None
    hour, minute, half = int(m.group(1)), int(m.group(2) or 0), m.group(3).lower()
    if not (1 <= hour <= 12 and minute < 60):
        return None
    hour = hour % 12 + (12 if half == "pm" else 0)
    base = datetime.datetime.fromtimestamp(time.time() if now is None else now).astimezone()
    at = base.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if at <= base:
        at += datetime.timedelta(days=1)
    return int(at.timestamp())


def shown_when(when, here, now=None):
    """`when` as the owner sees it: Discord's time mark when it is a clock time of this machine, else as written."""
    at = clock_at(when, now) if here else None
    return "<t:%d:t>" % at if at else (when[3:] if when.startswith("at ") else when)


def holding(st, now=None):
    """Is the channel in a usage-limit wait (so its window is not closed for being idle)?"""
    b = blocked.of(st, "usage_limit")
    since = b.get("since") if b and (b.get("msg") or b.get("until")) else None  # (one Claude Code was seen to wait
    # for: what is held otherwise is nobody's wait, manager 2026-10-26)
    return isinstance(since, (int, float)) and (time.time() if now is None else now) - since < WAIT_HOLD


def waits(view):
    """Does Claude Code's wait for the reset show on this screen?"""
    wait = screen.quota_wait(view) if view else None
    return bool(wait and wait["phase"] in ("armed", "soon"))


def samples_dir():
    return os.path.join(config.STATE, "samples", "usage-limit")


def masked(value):
    """Every text in a JSON-like value through masking.mask()."""
    if isinstance(value, str):
        return masking.mask(value)[0]
    if isinstance(value, dict):
        return {k: masked(v) for k, v in value.items()}
    if isinstance(value, list):
        return [masked(v) for v in value]
    return value


def bottom(view, coloured):
    """Only the rows under the input box, of the plain and the coloured screen: never the conversation above it."""
    rows = screen.under_box(view or "").rstrip("\n").split("\n")
    n = len(rows) if any(r.strip() for r in rows) else 0
    lines = (coloured or "").rstrip("\n").split("\n")
    return "\n".join(rows[-n:]) if n else "", "\n".join(lines[-n:]) if n else ""


def save_sample(ev, view, coloured, now=None):
    """The rows of the screen under the input box (Claude Code's own: no conversation) and the event (without what
    Claude said, nor its transcript's path), masked, as one sample file; only the last SAMPLES_KEPT stay. Never
    raises."""
    try:
        now = time.time() if now is None else now
        folder = samples_dir()
        os.makedirs(folder, exist_ok=True)
        view, coloured = bottom(view, coloured)
        keep = {k: v for k, v in ev.items() if k not in ("texts", "transcript_path", "last_assistant_message")}
        if ev.get("dc_kind") == "turn_failed":  # the error as the screen shows it: that one is no conversation
            keep["last_assistant_message"] = ev.get("last_assistant_message")
        name = "%s.%06d-%s-%s.json" % (time.strftime("%Y%m%d-%H%M%S", time.localtime(now)), int(now % 1 * 1e6),
                                       ev.get("dc_kind"), ev.get("channel"))  # (in the order written)
        with open(os.path.join(folder, name), "w", encoding="utf-8") as f:
            json.dump({"at": now, "event": masked(keep),
                       "screen": masked(view or ""), "colour": masked(coloured or "")}, f, ensure_ascii=False, indent=1)
        for old in sorted(n for n in os.listdir(folder) if n.endswith(".json"))[:-SAMPLES_KEPT]:
            os.remove(os.path.join(folder, old))
    except Exception as e:
        print("[quota] a sample was not saved (%s)" % type(e).__name__, flush=True)


class Quota(object):
    def __init__(self, bridge):
        self.bridge = bridge
        self.working = set()  # the tasks at work (tests wait for them)
        self.moves = {}  # channel id: how often its session moved on (a failed turn's screen is read meanwhile)

    @property
    def host(self):
        return self.bridge.host

    async def screens(self, st):
        """(raw screen, coloured screen) of the channel's pane, ("", "") when it cannot be read."""
        t = target(st)
        if not t:
            return "", ""
        try:
            coloured = await tmuxio.color_screen(t)  # (its text, and the pane's width)
            return (await tmuxio.raw_screen(t)) or "", (coloured[0] if isinstance(coloured, (list, tuple)) else coloured) or ""
        except Exception:
            return "", ""

    async def looked(self, st):
        """The screen after a failed turn: read at each of LOOKS until Claude Code's wait shows on it; the last read."""
        waited, view, coloured = 0, "", ""
        for at in LOOKS:
            await asyncio.sleep(at - waited)
            waited = at
            view, coloured = await self.screens(st)
            if waits(view):
                break
        return view, coloured

    def moved_on(self, ch, st, ev):
        """The session moved on (an input, a tool call, a turn's end): a ⏸️ message of an earlier wait is no more to
        be edited (#code-review on 1f3f11d), and a failure whose screen is still being read is no more to be said
        (#code-review-2 on b2ad60e). Claude Code's own prompt at the reset is no moving on: its Notification comes
        just before it."""
        if ev.get("dc_kind") == cc_hooks.INPUT and str(ev.get("prompt") or "").startswith(cc_hooks.QUOTA_CONTINUE):
            return
        st.pop("limit_msg", None)
        self.moves[str(ch.id)] = self.moves.get(str(ch.id), 0) + 1

    def handle(self, ch, st, ev, turn):
        """The event of a usage limit: worked on in a task of its own (screens are read, over SSH for another
        machine). True when it was one of ours (a failed turn not of rate_limit is not)."""
        kind = ev.get("dc_kind")
        if kind == "turn_failed" and ev.get("dc_why") != "rate_limit":
            return False
        # The ⏸️ message to edit is taken now, before the screen is read: what comes meanwhile may move on
        mid = st.pop("limit_msg", None) if kind in ("quota_resumed", "quota_stopped") else None
        task = asyncio.ensure_future(self.act(ch, st, ev, turn, mid, self.moves.get(str(ch.id), 0)))
        self.working.add(task)
        task.add_done_callback(self.working.discard)
        return True

    async def act(self, ch, st, ev, turn, mid=None, moves=0):
        try:
            kind = ev.get("dc_kind")
            if kind == "turn_failed":
                view, coloured = await self.looked(st)
            else:
                view, coloured = await self.screens(st)
            save_sample(ev, view, coloured)
            if kind == "turn_failed" and self.moves.get(str(ch.id), 0) != moves and not waits(view):
                return  # not waiting, and it moved on while the screen was read (up to 15 s): 「中斷了」 would come
                # after newer replies, its 「再試一次」 send the old prompt again (#code-review-2 on b2ad60e)
            await getattr(self, kind)(ch, st, ev, turn, view, mid)
            self.host.save()
        except Exception:
            traceback.print_exc()

    async def turn_failed(self, ch, st, ev, turn, view, mid=None):
        wait = screen.quota_wait(view) if view else None
        machine = self.bridge.machine_of(st)
        here = machine is None
        if not wait or wait["phase"] not in ("armed", "soon"):
            st.pop("limit_msg", None)  # (an earlier wait's message: not this one's to edit)
            return await self.bridge.failed(ch, ev, turn)  # Claude Code does not wait: the failure, as before (blocked:
            # api_error; C-99b: timed)
        when = wait["when"] or ("at " + wait["resets"] if wait["resets"] else "")
        shown = shown_when(when, here)  # (another machine's clock: as written)
        key = "armed" if shown else "armed_unknown"
        msg = await deliver.deliver(ch, "%s\n%s" % (say(key, when=shown), say("armed_note")),
                                    allowed_mentions=discord.AllowedMentions.none(), intent="status")
        blocked.put(st, "usage_limit", machine, until=clock_at(when) if here else None,
                    detail=ev.get("last_assistant_message"), msg=msg.id)
        await self.bridge.usage_limited(ch, shown or None)  # (C-103: once for the installation, this channel listed)
        st["limit_msg"] = msg.id  # kept apart: Claude Code's own prompt at the reset (an input) clears `blocked` while
        # the Notification that comes just before it is still being worked on (#code-review-2 on 9eedf30)
        await self.host.audit(say("audit_" + key, channel=getattr(ch, "name", "?"), when=shown))

    async def quota_resumed(self, ch, st, ev, turn, view, mid=None):
        # Only this wait's: another one, begun while this screen was read (slowly, over SSH), is kept for its own
        # reset (#code-review on b2ad60e)
        b = blocked.of(st, "usage_limit") or {}
        if mid is None or b.get("msg") in (None, mid):
            q = blocked.clear(st, "usage_limit") or {}
            mid = mid or q.get("msg")
            await self.bridge.usage_maybe_back()  # (C-103: the installation's line, when no channel waits any more)
        if mid:
            try:
                await deliver.deliver(ch, content=say("resumed"), intent="status", edit=mid)
            except Exception:
                await deliver.deliver(ch, say("resumed"), silent=True, intent="status")
        else:
            await deliver.deliver(ch, say("resumed"), silent=True, intent="status")
        await self.host.audit(say("audit_resumed", channel=getattr(ch, "name", "?")))

    async def quota_stale(self, ch, st, ev, turn, view, mid=None):
        b = blocked.of(st, "usage_limit")
        blocked.put(st, "usage_limit", b.get("machine") if b else None, detail=ev.get("message"),
                    msg=b.get("msg") if b else None)  # (no `until`: it has passed)
        await deliver.deliver(ch, say("stale"), intent="status")
        await self.host.audit(say("audit_stale", channel=getattr(ch, "name", "?")))

    async def quota_stopped(self, ch, st, ev, turn, view, mid=None):
        b = blocked.of(st, "usage_limit")  # (its message, taken by handle(), says when it would carry on: not edited)
        why = str(ev.get("message") or "").strip()[:500]
        blocked.put(st, "usage_limit", b.get("machine") if b else None, detail=why)  # held up still: for the owner now
        view_ = discord.ui.View(timeout=None)
        view_.add_item(discord.ui.Button(label="再試一次", custom_id="retry:%d" % ch.id, style=discord.ButtonStyle.primary))
        await deliver.deliver(ch, "%s\n%s" % (say("stopped", why="\n> " + why if why else ""), say("retry_note")), view=view_,
                              intent="status")
        view_.stop()
        await self.host.audit(say("audit_stopped", channel=getattr(ch, "name", "?"), why=why or "?"))
