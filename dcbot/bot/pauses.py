"""C-184: a channel paused until a time, and the bot telling its Claude to carry on then.

`altention dc post <channel> --pause-until <time> --why "…"` (posts.py hands it here): the channel state gets `paused`
{by, by_ch, why, until, at, msg, card, held}; a ⏸️ card is posted there and handed to its Claude. Until then, posts
from other Claudes are kept in `held` (shown on Discord, not handed over), and --answer / --talk-continue, which would
set it working, are refused; the owner's own messages and buttons go through as ever. Each minute (the pump's chores)
a pause whose time has come is resumed in a task of its own: the ⏸️ card marked, a ▶️ card, the same words handed to
its Claude, then what was held, in order, each with where and when it came from. Each step is written once done, so a
failure or a restart redoes only what is left: its Claude is told at least once (again, marked 重送, when not known;
manager on 8738b973), and an item leaves `held` only once handed over. Paused again meanwhile, the new pause takes
what is still held. A bot down at that time does it at its first chores, saying how late. `paused` is in
standby.MATTERS: a standby that takes over resumes it too.

C-199: the owner speaking there hands what was held over right after that message (wake(); the pause goes on), and
the channel's own Claude can drop what is held once it has read it (`--drop-held --why`, drop())."""
import asyncio
import time
import traceback

from . import deliver, messages, remote

MOST_HELD = 50  # posts kept for a paused channel; one more is refused, its sender told
LATE = 120  # seconds past the time before the ▶️ card says how late it is
WAKE_WAIT = 1  # seconds between two looks of a resume() waiting for the owner's wake() to finish handing over (C-199)


def clock(ts):
    """An instant as the owner reads it (the instance's time zone: altention/dc/pausetime.py)."""
    try:
        remote.alt()  # (the engine on the path)
        from altention.dc import pausetime
        return pausetime.shown(ts)
    except Exception:
        traceback.print_exc()
        return time.strftime("%m-%d %H:%M", time.localtime(ts))


class Pauses(object):
    """`posts` is the bot's Posts (its bot, tell(), give())."""

    def __init__(self, posts):
        self.posts, self.bot = posts, posts.bot
        self.resuming = set()  # channel ids whose resume() is going
        self.giving = {}  # channel id -> the held item resume() is handing over right now (pause() leaves it to it)
        self.waking = set()  # channel ids whose wake() is handing what was held over (C-199)

    def of(self, channel_id):
        """The channel's pause while it holds (not yet resumed), or None."""
        p = (self.bot.channels_state.get(str(channel_id)) or {}).get("paused")
        return p if isinstance(p, dict) and p.get("until") and not p.get("resumed") else None

    async def pause(self, ch, sender_ch, sender, post):
        from .posts import listens
        st = self.bot.channels_state.get(str(ch.id)) or {}
        if not listens(st):
            return False, messages.pause("not_conversation", channel=ch.name)
        until, why, now = int(post["pause"]), str(post.get("why") or "").strip(), time.time()
        old = self.of(ch.id)
        card = messages.pause("paused", until=clock(until), by=sender, why=why)
        msg = await deliver.deliver(ch, card, intent="card", key="pause")
        st = self.bot.chan(ch)
        prev = st.get("paused") if isinstance(st.get("paused"), dict) else {}
        held = list(prev.get("held") or [])
        if prev.get("resumed"):  # paused again while the last resume still hands over what it kept: the rest is the
            busy = self.giving.get(str(ch.id))  # new pause's (manager on 8738b973), but for the one going in now
            held = [h for h in held if h is not busy]
            prev["held"] = [h for h in prev.get("held") or [] if h is busy]
        st["paused"] = {"by": sender, "by_ch": sender_ch.id if sender_ch is not None else None, "why": why, "until": until,
                        "at": now, "msg": msg.id, "card": card, "held": held}
        self.bot.save()
        self.posts.later(self.posts.tell(ch, card))  # (its queue and receipt: not waited on here)
        from .posts import vouched
        if old:
            data = dict(who=sender + vouched(post), channel=ch.name, until=clock(until), old=clock(old["until"]), old_by=old.get("by"), why=why)
            await self.bot.audit(messages.pause("audit_changed", **data))
            return True, messages.pause("said_changed", **data)
        await self.bot.audit(messages.pause("audit_paused", who=sender + vouched(post), channel=ch.name, until=clock(until), why=why))
        return True, messages.pause("said_paused", channel=ch.name, until=clock(until))

    async def unpause(self, ch, sender, post):
        p = self.of(ch.id)
        if p is None:
            return False, messages.pause("not_paused", channel=ch.name)
        why = str(post.get("why") or "").strip()
        from .posts import vouched
        await self.bot.audit(messages.pause("audit_unpaused", who=sender + vouched(post), channel=ch.name, why=why))
        held = len(p.get("held") or [])
        self.posts.later(self.resume(ch, early=(sender, why)))
        return True, messages.pause("said_unpaused", channel=ch.name, held=held)

    async def give_held(self, ch, h):
        """One held item handed to the channel's Claude, with where and when it came from (resume() and wake())."""
        key = str(ch.id)
        sender_ch = self.bot.get_channel(h["from_ch"]) if h.get("from_ch") else None
        line = messages.pause("held_line", at=clock(h.get("at") or time.time()), sender=h.get("sender"))
        self.giving[key] = h
        try:
            await self.posts.give(ch, h.get("sender") or "?", sender_ch, h.get("text") or "", {"now": h.get("now")},
                                  before=line, named=h.get("files") or [])
        finally:
            self.giving.pop(key, None)

    async def wake(self, ch):
        """C-199 (owner's choice, manager 11-17): the owner spoke in the paused channel and that message is handed over:
        what was held goes in right after it, in order, each item dropped once handed over (so the resume does not hand
        it over again). Only what was held when the owner spoke: what comes after is held as ever, and the pause goes
        on until its time. An item that fails stays first, for the resume or the owner's next message; said in #audit.
        Not while a resume (or another wake) is handing over: that one hands it all."""
        from .posts import listens
        key = str(ch.id)
        p = self.of(ch.id)
        if p is None or not p.get("held") or key in self.resuming or key in self.waking:
            return
        self.waking.add(key)
        given = 0
        try:
            st = self.bot.channels_state.get(key) or {}
            for h in list(p["held"]):  # (a snapshot: posts held after the owner spoke stay held)
                if self.of(ch.id) is not p or not listens(st) or not p.get("held") or p["held"][0] is not h:
                    break
                try:
                    await self.give_held(ch, h)
                except Exception as e:
                    traceback.print_exc()
                    await self.bot.audit(messages.pause("audit_woken_failed", channel=ch.name, why=e, left=len(p["held"])))
                    break
                if p["held"] and p["held"][0] is h:
                    p["held"].pop(0)
                given += 1
                self.bot.save()
        finally:
            self.waking.discard(key)
        if given:
            left = len(p.get("held") or [])
            await self.bot.audit(messages.pause("audit_woken", channel=ch.name, n=given, until=clock(p["until"]),
                                                left=messages.pause("woken_left", left=left) if left else ""))

    async def drop(self, ch, sender_ch, sender, post):
        """--drop-held --why (C-199): what the paused channel holds dropped, for a Claude that read it itself; only that
        channel's own Claude, looked up (manager 11-17: no other channel's)."""
        from .posts import vouched
        if not post.get("verified") or sender_ch is None or (getattr(sender_ch, "parent_id", None) or sender_ch.id) != ch.id:
            await self.bot.audit(messages.pause("audit_drop_refused", who=sender + vouched(post), channel=ch.name,
                                                why=str(post.get("why") or "").strip()))  # (another's held posts: said, manager 11-17)
            return False, messages.pause("drop_refused", channel=ch.name)
        p = self.of(ch.id)
        if p is None:
            return False, messages.pause("not_paused", channel=ch.name)
        if not p.get("held"):
            return False, messages.pause("drop_none", channel=ch.name)
        busy = self.giving.get(str(ch.id))  # (the one going in now is left to its hand-over)
        n = len([h for h in p["held"] if h is not busy])
        p["held"] = [h for h in p["held"] if h is busy]
        self.bot.save()
        await self.bot.audit(messages.pause("audit_dropped", who=sender + vouched(post), channel=ch.name, n=n,
                                            why=str(post.get("why") or "").strip()))
        return True, messages.pause("said_dropped", channel=ch.name, n=n)

    def shown_until(self, p):
        """A pause's end as the owner reads it."""
        return clock(p["until"])

    def refused(self, ch):
        """Why --answer / --talk-continue is refused now, or None (the channel is not paused)."""
        p = self.of(ch.id)
        return messages.pause("refused", channel=ch.name, until=clock(p["until"])) if p else None

    def footer(self, ch):
        """The line under a post's card while the channel is paused, or None."""
        p = self.of(ch.id)
        return messages.pause("card_held", until=clock(p["until"])) if p else None

    async def hold(self, ch, sender, sender_ch, post, body, files=()):
        """A post for a paused channel: kept for its resume; its sender told (its receipt, or its channel)."""
        from .posts import replied
        p = self.of(ch.id)
        held = p.setdefault("held", [])
        if len(held) >= MOST_HELD:
            said, ok = messages.pause("held_full", channel=ch.name, most=MOST_HELD), False
        else:
            held.append({"sender": sender, "from_ch": sender_ch.id if sender_ch is not None else None, "text": body,
                         "files": list(files), "at": time.time(), "now": bool(post.get("now"))})
            self.bot.save()
            said, ok = messages.pause("told_held", channel=ch.name, until=clock(p["until"]), why=p.get("why")), True
        if post.get("wait"):
            return replied(ok, said)
        if sender_ch is not None:
            from .posts import listens
            if listens(self.bot.channels_state.get(str(sender_ch.id)) or {}):
                self.posts.later(self.posts.tell(sender_ch, "（機器人通知）" + said))

    def chores(self, now=None):
        """Each minute: a pause whose time has come, or a resumed one with posts still to hand over, is (re)started in
        a task of its own; nothing here waits."""
        now = time.time() if now is None else now
        for cid, st in list(self.bot.channels_state.items()):
            p = st.get("paused") if isinstance(st, dict) else None
            if not isinstance(p, dict) or cid in self.resuming or cid in self.waking:
                continue
            if (not p.get("resumed") and (p.get("until") or 0) <= now) or (p.get("resumed") and p.get("held")):
                ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
                if ch is not None:
                    self.posts.later(self.resume(ch))
            elif p.get("resumed") and not (p.get("shown") and p.get("told")):
                ch = self.bot.get_channel(int(cid)) if str(cid).isdigit() else None
                if ch is not None:
                    self.posts.later(self.resume(ch))
            elif p.get("resumed"):
                st.pop("paused", None)
                self.bot.save()

    async def resume(self, ch, early=None):
        """Carry on, each step written once it is done, so a failure or a restart redoes only what is left (at least
        once: manager on 8738b973): `resumed` (the #audit line, the ⏸️ card marked), `shown` (the ▶️ card on Discord;
        not shown: said once in #audit, posted again the next minute), `told` (its Claude took the ▶️ words; told
        again, marked 重送, when that is not known), then what was held, in order, each item dropped once handed
        over. `early`: (who, why) of an --unpause."""
        from .posts import listens
        from .posts import Origin
        key = str(ch.id)
        while key in self.waking:  # (C-199: the owner's wake hands over first; this is a task of its own, nothing waits)
            await asyncio.sleep(WAKE_WAIT)
        if key in self.resuming:
            return
        self.resuming.add(key)
        try:
            st = self.bot.channels_state.get(key) or {}
            p = st.get("paused")
            if not isinstance(p, dict):
                return
            again = bool(p.get("resumed"))  # a step left from before (a failure, a restart)
            if not again:
                now = time.time()
                data = dict(by=p.get("by"), at=clock(p.get("at") or now), until=clock(p["until"]), why=p.get("why"))
                late = now - p["until"]
                if early:
                    p["said"] = messages.pause("resumed_early", who=early[0], early_why=early[1], **data)
                else:
                    p["said"] = messages.pause("resumed", late=messages.pause("late", minutes=int(late // 60))
                                               if late >= LATE else "", **data)
                p["resumed"] = now
                self.bot.save()
                if not early:
                    await self.bot.audit(messages.pause("audit_resumed", channel=ch.name, by=p.get("by"), until=data["until"],
                                                        late=messages.pause("late", minutes=int(late // 60)) if late >= LATE
                                                        else "", held=len(p.get("held") or [])))
                if p.get("msg") and p.get("card"):
                    try:
                        await deliver.deliver(ch, content=p["card"] + messages.pause("done_mark"), intent="card", key="pause",
                                              edit=p["msg"])
                    except Exception:
                        traceback.print_exc()
            said = p.get("said") or messages.pause("resumed", by=p.get("by"), at="?", until=clock(p["until"]), why=p.get("why"), late="")
            if not p.get("shown"):
                try:
                    await deliver.deliver(ch, said, intent="status")
                    p["shown"] = True
                    self.bot.save()
                except Exception as e:
                    traceback.print_exc()
                    if not p.get("show_failed_said"):
                        p["show_failed_said"] = True
                        await self.bot.audit(messages.pause("audit_card_failed", channel=ch.name, why=e))
            if not p.get("told"):
                if listens(st):
                    try:
                        await self.bot.tmux.submit(Origin(ch), said + (messages.pause("resent_mark") if again else ""))
                    except Exception as e:
                        traceback.print_exc()
                        if not p.get("failed_said"):
                            p["failed_said"] = True
                            await self.bot.audit(messages.pause("audit_told_failed", channel=ch.name, why=e))
                        return  # (the next minute's chores try again; what was held goes in after it)
                p["told"] = True
                p.pop("failed_said", None)
                self.bot.save()
            while p.get("held") and listens(st):
                h = p["held"][0]
                try:
                    await self.give_held(ch, h)
                except Exception as e:
                    traceback.print_exc()
                    now_paused = st.get("paused")
                    if now_paused is not p:  # paused again meanwhile: this pause is no longer in the state, nobody would
                        if p["held"] and p["held"][0] is h:  # try again; first of the new pause's (#code-review on 2d74f22f)
                            p["held"].pop(0)
                        if isinstance(now_paused, dict):
                            now_paused.setdefault("held", []).insert(0, h)
                        else:
                            await self.bot.audit(messages.pause("audit_held_dropped", channel=ch.name, left=1))
                        self.bot.save()
                        return
                    if not p.get("failed_said"):
                        p["failed_said"] = True
                        await self.bot.audit(messages.pause("audit_held_failed", channel=ch.name, why=e, left=len(p["held"])))
                    return  # (the next minute's chores try again)
                if p["held"] and p["held"][0] is h:  # (moved to a new pause meanwhile: pause() took the rest)
                    p["held"].pop(0)
                p.pop("failed_said", None)
                self.bot.save()
            if p.get("held"):  # no conversation there any more: said, not dropped silently (the cards are on Discord)
                await self.bot.audit(messages.pause("audit_held_dropped", channel=ch.name, left=len(p["held"])))
            if st.get("paused") is p and p.get("shown"):  # (paused again meanwhile, its own Claude on the ▶️ words:
                st.pop("paused", None)                     # that one stays, #code-review; ▶️ not on Discord yet: next minute)
                self.bot.save()
        finally:
            self.resuming.discard(key)
