"""Earlier conversations of a working directory, as the owner sees them in Discord (for !resume, the recap and the
history posted when a session is attached). Where Claude Code keeps them and how they are read is the connector's
(altention/connectors/claude_code/sessions.py, stage 3b step 1.7); here are the words, the lists, and what each
channel remembers of the conversations it has held (st["history"]), so the list can tell this channel's own from
those of other channels or terminals in the same directory.
"""
import calendar
import time

from altention.connectors.claude_code import sessions as cc_sessions

from . import masking
from .util import split_message

KEEP = 20  # conversations remembered per channel


def remember(st, session_id):
    """Note that this channel held `session_id`, newest last."""
    if session_id:
        st["history"] = ([s for s in st.get("history", []) if s != session_id] + [session_id])[-KEEP:]


def pick(items, ref):
    """The conversation `ref` names: its number in the list (1 = newest) or the start of its id."""
    if ref.isdigit() and len(ref) < 4:
        return items[int(ref) - 1] if 1 <= int(ref) <= len(items) else None
    hits = [s for s in items if s["id"].startswith(ref.lower())] if len(ref) >= 4 else []
    return hits[0] if len(hits) == 1 else None


TASK = "⚙️ 背景工作完成"
MIDWAY = "（處理中途補充）"


def elsewhere(st):
    """The machine a channel's conversation is on when that is not this one (a window the bot opened in the owner's
    tmux there, a pane attached there), else None: its conversations are files of that machine."""
    return (st.get("home") or {}).get("host") or (st.get("pane") or {}).get("host") or None


def worded(kind, text):
    """How a turn's beginning reads in Discord, from what the connector says it was (sessions.turn_start)."""
    if kind == "midway":
        return MIDWAY + text
    if kind == "task":
        return "%s：%s" % (TASK, text[:300] if text else "（沒有說明）")
    return text


def recap(path, turns=3, tail_bytes=4 * 1024 * 1024, stamps=False):
    """The last `turns` exchanges of a transcript as [(what the user typed, Claude's last message of that
    turn)], oldest first; with `stamps`, each also carries the time it was typed. Read by the connector
    (sessions.turns: only the end of the file, unless `turns` and `tail_bytes` are None); the words are put here."""
    rows = cc_sessions.turns(path, turns, tail_bytes)
    return [(worded(kind, text), reply, stamp) if stamps else (worded(kind, text), reply) for kind, text, reply, stamp in rows]


def messages_of(rows, limit=1900):
    """A conversation as the Discord messages to post when a session is attached: for every turn (as the
    connector's sessions.turns gives them, here or on another machine) what the owner typed (quoted, with the
    time) and Claude's last message of that turn, nothing cut."""
    out = []
    for kind, said, answer, stamp in rows:
        text, reply = masking.masked(said or ""), masking.masked(answer) if answer else answer  # (B-12: before it is cut into messages)
        prompt = worded(kind, text)
        try:
            when = time.strftime("%m-%d %H:%M", time.localtime(
                calendar.timegm(time.strptime(stamp[:19], "%Y-%m-%dT%H:%M:%S"))))
        except ValueError:
            when = ""
        if prompt.startswith(TASK):  # not something the owner typed
            out += split_message("⚙️ **%s** %s" % (when, prompt[len(TASK):].lstrip("：")), limit)
        else:
            quoted = "\n".join("> " + row for row in prompt.split("\n"))
            out += split_message("🧑 **%s**\n%s" % (when, quoted), limit)
        out += split_message(reply or "（這一輪沒有文字回覆）", limit)
    return out


def history_messages(path, limit=1900):
    """The whole conversation of a transcript on this machine as those messages."""
    return messages_of(cc_sessions.turns(path, None, None), limit)


def backfilled(got):
    """The messages to post of what `dcremote backfill` on another machine answered: the conversation's turns, read
    over there by the connector, worded here."""
    turns = got.get("turns")
    return messages_of(turns) if isinstance(turns, list) else []


def recap_text(pairs, prompt_chars=400, reply_chars=1200):
    """The recap as Discord text: each prompt quoted, each reply underneath, long ones cut in the middle of nothing."""
    if not pairs:
        return "（讀不到這個對話之前的內容）"
    cut = lambda t, n: t if len(t) <= n else t[:n].rstrip() + " …（略）"  # noqa: E731
    parts = []
    for asked, answer in pairs:
        prompt, reply = masking.masked(asked or ""), masking.masked(answer) if answer else answer
        quoted = "\n".join("> " + row for row in cut(prompt, prompt_chars).split("\n"))
        parts.append("%s\n%s" % (quoted, cut(reply, reply_chars) if reply else "（這一輪還沒有回覆）"))
    return "\n\n".join(parts)


def _short(text, n):
    text = " ".join(text.split())
    return text if len(text) <= n else text[:n] + "…"


def label(s, current=None):
    """One line for a conversation in Discord's /resume picker."""
    return "%s · %s%s" % (time.strftime("%m-%d %H:%M", time.localtime(s["mtime"])),
                          _short((s["title"] + "：" if s["title"] else "") + s["last"], 70),
                          "（目前）" if s["id"] == current else "")


def listing(items, current=None, mine=(), taken=()):
    """The !resume list. `mine`: ids this channel has held; `taken`: ids other channels are on right now."""
    if not items:
        return "這個工作目錄還沒有可以接回的對話。"
    rows = []
    for i, s in enumerate(items, 1):
        tag = "　← 目前" if s["id"] == current else "　（其他頻道使用中）" if s["id"] in taken else \
              "　★" if s["id"] in mine else ""
        about = s["title"] or _short(s["first"], 40)
        if s["prompts"] > 1 or s["title"]:
            about += "　最後一句：" + _short(s["last"], 50)
        rows.append("`%d` %s · %d 則 · `%s`%s\n　%s" % (
            i, time.strftime("%m-%d %H:%M", time.localtime(s["mtime"])), s["prompts"], s["id"][:8], tag, about))
    return ("**這個工作目錄最近的對話**（★ 是這個頻道用過的）\n%s\n"
            "用 `!resume <編號>` 或 `!resume <id 開頭>` 接回。" % "\n".join(rows))
