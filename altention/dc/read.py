#!/usr/bin/env python3
"""altention dc read: read a Discord channel from any machine, so any Claude Code session can look at another channel.

  altention dc read bot-test             the last 20 messages of #bot-test, oldest first
  altention dc read bot-test 50          the last 50
  altention dc read bot-test --threads   its threads (subagents, workflows, /btw), with IDs
  altention dc read 100000000000000021   a thread (or a channel) by ID
  altention dc read bot-test --claude 5  the last 5 turns of the Claude conversation behind the channel (fuller than
                                         the channel: what was asked and the whole replies, from the transcript)
  altention dc read --map                the whole server: categories, what each channel is for (Claude and its folder,
                                         terminal, plain), and which channel you are in

Like altention dc notify, it runs dcbot's dcctl on the machine with the `dcbot` role (locally if that is this machine,
otherwise over SSH with the aliases in the machine list), so no Discord secret is needed here.
"""
import json
import os
import socket
import subprocess
import sys

HOME_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # altention/dc/ -> the engine


def local_dcctl(engine=None):
    """dcctl of the bot running on this machine: in its engine (`engine`, this machine's entry in the list, else
    ~/altention/engine); this checkout's only when that is not there. Not simply this checkout's: run from a worktree or
    a copy, posts would go into a state/ no running bot reads, and say nothing."""
    for base in ([engine] if engine else []) + ["~/altention/engine"]:
        path = os.path.join(os.path.expanduser(base), "dcbot", "tools", "dcctl")
        if os.path.exists(path):
            return path
    return os.path.join(HOME_REPO, "dcbot", "tools", "dcctl")

def quote(word):
    """One word for the shell on the bot's machine, always in single quotes (altention.config.shell_quote, kept
    here for the same reason as dcctl_on: zsh reads a bare word starting with = as a command's path)."""
    return "'" + str(word).replace("'", "'\"'\"'") + "'"


def dcctl_on(m):
    """dcctl on the bot's machine `m`, for a shell there: its engine is its list entry's "engine" (default
    ~/altention/engine; a leading ~ left to that shell). Worked out here, not by altention.config: the path problems are
    reported on must not depend on the settings that may be the problem."""
    engine = m.get("engine") if isinstance(m.get("engine"), str) and m.get("engine").strip() else "~/altention/engine"
    engine = "~" if engine == "~" else "~/" + quote(engine[2:]) if engine.startswith("~/") else quote(engine)
    return engine + "/dcbot/tools/dcctl"


def this_engine(name):
    """This machine's "engine" in the machine list, or None."""
    try:
        from altention import config
        engine = ((config.inventory().get("machines") or {}).get(name) or {}).get("engine")
        return engine if isinstance(engine, str) and engine.strip() else None
    except Exception:
        return None


def bot_machine():
    """(name, machine, here) of the machine that runs the bot (altention.config: this one if the bot's settings
    are here, else the machine list's; no list at all and no bot here is an error, said plainly)."""
    sys.path.insert(0, HOME_REPO)
    from altention import config, text
    try:
        name, m, here = config.bot_machine()
    except config.ConfigError as e:
        sys.exit("altention dc read：%s" % e)
    if name is None:
        sys.exit("altention dc read：" + text.t("bot.not_found"))
    return name, m, here


def dcctl_args(argv):
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    if argv[0] in ("--map", "--where"):  # who is asking: as altention dc post works out its sender
        if os.environ.get("DCBOT_CHANNEL"):
            return ["map", "--me", os.environ["DCBOT_CHANNEL"]]
        if os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
            return ["map", "--pane", "%s|%s|%s" % (socket.gethostname().split(".")[0], os.environ["TMUX"].split(",")[0],
                                                   os.environ["TMUX_PANE"])]
        return ["map"]
    where, rest = argv[0].lstrip("#"), argv[1:]
    if "--threads" in rest:
        return ["threads", where]
    if "--claude" in rest:
        n = rest[rest.index("--claude") + 1] if rest.index("--claude") + 1 < len(rest) else "5"
        return ["recap", where, n if n.isdigit() else "5"]
    n = next((a for a in rest if a.isdigit()), "20")
    return ["history", where, n, "--chars", "1500"]


def main():
    args = dcctl_args(sys.argv[1:])
    me = socket.gethostname().split(".")[0]
    name, m, here = bot_machine()
    if here:
        sys.exit(subprocess.run([local_dcctl(this_engine(name))] + args).returncode)
    remote = " ".join([dcctl_on(m)] + [quote(a) for a in args])
    for alias in m.get("ssh", []):
        if alias == "local":
            continue
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, remote])
        if r.returncode != 255:
            sys.exit(r.returncode)
    sys.exit("altention dc read：連不上 %s" % name)


if __name__ == "__main__":
    main()
