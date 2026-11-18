"""A message payload a tool hands the bot with altention dc post --json (C-172a P1d, instance docs/refactor/
c172a-design.md), made ready to send: where it comes from on top when it goes to another's channel, the flags asked
for, the files' attachments, the message it answers. Pure functions of what they are handed: each returns the new
payload and why it cannot be sent (a sentence for the one who sent it) or None; the payload handed in is never
changed. Standard library only (tools/dcctl may use them with the system Python)."""
import copy

V2 = 1 << 15  # IS_COMPONENTS_V2: the words are in Text Displays, no content
SILENT = 1 << 12  # SUPPRESS_NOTIFICATIONS
NO_EMBEDS = 1 << 2  # SUPPRESS_EMBEDS: no link previews
CONTENT_MOST = 2000  # Discord's, in UTF-16 units
COMPONENTS_MOST = 40  # a V2 message's components in all, nested ones counted (Discord's)
SOURCE = "-# 📨 來自 %s"  # the line on top of a post to another's channel (frames.SOURCED reads it back)


def units(text):
    return len(text.encode("utf-16-le")) // 2


def count(components):
    """Every component of a payload, nested ones (in containers, sections, rows; an accessory) counted."""
    n = 0
    for c in components or ():
        if isinstance(c, dict):
            n += 1 + count(c.get("components")) + (count([c["accessory"]]) if isinstance(c.get("accessory"), dict) else 0)
    return n


def sourced(body, where):
    """`body` with the line saying where it comes from on top (`where`: "<#id>", or a sender with no channel in
    words). Plain: the content's first line, before a code block too ("-# …\n" counted in the 2000; an empty content:
    the line alone). V2: a Text Display at the top. (payload, why not): never cut to fit (manager 11-16)."""
    line = SOURCE % where
    out = copy.deepcopy(body)
    if int(out.get("flags") or 0) & V2:
        parts = out.get("components") if isinstance(out.get("components"), list) else []
        if count(parts) + 1 > COMPONENTS_MOST:
            return None, "放不下來源那一行：元件已經 %d 個（上限 %d），請少一個再送" % (count(parts), COMPONENTS_MOST)
        out["components"] = [{"type": 10, "content": line}] + parts
        return out, None
    content = out.get("content") if isinstance(out.get("content"), str) else ""
    content = line + ("\n" + content if content else "")
    if units(content) > CONTENT_MOST:
        return None, "放不下來源那一行：加上它之後是 %d 字（上限 %d），請短一點再送（不會截斷）" % (units(content), CONTENT_MOST)
    out["content"] = content
    return out, None


def flagged(body, silent=False, no_embeds=False):
    """`body` with the flags --silent (no push) and --no-embeds (no link previews) add to its own."""
    if not silent and not no_embeds:
        return body
    flags = int(body.get("flags") or 0) | (SILENT if silent else 0) | (NO_EMBEDS if no_embeds else 0)
    return dict(body, flags=flags)


def with_files(body, names):
    """`body` with an attachments list for the files sent with it, in --file's order, when it has none of its own
    (a payload refers to one as attachment://<name>)."""
    if not names or "attachments" in body:
        return body
    return dict(body, attachments=[{"id": i, "filename": name} for i, name in enumerate(names)])


def replying(body, message_id, channel_id):
    """`body` answering a message of that channel (--reply-to): shown as a reply; a message gone is no error."""
    if not message_id:
        return body
    return dict(body, message_reference={"message_id": str(message_id), "channel_id": str(channel_id),
                                         "fail_if_not_exists": False})


def checked(body):
    """Why a payload is not one message to post, or None: a JSON object with something in it."""
    if not isinstance(body, dict) or not body:
        return "--json 要是一個有內容的 JSON 物件（一則訊息的 payload）"
    return None
