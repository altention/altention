"""Where attachments are kept, here or on another machine (dcremote attach): names, the one way in, pruning.

Standard library only, Python 3.8 is enough: tools/dcremote imports it on the other machines, which must not load
bot/config.py (it reads the bot's settings file). bot/attachments.py, the bot's side, builds on it.
"""
import os
import re
import time

KEEP_DAYS = 14
NAME_CHARS = 80  # an engine before 0.5.1 took up to 80 characters and nothing else: a name under both still passes there
NAME_BYTES = 150  # in UTF-8, after "<message id>-<attachment id>-" (two ids of up to 25 digits): under 255 in all
SUFFIX_CHARS = 16  # a longer one is not a suffix to keep, only more of the name


def safe_name(name):
    """A file name that is safe to create and to type into a terminal: no path parts, spaces, quotes or control
    characters (letters of any language stay), not starting with a dot, and short enough (cut())."""
    name = re.sub(r"[^\w.\-]", "_", os.path.basename((name or "").replace("\\", "/")), flags=re.UNICODE)
    return cut(name.lstrip(".")) or "file"


def cut(name):
    """At most NAME_CHARS characters and NAME_BYTES bytes: the end of the stem goes first, so a suffix stays
    (「很長的名字.png」 still opens as a picture). Never empties the stem: what is left never starts with a dot."""
    stem, ext = os.path.splitext(name)
    if not stem or len(ext) > SUFFIX_CHARS:
        stem, ext = name, ""
    while len(stem) + len(ext) > NAME_CHARS or len((stem + ext).encode("utf-8")) > NAME_BYTES:
        if len(stem) > 1:
            stem = stem[:-1]
        else:
            stem, ext = (stem + ext)[:-1], ""
    return stem + ext


def target(root, channel_id, message_id, attachment_id, name):
    """<message id>-<attachment id>-<name>: Discord names every pasted screenshot image.png, so the
    attachment's own id keeps several of them in one message apart."""
    return os.path.join(root, str(channel_id), "%s-%s-%s" % (message_id, attachment_id, safe_name(name)))


def folder(root, channel_id):
    """root/<channel>/, made when it is not there yet (readable by this user only), its path as given (not resolved:
    Claude Code reads a file through a linked folder and through the real one alike as inside a folder it was handed
    through the link, and not the other way round; tried on 2.1.316, 2026-11-03). The channel's Claude is handed this
    folder when it is opened (--add-dir): it reads what the owner attaches without asking (C-151). ValueError for an
    id that is not Discord's; OSError when it cannot be made."""
    if not re.fullmatch(r"[0-9]{1,25}", str(channel_id)):
        raise ValueError("不是 Discord 的 id：%r" % (channel_id,))
    path = os.path.join(root, str(channel_id))
    os.makedirs(path, mode=0o700, exist_ok=True)
    return path


def store(root, channel_id, message_id, attachment_id, name, data):
    """Write one attachment sent from the bot's machine under root/<channel>/, readable by this user only. The ids
    must be Discord's (digits) and the path must fall inside root: what comes over SSH is not trusted to be a name.
    Returns the path; ValueError when refused."""
    for v in (channel_id, message_id, attachment_id):
        if not re.fullmatch(r"[0-9]{1,25}", str(v)):
            raise ValueError("不是 Discord 的 id：%r" % (v,))
    if safe_name(name) != name:  # the bot sends it made safe already: anything else did not come from it
        raise ValueError("檔名不安全：%r" % (name,))
    root = os.path.realpath(root)
    path = target(root, channel_id, message_id, attachment_id, name)
    if os.path.dirname(os.path.realpath(path)) != os.path.join(root, str(channel_id)):
        raise ValueError("路徑不在附件資料夾底下：%s" % path)
    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
    return path


def prune(root, days=KEEP_DAYS, now=None):
    """Delete saved attachments older than `days`. Returns how many were removed."""
    cutoff, n = (now or time.time()) - days * 86400, 0
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
