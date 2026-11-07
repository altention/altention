"""Files the owner attaches in Discord: saved locally so Claude can open them with its own tools.

A Discord attachment URL is signed and expires, and Claude cannot look at an image through a URL anyway.
Saved under state/attachments/<channel>/ (not in git) and pruned after KEEP_DAYS. A channel whose Claude works on
another machine gets the file there too (dcremote attach, into that machine's state/attachments/<channel>/, pruned
there), and the path over there (C-107): the one here does not exist where its Claude reads.
"""
import asyncio
import base64
import os

from . import attachcore, config, remote

DIR = os.path.join(config.STATE, "attachments")
KEEP_DAYS = attachcore.KEEP_DAYS
MAX_BYTES = 100 * 1024 * 1024
REMOTE_MAX = 25 * 1024 * 1024  # sent over SSH as base64 (a third more): bigger ones stay here (manager 2026-10-28)
REMOTE_SECS = 60  # the message waits for this before it goes to Claude; past it, it goes with the path here
safe_name = attachcore.safe_name


def target(channel_id, message_id, attachment_id, name):
    return attachcore.target(DIR, channel_id, message_id, attachment_id, name)


def name_of(a):
    """The name the owner's file had. Discord gives a name that is not plain ASCII (「截圖.png」) a made-up one in
    `filename` and keeps the real one in `title` (discord.py 2.5); filename's suffix is added when the title lacks it."""
    title = getattr(a, "title", None)
    if not isinstance(title, str) or not title.strip():
        return a.filename
    ext = os.path.splitext(a.filename or "")[1]
    return title if not ext or title.lower().endswith(ext.lower()) else title + ext


def read_b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


async def send(host, channel_id, message_id, attachment_id, name, path, other=None):
    """Hand a saved file to `host`'s dcremote, once (remote.run: never sent twice). (its path there, None) or
    (None, why not). Run as a subprocess the loop waits on: only this message waits, no other channel. `other`, a
    second name: tried when that machine refused `name` (exit 2, nothing written there), as an engine that takes
    fewer names would; a send whose answer is not known is never repeated."""
    data = await asyncio.get_running_loop().run_in_executor(None, read_b64, path)
    err = []
    code, out = await remote.dcremote(host, "attach", channel_id, message_id, attachment_id, name, input=data,
                                      timeout=REMOTE_SECS, err=err)
    there = out.strip().splitlines()[-1] if code == 0 and out.strip() else ""
    if code == 2 and other and other != name:
        return await send(host, channel_id, message_id, attachment_id, other, path)
    if there.startswith("/"):
        return there, None
    why = " / ".join(err) or "結束碼 %s" % code
    return None, "那台的引擎還不會收附件" if "不認得 attach" in why else why


async def save(message, host=None):
    """Save the message's attachments; `host`, the machine the channel's Claude works on (None: this one) gets
    them too. Returns the lines to add to the text Claude receives."""
    here = remote.this_machine()
    host = None if host == here else host
    lines = []
    for a in message.attachments:
        name = name_of(a)
        path = target(message.channel.id, message.id, a.id, name)
        try:
            if a.size and a.size > MAX_BYTES:
                raise ValueError("too large")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await a.save(path)
        except Exception as e:
            lines.append("[附件] %s 沒有存成功（%s），網址：%s" % (name, type(e).__name__, a.url))
            continue
        if host and os.path.getsize(path) > REMOTE_MAX:
            lines.append("[附件] %s 太大（超過 %dMB），沒有送到 %s；檔案在 %s 的 %s（要的話用 scp 從那台拿），網址：%s"
                         % (name, REMOTE_MAX // (1024 * 1024), host, here, path, a.url))
            continue
        if host:
            there, why = await send(host, message.channel.id, message.id, a.id, safe_name(name), path,
                                   safe_name(a.filename))
            if there is None:
                lines.append("[附件] %s 沒有送到 %s（%s）；檔案在 %s 的 %s（要的話用 scp 從那台拿），網址：%s" % (name, host, why, here, path, a.url))
                continue
            path = there
        lines.append("[附件] %s 已存到 %s（用 Read 工具開啟；圖片也可以直接看），網址：%s" % (name, path, a.url))
    return lines


def prune(root=None, days=KEEP_DAYS, now=None):
    """Delete saved attachments older than `days`. Returns how many were removed."""
    return attachcore.prune(root or DIR, days, now)
