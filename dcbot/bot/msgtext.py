"""One Discord message as text, for everything that reads messages back (C-172a P1a, instance docs/refactor/
c172a-design.md): dcctl history (`altention dc read`), the reply context, forwards, frames, the boards.

Discord's REST shape (a dict) is what it reads; `of_discord()` turns discord.py's Message into it. The whole
component tree is walked and laid out top to bottom as the message shows: content as it is; each embed's author,
title, description, fields (「名稱：值」), footer; a Text Display's words; a Container's lines each after 「▌」, its
accent colour named on its first line (「▌〔黃〕」); a Section's words with what sits beside them (「〔按鈕：完成〕」,
「〔圖：…〕」); a row of buttons 「〔按鈕：A｜B〕」, a select 「〔選單：提示字〕」; images and files 「〔圖 2 張：…〕」
「〔檔案：name〕」; a separator an empty line; a reply 「↪ 回覆 誰：前 50 字」; a sticker, a poll, a voice message;
a forward's message through the same function. Attachments are not listed here: the callers list them (with their
URLs) as they did.
Standard library only (tools/dcctl runs it with the system Python); Python 3.8.
"""

TEXT, CONTAINER, SECTION, ROW, BUTTON = 10, 17, 9, 1, 2
SELECTS = (3, 5, 6, 7, 8)
THUMBNAIL, GALLERY, FILE, SEPARATOR = 11, 12, 13, 14
VOICE = 1 << 13  # a message's flags: a voice message
COLOURS = (("紅", (0xE7, 0x4C, 0x3C)), ("橘", (0xE6, 0x7E, 0x22)), ("黃", (0xF1, 0xC4, 0x0F)), ("綠", (0x2E, 0xCC, 0x71)),
           ("藍", (0x34, 0x98, 0xDB)), ("紫", (0x9B, 0x59, 0xB6)), ("灰", (0x95, 0xA5, 0xA6)))
REPLY_CHARS = 50


def colour_name(value):
    """The nearest of a few named colours to an accent colour (an int 0xRRGGBB), or None."""
    if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 0xFFFFFF:
        return None
    rgb = ((value >> 16) & 255, (value >> 8) & 255, value & 255)
    return min(COLOURS, key=lambda c: sum((a - b) ** 2 for a, b in zip(rgb, c[1])))[0]


def _str(v):
    return v if isinstance(v, str) else ""


def embed_rows(e):
    """An embed's words, top to bottom: author, title, description, fields, footer."""
    if not isinstance(e, dict):
        return []
    rows = [_str((e.get("author") or {}).get("name")), _str(e.get("title")), _str(e.get("description"))]
    for f in e.get("fields") or []:
        if isinstance(f, dict):
            name, value = _str(f.get("name")), _str(f.get("value"))
            rows.append("%s：%s" % (name, value) if name and value else name or value)
    rows.append(_str((e.get("footer") or {}).get("text")))
    return [r for r in rows if r]


def _media(c):
    item = c.get("media") if isinstance(c.get("media"), dict) else {}
    return _str(c.get("description")) or _str(item.get("description"))


def _beside(c):
    """What sits beside a Section's words: a button, a thumbnail."""
    t = c.get("type") if isinstance(c, dict) else None
    if t == BUTTON:
        return "〔按鈕：%s〕" % (_str(c.get("label")) or _str((c.get("emoji") or {}).get("name")) or "?")
    if t == THUMBNAIL:
        return "〔圖：%s〕" % (_media(c) or "沒有描述")
    return ""


def component_rows(items):
    """The rows a component tree shows, top to bottom (see the module's note)."""
    out = []
    for c in items or ():
        if not isinstance(c, dict):
            continue
        t = c.get("type")
        if t == TEXT:
            out.extend(_str(c.get("content")).split("\n") if _str(c.get("content")) else [])
        elif t == CONTAINER:
            inner = component_rows(c.get("components"))
            name = colour_name(c.get("accent_color"))
            if name:
                inner = ["〔%s〕%s" % (name, inner[0] if inner else "")] + inner[1:]
            out.extend("▌" + r for r in inner)
        elif t == SECTION:
            words = component_rows(c.get("components"))
            beside = _beside(c.get("accessory"))
            if beside:
                words = (words[:-1] + ["%s %s" % (words[-1], beside)]) if words else [beside]
            out.extend(words)
        elif t == ROW:
            buttons = [_str(b.get("label")) or _str((b.get("emoji") or {}).get("name")) or "?"
                       for b in c.get("components") or [] if isinstance(b, dict) and b.get("type") == BUTTON]
            selects = [_str(s.get("placeholder")) or "選一個" for s in c.get("components") or []
                       if isinstance(s, dict) and s.get("type") in SELECTS]
            if buttons:
                out.append("〔按鈕：%s〕" % "｜".join(buttons))
            out.extend("〔選單：%s〕" % s for s in selects)
        elif t in SELECTS:
            out.append("〔選單：%s〕" % (_str(c.get("placeholder")) or "選一個"))
        elif t == BUTTON:
            out.append("〔按鈕：%s〕" % (_str(c.get("label")) or "?"))
        elif t == GALLERY:
            items_ = [i for i in c.get("items") or [] if isinstance(i, dict)]
            said = "、".join(d for d in (_media(i) for i in items_) if d)
            out.append("〔圖 %d 張%s〕" % (len(items_), "：" + said if said else ""))
        elif t == THUMBNAIL:
            out.append(_beside(c))
        elif t == FILE:
            f = c.get("file") if isinstance(c.get("file"), dict) else {}
            url = _str(f.get("url"))
            out.append("〔檔案：%s〕" % (_str(c.get("name")) or url.split("attachment://", 1)[-1].rsplit("/", 1)[-1] or "?"))
        elif t == SEPARATOR:
            out.append("")
        elif isinstance(c.get("components"), list):  # a kind not known here: its parts still read
            out.extend(component_rows(c["components"]))
    return out


def reply_line(ref):
    """「↪ 回覆 誰：前 50 字」 for the message replied to (its REST dict), or ""."""
    if not isinstance(ref, dict):
        return ""
    a = ref.get("author") or {}
    who = "機器人" if a.get("bot") else (_str(a.get("global_name")) or _str(a.get("username")) or "?")
    words = " ".join(text_of(ref, reply=False).split())
    return "↪ 回覆 %s：%s" % (who, words[:REPLY_CHARS] + ("…" if len(words) > REPLY_CHARS else ""))


def text_of(m, reply=True):
    """The text of one message (Discord's REST dict), top to bottom; "" when it shows nothing to read."""
    if not isinstance(m, dict):
        return ""
    rows = []
    if reply and m.get("referenced_message"):
        rows.append(reply_line(m["referenced_message"]))
    if _str(m.get("content")):
        rows.append(m["content"])
    for e in m.get("embeds") or []:
        rows.extend(embed_rows(e))
    rows.extend(component_rows(m.get("components")))
    for s in m.get("sticker_items") or m.get("stickers") or []:
        if isinstance(s, dict):
            rows.append("〔貼圖：%s〕" % (_str(s.get("name")) or "?"))
    poll = m.get("poll")
    if isinstance(poll, dict):
        q = _str((poll.get("question") or {}).get("text"))
        answers = [_str((a.get("poll_media") or {}).get("text")) for a in poll.get("answers") or [] if isinstance(a, dict)]
        rows.append("〔投票：%s〕" % "｜".join(x for x in [q] + answers if x))
    if isinstance(m.get("flags"), int) and m["flags"] & VOICE:
        secs = next((a.get("duration_secs") for a in m.get("attachments") or [] if isinstance(a, dict)), None)
        rows.append("〔語音 %s 秒〕" % (int(secs) if isinstance(secs, (int, float)) else "?"))
    for snap in m.get("message_snapshots") or []:  # a forward: the original is here, not in content
        inner = (snap or {}).get("message") if isinstance(snap, dict) else None
        rows.append("[轉發] " + (text_of(inner, reply=False) if isinstance(inner, dict) else "") or "[轉發] （沒有文字）")
    return "\n".join(r for r in rows if r is not None).strip("\n")


def as_dicts(objs):
    """discord.py's components or embeds as Discord's dicts (to_dict()); dicts left as they are; what cannot be read
    is left out."""
    out = []
    for o in objs or ():
        if isinstance(o, dict):
            out.append(o)
            continue
        try:
            out.append(o.to_dict())
        except Exception:
            continue
    return out


def of_discord(message):
    """discord.py's Message as Discord's REST dict, as far as text_of() reads it (never raises: what cannot be read
    is left out)."""
    def safe(fn, default):
        try:
            return fn()
        except Exception:
            return default
    get = lambda name, default=None: getattr(message, name, default)  # noqa: E731
    author = get("author")
    out = {"id": str(get("id", "")), "content": get("content") or "",
           "author": {"bot": bool(getattr(author, "bot", False)), "username": getattr(author, "name", None),
                      "global_name": getattr(author, "global_name", None)},
           "embeds": as_dicts(get("embeds")),
           "components": as_dicts(get("components")),
           "attachments": safe(lambda: [{"filename": a.filename, "url": a.url,
                                         "duration_secs": getattr(a, "duration", None)} for a in get("attachments") or []], []),
           "sticker_items": safe(lambda: [{"name": s.name} for s in get("stickers") or []], []),
           "flags": safe(lambda: int(get("flags").value), 0)}
    ref = safe(lambda: get("reference").resolved, None)
    if ref is not None and hasattr(ref, "content"):
        out["referenced_message"] = safe(lambda: of_discord(ref), None)
    snaps = safe(lambda: list(get("message_snapshots") or []), [])
    if snaps:
        out["message_snapshots"] = [{"message": {"content": getattr(s, "content", "") or "",
                                                 "embeds": as_dicts(getattr(s, "embeds", None)),
                                                 "components": as_dicts(getattr(s, "components", None))}}
                                    for s in snaps]
    poll = get("poll")
    if poll is not None:
        out["poll"] = safe(lambda: {"question": {"text": poll.question}, "answers": [{"poll_media": {"text": a.text}}
                                                                                      for a in poll.answers]}, None)
    return out
