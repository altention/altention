"""altention dc post --delete <id…> --why (C-205, owner and manager 11-18..11-20): a Claude takes away messages in a
channel: the bot's, another Claude's, the owner's or anyone's, at most MOST at a time, one by one (no bulk delete).

For each message, in this order, and nothing taken away when a step before it fails:
 - read from Discord whole (its REST payload: text, embeds, Components V2, attachments); not read in FETCH_SECS, or
   refused: "讀不到這則，沒有刪". Gone already (Unknown Message): no error, "已經不在了", what still points at it is
   let go (below). Never a thread's first message (the thread would lose its head: handle the thread), never one
   in #audit (the record).
 - saved in #audit, before it goes: who wrote it, when, its link, its text (masked) and attachments in one message,
   and its whole payload as a .json file with every string in it masked (bot.keep_in_audit()). Not saved: not
   taken away.
 - taken away (deliver.remove(): its ledger entry goes too). A 429 that comes back to us is waited out (its
   retry_after) and tried again, up to TRIES; Discord's 403: the bot lacks Manage Messages, said as that.
 - let go wherever the bot's state still points at it: a board's part (boards / boards_v2), a question or plan
   still open whose card it was (bot/ask.py: closed, its Claude told the card was deleted, by whom and why, so it
   does not take it for the owner ignoring it), a ⏸️ notice of a question asked for another channel's post
   (asking), a line that @-mentions the owner (calls: marked over, never edited again; what it was about stays as
   it is).

The whole batch keeps to a deadline a little short of the time altention dc post waits for the answer (`wait`):
what is not reached by then is not tried and said as left over. Afterwards someone else's messages (not the bot's)
get one quiet line in their channel, one per author ("-# 🗑️ #x 的 Claude 刪了 N 則 <@author> 的訊息：why", no mention
rings), and #audit one line for the batch. The sender is answered through its receipt."""
import asyncio
import json
import time
import traceback

import discord

from . import config, deliver, ledger, masking, messages, util

MOST = 20          # ids at most in one go (altention dc post refuses more itself: DELETE_MOST)
FETCH_SECS = 5     # reading one message
SAVE_SECS = 10     # its copy into #audit
DELETE_SECS = 10   # taking it away (each try)
TRIES = 3          # tries of one delete that Discord answers 429
MARGIN = 8         # seconds kept back from the sender's wait: the answer gets there before it stops waiting
LEAST = 3          # seconds a message needs at least: with less left it is not begun
TEXT_SHOWN = 1200  # characters of a message's text in its #audit copy (the whole of it is in the .json)
WHY_SHOWN = 200    # characters of the reason in the channel's 🗑️ line
HAS_THREAD = 1 << 5  # a message's flag: a thread was started from it


def words(code, **data):
    return messages.words("delete", code, data)


def masked_json(value):
    """The payload with every string in it masked (ids, urls and times come through: masking leaves what is not a
    secret's shape as it is)."""
    if isinstance(value, str):
        return masking.mask(value)[0]
    if isinstance(value, list):
        return [masked_json(v) for v in value]
    if isinstance(value, dict):
        return {k: masked_json(v) for k, v in value.items()}
    return value


def text_of(raw):
    """A message's words from its REST payload: its content, else its embeds', else its Components V2's."""
    return (raw.get("content") or "") or util.embed_text(raw.get("embeds")) or util.components_text(raw.get("components"))


def retry_after(e):
    """How long Discord asked to wait (a 429 that came back to us), at least a second, at most 30."""
    secs = getattr(e, "retry_after", None)
    if not isinstance(secs, (int, float)):
        try:
            secs = float((getattr(e, "response", None).headers or {}).get("Retry-After"))
        except Exception:
            secs = 1.0
    return max(1.0, min(float(secs), 30.0))


def is_429(e):
    return type(e).__name__ == "RateLimited" or getattr(e, "status", None) == 429


class Deleter(object):
    def __init__(self, bot):
        self.bot = bot

    def ids_of(self, post):
        """The ids asked for, in order, each once (an older altention dc post sends one, as delete_message)."""
        got = post.get("delete_messages")
        if not isinstance(got, list):
            got = [post.get("delete_message")] if post.get("delete_message") else []
        return list(dict.fromkeys(str(m).strip() for m in got))

    def in_audit(self, ch):
        audit = getattr(self.bot, "audit_ch", None)
        return audit is not None and audit.id in (ch.id, getattr(ch, "parent_id", None))

    def who(self, sender_ch, key):
        from . import senders
        if sender_ch is not None:
            return "#%s 的 Claude" % sender_ch.name
        return senders.who(key) or "一個程式"

    async def run(self, ch, sender_ch, words_, post, key):
        """The whole of one --delete: (ok, the answer for the sender)."""
        ids, why = self.ids_of(post), " ".join(str(post.get("why") or "").split())
        head = "[altention dc post --delete] #%s" % ch.name
        bad = (not why and words("no_why")) or (not ids and words("no_ids")) \
            or (next((m for m in ids if not m.isdigit()), None) is not None and words("not_ids")) \
            or (len(ids) > MOST and words("too_many", most=MOST)) or (self.in_audit(ch) and words("audit"))
        if bad:
            await self.bot.audit("%s：%s（%s）" % (head, bad, words_))
            return False, "altention dc post --delete：沒有刪：" + bad
        # (how long altention dc post waits for the answer: "answer_wait", as dcctl takes "wait" for itself and never
        # writes it to the spool; "wait" for a post put there some other way)
        wait = next((w for w in (post.get("answer_wait"), post.get("wait")) if isinstance(w, (int, float)) and w > 0), 30)
        end = time.monotonic() + max(LEAST * 2, wait - MARGIN)
        by = self.who(sender_ch, key)
        done, gone, refused, left, others, notes, unknown = [], [], [], [], {}, {}, []
        for i, mid in enumerate(ids):
            if end - time.monotonic() < LEAST:
                left = ids[i:]
                break
            try:
                how, author, said = await self.one(ch, mid, by, why, words_, end)
            except Exception as e:  # (anything not foreseen: that one is not taken away, the rest go on)
                traceback.print_exc()
                how, author, said = "refused", None, words("failed", why=type(e).__name__)
            if how in ("done", "gone"):
                notes[mid] = await self.let_go(ch, mid, by, why)
            if how == "done":
                done.append(mid)
                if author is not None and not author.get("own"):
                    others.setdefault(author["id"], []).append(mid)
            elif how == "gone":
                gone.append(mid)
                if author is not None and author.get("known") is False:
                    unknown.append(mid)  # (Discord's 404 for "not in this channel" is the one for "deleted")
            else:
                refused.append((mid, said))
        if others:
            await self.notice(ch, by, others, why)
        await self.bot.audit(self.summary(head, words_, why, done, gone, refused, left, notes))
        return not refused and not left, self.answer(done, gone, refused, left, unknown)

    async def one(self, ch, mid, by, why, words_, end):
        """One message: ("done" | "gone" | "refused", its author {"id", "own"} or None, why not)."""
        from discord.http import Route
        secs = lambda most: max(0.5, min(most, end - time.monotonic()))  # noqa: E731
        try:
            raw = await asyncio.wait_for(self.bot.http.request(Route(
                "GET", "/channels/{channel_id}/messages/{message_id}", channel_id=ch.id, message_id=int(mid))), secs(FETCH_SECS))
        except discord.NotFound:
            if self.elsewhere(ch, mid):
                return "refused", None, words("elsewhere")
            known = ledger.channels_of(mid)  # (only the bot's are in the ledger: anyone else's may be in another channel)
            ledger.forget(ch.id, mid)
            return "gone", {"known": bool(known)}, None
        except discord.Forbidden:
            return "refused", None, words("unread", why=words("no_read"))
        except asyncio.TimeoutError:
            return "refused", None, words("unread", why=words("no_answer", secs=FETCH_SECS))
        except discord.HTTPException as e:
            return "refused", None, words("unread", why="Discord %s %s" % (e.status, e.code))
        if not isinstance(raw, dict) or str(raw.get("id")) != mid:
            return "refused", None, words("unread", why=words("no_answer", secs=FETCH_SECS))
        if str(raw.get("channel_id") or ch.id) != str(ch.id):
            return "refused", None, words("elsewhere")
        if int(mid) == ch.id or int(raw.get("flags") or 0) & HAS_THREAD or raw.get("thread"):
            return "refused", None, words("thread_starter")
        a = raw.get("author") or {}
        me = getattr(getattr(self.bot, "user", None), "id", None)
        author = {"id": str(a.get("id") or "?"), "own": me is not None and str(a.get("id")) == str(me)}
        try:
            saved = await asyncio.wait_for(self.bot.keep_in_audit(*self.copy(ch, raw, by, why, words_)), secs(SAVE_SECS))
        except Exception:
            traceback.print_exc()
            saved = False
        if not saved:
            return "refused", author, words("not_saved")
        for n in range(TRIES):
            try:
                await asyncio.wait_for(deliver.remove(ch, int(mid)), secs(DELETE_SECS))
                return "done", author, None
            except discord.NotFound:
                return "gone", author, None
            except asyncio.TimeoutError:
                return "refused", author, words("late", secs=DELETE_SECS)
            except discord.Forbidden:
                return "refused", author, words("no_manage")
            except Exception as e:
                if not is_429(e):
                    if isinstance(e, discord.HTTPException):
                        return "refused", author, words("refused", why="%s %s：%s" % (e.status, e.code, str(e.text)[:200]))
                    raise
                pause = retry_after(e)
                if n == TRIES - 1 or end - time.monotonic() < pause + LEAST:
                    return "refused", author, words("limited")
                await asyncio.sleep(pause)
        return "refused", author, words("limited")

    def elsewhere(self, ch, mid):
        """The ledger has this message in another channel (asked of the wrong one): not "gone"."""
        return any(cid != str(ch.id) for cid in ledger.channels_of(mid))

    def copy(self, ch, raw, by, why, words_):
        """(text, file name, bytes) of a message's copy in #audit."""
        a = raw.get("author") or {}
        name = a.get("global_name") or a.get("username") or "?"
        files = ["%s（%s bytes）%s" % (f.get("filename"), f.get("size"), f.get("url"))
                 for f in raw.get("attachments") or [] if isinstance(f, dict)]
        text = masking.mask(text_of(raw))[0] or ""
        if len(text) > TEXT_SHOWN:
            text = text[:TEXT_SHOWN] + "…"
        rows = [words("saved_head", channel=ch.name, mid=raw.get("id"), by=by, words=words_, why=why),
                words("saved_author", name=name, id=a.get("id") or "?", bot="（機器人）" if a.get("bot") else "",
                      at=raw.get("timestamp") or "?"),
                "https://discord.com/channels/%s/%s/%s" % (config.GUILD_ID, ch.id, raw.get("id"))]
        if files:
            rows.append(words("saved_files", files="；".join(files)))
        rows.append(words("saved_text") + ("\n" + text if text else words("saved_no_text")))
        body = "\n".join(rows)
        if len(body) > 1900:
            body = body[:1899] + "…"
        data = json.dumps(masked_json(raw), ensure_ascii=False, indent=1).encode("utf-8")
        return body, "message-%s.json" % raw.get("id"), data

    async def let_go(self, ch, mid, by, why):
        """What the bot's state still holds of a message now gone: let go. The names of what was, for #audit."""
        said = []
        st = self.bot.channels_state.get(str(ch.id))
        if isinstance(st, dict):
            boards, framed = st.get("boards"), st.get("boards_v2")
            for name, ids in list(boards.items()) if isinstance(boards, dict) else ():
                ids_s = [str(x) for x in ids] if isinstance(ids, list) else []
                if mid in ids_s:
                    i = ids_s.index(mid)
                    ids.pop(i)
                    v2 = framed.get(name) if isinstance(framed, dict) else None
                    if isinstance(v2, list) and i < len(v2):
                        v2.pop(i)
                    if not ids:
                        boards.pop(name, None)
                        if isinstance(framed, dict):
                            framed.pop(name, None)
                    said.append(words("was_board", name=name))
            calls = st.get("calls")
            for k, rec in list(calls.items()) if isinstance(calls, dict) else ():
                if isinstance(rec, list) and rec and str(rec[0]) == mid:
                    rec[0] = None  # (never edited again: done() skips a line with no message)
                    if len(rec) > 2:
                        rec[2] = True  # (over: not mentioned again for this key)
                    said.append(words("was_call", key=k))
        for cst in list(self.bot.channels_state.values()):
            asking = cst.get("asking") if isinstance(cst, dict) else None
            for aid, where in list(asking.items()) if isinstance(asking, dict) else ():
                if isinstance(where, list) and len(where) > 1 and str(where[0]) == str(ch.id) and str(where[1]) == mid:
                    asking.pop(aid, None)
                    said.append(words("was_asking", ask=aid))
        if said:
            self.bot.save()
        try:  # (the questions' records are bot/ask.py's alone: asked to close the one of this card)
            asker = self.bot.tmux.asks
        except AttributeError:
            asker = None
        if asker is not None and hasattr(asker, "card_deleted"):
            try:
                got = await asker.card_deleted(ch, mid, by, why)
            except Exception:
                traceback.print_exc()
                got = None
            if got:
                said.append(words("was_ask", ask=got))
        menus = getattr(getattr(self.bot, "tmux", None), "menus", None)
        if menus is not None and hasattr(menus, "card_deleted"):
            try:
                got = await menus.card_deleted(ch, mid)
            except Exception:
                traceback.print_exc()
                got = None
            if got is not None:
                said.append(words("was_menu", title=got))
        return said

    async def notice(self, ch, by, others, why):
        """One quiet line for each author whose messages were taken away (not the bot's own): nobody is rung."""
        why = " ".join(masking.mask(why)[0].split())
        why = why[:WHY_SHOWN] + "…" if len(why) > WHY_SHOWN else why
        text = "\n".join(words("notice", by=by, n=len(mids), author=author, why=why) for author, mids in others.items())
        try:
            for part in util.split_message(text):  # (many authors with a long reason: more than one message's 2000)
                await deliver.deliver(ch, part, allowed_mentions=discord.AllowedMentions.none(), intent="status")
        except Exception as e:
            traceback.print_exc()
            await self.bot.audit("[altention dc post --delete] #%s：🗑️ 那行沒貼出去（%s）" % (ch.name, type(e).__name__))

    def summary(self, head, words_, why, done, gone, refused, left, notes):
        def ids(mids):
            return "、".join(m + ("（%s）" % "；".join(notes[m]) if notes.get(m) else "") for m in mids)
        parts = []
        if done:
            parts.append(words("sum_done", n=len(done), ids=ids(done)))
        if gone:
            parts.append(words("sum_gone", n=len(gone), ids=ids(gone)))
        if refused:
            parts.append(words("sum_refused", n=len(refused), ids="、".join("%s：%s" % r for r in refused)))
        if left:
            parts.append(words("sum_left", n=len(left), ids="、".join(left)))
        return "%s：%s（%s；理由：%s）" % (head, "；".join(parts), words_, why)

    def answer(self, done, gone, refused, left, unknown=()):
        parts = []
        if done:
            parts.append(words("ans_done", ids="、".join(done)))
        known = [m for m in gone if m not in unknown]
        if known:
            parts.append(words("ans_gone", ids="、".join(known)))
        if unknown:
            parts.append(words("ans_unknown", ids="、".join(unknown)))
        for mid, why in refused:
            parts.append(words("ans_refused", mid=mid, why=why))
        if left:
            parts.append(words("ans_left", ids="、".join(left)))
        return "\n".join(parts)
