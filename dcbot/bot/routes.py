"""Routing between Discord channels and Claude Code sessions the owner runs in their own tmux.

A session's address is its tmux pane: {"socket": path of the tmux server's socket, "id": "%3"}.
- Discord -> session: the channel's state holds the address; tmuxio types into that pane.
- session -> Discord: every Claude Code on this machine calls `dchook --global` (installed in
  ~/.claude/settings.json). The hook looks its own pane ($TMUX, $TMUX_PANE) up in state/routes.json, written
  here, and reports to the channel found there; a pane that is not in the table stays silent.
So attaching a running session needs no restart, and the terminal keeps working alongside Discord.
"""
import os
import re
import time

import discord

from .routecore import bind_key, channel_of, entry, load, project_name, save, unbind  # noqa: F401  (the table itself: routecore.py)


def key(pane):
    """The pane's address as one string; a pane on another machine starts with that machine's name."""
    return ("%s|%s|%s" % (pane["host"], pane["socket"], pane["id"])) if pane.get("host") else \
        "%s|%s" % (pane["socket"], pane["id"])


def routed_to(pane, table):
    """The channel a pane is attached to: from its own machine's table (remote panes bring it along)."""
    return pane.get("routed") if pane.get("host") else channel_of(table.get(key(pane)))


async def bind_any(pane, channel_id):
    """bind(), also for a pane on another machine (its route table lives there, next to its hook)."""
    if pane.get("host"):
        from . import remote
        await remote.dcremote(pane["host"], "bind", "%s|%s" % (pane["socket"], pane["id"]), str(channel_id),
                              str(pane.get("pid") or ""))
    else:
        bind(pane, channel_id)


async def unbind_any(channel_id, host=None):
    if host:
        from . import remote
        await remote.dcremote(host, "unbind", str(channel_id))
    else:
        unbind(channel_id)


def bind(pane, channel_id):
    """Route the pane to the channel (routecore.bind_key, by the pane's address on this machine)."""
    bind_key(key(pane), channel_id, pane.get("pid"))


def channel_name(pane, project):
    """A Discord channel name for a pane: "<tmux session>-<window>", so one session's windows sort together.
    The window is its name if the owner gave it one, else its number; a session that is just a number is
    spelled "<project>-<number>"."""
    base = "%s-%s" % (project, pane["session"]) if pane["session"].isdigit() else pane["session"]
    name = "%s-%s" % (base, pane.get("window_name") or pane["window"])
    return re.sub(r"[^\w-]+", "-", name.lower(), flags=re.UNICODE).strip("-")[:90] or "session"


def project_of(pane):
    return pane.get("project") or project_name(pane["cwd"])  # a remote pane's project is worked out over there


def by_project(panes, table):
    """{project: [panes not attached yet]}, in the order the panes are listed."""
    groups = {}
    for p in panes:
        if not routed_to(p, table):
            groups.setdefault(project_of(p), []).append(p)
    return groups


PROJECT = "project:"  # `!attach project:<name>` attaches every pane of that project; `!attach all` everything


def topic(host, pane):
    """The channel topic of an attached pane. A channel put away by !detach is taken out again for the
    pane it was made for, and this is how it is recognised (so its path stays shortened as it always was, also for
    another machine's pane: a topic written before must still match)."""
    return "%s 上的 Claude Code：tmux %s:%s，%s" % (host, pane["session"], pane["window"], short_cwd(pane["cwd"]))


def archived_channel(channels, host, pane, project):
    """Among the archive's channels, the one this pane had before (same name, same topic), if any."""
    name, want = channel_name(pane, project), topic(host, pane)
    return next((c for c in channels if c.name == name and (c.topic or "") == want), None)


def sibling_cwd(sibling_ids, channels_state):
    """The folder the other channels of a category work in (the most common one that still exists), so a
    channel opened by hand inside a project's category works in that project too. None if there is none."""
    counts = {}
    for cid in sibling_ids:
        cwd = (channels_state.get(str(cid)) or {}).get("cwd")
        if cwd and os.path.isdir(cwd):
            counts[cwd] = counts.get(cwd, 0) + 1
    return max(sorted(counts), key=lambda c: counts[c]) if counts else None


def short_cwd(cwd, host=None):
    """~ for this machine's home; a path on another machine (`host`) as it is: this home means nothing there."""
    if host:
        return cwd
    home = os.path.expanduser("~")
    return "~" + cwd[len(home):] if cwd == home or cwd.startswith(home + "/") else cwd


def window(pane):
    return "%s%s:%s" % ("%s · " % pane["host"] if pane.get("host") else "", pane["session"],
                        pane.get("window_name") or pane["window"])


def label(pane):
    """One line for a pane in Discord's /attach picker."""
    return "%s · %s" % (window(pane), short_cwd(pane["cwd"], pane.get("host")))


BUTTON = "att:"  # custom_id of a button under the !attach list: "att:<when it was listed>:<what !attach takes>"
BUTTON_SECS = 15 * 60  # an old list no longer matches what is running; a stray click on it must do nothing


def button_ref(custom_id, now=None):
    """What a clicked button asks !attach for; "" if the list it belongs to is too old; None if not ours."""
    if not (custom_id or "").startswith(BUTTON):
        return None
    when, _, ref = custom_id[len(BUTTON):].partition(":")
    if not when.isdigit() or not ref:
        return None
    return ref if (now or time.time()) - int(when) <= BUTTON_SECS else ""


def buttons(panes, table, now=None):
    """A button per pane that is not attached yet, and one per project that has several. None if nothing to offer."""
    free = [p for p in panes if not routed_to(p, table)]
    picks = [("📁 整個專案 %s（%d 個）" % (name, len(ps)), PROJECT + name)
             for name, ps in by_project(panes, table).items() if len(ps) > 1]
    picks += [("%s（%s）" % (window(p), os.path.basename(p["cwd"].rstrip("/"))), key(p)) for p in free]
    stamp = "%s%d:" % (BUTTON, now or time.time())
    picks = [(text, ref) for text, ref in picks if len(stamp + ref) <= 100][:25]  # Discord's limits
    if not picks:
        return None
    view = discord.ui.View(timeout=None)
    for text, ref in picks:
        view.add_item(discord.ui.Button(label=text[:80], custom_id=stamp + ref,
                                        style=discord.ButtonStyle.primary if ref.startswith(PROJECT)
                                        else discord.ButtonStyle.secondary))
    return view


def pick(panes, ref):
    """The pane `ref` names: its number in the list (1-based) or its key."""
    if ref.isdigit():
        return panes[int(ref) - 1] if 1 <= int(ref) <= len(panes) else None
    return next((p for p in panes if key(p) == ref), None)


def listing(panes, table):
    """The !attach list. `table`: load(), to show which panes already have a channel."""
    if not panes:
        return ("這台機器的 tmux 裡沒有正在跑的 Claude Code。\n"
                "先在 tmux 裡開一個（例如 `tmux new -s 名稱`，再執行 `claude`），它就會出現在這裡。")
    rows = []
    for i, p in enumerate(panes, 1):
        to = routed_to(p, table)
        rows.append("`%d` tmux `%s` · `%s` · 專案 %s%s" % (i, window(p), short_cwd(p["cwd"], p.get("host")), project_of(p),
                                                       "　→ 已接到 <#%s>" % to if to else ""))
    return ("**tmux 裡的 Claude Code**（別台機器的會標出機器名稱）\n%s\n"
            "點下面的按鈕接上：機器人會在該專案的分類下開好頻道。按鈕 15 分鐘後失效。\n"
            "-# 也可以打字：`!attach <編號>`、`!attach project:<專案名>`、`!attach all`" % "\n".join(rows))
