#!/usr/bin/env python3
"""altention dc post: post to a Discord channel from any machine: a report, a note, or a message for another Claude.

  altention dc post reports "今天的回測結果：…"
  altention dc post "my-project/notifications" "…"                 a channel in a given category (several channels may share a name;
                                                                   a plain name means the one in your own project's category first)
  some-command | altention dc post logs                            the text from stdin (long text is split over several messages)
  altention dc post reports --file report.md "週報"                attach a file (a long report reads better as one); --file again
                                                                   for more, up to 10 (8 MB together), all on the last message
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
  altention dc post st-0 --history                                 post the history of the conversation the channel is on now
  altention dc post 新頻道 --create "…"                            a channel that does not exist yet is made (a plain one, for records);
                                                                   without --create an unknown name is an error, with the closest names
  altention dc post 100000000000000020 "…"                         a channel by its ID: still right after the channel is renamed
                                                                   (what scripts and cron jobs should use)
  altention dc post 行程 --board 本週 "…"                          a board: one message kept up to date, edited in place each time
                                                                   (its title on top, with when it was last updated); never handed
                                                                   to a Claude
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
MAX_FILE = 8 * 1024 * 1024  # all of a post's files together
MAX_FILES = 10              # files on one post (Discord's limit for one message)


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
        sys.exit("altention dc post：%s" % e)
    if name is None:
        sys.exit("altention dc post：" + text.t("bot.not_found"))
    return name, m, here


def pane_address(target):
    """{"socket", "pane"} of a tmux target on this machine (the owner's default tmux server)."""
    path = "%s/.local/bin:/opt/homebrew/bin:/usr/local/bin:%s" % (os.path.expanduser("~"), os.environ.get("PATH", ""))
    r = subprocess.run(["tmux", "display-message", "-p", "-t", target, "#{socket_path}\t#{pane_id}"], stdout=subprocess.PIPE,
                       stderr=subprocess.PIPE, universal_newlines=True, env=dict(os.environ, PATH=path))
    sock, _, pane = r.stdout.strip().partition("\t")
    if r.returncode != 0 or not pane.startswith("%"):
        sys.exit("altention dc post：找不到 tmux 的 %s（%s）" % (target, r.stderr.strip()))
    return {"socket": sock, "pane": pane}


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
    if board:
        post["board"] = board
    if move_to:
        post["move_to"] = move_to.strip()
    if create:
        post["create"] = True
    if resume:
        post["resume"] = resume
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
    if files:  # the first where a bot before C-73 looks for it (it shows that one at least), the rest beside it
        post["file_name"], post["file_b64"] = files[0]["name"], files[0]["b64"]
        if files[1:]:
            post["more_files"] = files[1:]
    if not post["text"] and not files and not any(k in post for k in ("new_claude", "resume", "history", "move_to")):
        sys.exit("altention dc post：沒有內容")
    return post


def deliver(post, capture=False):
    """Hand a post to dcctl post on the bot's machine (here, or over SSH). (exit status, what it printed): with
    `capture` its output is collected (altention.dc.card raises with it), else it goes to this terminal. Never
    exits: no machine with the bot is (1, why) too; only main() turns a failure into an exit (manager 2026-10-19)."""
    data = json.dumps(post, ensure_ascii=False).encode("utf-8")
    out = {"stdout": subprocess.PIPE, "stderr": subprocess.STDOUT} if capture else {}
    try:
        name, m, here = bot_machine()
    except SystemExit as e:  # bot_machine() is shared with altention dc notify, which exits on it
        return 1, str(e.code if e.code is not None else "altention dc post：找不到負責 Discord 機器人的機器")
    if here:
        r = subprocess.run([local_dcctl(this_engine(name)), "post"], input=data, **out)
        return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    for alias in m.get("ssh", []):
        if alias == "local":
            continue
        r = subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, dcctl_on(m) + " post"], input=data, **out)
        if r.returncode != 255:
            return r.returncode, (r.stdout or b"").decode("utf-8", "replace")
    return 1, "altention dc post：連不上 %s" % name


def main():
    host = socket.gethostname().split(".")[0]
    post = build(sys.argv[1:], lambda: "" if sys.stdin.isatty() else sys.stdin.read(), os.environ, host, os.getcwd())
    code, said = deliver(post)
    if said:
        sys.exit(said)
    sys.exit(code)


if __name__ == "__main__":
    main()
