"""Claude Code sessions talking to each other, as seen from Discord.

Claude Code lets the sessions on one machine message each other by name (the SendMessage tool; the other
side receives a <cross-session-message from-name="…"> prompt). This module only joins those names to
Discord channels:
- a channel mention in the owner's message (<#id>) is spelled out with the session name behind it, so the
  owner can say "report to #st-0" and Claude knows whom to message;
- a message sent or received is shown in both channels ("📤 to #st-0", "📨 from #st-1").
A session's name comes from Claude Code's record of its running processes (~/.claude/sessions/<pid>.json),
found by the tmux pane (attached channels) or the tmux session (channels the bot started).
"""
import os
import re

from altention.connectors.claude_code import hooks as cc_hooks
from altention.connectors.claude_code import sessions as cc_sessions

MENTION = re.compile(r"<#(\d+)>")


def records():
    """Every running Claude Code that has a name: [{"name", "tmux", "pid", …}] (Claude Code's own records of its
    processes, read by the connector; a record whose process is gone is left out)."""
    return [rec for rec in cc_sessions.process_records() if rec.get("name") and os.path.exists("/proc/%s" % rec.get("pid"))]


def name_of(st, recs=None):
    """The Claude Code session name of a channel's session, or None."""
    for rec in recs if recs is not None else records():
        where = str(rec.get("tmux") or "")
        if st.get("pane"):
            if where.endswith("." + st["pane"]["id"]) and (not st.get("pane_session") or where.startswith(st["pane_session"] + ":")):
                return rec["name"]
        elif st.get("tmux") and where.startswith(st["tmux"] + ":"):
            return rec["name"]
    return None


def directory(channels_state):
    """{session name: channel id} for every channel whose session is running."""
    recs = records()
    out = {}
    for cid, st in channels_state.items():
        n = name_of(st, recs)
        if n:
            out[n] = int(cid)
    return out


def spell_mentions(text, channels_state, channel_name, cmd="altention"):
    """Put the session name behind each channel mention, for Claude. `channel_name(id)` gives "#name"; `cmd` is the
    altention command as that Claude's machine has it (remote.altention_cmd)."""
    names = dict((cid, n) for n, cid in directory(channels_state).items())

    def one(m):
        cid = int(m.group(1))
        label = channel_name(cid) or "#%d" % cid
        name = label.lstrip("#")
        read = "讀它最近的訊息：`%s dc read %s`" % (cmd, name)
        post = "貼東西過去或跟它說話：`%s dc post %s \"…\"`" % (cmd, name)
        if cid in names:
            return "%s（這個頻道有一個 Claude Code 對話；%s；%s）" % (label, post, read)
        return "%s（%s；%s）" % (label, post, read)
    return MENTION.sub(one, text)


UNNAMED = "另一個對話"  # the sender of a message between conversations that gives no name


def incoming(prompt):
    """(sender name, message) if the prompt is a message from another session, else None. How such a prompt is
    told is the connector's (hooks.peer_message, in one pass over the text); the words for a sender with no name
    are here."""
    got = cc_hooks.peer_message(prompt)
    return got and (got[0] or UNNAMED, got[1])


def who(name, channels_state):
    """How to show a session in Discord: its channel mention if it has one, else its name."""
    cid = directory(channels_state).get(name)
    return "<#%d>" % cid if cid else "`%s`" % name
