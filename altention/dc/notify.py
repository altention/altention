#!/usr/bin/env python3
"""altention dc notify: push a message to Discord from any machine.

  altention dc notify "訓練跑完了，loss 0.12"
  altention dc notify -c project-a "實驗 C 結束，結果在 results/c.json"
  altention dc notify -c "project-a/notifications" "…"          a channel in a given category (names can repeat)
  some-command 2>&1 | tail -20 | altention dc notify -c alerts  # message from stdin
  altention dc notify -c @notify --wait 30 "…"                 wait until it is on Discord (as altention dc post --wait)

Runs dcbot's dcctl on the machine with the `dcbot` role (locally if that's this machine, otherwise
over SSH using the aliases in the machine list), so no Discord secret is needed here. Default channel:
$DCNOTIFY_CHANNEL, else whatever the bot's machine sets (its instance's core.notify_channel, else notifications).
A name several channels share goes to one of them, saying so: a notification is never refused for that.

This is the path problems are reported on (a sync failing for hours, for one), so it does not depend on this
machine's settings, which may be the problem: without a readable machine list it uses the bot's machine it last
reached (~/.local/state/altention/bot.json, kept by the sync), and the default channel is decided where the bot runs. A channel that does not exist: the
notification goes to the default channel instead, saying where it was meant to go (it is never dropped; only the
default channel itself is made when it is not there). A channel that does not take it (Discord failing, no
permission) is treated the same; when the default does not take it either the words go to #audit, and when not even
that can be written the bot keeps the notification and tries again for a day (doctor: dcbot.posts-failed). altention dc notify never makes the channel you name: altention dc post --create does;
long text is split over several messages. A notification is only shown: even in a channel with a Claude
conversation, it is not handed to that Claude (that is what altention dc post is for).
"""
import argparse
import json
import os
import socket
import subprocess
import sys

HOME_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # altention/dc/ -> the engine
sys.path.insert(0, HOME_REPO)
from altention.dc import botside  # noqa: E402  (which machine runs the bot, and the one way to its dcctl)

quote, dcctl_on, this_engine = botside.quote, botside.dcctl_on, botside.this_engine


def local_dcctl(engine=None):
    """botside.local_dcctl, falling back on this checkout's dcctl (a copy's own, not the engine botside came from)."""
    return botside.local_dcctl(engine, HOME_REPO)


CACHE = os.path.expanduser("~/.local/state/altention/bot.json")  # the bot's machine as last found: {"name", "ssh"}


def bot_machine():
    """botside.bot_machine with the machine last found (CACHE) when the settings cannot be read; exits when not even
    that is there."""
    try:
        return botside.bot_machine(CACHE, "altention dc notify")
    except botside.NoBot as e:
        sys.exit("altention dc notify：%s" % e)


def main():
    ap = argparse.ArgumentParser(description="Push a message to Discord")
    ap.add_argument("-c", "--channel", default=os.environ.get("DCNOTIFY_CHANNEL") or "")  # none: the bot's machine decides
    ap.add_argument("--wait", type=int, default=None)  # C-87: as altention dc post --wait (exit 0 and the ID once shown)
    ap.add_argument("message", nargs="*")
    a = ap.parse_args()
    if a.wait is not None and not 1 <= a.wait <= botside.WAIT_MOST:
        sys.exit("altention dc notify：--wait 要給 1 到 %d 的秒數" % botside.WAIT_MOST)
    text = " ".join(a.message) if a.message else sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("altention dc notify：沒有訊息")
    me = socket.gethostname().split(".")[0]
    post = {"text": text, "from_name": me, "notify": True}
    if a.wait is not None:
        post["wait"] = a.wait
    if a.channel:
        post["channel"] = a.channel.lstrip("#")
    # who sends it decides which of several same-named channels is meant (its own project's first)
    if os.environ.get("DCBOT_CHANNEL"):
        post["from_channel"] = os.environ["DCBOT_CHANNEL"]
    elif os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
        post["pane"] = "%s|%s|%s" % (me, os.environ["TMUX"].split(",")[0], os.environ["TMUX_PANE"])
    data = json.dumps(post, ensure_ascii=False).encode("utf-8")
    where = bot_machine()
    try:
        r = botside.run(["post"], data, repo=HOME_REPO, where=where, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except botside.NoBot as e:
        sys.exit("altention dc notify：%s" % e)
    if r.returncode != 0:
        sys.stderr.write((r.stderr or r.stdout).decode("utf-8", "replace"))
    elif a.wait is not None:  # the message's ID, on the last line
        sys.stdout.write(r.stdout.decode("utf-8", "replace"))
    sys.exit(r.returncode)

if __name__ == "__main__":
    main()
