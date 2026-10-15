"""Files the owner attaches in Discord: saved locally so Claude can open them with its own tools.

A Discord attachment URL is signed and expires, and Claude cannot look at an image through a URL anyway.
Saved under state/attachments/<channel>/ (not in git) and pruned after KEEP_DAYS.
"""
import os
import re
import time

from . import config

DIR = os.path.join(config.STATE, "attachments")
KEEP_DAYS = 14
MAX_BYTES = 100 * 1024 * 1024


def safe_name(name):
    """A file name that is safe to create and to type into a terminal: no path parts, spaces or quotes."""
    name = re.sub(r"[^\w.\-]", "_", os.path.basename((name or "").replace("\\", "/")), flags=re.UNICODE)
    return name.lstrip(".")[:80] or "file"


def target(channel_id, message_id, name):
    return os.path.join(DIR, str(channel_id), "%s-%s" % (message_id, safe_name(name)))


async def save(message):
    """Save the message's attachments. Returns the lines to add to the text Claude receives."""
    lines = []
    for a in message.attachments:
        path = target(message.channel.id, message.id, a.filename)
        try:
            if a.size and a.size > MAX_BYTES:
                raise ValueError("too large")
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await a.save(path)
            lines.append("[附件] %s 已存到 %s（用 Read 工具開啟；圖片也可以直接看）" % (a.filename, path))
        except Exception as e:
            lines.append("[附件] %s 沒有存成功（%s），網址：%s" % (a.filename, type(e).__name__, a.url))
    return lines


def prune(root=None, days=KEEP_DAYS, now=None):
    """Delete saved attachments older than `days`. Returns how many were removed."""
    root, cutoff, n = root or DIR, (now or time.time()) - days * 86400, 0
    for d, _, names in os.walk(root):
        for name in names:
            path = os.path.join(d, name)
            try:
                if os.path.getmtime(path) < cutoff:
                    os.remove(path)
                    n += 1
            except OSError:
                pass
    return n
