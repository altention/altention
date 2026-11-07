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
import os
import socket
import sys

HOME_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # altention/dc/ -> the engine
sys.path.insert(0, HOME_REPO)
from altention.dc import botside  # noqa: E402  (which machine runs the bot, and the one way to its dcctl)

quote, dcctl_on, this_engine = botside.quote, botside.dcctl_on, botside.this_engine


def local_dcctl(engine=None):
    """botside.local_dcctl, falling back on this checkout's dcctl (a copy's own, not the engine botside came from)."""
    return botside.local_dcctl(engine, HOME_REPO)


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
    try:
        r = botside.run(args, prog="altention dc read", repo=HOME_REPO)
    except botside.NoBot as e:
        sys.exit("altention dc read：%s" % e)
    sys.exit(r.returncode)


if __name__ == "__main__":
    main()
