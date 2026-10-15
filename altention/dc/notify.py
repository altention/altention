#!/usr/bin/env python3
"""altention dc notify: push a message to Discord from any machine.

  altention dc notify "訓練跑完了，loss 0.12"
  altention dc notify -c project-a "實驗 C 結束，結果在 results/c.json"
  altention dc notify -c "project-a/notifications" "…"          a channel in a given category (names can repeat)
  some-command 2>&1 | tail -20 | altention dc notify -c alerts  # message from stdin

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


CACHE = os.path.expanduser("~/.local/state/altention/bot.json")  # the bot's machine as last found: {"name", "ssh"}


def bot_machine():
    """(name, machine, here) of the machine that runs the bot (altention.config: this one if the bot's settings
    are here, else the machine list's). When the settings cannot be read, the machine last found (CACHE)."""
    sys.path.insert(0, HOME_REPO)
    problem = None
    try:
        from altention import config, text
        name, m, here = config.bot_machine()
        if name is None:
            problem = text.t("bot.not_found")
        else:
            try:
                os.makedirs(os.path.dirname(CACHE), exist_ok=True)
                with open(CACHE + ".tmp", "w") as f:
                    json.dump({"name": name, "ssh": m.get("ssh", []), "here": bool(here)}, f)
                os.replace(CACHE + ".tmp", CACHE)
            except OSError:
                pass
            return name, m, here
    except Exception as e:  # a broken machine list or instance pointer: the one this is likely to report
        problem = "設定讀不出來（%s）" % e
    try:
        with open(CACHE) as f:
            last = json.load(f)
        sys.stderr.write("altention dc notify：%s；用上次找到的機器人（%s）\n" % (problem, last["name"]))
        return last["name"], {"ssh": last.get("ssh", [])}, bool(last.get("here"))
    except (OSError, ValueError, KeyError):
        sys.exit("altention dc notify：%s，也沒有上次找到的機器人可用" % problem)


def main():
    ap = argparse.ArgumentParser(description="Push a message to Discord")
    ap.add_argument("-c", "--channel", default=os.environ.get("DCNOTIFY_CHANNEL") or "")  # none: the bot's machine decides
    ap.add_argument("message", nargs="*")
    a = ap.parse_args()
    text = " ".join(a.message) if a.message else sys.stdin.read()
    text = text.strip()
    if not text:
        sys.exit("altention dc notify：沒有訊息")
    me = socket.gethostname().split(".")[0]
    post = {"text": text, "from_name": me, "notify": True}
    if a.channel:
        post["channel"] = a.channel.lstrip("#")
    # who sends it decides which of several same-named channels is meant (its own project's first)
    if os.environ.get("DCBOT_CHANNEL"):
        post["from_channel"] = os.environ["DCBOT_CHANNEL"]
    elif os.environ.get("TMUX") and os.environ.get("TMUX_PANE"):
        post["pane"] = "%s|%s|%s" % (me, os.environ["TMUX"].split(",")[0], os.environ["TMUX_PANE"])
    data = json.dumps(post, ensure_ascii=False).encode("utf-8")
    name, m, here = bot_machine()
    if here:
        r = subprocess.run([local_dcctl(), "post"], input=data, stdout=subprocess.DEVNULL)
        sys.exit(r.returncode)
    for alias in m.get("ssh", []):
        if alias == "local":
            continue
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, dcctl_on(m) + " post"],
                           input=data, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if r.returncode != 255:
            if r.returncode != 0:
                sys.stderr.write((r.stderr or r.stdout).decode("utf-8", "replace"))
            sys.exit(r.returncode)
    sys.exit("altention dc notify：連不上 %s" % name)


if __name__ == "__main__":
    main()
