"""What a message in a channel is, apart from the frame the bot puts round what it posts.

The bot posts many things in one name: the channel's agent's reply (with a small status line under it), words the
agent said on the way (">>> "), a message another conversation sent here (a card headed "📨 來自 …"), a notification
("🔔 **[…]** "), a board ("📌 **…**"), what the owner typed at the terminal, its own notices, and the pieces of a turn's
thread. To one who reads a channel they all look like "the bot said". of() tells them apart and gives the content
without the frame, as a structure; the words for it are another layer's (worded()). Pure functions of what a message
holds: nothing of Discord's but the fields handed in, no state. Used for a forwarded message (step B-13); a reader of
channels (the protocol's `read`) is to use the same structure.

Kinds: REPLY (the channel's agent's reply), INTERIM (its words on the way), RELAYED (sent here by another
conversation, agent or script: `sender`), NOTICE (a notification: `sender`), BOARD (a board: `name`, `updated`),
TERMINAL (typed by the owner at the terminal, shown by the bot), PROCESS (a piece of a turn's 📋 thread), BOT (a
notice of the bot's own), BOT_UNKNOWN (the bot's, a reply or a notice: not to be told), OWNER, OTHER (somebody
else: `sender`), UNKNOWN (who said it could not be found out: `why`)."""
import re

from . import senders

REPLY, INTERIM, RELAYED, NOTICE, BOARD, TERMINAL, PROCESS, BOT, BOT_UNKNOWN, OWNER, OTHER, UNKNOWN = (
    "reply", "interim", "relayed", "notice", "board", "terminal", "process", "bot", "bot_unknown", "owner", "other", "unknown")
CARD = re.compile(r"^📨 來自 (.+?)(?:（(\d+)/(\d+)）)?$")
HEADED = re.compile(r"^📨 \*\*來自 (.+?)\*\*\n", re.S)
NOTIFIED = re.compile(r"^🔔 \*\*\[(.*?)\]\*\* ?", re.S)
BOARDED = re.compile(r"^📌 \*\*(.+?)\*\*\n(?:-# 更新於 ([^\n]*)\n)?", re.S)
TYPED = "⌨️ **終端機那邊輸入**\n"
SOURCED = re.compile(r"^-# 📨 來自 ([^\n]*)(?:\n|$)")  # the line on top of a post to another's channel (dc post --json,
# C-172a P1d: bot/payloads.py writes it); only the bot puts it there
SOURCE_WORDS = re.compile(r"^📨 來自 ")
PIECES = ("🧑 **你**", "💬 ", "🔧 **", "❌\n```", "-# ⤵", "…（這一輪太長")  # how the pieces of a turn's thread begin (bot/process.py)
NOTICES = ("ℹ️", "⚠️", "❓", "📋", "🧩", "🤖", "✅", "⏳", "⌛", "處理中…", "🖥", "📂", "📝", "🔁", "⏸️", "▶️")  # how the bot's
# own notices begin (⏸️ ▶️: the talk limit's hold, C-184's pause and resume)
ZWSP = "\u200b"


def reserved(text):
    """The frame a text begins with that only the bot may put there, or None: what reads back as another channel's
    post (📨 **來自 …**), a notification (🔔 **[…]**), a board (📌 **…**), or what the owner typed at the terminal
    (messaging review P0-6), or where a post to another's channel comes from (-# 📨 來自 …, C-172a P1d); looked for
    after a "-# " at the start too. A Claude or a script writing it would be read as one of those."""
    text = text or ""
    text = text[3:] if text.startswith("-# ") else text  # (small print first is no way round it: C-172a P1d)
    for name, hit in (("📨 **來自 …**", HEADED.match(text)), ("🔔 **[…]**", NOTIFIED.match(text)),
                      ("📌 **…**", BOARDED.match(text)), (TYPED.strip(), text.startswith(TYPED)),
                      ("-# 📨 來自 …", SOURCE_WORDS.match(text))):
        if hit:
            return name
    return None


def defused(text):
    """A reply of the channel's agent that begins with a reserved frame, shown as it is but no longer read back as
    that frame: a zero-width space before its first "**"."""
    if not reserved(text):
        return text
    i = text.find("**")
    return text[:i] + ZWSP + text[i:] if i >= 0 else ZWSP + text


STATUS = re.compile(r"^-# (?=.*(×\d+|\d+ 秒|context \d+%|背景工作 \d+ 個進行中|<@\d+>))")  # the line events.footer() makes


def footed(text):
    """Does a message end with the status line the bot puts under a reply ("-# Bash×1 · 4 秒 · context 5%": tools,
    seconds, work in the background, context, a mention of the owner)?"""
    return bool(STATUS.match((text or "").rstrip().split("\n")[-1]))


def unfooted(text):
    """A message of the bot's without that one status line at its end: the bot's frame, no part of what was said.
    Only that line: small print the agent itself wrote at the end of its reply ("-# 註：…") is the reply's."""
    lines = (text or "").rstrip().split("\n")
    if lines and STATUS.match(lines[-1]):
        lines.pop()
    return "\n".join(lines).rstrip()


def unquoted(text):
    return "\n".join(line[2:] if line.startswith("> ") else line[1:] if line.startswith(">") else line for line in text.split("\n"))


NOT_LOOKED = object()  # of()'s `entry` when the caller did not ask the ledger (nothing is said of guessing then)


def of(content, embeds=(), by_bot=False, by_owner=False, remembered=False, author=None, board=None, entry=NOT_LOOKED,
       name_of=None):
    """of_guessed() unless the ledger has the message (`entry`: bot/ledger.py's, C-172a P1a): then what it is, and
    which frame comes off, is looked up, never guessed from how it looks (a reply's own "-#" small print with "×3 · 10
    秒" in it stays; a board is told by its name). A bot's message the ledger was asked about and does not have is
    told by its looks, and says so: "guessed" (「未登記」)."""
    if not by_bot or not isinstance(entry, dict) or entry.get("intent") not in INTENTS_KNOWN:
        out = of_guessed(content, embeds, by_bot, by_owner, remembered, author, board)
        if by_bot and entry is not NOT_LOOKED:
            out["guessed"] = True
        return out
    content, intent, framed = content or "", entry["intent"], set(entry.get("framed") or ())
    if intent == "reply":
        return {"kind": REPLY, "text": unfooted(content) if "status" in framed else content, "registered": True}
    if intent == "board":
        m = BOARDED.match(content) if "board_head" in framed else None
        return {"kind": BOARD, "name": entry.get("key") or board or (m.group(1) if m else "?"),
                "updated": m.group(2) if m else None, "text": content[m.end():] if m else content, "registered": True}
    if intent == "post" and "source" in framed:  # (dc post --json to another's channel: its line on top comes off)
        m = SOURCED.match(content)
        return {"kind": RELAYED, "sender": senders.label(entry.get("from"), name_of) or "?",
                "text": content[m.end():] if m else content, "registered": True}
    if intent in ("post", "notify", "talk"):
        out = of_guessed(content, embeds, by_bot, by_owner, remembered, author, board)
        if out["kind"] not in (RELAYED, NOTICE):  # (posted into its sender's own channel: no frame round it)
            out = {"kind": NOTICE if intent == "notify" else RELAYED, "sender": senders.label(entry.get("from"), name_of) or "?",
                   "text": content}  # (the ledger's key in words: "#name" by `name_of(channel id)`; bot/senders.py)
        out["registered"] = True
        return out
    if "interim" in framed and content.startswith(">>> "):
        return {"kind": INTERIM, "text": content[4:], "registered": True}
    return {"kind": BOT, "text": content, "registered": True}


INTENTS_KNOWN = ("talk", "post", "notify", "board", "card", "reply", "status")


def of_guessed(content, embeds=(), by_bot=False, by_owner=False, remembered=False, author=None, board=None):
    """{"kind", "text"} and, by the kind, "sender", "name", "updated", "part" (i, n: one card of several).
    `content`: the message's text; `embeds`: [{"author", "title", "description", "url"}] (plain values); `by_bot`: the
    bot itself posted it; `by_owner`: the owner did; `remembered`: the bot has it on record as a reply of the
    channel's agent (a plain fact, where there is one: the turn it remembers by that message's id); `author`: the
    display name of whoever posted it, for somebody else's; `board`: the name of the board the bot has this message on
    record for (its channel state's "boards"): the parts after the first have no heading of their own."""
    content = content or ""
    if not by_bot:
        return {"kind": OWNER, "text": content} if by_owner else {"kind": OTHER, "sender": author or "?", "text": content}
    if remembered:  # a plain fact, ahead of how it looks: a reply that begins "📌 **重點**" or ">>> " is a reply
        return {"kind": REPLY, "text": unfooted(content)}
    if board:  # (a plain fact too; the first part's heading is taken off as ever)
        m = BOARDED.match(content)
        return {"kind": BOARD, "name": board, "updated": m.group(2) if m else None, "text": content[m.end():] if m else content}
    for e in embeds or ():
        m = CARD.match(str((e or {}).get("author") or ""))
        if m:
            out = {"kind": RELAYED, "sender": m.group(1), "text": str(e.get("description") or "")}
            if m.group(2):
                out["part"] = (int(m.group(2)), int(m.group(3)))
            return out
    m = HEADED.match(content)
    if m:
        return {"kind": RELAYED, "sender": m.group(1), "text": content[m.end():]}
    m = SOURCED.match(content)
    if m:
        return {"kind": RELAYED, "sender": m.group(1), "text": content[m.end():]}
    m = NOTIFIED.match(content)
    if m:
        return {"kind": NOTICE, "sender": m.group(1) or "?", "text": content[m.end():]}
    m = BOARDED.match(content)
    if m:
        return {"kind": BOARD, "name": m.group(1), "updated": m.group(2), "text": content[m.end():]}
    if content.startswith(">>> "):
        return {"kind": INTERIM, "text": content[4:]}
    if content.startswith(TYPED):
        return {"kind": TERMINAL, "text": unquoted(content[len(TYPED):])}
    if content.startswith(PIECES):
        return {"kind": PROCESS, "text": content}
    if content.startswith(NOTICES) and not footed(content):
        return {"kind": BOT, "text": content}
    # the bot's, in no frame known here, or with a reply's status line under it though it begins like a notice (a
    # reply no longer remembered: only the newest are): a reply or a notice, not to be told
    return {"kind": BOT_UNKNOWN, "text": unfooted(content)}


def worded(what, where, connector=None):
    """Who said it, in words, for a reader of the channel `where` ("#秘書", or None when the channel is not known).
    The protocol's wording: not tied to one agent ("agent"; the channel's connector may be named), the sender of
    what was sent or notified as the sender wrote itself ("#頻道", "機器 上的 …"), a board by its name. A message
    another conversation sent is a "訊息" until senders say which kind (交談／紀錄)."""
    kind, place = what.get("kind"), where or "別的頻道"
    agent = "agent（%s）" % connector if connector else "agent"
    if kind == REPLY:
        return "%s 的 %s 的回覆" % (place, agent)
    if kind == INTERIM:
        return "%s 的 %s 中途說的話" % (place, agent)
    if kind == RELAYED:
        part = "（第 %d/%d 張）" % what["part"] if what.get("part") else ""
        return "%s 傳到 %s 的訊息%s" % (what.get("sender") or "?", place, part)
    if kind == NOTICE:
        return "%s 在 %s 的通知" % (what.get("sender") or "?", place)
    if kind == BOARD:
        return "%s 的看板「%s」%s" % (place, what.get("name") or "?", "（更新於 %s）" % what["updated"] if what.get("updated") else "")
    if kind == TERMINAL:
        return "使用者在 %s 的終端機打的" % place
    if kind == PROCESS:
        return "%s 的 📋 過程（機器人整理的）" % place
    if kind == BOT:
        return "機器人在 %s 的通知" % place
    if kind == BOT_UNKNOWN:
        return "機器人在 %s 的訊息（分不出是回覆還是通知）" % place
    if kind == OWNER:
        return "使用者（在 %s）" % place
    if kind == OTHER:
        return "%s 裡 %s 的訊息" % (place, what.get("sender") or "?")
    return "%s 的訊息（查不到是誰說的：%s）" % (place, what.get("why") or "原因不明")
