#!/usr/bin/env python3
"""altention tmux: the panes of this user's tmux servers, on any machine of the list, by one identifier (C-92).

  altention tmux list [機器] [--json]        every pane there, with its tmux:// identifier
  altention tmux read <識別碼> [行數]         a pane's last lines (50 by default, at most 2000)
  altention tmux send <識別碼> "文字" [--enter] [--reason "…"]
  altention tmux send <識別碼> --keys C-c Enter Up … [--reason "…"]
                                             type into that pane (C-92 b-1): checked first, written to #audit,
                                             checked again, then typed (dcbot/bot/tmuxsend.py says what is refused)

The identifier: tmux://<機器>[/<socket>]/<session>:<window>.<pane>#%<窗格編號> (dcbot/bot/tmuxref.py says it in full).
`list` prints them; `read` refuses when the pane at that place is not the one #% names (the windows were moved, or the
tmux server restarted): list again. What `read` prints has the tokens and keys it can tell masked, as the bot masks a
screen it posts (dcbot/bot/masking.py, C-127): not a promise, a format it does not know shows as it is.
Another machine is reached over SSH (its own engine's dcremote), kept open for 10 minutes (ControlMaster: the second
call on is about 0.15 s, not 1 s or more) by a socket in a folder of this user's own, /tmp/altention-ssh-<uid>, never
the bot's (each closing its own does not cut the other's). A connection that died without a word (the network
changed, the machine went to sleep) is found out within about 45 s (ServerAliveInterval, as the bot's) and made anew.
Exit codes: 0 done, 2 a wrong identifier or arguments, 3 no such pane (or another one there now), 4 send refused
(the reason is said), 5 send not done because #audit did not take its line, 255 the machine could not be reached.
Standard library only, Python 3.8.
"""
import json
import os
import stat
import subprocess
import sys

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ENGINE)
sys.path.insert(0, os.path.join(ENGINE, "dcbot"))
from altention import config  # noqa: E402
from bot import tmuxref  # noqa: E402

SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
PATH_FIX = 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH; '
TIMEOUT = 20
UNREACHABLE = 255
CONTROL_BASE = "/tmp"  # short: a socket's path has room for about 104 bytes on macOS, and %C is 40 of them
KEEP = 600  # seconds a connection is kept open after its last use (as the bot keeps its own)


def control_dir():
    """The folder of this user's kept ssh connections (ACT-71 A): CONTROL_BASE/altention-ssh-<uid>, made 0700 if it
    is not there. None when it cannot be made, or what is there is not a folder of this user's that only they can
    open (made by someone else first, or a link): then nothing is kept and every call connects anew."""
    d = os.path.join(CONTROL_BASE, "altention-ssh-%d" % os.getuid())
    try:
        os.mkdir(d, 0o700)
    except FileExistsError:
        pass
    except OSError:
        return None
    try:
        st = os.lstat(d)
    except OSError:
        return None
    if not stat.S_ISDIR(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077:
        return None
    return d


def ssh_command():
    """ssh and its options: SSH, with the connection kept open when control_dir() gives a folder."""
    d = control_dir()
    if d is None:
        return list(SSH)
    return SSH + ["-o", "ServerAliveInterval=15",  # a kept master learns that its line is dead (#code-review on 23dc5e2)
                  "-o", "ControlMaster=auto", "-o", "ControlPath=%s/%%C" % d, "-o", "ControlPersist=%d" % KEEP]


def here():
    return config.this_machine()


def dcremote(machine, *args, input=None, timeout=TIMEOUT):
    """(exit code, stdout, stderr) of dcremote on `machine`: this one's own, else over SSH by its aliases in turn.
    UNREACHABLE when no way got there. `input`: its stdin."""
    if machine == here():
        cmd = [sys.executable, os.path.join(ENGINE, "dcbot", "tools", "dcremote")] + list(args)
        try:
            r = subprocess.run(cmd, input=input or "", stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               universal_newlines=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            return UNREACHABLE, "", "dcremote 沒有回應"
        return r.returncode, r.stdout, r.stderr
    m = (config.machines() or {}).get(machine)
    if m is None:
        return 2, "", "機器清單裡沒有 %s" % machine
    command = "%spython3 %s/dcbot/tools/dcremote %s" % (PATH_FIX, config.engine_on(m), " ".join(config.shell_quote(a) for a in args))
    said = []
    for alias in config.ssh_aliases(m) or [machine]:
        try:
            r = subprocess.run(ssh_command() + [alias, command], input=input or "", stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            said.append("%s：逾時" % alias)
            continue
        if r.returncode != 255:
            return r.returncode, r.stdout, r.stderr
        said.append("%s：%s" % (alias, (r.stderr.strip().splitlines() or ["連不上"])[-1]))
    return UNREACHABLE, "", "；".join(said)


def shown(text):
    """A pane's text as `read` prints it: masked again here (tmuxref.masked, as it was on the machine it was read on;
    masking twice changes nothing), so what is printed is masked by this engine's rules whatever the other machine's
    engine is (a plugin may post it as it is: manager 2026-11-03)."""
    return tmuxref.masked(text)


def show(p):
    tags = []
    if p.get("claude"):
        tags.append("Claude Code")
    if p.get("others"):
        tags.append("前景是 %s" % "、".join(p["others"]))
    elif p.get("others") is None:
        tags.append("前景是誰看不出來")
    if p.get("channel"):
        tags.append("頻道 %s" % p["channel"])
    return "%s  %s  %s%s" % (p["id"], p.get("command") or "?", p.get("cwd") or "", "（%s）" % "，".join(tags) if tags else "")


SEND_TIMEOUT = 90  # the other machine waits up to 30 s on #audit, and looks twice


def caller():
    """Who sends, for #audit: the Discord channel of the Claude running this (DCBOT_CHANNEL), else machine:folder."""
    ch = os.environ.get("DCBOT_CHANNEL", "")
    return "頻道 <#%s>" % ch if ch.isdigit() else "%s:%s" % (here(), os.getcwd())


def send(args):
    """altention tmux send <id> "text" [--enter] [--reason …] | --keys K… [--reason …]: exit as the design says."""
    def fail(why, code=2):
        print("altention tmux send：%s" % why, file=sys.stderr)
        return code
    args = list(args)
    reason = ""
    if "--reason" in args:
        i = args.index("--reason")
        if i + 1 >= len(args):
            return fail("--reason 要給一句理由")
        reason = args[i + 1]
        del args[i:i + 2]
    enter = "--enter" in args
    if enter:
        args.remove("--enter")
    keys = None
    if "--keys" in args:
        i = args.index("--keys")
        keys, args = args[i + 1:], args[:i]
        if not keys:
            return fail("--keys 要給按鍵")
        if enter:
            return fail("--keys 時要按 Enter 請寫在按鍵裡")
    if len(args) != (1 if keys else 2):
        return fail("用法：altention tmux send <識別碼> \"文字\" [--enter]，或 send <識別碼> --keys 鍵…")
    try:
        ref = tmuxref.parse(args[0])
    except tmuxref.BadId as e:
        return fail(str(e))
    asked = dict({"keys": keys} if keys else {"text": args[1]}, enter=enter, caller=caller(), reason=reason)
    code, out, err = dcremote(ref["machine"], "tmux-send", args[0], input=json.dumps(asked, ensure_ascii=False),
                              timeout=SEND_TIMEOUT)
    if code == UNREACHABLE:
        return fail("連不上 %s（%s）" % (ref["machine"], err.strip()), UNREACHABLE)
    try:
        said = json.loads(out)
    except ValueError:
        return fail("%s 的回答讀不懂（那台的引擎太舊，還沒有 tmux-send？）：%s" % (ref["machine"], (err or out).strip()[-300:]), 1)
    if "error" in said:
        return fail(said["error"], said.get("code", code or 1))
    print("送出了（窗格 %s）" % said.get("pane", "?"))
    return 0


def main(argv=None):
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        return 0
    if argv[0] == "list":
        rest = [a for a in argv[1:] if a != "--json"]
        if len(rest) > 1:
            print("altention tmux list：最多一台機器", file=sys.stderr)
            return 2
        machine = rest[0] if rest else here()
        code, out, err = dcremote(machine, "tmux-list", machine)
        if code == UNREACHABLE:
            print("altention tmux list：連不上 %s（%s）" % (machine, err.strip()), file=sys.stderr)
            return UNREACHABLE
        try:
            panes = json.loads(out)
        except ValueError:
            print("altention tmux list：%s 的回答讀不懂（那台的引擎太舊？）：%s" % (machine, (err or out).strip()[-300:]), file=sys.stderr)
            return 1
        if "--json" in argv:
            print(json.dumps(panes, ensure_ascii=False))
        else:
            for p in panes:
                print(show(p))
            if not panes:
                print("%s 沒有 tmux 窗格" % machine)
        return 0
    if argv[0] == "read" and len(argv) in (2, 3):
        try:
            ref = tmuxref.parse(argv[1])
        except tmuxref.BadId as e:
            print("altention tmux read：%s" % e, file=sys.stderr)
            return 2
        lines = argv[2] if len(argv) == 3 else str(tmuxref.LINES)
        if not lines.isdigit():
            print("altention tmux read：行數要是數字", file=sys.stderr)
            return 2
        code, out, err = dcremote(ref["machine"], "tmux-read", argv[1], lines)
        if code == UNREACHABLE:
            print("altention tmux read：連不上 %s（%s）" % (ref["machine"], err.strip()), file=sys.stderr)
            return UNREACHABLE
        try:
            got = json.loads(out)
        except ValueError:
            print("altention tmux read：%s 的回答讀不懂（那台的引擎太舊？）：%s" % (ref["machine"], (err or out).strip()[-300:]),
                  file=sys.stderr)
            return 1
        if "error" in got:
            print("altention tmux read：%s" % got["error"], file=sys.stderr)
            return int(got.get("code") or 3)
        sys.stdout.write(shown(got["text"]))
        return 0
    if argv[0] == "send":
        return send(argv[1:])
    print("altention tmux：不認得 %s（-h 看說明）" % " ".join(argv), file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
