"""📝 on a picture the bot posted: what it was made from (C-91).

A program that draws can say what a picture was made from (altention/sources.py: kind, lang, tool, source), on
`altention dc post --file` (a sidecar, the post's file_source / more_files[i].source) or in a rewritten reply
(rewrite.py: a message's "sources"). Once the message is on Discord, remember() keeps "message, which attachment →
source" in state/sources/<message id>.json, masked first (kind and tool too) (masking.mask: the same pattern as chatlog). At
most KEEP_DAYS days and KEEP_COUNT messages are kept: the oldest go when one is written.

When the owner taps 📝 on such a message, show() answers it: "<kind>，<tool> 做的" and the source in a code block of
its language, or as a file when it does not fit in one message. Pictures with the same source (one long document
cut into several) are one answer ("第 1–3 張："). The bot puts no 📝 on these messages itself; a 📝 on a message it
kept nothing for is not answered here (a /screen picture's goes to screens.py first)."""
import io
import json
import os
import time
import traceback

import discord

from . import config, masking

FOLDER = os.path.join(config.STATE, "sources")
KEEP_DAYS, KEEP_COUNT = 30, 500
FENCE_MAX = 1900  # one message: the line on top and the code block


def remember(message, channel_id, sources, count, folder=None):
    """`sources`: one per attachment of `message` (in order), each altention.sources' object or None; `count`: how
    many attachments it has. Nothing is written when none has one. Never raises (the message is posted already)."""
    if message is None or getattr(message, "id", None) is None or not any(sources):
        return
    folder = folder or FOLDER
    items = [dict(src, n=i + 1, **{k: masking.mask(src[k])[0] for k in ("kind", "tool", "source")})
             for i, src in enumerate(sources) if src]
    try:
        os.makedirs(folder, exist_ok=True)
        name = "%d.json" % message.id
        tmp = os.path.join(folder, ".%s.%d" % (name, os.getpid()))
        with open(tmp, "w") as f:
            json.dump({"at": time.time(), "channel": str(channel_id), "count": count, "items": items}, f,
                      ensure_ascii=False)
        os.rename(tmp, os.path.join(folder, name))
        prune(folder)
    except OSError:
        traceback.print_exc()


def prune(folder, now=None):
    """Older than KEEP_DAYS, and past the newest KEEP_COUNT, removed."""
    now = now or time.time()
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json") and not n.startswith(".")]
    except OSError:
        return
    dated = []
    for n in names:
        try:
            dated.append((os.path.getmtime(os.path.join(folder, n)), n))
        except OSError:
            pass
    dated.sort(reverse=True)
    for i, (at, n) in enumerate(dated):
        if i >= KEEP_COUNT or now - at > KEEP_DAYS * 86400:
            try:
                os.remove(os.path.join(folder, n))
            except OSError:
                pass


def kept(message_id, channel_id, folder=None):
    """The record of a message, or None (none kept, too old, unreadable, or another channel's)."""
    path = os.path.join(folder or FOLDER, "%d.json" % int(message_id))
    try:
        with open(path) as f:
            rec = json.load(f)
        if time.time() - os.path.getmtime(path) > KEEP_DAYS * 86400:
            return None
    except (OSError, ValueError):
        return None
    if not isinstance(rec, dict) or rec.get("channel") != str(channel_id) or not isinstance(rec.get("items"), list):
        return None
    return rec


def numbers(ns):
    """[1, 2, 3] -> "1–3", [1, 3] -> "1、3", [1, 2, 4] -> "1–2、4"."""
    runs = []
    for n in ns:
        if runs and n == runs[-1][1] + 1:
            runs[-1][1] = n
        else:
            runs.append([n, n])
    return "、".join(str(a) if a == b else "%d–%d" % (a, b) for a, b in runs)


def answers(rec):
    """[(text, file name or None, file bytes or None)]: one per distinct source, in the order of the pictures."""
    groups = []
    for it in rec["items"]:
        if not isinstance(it, dict) or not all(isinstance(it.get(k), str) for k in ("kind", "lang", "tool", "source")):
            continue
        key = (it["kind"], it["lang"], it["tool"], it["source"])
        for g in groups:
            if g[0] == key:
                g[1].append(it.get("n"))
                break
        else:
            groups.append((key, [it.get("n")]))
    out, several = [], (rec.get("count") or 0) > 1
    for (kind, lang, tool, source), ns in groups:
        head = ("第 %s 張：" % numbers([n for n in ns if isinstance(n, int)]) if several else "") + "%s，%s 做的" % (kind, tool)
        block = "```%s\n%s\n```" % (lang, source.rstrip("\n"))
        if "```" not in source and len(head) + 1 + len(block) <= FENCE_MAX:
            out.append((head + "\n" + block, None, None))
        else:  # does not fit, or would break the block: as a file
            name = "source%s.%s" % ("-%d" % ns[0] if several and isinstance(ns[0], int) else "", lang)
            out.append((head, name, source.encode("utf-8")))
    return out


async def show(bot, payload):
    """The owner tapped 📝: answer with the sources kept for that message. -> whether there were any."""
    rec = kept(payload.message_id, payload.channel_id)
    ch = bot.get_channel(payload.channel_id) if rec else None
    if ch is None:
        return False
    ref, quiet = ch.get_partial_message(payload.message_id), discord.AllowedMentions.none()
    for i, (text, name, data) in enumerate(answers(rec)):
        extra = {"file": discord.File(io.BytesIO(data), name)} if name else {}
        if i == 0:
            extra.update(reference=ref, mention_author=False)
        await ch.send(text, allowed_mentions=quiet, **extra)
    return True
