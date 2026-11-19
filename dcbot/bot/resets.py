"""A new conversation for a channel, asked by a Claude: `altention dc post <channel> --reset --why "…" [--then "…"]`
(v0.6.1, manager 11-19). It goes the way the owner's !reset goes (new_conversation(): the channel's session closed,
the old conversation kept for !resume; the C-201 record follows with the next save), never a /clear typed in.

- Its own channel: the Claude asking is in the middle of a turn. The reset waits for that turn to end
  (st["reset_after"], kept over a restart; the bridge calls after_turn() at the turn's end), then the new conversation
  is told to read the handover (or gets the --then words).
- Another channel: only while it is idle (no turn, no background work, no subagent or workflow, nothing queued for
  it); otherwise refused with why. There is no force.

Each one is said in #audit (who, why) and in the channel ("🔄 由 #xxx 開了新對話：why")."""
import asyncio
import time
import traceback

from . import deliver, kinds, messages, sessions


async def new_conversation(bot, ch, st):
    """The owner's !reset: the session closed (background work in it ends too), the conversation kept for !resume."""
    await bot.tmux.close(ch)
    sessions.remember(st, st.pop("session_id", None))
    bot.save()


def busy_with(bot, ch, st):
    """What the channel's Claude is doing now, as words, or None when it is idle."""
    if st.get("busy"):
        return messages.reset("busy_turn")
    if st.get("bg"):
        return messages.reset("busy_bg", n=st["bg"])
    if st.get("subs"):
        return messages.reset("busy_subs", n=len(st["subs"]))
    rt = getattr(bot.tmux, "rts", {}).get(str(ch.id))
    if rt is not None and getattr(rt, "pending", None):
        return messages.reset("busy_queue", n=len(rt.pending))
    return None


def refused(bot, ch, st, own, paused):
    """Why --reset is not done in `ch`, or None."""
    if not st or kinds.channel_kind(st) != kinds.CONVERSATION:
        return messages.reset("not_claude", channel=ch.name)
    if st.get("pane") and not st.get("home"):  # the owner's own pane (as !reset: main.Bot.command)
        return messages.reset("attached", channel=ch.name)
    if paused:
        return messages.reset("paused", channel=ch.name, until=paused)
    if not own:
        what = busy_with(bot, ch, st)
        if what:
            return messages.reset("busy", channel=ch.name, what=what)
    return None


class Resets(object):
    def __init__(self, posts):
        self.posts, self.bot = posts, posts.bot

    async def ask(self, ch, sender_ch, sender, post, paused=None):
        """--reset for channel `ch` from `sender` (its channel `sender_ch`, or None). (ok, what the sender is told)."""
        st = self.bot.channels_state.get(str(ch.id)) or {}
        own = sender_ch is not None and sender_ch.id == ch.id
        why_not = refused(self.bot, ch, st, own, paused)
        if why_not:
            return False, why_not
        job = {"by": sender, "why": post.get("why") or "", "then": (post.get("then") or "").strip(), "own": own,
               "from_channel": post.get("from_channel"), "at": time.time()}
        if own and st.get("busy"):  # (the turn that asks: after it ends, the bridge's after_turn())
            rt = getattr(self.bot.tmux, "rts", {}).get(str(ch.id))
            job["turn"] = getattr(getattr(rt, "turn", None), "pid", None)  # (that turn's prompt_id; #code-review on
            # 882c1062: tied to it, never done at the end of a later one)
            self.bot.chan(ch)["reset_after"] = job
            self.bot.save()
            return True, messages.reset("later", channel=ch.name,
                                        then=messages.reset("later_then" if job["then"] else "later_default"))
        await self.run(ch, job)
        return True, messages.reset("done", channel=ch.name, then=messages.reset("done_then") if job["then"] else "")

    def after_turn(self, ch, st, pid=None):
        """A turn of `ch` ended (`pid`: its prompt_id): a reset its Claude asked for during it is done now, in a task
        of its own (the bridge's event handling goes on: the new conversation's receipt comes as an event). The task,
        or None. Another turn's end does nothing: new_turn() drops the reset when that one began."""
        job = st.get("reset_after")
        if not isinstance(job, dict) or (job.get("turn") and pid and job["turn"] != pid):
            return None
        st.pop("reset_after", None)
        self.bot.save()
        return asyncio.ensure_future(self.run_logged(ch, job))

    async def new_turn(self, ch, st, pid=None):
        """A turn begins in `ch` (UserPromptSubmit, `pid` its prompt_id): a reset asked during another turn is dropped.
        That turn did not end well: stopped with Esc (Claude Code sends no Stop then) or failed; done after this one, it
        would take away what the owner just began (#code-review on 882c1062)."""
        job = st.get("reset_after")
        if not isinstance(job, dict) or (job.get("turn") and pid and job["turn"] == pid):
            return
        st.pop("reset_after", None)
        self.bot.save()
        try:
            await deliver.deliver(ch, messages.reset("cancelled"), silent=True, intent="status")
            await self.bot.audit(messages.reset("audit_cancelled", channel=ch.name, by=job.get("by"), why=job.get("why") or ""))
        except Exception:
            traceback.print_exc()

    async def run_logged(self, ch, job):
        try:
            await self.run(ch, job)
        except Exception:
            traceback.print_exc()

    async def run(self, ch, job):
        st = self.bot.chan(ch)
        await new_conversation(self.bot, ch, st)
        why, then = job.get("why") or "", job.get("then") or ""
        line = messages.reset("channel_own", why=why) if job.get("own") else messages.reset("channel", by=job.get("by"), why=why)
        await deliver.deliver(ch, line, silent=True, intent="status")
        await self.bot.audit(messages.reset("audit", by=job.get("by"), channel=ch.name, why=why,
                                            then=messages.reset("audit_then", then=then) if then else ""))
        if job.get("own"):  # its new conversation carries on: the handover, or what --then says
            from .posts import Origin
            await self.bot.tmux.submit(Origin(ch, auto=True), then or messages.reset("own_default", why=why))
        elif then:  # another channel's Claude: as a post from the one that asked (it can answer back)
            fr = self.bot.get_channel(int(job["from_channel"])) if str(job.get("from_channel") or "").isdigit() else None
            await self.posts.give(ch, job.get("by"), fr, then, {"then": then})
