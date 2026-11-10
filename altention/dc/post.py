#!/usr/bin/env python3
"""altention dc post: post to a Discord channel from any machine: a report, a note, or a message for another Claude.

  altention dc post reports "今天的回測結果：…"
  altention dc post "my-project/notifications" "…"                 a channel in a given category (several channels may share a name;
                                                                   a plain name means the one in your own project's category first)
  some-command | altention dc post logs                            the text from stdin (long text is split over several messages)
  altention dc post reports --file report.md "週報"                attach a file (a long report reads better as one); --file again
                                                                   for more, up to 10 (8 MB together), all on the last message;
                                                                   x.png.source.json beside x.png: what it was made from, given
                                                                   back when the owner taps 📝 (altention.sources)
  altention dc post st-0 "我看完專案了，問題是 …"                  the channel has a Claude: it gets the message, knows it came
                                                                   from you and how to answer
  altention dc post cv --attach cv:0                               attach a Claude Code running in this machine's tmux (a tmux target:
                                                                   session:window, or %pane) to a channel of its own, under the
                                                                   category named first (as /attach does; the bot answers where)
  altention dc post 專案/demo --new-claude ~/projects/demo "主題"  a channel with a Claude of its own (on the bot's machine, in the
                                                                   bot's tmux, working in that folder), as if set up on its card;
                                                                   the text becomes the channel's topic. The bot answers when done
  altention dc post st-0 --resume eb106548                         switch that channel's Claude to an earlier conversation of its folder
                                                                   (as the owner's !resume: by the start of its id) and post its history
                                                                   (only its last turns when the channel has it already; --quiet: none)
  altention dc post st-0 --history                                 post the history of the conversation the channel is on now
  altention dc post 新頻道 --create "…"                            a channel that does not exist yet is made (a plain one, for records);
                                                                   without --create an unknown name is an error, with the closest names
  altention dc post 100000000000000020 "…"                         a channel by its ID: still right after the channel is renamed
                                                                   (what scripts and cron jobs should use)
  altention dc post @audit "…"                                     the bot's own #audit (@notify: the default notification channel),
                                                                   found by the bot itself, for plugins; posts and notifications only
  altention dc post @notify --wait 30 "…"                          wait until the bot has put it on Discord: exit 0 and the message's ID
                                                                   on the last line; 3 still queued when the time was up; 4 not shown
                                                                   (why on stderr); without --wait, 0 means queued
  altention dc post 行程 --board 本週 "…"                          a board: one message kept up to date, edited in place each time
                                                                   (its title on top, with when it was last updated); never handed
                                                                   to a Claude
  altention dc post st-0 --activity demo/files --arg path=~/r.pdf "報告"
                                                                   a message with an 「打開」 button that opens that page of a
                                                                   plugin's Discord Activity (the plugin declares it; an "owner"
                                                                   page opens for the owner alone); --arg again for more; never
                                                                   handed to a Claude
  altention dc post st-0 --secret demo/api-token ["…"]          a message with a 「填入」 button: the owner presses it, types the
                                                                   plugin's secret into Discord's box, and the core stores it on the
                                                                   bot's machine (altention secret set; only a name the plugin
                                                                   declares in secrets[]); the value is never posted or handed to
                                                                   a Claude
  altention dc post audit --move-to 系統 ["why"]                   move that channel into a category (made if there is none: the
                                                                   bot's system category at the bottom); never makes the channel;
                                                                   said in #audit, with the text when given

The message is headed with where it comes from ("📨 來自 #st-1"), worked out here: the channel whose Claude
this is (when run by a session the Discord bot looks after), else "<machine> 上的 Claude（<folder>）".
A Claude posting into its own channel gets plain messages, no heading: text and pictures, as its own replies.
One sender's posts to one channel are shown in the order sent.
A name no channel has is refused (it may have been renamed), unless --create.

Like altention dc notify, it runs dcbot's dcctl on the machine with the `dcbot` role (locally, or over SSH).
"""
import base64
import json
import os
import re
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


MAX_FILE = 8 * 1024 * 1024  # all of a post's files together
MAX_FILES = 10              # files on one post (Discord's limit for one message)
MAX_ARGS = 2000             # --activity's --arg pairs together, as JSON: kept with the button in the channel's state
NAME = re.compile(r"^[a-z][a-z0-9-]*$")  # a plugin's or a page's name (altention.plugin.check_name)


def file_source(path):
    """What an attached picture was made from, from its sidecar (altention.sources, C-91): (object, why)."""
    sys.path.insert(0, HOME_REPO)
    from altention import sources
    return sources.sidecar(path)


PLACE = "#{socket_path}\t#{pane_id}\t#{session_name}\t#{window_index}\t#{window_name}\t#{window_id}\t#{pane_index}"


def same_place(target, session, window_index, window_name, window_id, pane_index, pane_id):
    """Is the pane tmux answered for (its session, window and pane) the one `target` names? tmux 3.4's
    display-message answers for the current pane, exit 0, when the window or pane a target names is not there (a:7,
    a:0.5) and even for a session that is not there (C-154): the parts the target names are compared. A session is
    tmux's exact name or a beginning of it (as tmux takes it); a window its index, @id, or name or a beginning of it;
    a pane its index or %id. A part written with tmux's other forms (!, ^, $, +, -, {…}) is taken as tmux took it."""
    if target.startswith("%"):
        return pane_id == target
    special = ("!", "^", "$", "+", "-", "{", "~")

    def window_is(win):
        if not win or win.startswith(special):
            return True
        if win.startswith("@"):
            return window_id == win
        if win.isdigit():
            return window_index == win
        return window_name == win.lstrip("=") or (not win.startswith("=") and window_name.startswith(win))

    def session_is(sess):
        name = sess.lstrip("=")
        return not name or session == name or (not sess.startswith("=") and session.startswith(name))

    if ":" not in target and "." not in target:  # one name: tmux takes it as a session, else as a window
        return bool(session) and (session_is(target) or window_is(target))
    sess, colon, rest = target.partition(":")
    if not colon:
        sess, rest = "", target
    win, _, pane = rest.partition(".")
    if not session_is(sess) or not window_is(win):
        return False
    if pane and not pane.startswith(special):
        if pane.startswith("%"):
            return pane_id == pane
        if pane.isdigit() and pane_index != pane:
            return False
    return bool(session)


def pane_address(target, socket=None):
    """{"socket", "pane"} of a tmux target on this machine (the owner's default tmux server; `socket`: another, for
    tests). Refused when tmux answers for another pane than the target names (same_place, C-154)."""
    path = "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s" % (os.path.expanduser("~"), os.environ.get("PATH", ""))
    where = ["-S", socket] if socket else []
    try:
        r = subprocess.run(["tmux"] + where + ["display-message", "-p", "-t", target, PLACE], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, PATH=path), timeout=10)
    except subprocess.TimeoutExpired:
        sys.exit("altention dc post：找不到 tmux 的 %s（tmux 10 秒沒有回答）" % target)
    f = r.stdout.rstrip("\n").split("\t")
    if r.returncode != 0 or len(f) != 7 or not f[1].startswith("%"):
        sys.exit("altention dc post：找不到 tmux 的 %s（%s）" % (target, r.stderr.strip()))
    if not same_place(target, *f[2:], f[1]):
        sys.exit("altention dc post：tmux 裡沒有 %s（tmux 回的是 %s:%s.%s，不是它）：用 tmux list-panes -a 看有哪些"
                 % (target, f[2], f[3], f[6]))
    return {"socket": f[0], "pane": f[1]}


def wait_secs(text):
    """--wait's seconds (1 to 600), or exit saying so."""
    try:
        secs = int(text)
    except (TypeError, ValueError):
        secs = 0
    if not 1 <= secs <= botside.WAIT_MOST:
        sys.exit("altention dc post：--wait 要給 1 到 %d 的秒數" % botside.WAIT_MOST)
    return secs


def build(argv, stdin_text, env, host, cwd):
    """The post (a dict for `dcctl post`) from the command line. `stdin_text`: the text read from stdin, or a function
    that reads it, called only when the command line holds no text and asks for no --attach, --resume, --history,
    --new-claude or --move-to."""
    if not argv or argv[0] in ("-h", "--help"):
        print(__doc__)
        sys.exit(0)
    args, file_paths = list(argv[1:]), []
    while "--file" in args:
        i = args.index("--file")
        if i + 1 >= len(args):
            sys.exit("altention dc post：--file 要給檔案")
        file_paths.append(args[i + 1])
        del args[i:i + 2]
    if len(file_paths) > MAX_FILES:
        sys.exit("altention dc post：一則最多 %d 個附件（給了 %d 個）" % (MAX_FILES, len(file_paths)))
    attach = None
    if "--attach" in args:
        i = args.index("--attach")
        target = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        attach = pane_address(target)
    resume = None
    if "--resume" in args:
        i = args.index("--resume")
        resume = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not resume:
            sys.exit("altention dc post：--resume 要給對話 id（開頭 8 碼以上）")
    create = "--create" in args
    if create:
        args.remove("--create")
    quiet = "--quiet" in args  # with --resume: the history is not posted
    if quiet:
        args.remove("--quiet")
    history = "--history" in args
    if history:
        args.remove("--history")
    new_claude = None
    if "--new-claude" in args:
        i = args.index("--new-claude")
        new_claude = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not new_claude:
            sys.exit("altention dc post：--new-claude 要給資料夾")
    move_to = None
    if "--move-to" in args:
        i = args.index("--move-to")
        move_to = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not move_to.strip():
            sys.exit("altention dc post：--move-to 要給分類名稱")
    activity, activity_args = None, {}
    if "--activity" in args:
        i = args.index("--activity")
        activity = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        plugin_name, _, page = activity.partition("/")
        if not (NAME.match(plugin_name) and NAME.match(page)):
            sys.exit("altention dc post：--activity 要寫成 <插件>/<頁>（例如 demo/files）")
    while "--arg" in args:
        i = args.index("--arg")
        pair = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        key, eq, value = pair.partition("=")
        if not eq or not key.strip():
            sys.exit("altention dc post：--arg 要寫成 名稱=值")
        activity_args[key.strip()] = value
    if activity_args and not activity:
        sys.exit("altention dc post：--arg 只能跟 --activity 一起用")
    if len(json.dumps(activity_args, ensure_ascii=False)) > MAX_ARGS:
        sys.exit("altention dc post：--arg 合計太長（上限 %d 字）" % MAX_ARGS)
    secret = None
    if "--secret" in args:  # C-77 G3b
        i = args.index("--secret")
        secret = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        plugin_name, _, name = secret.partition("/")
        if not (NAME.match(plugin_name) and NAME.match(name)):
            sys.exit("altention dc post：--secret 要寫成 <插件>/<名字>（例如 demo/api-token）")
        if activity:
            sys.exit("altention dc post：--secret 和 --activity 不能一起用")
    wait = None
    if "--wait" in args:  # C-87: until the bot says it is on Discord (exit 0 and the message's ID), or why not
        i = args.index("--wait")
        wait = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        wait = wait_secs(wait)
    board = None
    if "--board" in args:
        i = args.index("--board")
        board = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
        if not board:
            sys.exit("altention dc post：--board 要給名稱")
    if not args and callable(stdin_text):  # stdin is read only when no text was given here (--file with it too)
        stdin_text = "" if any((attach, resume, history, new_claude, move_to)) else stdin_text()
    text = " ".join(args) if args else (stdin_text or "")
    post = {"channel": argv[0].lstrip("#"), "text": text.strip(),
            "from_name": "%s 上的 Claude（%s）" % (host, os.path.basename(cwd.rstrip("/")) or cwd), "from_host": host}
    if wait is not None:
        if any((board, attach, resume, history, new_claude, move_to)):
            sys.exit("altention dc post：--wait 只用在貼訊息和通知")
        post["wait"] = wait
    if board:
        post["board"] = board
    if activity:
        post["activity"], post["args"] = activity, activity_args
    if secret:
        if any((board, attach, resume, history, new_claude, move_to)):
            sys.exit("altention dc post：--secret 只用在貼訊息")
        post["secret"] = secret
    if move_to:
        post["move_to"] = move_to.strip()
    if create:
        post["create"] = True
    if resume:
        post["resume"] = resume
        if quiet:
            post["quiet"] = True
    if history:
        post["history"] = True
    if new_claude:  # a folder on the bot's machine: expanded there (~ is the same user)
        post["new_claude"], post["text"] = new_claude, post["text"] or ""
    if attach:
        post["attach"], post["text"] = attach, post["text"] or "（請機器人接上 tmux 對話）"
    if env.get("DCBOT_CHANNEL"):
        post["from_channel"] = env["DCBOT_CHANNEL"]
    elif env.get("TMUX") and env.get("TMUX_PANE"):  # dcctl looks up its channel ("<machine>|socket|%id")
        post["pane"] = "%s|%s|%s" % (host, env["TMUX"].split(",")[0], env["TMUX_PANE"])
    room, files = MAX_FILE, []
    for path in file_paths:
        with open(path, "rb") as f:
            data = f.read(room + 1)
        if len(data) > room:
            sys.exit("altention dc post：檔案太大（全部加起來上限 8 MB）")
        room -= len(data)
        files.append({"name": os.path.basename(path), "b64": base64.b64encode(data).decode()})
        source, why = file_source(path)
        if source:
            files[-1]["source"] = source
        elif why:  # the file goes without: its picture is still worth posting
            print("altention dc post：%s，這個附件不帶原文" % why, file=sys.stderr)
    if files:  # the first where a bot before C-73 looks for it (it shows that one at least), the rest beside it
        post["file_name"], post["file_b64"] = files[0]["name"], files[0]["b64"]
        if files[0].get("source"):
            post["file_source"] = files[0]["source"]
        if files[1:]:
            post["more_files"] = files[1:]
    if not post["text"] and not files and not any(k in post for k in ("new_claude", "resume", "history", "move_to", "secret")):
        sys.exit("altention dc post：沒有內容")
    return post


def deliver(post, capture=False):
    """Hand a post to dcctl post on the bot's machine (here, or over SSH: botside.run, sent once). (exit status, what
    it printed): with `capture` its output is collected (altention.dc.card raises with it), else it goes to this
    terminal. Never exits: no machine with the bot is (1, why) too; only main() turns a failure into an exit (manager
    2026-10-19)."""
    data = json.dumps(post, ensure_ascii=False).encode("utf-8")
    out = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT} if capture else {}
    try:
        r = botside.run(["post"], data, prog="altention dc post", repo=HOME_REPO, **out)
    except botside.NoBot as e:
        return 1, "altention dc post：%s" % e
    return r.returncode, (r.stdout or b"").decode("utf-8", "replace")


def main():
    host = socket.gethostname().split(".")[0]
    post = build(sys.argv[1:], lambda: "" if sys.stdin.isatty() else sys.stdin.read(), os.environ, host, os.getcwd())
    code, said = deliver(post)
    if said:
        sys.exit(said)
    sys.exit(code)


if __name__ == "__main__":
    main()
