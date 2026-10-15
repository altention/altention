"""📋 on a Claude reply: the whole turn behind it, in a thread under that reply.

Discord has no collapsible block, but a thread shows as one line ("N messages") until opened. Nothing is
posted until the owner asks: they add the 📋 reaction to a reply, and the bot reads that turn back from
Claude Code's own transcript (what was asked, what Claude said along the way, every tool call with its
input and output) and posts it into a thread hanging from the reply.

When a reply is posted, the bridge records where its turn lies in the transcript (st["turns"]: message id
-> [path, start byte, end byte or None, thread id]; None: the end is looked for when asked, the turn's
last rows being written after its Stop hook). Transcripts are kept for 36500 days on this machine (set by
chatlog) and also copied to the logs repo; if one is gone after all, the owner is told so.

A reply of a Claude on another machine (stage 3b step 1.10): its transcript is over there. The record is the same,
with the machine's name beside it (st["turn_hosts"]: message id -> host; none: this machine), and when asked the
turn is read there by `dcremote turn`, which sends it back as data, made small (the connector's slim()); the
words are put on here, by the same pieces() as for a turn of this machine.
"""
import io
import re
import json
import traceback

import discord

from altention.connectors.claude_code import transcript

from . import masking
from .util import split_message

EMOJI = "📋"
KEEP = 500          # replies remembered per channel
MAX_MESSAGES = 30   # beyond this, the rest of the process comes as a file
MORE_KEEP = 300     # messages that end cut short remembered per channel (st["more"]), the newest
EXPAND = 10000      # characters of the cut text one 📋 on such a message posts (not counting the fences put round them)
MORE = "…（還有 %d 字，對這則按 📋 接著看）"  # the last line of a message that ends cut short: always these words,
MARK = re.compile(r"…（還有 \d+ 字，對這則按 📋 接著看）\s*$")  # so a message says by itself that it was cut


def remember(st, message_ids, path, start, end, prompt_id=None, host=None):
    """`prompt_id`: the turn's own id, kept beside the record (st["turn_ids"]: message id -> id) and not in it: a
    version of before stage 3b step 1.6a reads the four fields of a record and nothing else. `host`: the machine
    the transcript is on when it is not this one, kept beside it the same way (st["turn_hosts"])."""
    turns, ids = st.setdefault("turns", {}), st.setdefault("turn_ids", {})
    hosts = st.setdefault("turn_hosts", {}) if host or "turn_hosts" in st else {}
    for mid in message_ids:
        turns[str(mid)] = [path, start, end, None]
        if prompt_id:
            ids[str(mid)] = prompt_id
        if host:
            hosts[str(mid)] = host
    for old in list(turns)[:-KEEP]:
        turns.pop(old, None)
    for kept in (ids, hosts):
        for old in [mid for mid in kept if mid not in turns]:
            kept.pop(old, None)
    more = st.get("more") or {}
    for old in [mid for mid, rec in more.items() if not isinstance(rec, list) or not rec or str(rec[0]) not in turns]:
        more.pop(old, None)  # a message cut short whose turn is no longer remembered


def note_more(st, message_id, reply_id, more):
    """A message in a turn's thread ends cut short: where the text goes on (st["more"]: its id -> [the reply's
    id, which item of the turn, which field, from which character, the first message of what a 📋 on it posted:
    None until one did]; while a posting that stopped halfway is to be gone on with, a sixth: the first message of
    what did get there, and the fourth says up to where). The reply's id is the key of st["turns"]: the file, where the turn begins, its id and its
    machine are looked up there. Kept in the channel's state (a restart keeps them), the newest MORE_KEEP."""
    recs = st.setdefault("more", {})
    recs[str(message_id)] = [str(reply_id), more[0], more[1], more[2], None]
    for old in list(recs)[:-MORE_KEEP]:
        recs.pop(old, None)


class Piece(str):
    """A piece of a turn worded for Discord. `more`: None, or where its text was cut: (which item of the turn, which
    field of it, the character the rest begins at, how many the whole has); such a piece ends with the MORE line."""
    more = None


def clip(text, n, whole=None, prose=False):
    """(the first `n` characters of a text, where the rest begins or None when it was all there, how long the whole
    is). `whole`: how long the text was before it was cut on its way here (a result read on another machine).
    `prose`: cut at the end of a line when there is one (what was asked or said: markdown of its own)."""
    text = transcript.written(text).strip()  # (anything that is no string: as it is written; nothing fails here)
    total = max(whole if isinstance(whole, int) and not isinstance(whole, bool) else 0, len(text))
    if total <= n:
        return text, None, total
    at = transcript.cut_at(text, 0, n)  # at the end of a line when one is near: not in the middle of a number or a word
    return text[:at], at, total


def cut(text, n, whole=None):
    """The text cut to `n` characters, with the MORE line when it was cut."""
    shown, at, total = clip(text, n, whole)
    return shown if at is None else shown.rstrip() + "\n" + MORE % (total - at)


def paired(text):
    """`text` with its code fences paired: when it holds an odd number of ``` (Discord counts every one, wherever it
    stands in a line), one is added at its end, ahead of the MORE line when it ends with one. A message with a
    fence left open shows everything after it as code, and so does the next one that begins inside it."""
    if text.count("```") % 2 == 0:
        return text
    m = MARK.search(text)
    return text + "\n```" if not m else text[:m.start()].rstrip("\n") + "\n```\n" + text[m.start():]


def made(text, shown_at, total, n, field):
    """A Piece: `text`, with the MORE line and where the rest begins when it was cut (`shown_at`: None when not).
    Its code fences are paired: each piece is whole in itself, whatever the text it shows holds."""
    if shown_at is None:
        return Piece(paired(text))
    piece = Piece(paired(text + "\n" + MORE % (total - shown_at)))
    piece.more = (n, field, shown_at, total)
    return piece


def prose_piece(head, text, limit, n):
    """What was asked or said: cut at the end of a line, and a code fence the cut left open is closed."""
    shown, at, total = clip(text, limit, prose=True)
    shown = shown.rstrip()
    if at is not None and transcript.fence_at(shown, len(shown)) is not None:
        shown += "\n```"
    return made(head + shown, at, total, n, transcript.TEXT)


def tool_line(name, inp, n=None):
    """One line saying what a tool call did. What sort of call it is, and where in its input the thing is, the
    connector says (transcript.tool_call); the words are here. `n`: which item of its turn it is (for a piece that
    is cut: where the rest is to be found)."""
    sort, what = transcript.tool_call(name, inp)
    if sort == transcript.COMMAND:
        note = what["note"]
        shown, at, total = clip(what["command"], 800)
        return made("🔧 **%s**%s\n```\n%s\n```" % (name, "（%s）" % note if note else "", shown.replace("```", "'''")),
                    at, total, n, transcript.COMMAND_TEXT)
    if sort == transcript.FILE:
        extra = ""
        if what["old"] is not None:
            extra = "（改 %d 字 → %d 字）" % (what["old"], what["new"])
        elif what["chars"] is not None:
            extra = "（%d 字）" % what["chars"]
        return Piece(paired("🔧 **%s** `%s`%s" % (name, what["path"] or "?", extra)))
    if sort == transcript.SEARCH:
        return Piece(paired("🔧 **%s** `%s`%s" % (name, what["pattern"], " in `%s`" % what["path"] if what["path"] else "")))
    if sort == transcript.AGENT:
        if what["description"]:
            return Piece(paired("🔧 **%s**：%s" % (name, what["description"])))
        shown, at, total = clip(what["prompt"], 200)
        return made("🔧 **%s**：%s" % (name, shown), at, total, n, transcript.PROMPT_TEXT)
    if sort == transcript.WEB:
        return Piece(paired("🔧 **%s** %s" % (name, what["what"])))
    shown, at, total = clip(json.dumps(what["input"], ensure_ascii=False), 300)
    return made("🔧 **%s** `%s`" % (name, shown.replace("`", "'")), at, total, n, transcript.INPUT_TEXT)


def result_text(block, n=None):
    content = block.get("content")
    if isinstance(content, list):
        content = "\n".join(transcript.text_of(b) for b in content if transcript.is_text(b))
    shown, at, total = clip(str(content or ""), 1200, block.get("chars"))
    text = shown.rstrip().replace("```", "'''")
    # (a failed call's mark stands on a line of its own: the fence after it then begins a line, and is seen by the
    # cutting into messages; "❌ ```" left a message with one fence too many when it was cut there)
    mark = "❌\n" if block.get("is_error") else ""
    return made("%s```\n%s\n```" % (mark, text or "（沒有輸出）"), at, total, n, transcript.TEXT) if text or mark else Piece("")


def turn_end(path, start, recorded, prompt_id=None):
    """Where the turn of a remembered reply ends. A record made since stage 3b step 1.6a has no end (None): it is
    found now, in the file as it has become: the first message row of another turn than this one (`prompt_id`,
    kept with the record), or the end of the file. An older record keeps the end it was given: the size of the
    file when the bot got to the turn's Stop."""
    return transcript.turn_end(path, start, prompt_id) if recorded is None else recorded


def pieces_of(st, message_id):
    """The Discord pieces of the turn behind a remembered reply of this machine, or None (no record, or its
    transcript is gone)."""
    rec = (st.get("turns") or {}).get(str(message_id))
    if rec is None:
        return None
    path, start, end = rec[:3]
    return read_turn(path, start, turn_end(path, start, end, (st.get("turn_ids") or {}).get(str(message_id))))


def read_turn(path, start, end):
    """The turn as a list of Discord-ready pieces, or None if the transcript is gone."""
    items = transcript.turn(path, start, end)
    return None if items is None else pieces(items)


def pieces(items):
    """A turn as the connector gives it (transcript.turn(), or that made small and sent from another machine: lists
    where those were tuples), worded for Discord."""
    out = []
    for n, item in enumerate(items):  # (n: which item of the turn: where the rest of a piece that is cut is found)
        if item[0] == "asked":
            out.append(prose_piece("🧑 **你**\n", item[1], 1500, n))
        elif item[0] == "result":
            r = result_text(item[1], n)
            if r:
                out.append(r)
        elif item[0] == "said":
            out.append(prose_piece("💬 ", item[1], 3000, n))
        elif item[0] == "tool":
            out.append(tool_line(item[1], item[2], n))
        elif item[0] == "more":
            out.append(Piece("…（這一輪太長，後面還有 %s 項沒有傳回來）" % item[1]))
    return out


def whole_fences(chunks):
    """The messages a text was cut into, each with its code fences paired. The cutting pairs the fences that begin a
    line; one that stands inside a line and is cut off from its end is closed here, and the message after it opens
    with one (it goes on inside that block). The pieces put together are each whole (paired()), so what is open at
    a message's end is always a block that goes on."""
    out, inside = [], False
    for chunk in chunks:
        if inside:
            chunk = "```\n" + chunk
        inside = chunk.count("```") % 2 == 1
        out.append(paired(chunk) if inside else chunk)
    return out


def messages_of(got):
    """A turn's pieces as the messages of its thread: [(text, where it was cut or None)]. Pieces are put together up
    to a message's size as before; a piece that was cut ends its message, so a message has one cut at most, and at
    its end: what a 📋 on that very message goes on from."""
    out, buf = [], []

    def close(more=None):
        if buf:
            chunks = whole_fences(split_message("\n".join(buf), 1900))
            out.extend((c, None) for c in chunks[:-1])
            out.append((chunks[-1], more))
            del buf[:]
    for p in got:
        buf.append(p)
        if getattr(p, "more", None):
            close(p.more)
    close()
    return out or [("（這一輪沒有內容）", None)]


UNREACHABLE, BEHIND, UNREADABLE = "unreachable", "behind", "unreadable"  # remote_pieces(): why there are none


async def remote_pieces(host, st, message_id):
    """The pieces of the turn behind a remembered reply of a Claude on `host`: (pieces, None); (None, None) when its
    transcript is gone there; (None, why) when it could not be asked: the machine does not answer (or took too
    long), its engine is a step behind (its dcremote has no `turn` yet), or what came back cannot be read."""
    from . import remote
    path, start = st["turns"][str(message_id)][:2]
    code, out = await remote.dcremote(host, "turn", path, str(start), (st.get("turn_ids") or {}).get(str(message_id)) or "",
                                      timeout=20)
    if code == 255:
        return None, UNREACHABLE
    if code != 0:
        return None, BEHIND if code == 1 else UNREADABLE
    try:
        items = json.loads(out)
    except ValueError:
        return None, UNREADABLE
    if items is None:
        return None, None
    if not isinstance(items, list) or not all(isinstance(i, list) and len(i) >= 2 and isinstance(i[0], str) for i in items):
        return None, UNREADABLE
    try:
        return pieces(items), None
    except Exception:  # an item not shaped as the connector shapes it: said, not an event that ends in silence
        traceback.print_exc()
        return None, UNREADABLE


async def remote_part(host, st, reply_id, item, field, offset):
    """A stretch of a field of a turn on `host` (tools/dcremote part): (what transcript.piece() gave there, None);
    (None, None) when the transcript, the item or the field is not there; (None, why) when it could not be asked,
    as remote_pieces()."""
    from . import remote
    path, start = st["turns"][str(reply_id)][:2]
    code, out = await remote.dcremote(host, "part", path, str(start), (st.get("turn_ids") or {}).get(str(reply_id)) or "",
                                      str(item), str(field), str(offset), str(EXPAND), timeout=20)
    if code == 255:
        return None, UNREACHABLE
    if code != 0:
        return None, BEHIND if code == 1 else UNREADABLE
    try:
        got = json.loads(out)
    except ValueError:
        return None, UNREADABLE
    if got is None:
        return None, None
    if not (isinstance(got, dict) and isinstance(got.get("text"), str) and all(isinstance(got.get(k), int) for k in ("total", "next"))):
        return None, UNREADABLE
    return got, None


def named(got, field=None):
    """The name of the piece a stretch belongs to, as the thread shows such pieces: 🧑 你說的, 💬 回覆 or 中途的話,
    🔧 <tool> 的指令／任務／輸入／輸出. What the item is the connector says (transcript.source(), in the stretch's
    "of"); a stretch from a machine that does not say gets a plain name."""
    of = got.get("of") if isinstance(got.get("of"), dict) else {}
    kind, tool = of.get("kind"), of.get("tool")
    part = {transcript.COMMAND_TEXT: "指令", transcript.PROMPT_TEXT: "任務"}.get(field, "輸入")
    tool = tool if not tool or len(tool) <= NAME_MAX else tool[:NAME_MAX - 1] + "…"  # (an MCP tool's name may be 64 characters long)
    return {"asked": "🧑 你說的", "said": "💬 回覆" if of.get("last") else "💬 中途的話",
            "result": "🔧 %s 的輸出" % tool if tool else "🔧 工具的輸出", "tool": "🔧 %s 的%s" % (tool or "工具", part)}.get(kind, "上面那一段")


def head(got, offset, field=None):
    """The small first line of what a 📋 on a cut message posts: which piece it goes on with, and which characters
    of it these are. The posted stretches stand at the thread's end, far from the piece they belong to; without it
    the owner saw numbers, then code, then numbers again, and could not tell what the last lot went with. It is the
    bot's own frame: no part of the text, nor of its EXPAND."""
    return "-# ⤵ 接「%s」第 %s–%s 字（共 %s 字）" % (named(got, field), format(offset + 1, ","), format(got["next"], ","), format(got["total"], ","))


def foot(got, field=None):
    """The small last line of a stretch that ends its piece: where that lot stops. (One that does not end it ends
    with the MORE line.)"""
    return "-# ⤴ 「%s」到這裡結束" % named(got, field)


NAME_MAX = 30    # characters of a tool's name in the small lines of a batch
DISCORD_MAX = 2000  # characters of one message: Discord refuses a longer one whole


def fitting(parts):
    """The messages of a batch, none over DISCORD_MAX: the last guard, after the room each was given (stretch()). One
    that is too long all the same loses its small lines; still too long, it is cut again (its place in the field
    stays with its last part; the parts before it are no place to go on from: None). Said once in the log: it means
    the room was reckoned wrong somewhere."""
    out = []
    for body, at in parts:
        if len(body) <= DISCORD_MAX:
            out.append((body, at))
            continue
        global fitted_said
        if not fitted_said:
            fitted_said = True
            print("process: a message of a 📋 stretch came to %d characters; posted without its small lines, or cut again (said once)" % len(body), flush=True)
        plain = "\n".join(ln for ln in body.split("\n") if not ln.startswith(("-# ⤵", "-# ⤴")))
        if len(plain) <= DISCORD_MAX:
            out.append((plain, at))
            continue
        chunks = whole_fences(split_message(plain, 1900))
        out.extend((c, None) for c in chunks[:-1])
        out.append((chunks[-1], at))  # (the guard itself never refuses: whatever it made is posted)
    return out


fitted_said = False
ROOM = 1850  # characters of the text itself in one message of a stretch (the fences put round it and the MORE line fit beside)


def stretch(got, first=0, last=0):
    """What transcript.piece() gave, as the messages it is posted in: [(text, where in the whole field the next
    message goes on)]. Prose as it is, cut at the end of a line, with the fence that is open where a message begins
    opened again and the one open where it ends closed; anything else inside a fence of its own, cut anywhere. Each
    message knows where it ends in the field: a posting that stops halfway is gone on with from there. The fences
    are no part of the EXPAND characters. `first`, `last`: how long the lines are that the caller puts ahead of the
    first message and after the last."""
    text, at, prose = got["text"], got["next"] - len(got["text"]), bool(got.get("prose"))
    fence, out, pos = (got.get("fence") if prose else None), [], 0
    if not prose:
        text = text.replace("```", "'''")  # (as long as it was; before it is cut up: a fence may lie across two messages)
    while pos < len(text):
        # (room is left in every message for the line that may end the batch, and in the first for the line that
        # begins it: `last`, `first` characters. A message over Discord's 2000 is refused whole, at every press.)
        room = max(200, ROOM - last - (first if pos == 0 else 0))
        end = transcript.cut_at(text, pos, room)  # at a line's end, for a command or an output too (a number was cut in two)
        raw = text[pos:end]
        if prose:
            after = transcript.fence_at(raw, len(raw), fence)
            body = ("```%s\n" % fence if fence is not None else "") + raw.strip("\n") + ("\n```" if after is not None else "")
            fence = after
        else:
            body = "```\n%s\n```" % raw.strip("\n")
        if raw.strip("\n"):
            out.append((body, at + end))
        elif out:
            out[-1] = (out[-1][0], at + end)  # (only line ends: nothing to show, the place moves on)
        pos = end
    return out


def answering(thread, message_id):
    """What makes a message a reply to another of its thread, without a ping; nothing when it cannot be made (the
    message is then sent as any other, and still begins with its head line)."""
    try:
        return {"reference": discord.MessageReference(message_id=int(message_id), channel_id=int(thread.id), fail_if_not_exists=False),
                "mention_author": False}
    except Exception:
        return {}


EXPANDING = set()  # (thread id, message id) of the cut messages whose next stretch is being posted right now


async def expand(bot, payload, thread, st):
    """The owner reacted with 📋 on a message inside a turn's thread: one that ends cut short (the MORE line) is
    gone on with, EXPAND characters at a time, in the same thread; the last of what is posted ends cut short in its
    turn while there is more."""
    said = dict(delete_after=15)
    rec = (st.get("more") or {}).get(str(payload.message_id))
    if rec is None:  # cut and no longer remembered (the newest MORE_KEEP are), or never cut: the message says which
        try:
            marked = bool(MARK.search((await thread.fetch_message(payload.message_id)).content or ""))
        except Exception:
            marked = False
        return await thread.send("📋 這則的紀錄已經不在了。" if marked else "📋 這則沒有被省略的內容。", **said)
    reply, item, field, offset, done = rec[:5]
    if done:
        return await thread.send("📋 這則已經展開過，接下去的在下面：https://discord.com/channels/%s/%d/%d"
                                 % (getattr(payload, "guild_id", None) or "@me", thread.id, done), **said)
    key = (thread.id, str(payload.message_id))
    if key in EXPANDING:  # pressed again while its stretch is still on its way (reading another machine, posting
        return await thread.send("📋 這則正在展開，稍等一下。", **said)  # several messages): not posted twice
    EXPANDING.add(key)
    try:
        return await expand_now(bot, payload, thread, st, rec, said)
    finally:
        EXPANDING.discard(key)


async def expand_now(bot, payload, thread, st, rec, said):
    """expand(), for a cut message that has a record and is not being gone on with already."""
    reply, item, field, offset, done = rec[:5]
    turn = (st.get("turns") or {}).get(str(reply))
    if turn is None:
        return await thread.send("📋 這一輪的對話紀錄已經不在了。", **said)
    host = (st.get("turn_hosts") or {}).get(str(reply))
    if host:
        got, why = await remote_part(host, st, reply, item, field, offset)
        if why:  # the record stays as it is: asked again later
            return await thread.send({UNREACHABLE: "📋 這一輪在 %s 上，現在連不上，等它回來再按一次。",
                                      BEHIND: "📋 這一輪在 %s 上，那台的程式還沒更新到能讀它，稍後再按一次。",
                                      UNREADABLE: "📋 這一輪在 %s 上，那台回來的內容讀不出來。"}[why] % host, **said)
    else:
        path, start, end = turn[:3]
        items = transcript.turn(path, start, turn_end(path, start, end, (st.get("turn_ids") or {}).get(str(reply))))
        got = None if items is None else transcript.piece(items, item, field, offset, EXPAND)
    if got is None or not got["text"]:
        return await thread.send("📋 這一輪的對話紀錄已經不在了。", **said)
    left = got["total"] - got["next"]
    top, end = head(got, got["next"] - len(got["text"]), field), MORE % left if left > 0 else foot(got, field)
    parts = stretch(got, first=len(top) + 1, last=len(end) + 1)
    if not parts:
        return await thread.send("📋 這一輪的對話紀錄已經不在了。", **said)
    parts[-1] = (parts[-1][0] + "\n" + end, parts[-1][1])
    parts[0] = (top + "\n" + parts[0][0], parts[0][1])
    parts = fitting(parts)
    sent = []
    try:
        for body, _ in parts:
            # The first of them is sent as a reply to the message it goes on from (no ping): Discord then shows
            # which message it answers, and a click on that goes back to the piece.
            sent.append(await thread.send(masking.masked(body, cut=True), silent=True,
                                          **(answering(thread, payload.message_id) if not sent else {})))
    finally:
        if sent:
            first = (rec[5] if len(rec) > 5 else None) or sent[0].id  # (of the first press, when this one goes on after a failure)
            if len(sent) == len(parts):  # all of it is there: the message is gone on with, once
                rec[4] = first
                del rec[5:]
                if left > 0:
                    note_more(st, sent[-1].id, reply, (item, field, got["next"], got["total"]))
            else:
                # Posting stopped halfway (Discord): the record now says up to where the text is there, and is not
                # done. A 📋 on the same message goes on from that place: nothing is posted twice, and nothing is
                # out of reach (the manager, 2026-10-13).
                reached = [at for _, at in parts[:len(sent)] if at is not None]  # (a part cut again has no place of its own)
                if reached:
                    rec[3] = reached[-1]
                rec[5:] = [first]
            bot.save()


async def show(bot, payload):
    """The owner reacted with 📋: post the turn behind that reply into a thread under it."""
    ch = bot.get_channel(payload.channel_id)
    if ch is None and str(payload.channel_id) not in bot.channels_state:  # a thread gone to sleep is not in the cache
        try:
            ch = await bot.fetch_channel(payload.channel_id)
        except Exception:
            ch = None
    parent = getattr(ch, "parent_id", None)
    if ch is not None and parent and str(payload.channel_id) not in bot.channels_state and str(parent) in bot.channels_state:
        return await expand(bot, payload, ch, bot.channels_state[str(parent)])  # inside a thread: a message cut short
    st = bot.channels_state.get(str(payload.channel_id)) or {}
    rec = (st.get("turns") or {}).get(str(payload.message_id))
    if ch is None:
        return
    if rec is None:
        return await ch.send("📋 這則沒有記下過程（不是 Claude 的回覆，或是這個功能上線前的回覆）。", delete_after=15)
    path, start, end, thread_id = rec
    if thread_id and bot.get_channel(thread_id) is not None:
        return await ch.send("📋 這則的過程在 <#%d>。" % thread_id, delete_after=15)
    host = (st.get("turn_hosts") or {}).get(str(payload.message_id))
    if host:
        got, why = await remote_pieces(host, st, payload.message_id)
        if why:  # the record stays as it is: asked again later
            return await ch.send({UNREACHABLE: "📋 這一輪在 %s 上，現在連不上，等它回來再按一次。",
                                  BEHIND: "📋 這一輪在 %s 上，那台的程式還沒更新到能讀它，稍後再按一次。",
                                  UNREADABLE: "📋 這一輪在 %s 上，那台回來的內容讀不出來。"}[why] % host, delete_after=15)
    else:
        got = pieces_of(st, payload.message_id)
    if got is None:
        return await ch.send("📋 這一輪的對話紀錄已經不在了。", delete_after=15)
    try:
        msg = await ch.fetch_message(payload.message_id)
        first = next((p for p in got if p.startswith("🧑")), "過程")
        title = masking.mask(" ".join(first.split("\n", 1)[-1].split()))[0][:80] or "過程"  # (masked before cut)
        thread = await msg.create_thread(name="📋 " + title, auto_archive_duration=60)
    except discord.HTTPException:
        traceback.print_exc()
        return
    rec[3] = thread.id
    bot.save()
    messages, noted = messages_of(got), False
    for part, more in messages[:MAX_MESSAGES]:
        sent = await thread.send(masking.masked(part, cut=bool(more)), silent=True)
        if more and getattr(sent, "id", None) is not None:  # it ends cut short: a 📋 on it goes on from there (expand())
            note_more(st, sent.id, payload.message_id, more)
            noted = True
    if noted:
        bot.save()
    messages = [part for part, _ in messages]
    if len(messages) > MAX_MESSAGES:
        rest = masking.masked("\n".join(messages[MAX_MESSAGES:]))
        await thread.send("後面還有 %d 則的量，放在檔案裡：" % (len(messages) - MAX_MESSAGES),
                          file=discord.File(io.BytesIO(rest.encode("utf-8")), "process.md"), silent=True)
