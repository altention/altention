"""A message payload as JSON that dcctl send-json and dcctl edit hand the bot (C-172a P1b batch 4): what is refused,
and how the #audit line names it. Checked twice, by dcctl before it queues and by the bot before it sends: the queue
is files, a hand-written one is not dcctl's (manager 11-16). Standard library only: tools/dcctl runs this with the
system Python."""
from . import frames, util

QUIET = {"parse": []}  # what a payload's allowed_mentions is always made: nobody is pinged


def reserved_said(text, how):
    """Why a text is refused (P0-6: it begins with a frame only the bot uses), or None."""
    frame = frames.reserved(text or "")
    if frame:
        return "%s：沒有送出：開頭「%s」是機器人自己用的格式，讀回時會被當成別的頻道傳來的、通知、看板或終端機輸入。請換個開頭" % (util.tool(how), frame)
    return None


def refused(body, how="send-json"):
    """Why a payload is refused, or None: a reserved frame in its content or in its V2 Text Displays (that is how it
    reads back), or an embed made to look like the bot's relay card (📨 來自 …)."""
    said = reserved_said(body.get("content") if isinstance(body.get("content"), str) else "", how)
    said = said or reserved_said(util.components_text(body.get("components")), how)
    if said:
        return said
    for e in body.get("embeds") or []:
        if isinstance(e, dict) and frames.CARD.match(str((e.get("author") or {}).get("name") or "")):
            return "%s：沒有送出：embed 的 author 是「📨 來自 …」，那是機器人轉傳別的頻道訊息的卡片格式" % util.tool(how)
    return None


def quiet(body):
    """The payload as it is, except that it pings nobody, whatever its allowed_mentions said."""
    return dict(body, allowed_mentions=dict(QUIET))


def said(body):
    """How the #audit line names a payload send-json posted: its flags and how many components it has on top."""
    flags, parts = body.get("flags"), body.get("components")
    return "flags %s、頂層元件 %d 個" % (flags if flags is not None else "無", len(parts) if isinstance(parts, list) else 0)
