#!/usr/bin/env python3
"""altention dc post: post to a Discord channel from any machine: a report, a note, or a message for another Claude.

  altention dc post reports "今天的回測結果：…"
  altention dc post "my-project/notifications" "…"                 a channel in a given category (several channels may share a name;
                                                                   a plain name means the one in your own project's category first)
  some-command | altention dc post logs                            the text from stdin (long text is split over several messages)
  altention dc post reports --file report.md "週報"                attach a file (a long report reads better as one); --file again
                                                                   for more, up to 10 (8 MB together), all on the last message;
                                                                   x.png.source.json beside x.png: what it was made from, given
                                                                   back when the owner taps 📝 (altention.sources); when a
                                                                   plugin page opens files, each file gets its button
  altention dc post st-0 "我看完專案了，問題是 …"                  the channel has a Claude: it gets the message, knows it came
                                                                   from you and how to answer
  altention dc post cv --attach cv:0                               attach a Claude Code running in this machine's tmux (a tmux target:
                                                                   session:window, or %pane, or a tmux:// identifier of any machine's
                                                                   pane: altention tmux list) to a channel of its own, under the
                                                                   category named first (as /attach does; the bot answers where)
  altention dc post 專案/demo --new-claude ~/projects/demo "主題"  a channel with a Claude of its own, working in that folder, on this
                                                                   machine (the bot's machine: in the bot's tmux; another: in your
                                                                   tmux there, as the setup card does); --on <machine> opens it on
                                                                   another one (a folder there: a full path, or ~/… in quotes:
                                                                   '~/…'; unquoted, this machine's shell turns ~ into this
                                                                   machine's home first). The text becomes the channel's topic.
                                                                   The bot answers when done
  altention dc post st-0 --resume eb106548                         switch that channel's Claude to an earlier conversation of its folder
                                                                   (as the owner's !resume: by the start of its id) and post its history
                                                                   (only its last turns when the channel has it already; --quiet: none)
  altention dc post st-0 --history                                 post the history of the conversation the channel is on now
  altention dc post st-0 --now "…"                                 like the owner's !!: when that channel's Claude is busy and its
                                                                   screen offers send-now (ctrl+x ctrl+s), it is pressed; said in
                                                                   #audit
  altention dc post st-0 --waiting                                 what that channel's Claude waits on you for (questions, plans,
                                                                   Claude Code's menus), each with an id
  altention dc post st-0 --answer q-… --pick "選項" --why "…"       answer one for you, as your buttons would: --pick per question
                                                                   left (in order; several labels of a multi-choice one: "A, B"),
                                                                   or --text "…" for the one left; a plan: --approve or --revise
                                                                   "…"; a menu (m-…): --key <number>, --key enter/esc/up/down/left/right/tab/space, or --text "…".
                                                                   --why (what you were told, or why this answer) is required.
                                                                   Not sent when it was answered already, the screen changed,
                                                                   the channel turned it off (!agentanswers off), or a menu counts
                                                                   down to Claude Code's own denial (yours only). Said in #audit;
                                                                   the card says who answered; the result is printed
  altention dc post st-0 --talk-continue --why "…"                 a post held by the two channels' talk limit goes on, as
                                                                   「繼續轉交」 (at most twice a day for a pair; lifting the limit
                                                                   stays yours)
  altention dc post st-0 --pause-until 00:00 --why "…"             pause that channel until then (HH:MM: the next time the clock
                                                                   says it; YYYY-MM-DD HH:MM; 30m, 2h, 1d; at most 7 days): its
                                                                   Claude is asked to stop, posts from other Claudes are kept and
                                                                   handed over when the bot tells it to carry on at that time;
                                                                   the owner's own messages still reach it. Said in #audit
  altention dc post st-0 --unpause --why "…"                       carry on now instead (the posts kept go in after it)
  altention dc post st-0 --reset --why "…" [--then "…"]           a new conversation for that channel, as the owner's !reset (the
                                                                   old one stays for !resume). Your own channel: done when this
                                                                   turn ends, then the new conversation is told to read your
                                                                   handover (or gets --then's words). Another channel: only while
                                                                   it is idle (no turn, background work, subagent or workflow),
                                                                   else refused with why; --then is then handed to it as your
                                                                   post. Said in #audit and in that channel; the answer is printed
  altention dc post st-0 --delete <id> [<id>…] --why "…"           take away messages in that channel (at most 20): the bot's,
                                                                   a Claude's, the owner's or anyone's. Each is saved whole
                                                                   in #audit first (masked, its JSON as a file); one that
                                                                   cannot be read or saved is not taken away. Someone else's
                                                                   gets a 🗑️ line in the channel (no mention). Never in
                                                                   #audit, never a thread's first message. The bot's answer
                                                                   is printed. Only the Discord message goes: a Claude may
                                                                   have read it already
  altention dc post st-0 --drop-held --why "…"                     drop the posts a paused channel keeps (its own Claude only,
                                                                   having read them itself); the owner speaking there hands
                                                                   them over after that message anyway. Said in #audit
  altention dc post st-0 --rename 回測 --why "…"                    rename that channel and what its record takes along (its Claude
                                                                   Code conversation, its tmux window, the bot's own session of
                                                                   it), as /rename; the card there and #audit say whose
  altention dc post st-0 --move session work --why "…"             move that channel's Claude to another session of its tmux
                                                                   (same pane, nothing stops); --move tmux bot, --move tmux user
                                                                   <session>: to the bot's tmux or the owner's (the old Claude
                                                                   ended first, the same conversation taken up there, or back
                                                                   where it was); the card there and #audit say whose;
                                                                   --move machine <machine> [folder], --move folder <path>: to
                                                                   another machine or folder (its record copied, the old kept)
  altention dc post 新頻道 --create "…"                            a channel that does not exist yet is made (a plain one, for records);
                                                                   without --create an unknown name is an error, with the closest names
  altention dc post 100000000000000020 "…"                         a channel by its ID: still right after the channel is renamed
                                                                   (what scripts and cron jobs should use)
  altention dc post @audit "…"                                     the bot's own #audit (@notify: the default notification channel),
                                                                   found by the bot itself, for plugins; posts and notifications only
  altention dc post @notify --wait 30 "…"                          wait until the bot has put it on Discord: exit 0 and the message's ID
                                                                   on the last line; 3 still queued when the time was up; 4 not shown
                                                                   (why on stderr); without --wait, 0 means queued. To a paused
                                                                   channel: 0 with the words that it is held; 4 when it could not
                                                                   be kept (also past a full talk-limit card): it is on Discord
                                                                   all the same, its ID on the last line
  altention dc post lab --json payload.json                        one Discord message's payload (JSON: text, embeds, Components V2),
                                                                   posted as it is (C-172a P1d; "-": from stdin); a post, never
                                                                   handed to the channel's Claude (tell it with a plain post).
                                                                   In another's channel "-# 📨 來自 #you" goes on top (a Text
                                                                   Display for V2): no room, refused, never cut. The bot's rules
                                                                   (P1c): custom_ids begin "user:", no @everyone/@here, no
                                                                   reserved opening, @ only by --mention. Waited for 30 seconds
                                                                   unless --wait says otherwise: the message's ID, or why not (4)
       … --json p.json --file tbl.png                              files uploaded with it: attachment://tbl.png in the payload
       … --json p.json --edit <message ID>                         change a message you posted (the bot's ledger says whose)
       … --json p.json --thread-new "名稱"                         open a thread in the channel and post in it (its ID printed)
       … --json p.json --mention owner                             let <@owner> in it ping (user:ID / role:ID: dcbot.json_mentions
                                                                   "any"); --no-embeds: no link previews
  altention dc post st-0 --reply-to <message ID> [--silent] "…"     answer that message (shown as a reply); --silent: no push
                                                                   (both with --json too)
  altention dc post 行程 --board 本週 --json board.json            a framed board: a payload, or a list of them (one message each,
                                                                   at most 10), kept up to date in place; no heading or time
                                                                   put in (write your own); a framed one back to plain is
                                                                   posted anew (its pin goes with it)
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

The message is headed with where it comes from ("📨 來自 #st-1"), worked out here from the Claude Code this runs
under, by that Claude's own environment, never this command's: the channel whose Claude it is (a session the
Discord bot looks after), else "<machine> 上的 Claude（<folder>）"; with no Claude Code above it (cron, a script)
"<machine> 上的程式（<folder>）". A DCBOT_CHANNEL of this command's own that names another channel is said in #audit.
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
BOARD_PARTS = 10            # a framed board's messages at most
DELETE_MOST = 20  # --delete: ids in one go (C-205: more are refused here, not split)


def delete_wait(n):
    """--delete without --wait: long enough for the bot to save and take away `n` messages one by one."""
    return min(30 + 6 * n, botside.WAIT_MOST)


JSON_WAIT = 30              # --json without --wait: waited for this long, so a refusal is said (C-172a P1d)
MAX_WHERE = 2048            # a file's {"machine", "path"} as UTF-8 JSON: longer, and it gets no button (C-116)
NAME = re.compile(r"^[a-z][a-z0-9-]*$")  # a plugin's or a page's name (altention.plugin.check_name)


def file_source(path):
    """What an attached picture was made from, from its sidecar (altention.sources, C-91): (object, why)."""
    sys.path.insert(0, HOME_REPO)
    from altention import sources
    return sources.sidecar(path)


def pane_by_id(ident):
    """{"socket", "pane"} and the machine (None: this one) of a tmux:// identifier (C-92), checked on the machine it
    names as `altention tmux read` checks it: the pane at that place must be the #% it names. Else exit saying why."""
    from altention import tmux
    from bot import tmuxref
    try:
        ref = tmuxref.parse(ident)
    except tmuxref.BadId as e:
        sys.exit("altention dc post：%s" % e)
    code, out, err = tmux.dcremote(ref["machine"], "tmux-at", ident)
    try:
        got = json.loads(out)
    except ValueError:
        got = {"error": (err or out).strip()[-300:] or "結束碼 %s" % code}
    if code != 0 or "error" in got:
        sys.exit("altention dc post：%s 的 %s：%s" % (ref["machine"], ident, got.get("error") or err.strip()))
    return {"socket": got["socket"], "pane": got["pane"]}, (None if ref["machine"] == tmux.here() else ref["machine"])


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


def where_of(machine, path, cwd):
    """{"machine", "path"} of a file posted with --file: this machine's name and the file's absolute path, what a
    plugin page that opens files is given when its button is pressed (C-116); None when it is longer than MAX_WHERE."""
    where = {"machine": machine, "path": os.path.normpath(os.path.join(cwd, path))}
    return where if len(json.dumps(where, ensure_ascii=False).encode("utf-8")) <= MAX_WHERE else None


def this_machine(host):
    """This machine's name in the machine list (altention.config), else `host`, its hostname."""
    try:
        from altention import config
        return config.this_machine() or host
    except Exception:
        return host


def build(argv, stdin_text, env, host, cwd, machine=None, claude=None):
    """The post (a dict for `dcctl post`) from the command line. `stdin_text`: the text read from stdin, or a function
    that reads it, called only when the command line holds no text and asks for no --attach, --resume, --history,
    --new-claude or --move-to. `machine`: this machine's name in the list (`host` when not given), said with each file."""
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
    attach = attach_host = None
    if "--attach" in args:
        i = args.index("--attach")
        target = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if target.startswith("tmux://"):  # (C-92) by its identifier, on whichever machine it names
            attach, attach_host = pane_by_id(target)
        else:
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
    on = None
    if "--on" in args:  # C-111: the machine --new-claude opens on (default: this one)
        i = args.index("--on")
        on = args[i + 1].strip() if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not on:
            sys.exit("altention dc post：--on 要給機器名稱")
        if new_claude is None:
            sys.exit("altention dc post：--on 只能跟 --new-claude 一起用")
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
    now = "--now" in args  # C-176: as the owner's !!
    if now:
        args.remove("--now")
    asked = {}  # C-176 batch 2: --waiting, --answer (and its sub-flags), --talk-continue: the bot answers, printed here
    for flag in ("--waiting", "--approve", "--talk-continue"):
        if flag in args:
            args.remove(flag)
            asked[flag[2:].replace("-", "_")] = True
    for flag in ("--answer", "--text", "--key", "--revise", "--why"):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            if not value.strip():
                sys.exit("altention dc post：%s 要給值" % flag)
            asked[flag[2:]] = value
    while "--pick" in args:
        i = args.index("--pick")
        value = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not value.strip():
            sys.exit("altention dc post：--pick 要給選項文字")
        asked.setdefault("pick", []).append(value.strip())
    pause_at = None  # C-184: --pause-until <time> / --unpause, with --why: the bot keeps the channel paused, answers here
    if "--pause-until" in args:
        i = args.index("--pause-until")
        pause_at = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not pause_at.strip():
            sys.exit("altention dc post：--pause-until 要給時間（HH:MM、YYYY-MM-DD HH:MM，或 30m、2h、1d）")
    unpause = "--unpause" in args
    if unpause:
        args.remove("--unpause")
    drop_held = "--drop-held" in args  # C-199: what a paused channel keeps, dropped by its own Claude
    if drop_held:
        args.remove("--drop-held")
    reset = "--reset" in args  # v0.6.1: a new conversation for that channel, as the owner's !reset (dcbot/bot/resets.py)
    if reset:
        args.remove("--reset")
    then = None  # --then "…": with --reset, what the new conversation is told first
    if "--then" in args:
        i = args.index("--then")
        then = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not then.strip():
            sys.exit("altention dc post：--then 要給新對話的第一句話")
    rename = None  # C-201 2b: --rename <name>, with --why: the channel and everything its record follows renamed
    if "--rename" in args:
        i = args.index("--rename")
        rename = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not rename.strip():
            sys.exit("altention dc post：--rename 要給新名字")
    move_place = None  # C-201 2c1: --move session <name> | --move tmux bot | --move tmux user <session>, with --why
    if "--move" in args:
        i = args.index("--move")
        given = lambda j: args[j] if j < len(args) and not args[j].startswith("--") else ""  # noqa: E731 (a flag: not given)
        kind, target = given(i + 1), given(i + 2)
        n = 1 + bool(kind) + bool(kind and target)
        session = None
        if (kind == "tmux" and target == "user") or kind == "machine":  # (machine: its folder there, if given)
            session = given(i + 3) or None
            n += bool(session)
            if kind == "tmux" and session is None:
                session = ""
        del args[i:i + n]
        if kind not in ("session", "tmux", "machine", "folder") or not target.strip() or (kind == "tmux" and target not in ("bot", "user")) \
                or (session is not None and not session.strip()) \
                or (kind == "folder" and not target.startswith(("/", "~"))) or (kind == "machine" and session and not session.startswith(("/", "~"))):
            sys.exit("altention dc post：--move 的寫法：--move session <名字>、--move tmux bot、--move tmux user <session 名字>、"
                     "--move machine <機器> [資料夾]、--move folder <路徑>（資料夾寫完整路徑）")
        move_place = (kind, target.strip(), session.strip() if session else None)
    delete_mid = None  # --delete <message id…>: messages taken away, saved in #audit first (C-205)
    if "--delete" in args:
        i = args.index("--delete")
        j = i + 1
        while j < len(args) and args[j].strip().isdigit():
            j += 1
        delete_mid = [a.strip() for a in args[i + 1:j]]
        del args[i:j]
        if not delete_mid:
            sys.exit("altention dc post：--delete 要給訊息 ID（可以給好幾個，用空白隔開）")
        delete_mid = list(dict.fromkeys(delete_mid))  # (one given twice: taken away once)
        if len(delete_mid) > DELETE_MOST:
            sys.exit("altention dc post：--delete 一次最多 %d 則，請分批" % DELETE_MOST)
    board = None
    if "--board" in args:
        i = args.index("--board")
        board = args[i + 1] if i + 1 < len(args) else None
        del args[i:i + 2]
        if not board:
            sys.exit("altention dc post：--board 要給名稱")
    payload, json_opts = None, {}  # C-172a P1d: --json and what goes with it
    for flag in ("--json", "--edit", "--reply-to", "--thread-new"):
        if flag in args:
            i = args.index(flag)
            value = args[i + 1] if i + 1 < len(args) else ""
            del args[i:i + 2]
            if not value.strip():
                sys.exit("altention dc post：%s 要給值" % flag)
            json_opts[flag[2:]] = value
    while "--mention" in args:
        i = args.index("--mention")
        value = args[i + 1] if i + 1 < len(args) else ""
        del args[i:i + 2]
        if not value.strip():
            sys.exit("altention dc post：--mention 要給 owner、user:ID 或 role:ID")
        json_opts.setdefault("mention", []).append(value.strip())
    for flag in ("--silent", "--no-embeds"):
        if flag in args:
            args.remove(flag)
            json_opts[flag[2:]] = True
    if "json" in json_opts:
        others = [k for k, v in (("--attach", attach), ("--resume", resume), ("--history", history),
                                 ("--new-claude", new_claude), ("--move-to", move_to), ("--activity", activity),
                                 ("--secret", secret), ("--now", now), ("--pause-until", pause_at), ("--unpause", unpause),
                                 ("--drop-held", drop_held), ("--rename", rename), ("--delete", delete_mid), ("--move", move_place),
                                 ("--reset", reset), ("--then", then))
                  if v] + ["--" + k.replace("_", "-") for k in asked]
        if others or args:
            sys.exit("altention dc post：--json 是一則訊息的 payload，不能再帶%s" % ("、".join(others) if others else "文字"))
        source = json_opts["json"]
        try:
            if source == "-":  # (stdin: the only flag where "-" means it)
                raw = stdin_text() if callable(stdin_text) else (stdin_text or "")
            else:
                with open(os.path.join(cwd, source), encoding="utf-8") as f:
                    raw = f.read()
            payload = json.loads(raw)
        except OSError as e:
            sys.exit("altention dc post：--json 讀不到 %s（%s）" % (source, e.strerror or e))
        except ValueError as e:
            sys.exit("altention dc post：--json 不是 JSON（%s），沒有送出" % e)
        if board and isinstance(payload, list):  # (a framed board: a payload a message, C-172a P1d)
            if not payload or len(payload) > BOARD_PARTS or not all(isinstance(p, dict) and p for p in payload):
                sys.exit("altention dc post：--board --json 的陣列要是 1 到 %d 個有內容的 JSON 物件（一則一個），沒有送出"
                         % BOARD_PARTS)
        elif not isinstance(payload, dict) or not payload:
            sys.exit("altention dc post：--json 要是一個有內容的 JSON 物件（一則訊息的 payload；--board 時也可以是陣列），沒有送出")
        if board and any(k in json_opts for k in ("edit", "reply-to", "thread-new", "mention")):
            sys.exit("altention dc post：--board --json 不能帶 --edit、--reply-to、--thread-new 或 --mention（看板原地改，不 @ 人）")
        if "edit" in json_opts and any(k in json_opts for k in ("reply-to", "thread-new", "silent")):
            sys.exit("altention dc post：--edit 只改內容，不能帶 --reply-to、--thread-new 或 --silent")
        if "edit" in json_opts and not json_opts["edit"].isdigit():
            sys.exit("altention dc post：--edit 要給訊息 ID")
    else:
        only = [f for f in ("edit", "thread-new", "no-embeds", "mention") if f in json_opts]
        if only:
            sys.exit("altention dc post：%s 只跟 --json 一起用" % "、".join("--" + f for f in only))
        if json_opts and any((board, attach, resume, history, new_claude, move_to, activity, secret, pause_at, unpause,
                              drop_held, delete_mid, reset)) \
                or json_opts and asked:
            sys.exit("altention dc post：--reply-to 和 --silent 只用在貼訊息")
    if "reply-to" in json_opts and not json_opts["reply-to"].isdigit():
        sys.exit("altention dc post：--reply-to 要給訊息 ID")
    if not args and callable(stdin_text) and payload is None:  # stdin is read only when no text was given here
        stdin_text = "" if any((attach, resume, history, new_claude, move_to, move_place)) or asked or pause_at or unpause or drop_held or delete_mid or reset \
            else stdin_text()
    text = " ".join(args) if args else ("" if payload is not None else stdin_text or "")
    post = {"channel": argv[0].lstrip("#"), "text": text.strip(),
            "from_name": "%s 上的 Claude（%s）" % (host, os.path.basename(cwd.rstrip("/")) or cwd), "from_host": host,
            "from_machine": machine or host, "from_folder": os.path.basename(cwd.rstrip("/")) or cwd}  # (the two:
    # what the bot's ledger keeps of a sender with no channel, bot/senders.py, C-172a P1d)
    if payload is not None:
        post["json"] = payload
        for k, field in (("edit", "edit_message"), ("reply-to", "reply_to"), ("thread-new", "thread_new"),
                         ("silent", "silent"), ("no-embeds", "no_embeds"), ("mention", "ping")):
            if k in json_opts:
                post[field] = json_opts[k]
        post["wait"] = JSON_WAIT if wait is None else wait  # (whether it went, or why not, is what this is for)
    elif json_opts:
        post.update({"reply_to": json_opts["reply-to"]} if "reply-to" in json_opts else {})
        post.update({"silent": True} if json_opts.get("silent") else {})
    if wait is not None:
        if any((board and payload is None, attach, resume, history, new_claude, move_to)):  # (a framed board: waited)
            sys.exit("altention dc post：--wait 只用在貼訊息和通知")
        post["wait"] = wait
    if now:
        if any((board, attach, resume, history, new_claude, move_to, activity, secret)):
            sys.exit("altention dc post：--now 只用在交給 Claude 的訊息")
        post["now"] = True
    if then is not None and not reset:
        sys.exit("altention dc post：--then 只跟 --reset 一起用")
    if reset:
        why = asked.pop("why", None)
        if pause_at is not None or unpause or drop_held or delete_mid or rename is not None:
            sys.exit("altention dc post：--reset 不能跟 --pause-until、--unpause、--drop-held、--delete、--rename 一起用")
        if asked or now or args or any((board, attach, resume, history, new_claude, move_to, activity, secret, file_paths)):
            sys.exit("altention dc post：--reset 單獨用（只帶 --why 和 --then，不能帶訊息、--file 或其他旗標）")
        if not why:
            sys.exit("altention dc post：--reset 要帶 --why \"為什麼\"（會記在 #audit，也寫在那個頻道）")
        post["reset"], post["why"], post["wait"] = True, why, wait or 30
        if then is not None:
            post["then"] = then.strip()
    if rename is not None:
        why = asked.pop("why", None)
        if asked or now or args or pause_at is not None or unpause or drop_held or delete_mid or \
                any((board, attach, resume, history, new_claude, move_to, activity, secret, file_paths)):
            sys.exit("altention dc post：--rename 單獨用（只帶 --why，不能帶訊息、--file 或其他旗標）")
        if not why:
            sys.exit("altention dc post：--rename 要帶 --why \"為什麼\"（會記在 #audit，也寫在頻道裡）")
        post["rename"], post["why"], post["wait"] = rename.strip(), why, wait or 60
    if move_place is not None:
        why = asked.pop("why", None)
        if asked or now or args or pause_at is not None or unpause or drop_held or delete_mid or reset or rename is not None or \
                any((board, attach, resume, history, new_claude, move_to, activity, secret, file_paths)):
            sys.exit("altention dc post：--move 單獨用（只帶 --why，不能帶訊息、--file 或其他旗標）")
        if not why:
            sys.exit("altention dc post：--move 要帶 --why \"為什麼\"（會記在 #audit，也寫在那個頻道的卡片上）")
        post["move_place"], post["move_target"], post["why"], post["wait"] = move_place[0], move_place[1], why, wait or 60
        if move_place[2]:
            post["move_folder" if move_place[0] == "machine" else "move_session"] = move_place[2]
    if pause_at is not None or unpause or drop_held or delete_mid:
        flag = "--pause-until" if pause_at is not None else "--unpause" if unpause else "--drop-held" if drop_held \
            else "--delete"
        why = asked.pop("why", None)
        if (pause_at is not None) + unpause + drop_held + bool(delete_mid) > 1:
            sys.exit("altention dc post：--pause-until、--unpause、--drop-held、--delete 不能一起用")
        if asked or now or args or any((board, attach, resume, history, new_claude, move_to, activity, secret, file_paths)):
            sys.exit("altention dc post：%s 單獨用（只帶 --why，不能帶訊息、--file 或其他旗標）" % flag)
        if not why:
            sys.exit("altention dc post：%s 要帶 --why \"為什麼\"（會記在 #audit，也寫在頻道裡）" % flag)
        if pause_at is not None:
            from altention.dc import pausetime
            until, why_not = pausetime.parse(pause_at)
            if until is None:
                sys.exit("altention dc post：--pause-until %s" % why_not)
            post["pause"] = until
        elif unpause:
            post["unpause"] = True
        elif drop_held:
            post["drop_held"] = True
        else:
            post["delete_messages"] = delete_mid
        post["why"], post["wait"] = why, wait or (delete_wait(len(delete_mid)) if delete_mid else 30)  # the bot's
        # answer is what this is for: waited for, and printed
        if delete_mid:  # (dcctl takes "wait" for itself, never in the spool: the bot keeps to its deadline by this one)
            post["answer_wait"] = post["wait"]
    if asked:
        kinds = [k for k in ("waiting", "answer", "talk_continue") if asked.get(k)]
        if len(kinds) != 1 or now or args or any((board, attach, resume, history, new_claude, move_to, activity, secret,
                                                   file_paths)):
            sys.exit("altention dc post：--waiting、--answer、--talk-continue 各自單獨用（不能帶訊息、--file 或其他旗標）")
        subs = [k for k in ("pick", "text", "key", "approve", "revise") if k in asked]
        if subs and kinds != ["answer"]:
            sys.exit("altention dc post：--pick、--text、--key、--approve、--revise 只跟 --answer 一起用")
        if kinds[0] in ("answer", "talk_continue") and not asked.get("why"):
            sys.exit("altention dc post：%s 要帶 --why \"使用者怎麼說的，或為什麼這樣答\"" % ("--answer" if kinds[0] == "answer"
                                                                                       else "--talk-continue"))
        if kinds == ["answer"] and not subs:
            sys.exit("altention dc post：--answer 要配 --pick、--text、--key、--approve 或 --revise")
        post.update(asked)
        post.setdefault("wait", wait or 30)  # the bot's answer is what this is for: waited for, and printed
    if board:
        if len(file_paths) > 1:
            sys.exit("altention dc post：--board 只能附一個檔案（看板的第一則上只放一張圖）")
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
    if new_claude:  # a folder on the machine it opens on: ~ expanded there; a relative one only from here
        if not os.path.isabs(new_claude) and not new_claude.startswith("~"):
            if on is not None:
                sys.exit("altention dc post：--on 的資料夾要寫完整路徑或 ~ 開頭")
            new_claude = os.path.normpath(os.path.join(cwd, new_claude))
        post["new_claude"], post["text"] = new_claude, post["text"] or ""
        post["on"] = on or this_machine(host)
    if attach:
        post["attach"], post["text"] = attach, post["text"] or "（請機器人接上 tmux 對話）"
        if attach_host:
            post["attach_host"] = attach_host  # (a pane of another machine than the sender's: C-92)
    # Who sends (C-186 P0-2, manager 11-12): the Claude Code this runs under, by that Claude's own environment
    # (`claude`: sender.claude_env()), never by this command's (`env`), which its caller may set to anything.
    if claude is None:  # no Claude Code above: cron, a script, a terminal; a program, not counted for the talk limit
        post["from_name"] = "%s 上的程式（%s）" % (host, os.path.basename(cwd.rstrip("/")) or cwd)
    else:
        post["verified"] = True
        if claude.get("DCBOT_CHANNEL"):
            post["from_channel"] = claude["DCBOT_CHANNEL"]
        elif claude.get("TMUX") and claude.get("TMUX_PANE"):  # dcctl looks up its channel ("<machine>|socket|%id")
            post["pane"] = "%s|%s|%s" % (host, claude["TMUX"].split(",")[0], claude["TMUX_PANE"])
    said = env.get("DCBOT_CHANNEL")
    if said and said != (claude or {}).get("DCBOT_CHANNEL"):  # sent as it is: the bot says so in #audit
        post["claimed_channel"] = said
    room, files = MAX_FILE, []
    for path in file_paths:
        with open(path, "rb") as f:
            data = f.read(room + 1)
        if len(data) > room:
            sys.exit("altention dc post：檔案太大（全部加起來上限 8 MB）")
        room -= len(data)
        files.append({"name": os.path.basename(path), "b64": base64.b64encode(data).decode()})
        where = where_of(machine or host, path, cwd)
        if where:
            files[-1]["where"] = where
        source, why = file_source(path)
        if source:
            files[-1]["source"] = source
        elif why:  # the file goes without: its picture is still worth posting
            print("altention dc post：%s，這個附件不帶原文" % why, file=sys.stderr)
    if files:  # the first where a bot before C-73 looks for it (it shows that one at least), the rest beside it
        post["file_name"], post["file_b64"] = files[0]["name"], files[0]["b64"]
        if files[0].get("source"):
            post["file_source"] = files[0]["source"]
        if files[0].get("where"):
            post["file_where"] = files[0]["where"]
        if files[1:]:
            post["more_files"] = files[1:]
    if not post["text"] and not files and not any(k in post for k in ("json", "new_claude", "resume", "history", "move_to", "secret",
                                                                       "waiting", "answer", "talk_continue", "pause",
                                                                       "unpause", "drop_held", "rename", "delete_messages", "reset", "move_place")):
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
    from altention.dc import sender
    post = build(sys.argv[1:], lambda: "" if sys.stdin.isatty() else sys.stdin.read(), os.environ, host, os.getcwd(),
                 this_machine(host), sender.claude_env())
    code, said = deliver(post)
    if said:
        sys.exit(said)
    sys.exit(code)


if __name__ == "__main__":
    main()
