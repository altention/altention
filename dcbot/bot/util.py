"""Small pure helpers, kept free of discord imports so they are easy to test."""


def card_color(name):
    """A colour that stays the same for one sender, so a channel's posts are told apart at a glance."""
    import zlib
    palette = [0x4C78A8, 0xF58518, 0x54A24B, 0xB279A2, 0xE45756, 0x72B7B2, 0xEECA3B, 0x9D755D]
    return palette[zlib.crc32((name or "").encode("utf-8")) % len(palette)]


def embed_text(embeds):
    """What embeds (the 📨 cards of posts) say, as text: their heading and body."""
    out = []
    for e in embeds or []:
        author = getattr(getattr(e, "author", None), "name", None) if not isinstance(e, dict) else (e.get("author") or {}).get("name")
        body = getattr(e, "description", None) if not isinstance(e, dict) else e.get("description")
        out.append("\n".join(x for x in (author, body) if x))
    return "\n".join(x for x in out if x)


def reply_context(from_bot, content, attachments=0, limit=800, who=None):
    """The line put in front of a reply, saying what it replies to."""
    text = " ".join((content or "").split())
    text = text if len(text) <= limit else text[:limit] + "…"
    who = who or ("你（Claude）先前的這則訊息" if from_bot else "使用者自己先前的這則訊息")
    extra = "（附 %d 個附件）" % attachments if attachments else ""
    return "（這則是回覆，針對%s%s：「%s」）\n" % (who, extra, text or "（沒有文字）")


FORWARDED = "（使用者轉發了下面這則訊息過來；「> 」開頭的是原訊息，不是使用者現在說的話）"
THEN = "（使用者接著說：）"


def forward_context(who, content, when=None, message_id=None, link=None, notes=(), limit=6000):
    """How a message forwarded from another channel is put in front of the channel's agent: a first line that says
    these are not the owner's words now; then, as a reader of a channel shows a message, "[when] who (id …)"; its
    link; the message itself, every line quoted; and what else it held (`notes`: an embed, a sticker, that it was
    cut). `who`: who said it and where, in words (bot/frames.worded())."""
    text = (content or "").strip()
    whole = len(text)
    text = text if whole <= limit else text[:limit] + "…"
    head = "%s%s%s" % ("[%s] " % when if when else "", who or "別的頻道的訊息", " (id %s)" % message_id if message_id else "")
    rows = [FORWARDED, head] + (["連結：%s" % link] if link else [])
    rows.append("\n".join("> " + row for row in text.split("\n")) if text else "> （沒有文字）")
    if whole > limit:
        rows.append("（原訊息共 %d 字，這裡是前 %d 字；全文用上面的連結看）" % (whole, limit))
    return "\n".join(rows + list(notes)) + "\n"


def split_message(text, limit=1900):
    """Split text into Discord-sized chunks, preferring line breaks and keeping ``` fences balanced."""
    chunks, cur, fence = [], "", None
    for line in text.split("\n"):
        while len(line) > limit:  # a single huge line
            if cur:
                chunks.append(cur + ("\n```" if fence is not None else ""))
                cur = ("```%s\n" % fence) if fence is not None else ""
            room = limit - len(cur)
            cur += line[:room]
            line = line[room:]
            chunks.append(cur + ("\n```" if fence is not None else ""))
            cur = ("```%s\n" % fence) if fence is not None else ""
        add = (line if not cur else "\n" + line)
        if len(cur) + len(add) > limit - 4:
            chunks.append(cur + ("\n```" if fence is not None else ""))
            cur = (("```%s\n" % fence) if fence is not None else "") + line
        else:
            cur += add
        if line.strip().startswith("```"):
            fence = None if fence is not None else line.strip()[3:]
    if cur.strip():
        chunks.append(cur)
    return chunks or [""]


def target(st):
    """Where the channel's Claude lives, as tmuxio takes it: the attached pane, or the bot's session name."""
    return st.get("pane") or st.get("tmux")


def same_socket(a, b, here=False):
    """Do two paths name the same tmux socket? As written; or, both being of this machine (`here`), the same
    file (links followed); or the same but for macOS's /private in front (there /tmp, /var and /etc are links
    into /private: tmux names its socket /private/tmp/tmux-501/default, the bot writes /tmp/tmux-501/default,
    and a path of another machine cannot be followed from here)."""
    import os
    a, b = str(a or ""), str(b or "")
    if not a or not b:
        return False
    if a == b:
        return True
    plain = lambda path: path[len("/private"):] if path.startswith("/private/") else path  # noqa: E731
    if plain(a) == plain(b):
        return True
    try:
        return bool(here) and os.path.realpath(a) == os.path.realpath(b)
    except (OSError, ValueError):
        return False
