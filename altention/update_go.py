#!/usr/bin/env python3
"""altention update: updating the engine from #update's card (C-57 U2), or from a terminal.

  altention update check [--json]         look first: can this machine take the newest release as it is (nothing moved)
  altention update go <vX.Y.Z> [--card <id>]
                                          the whole way: check, update.py --release, ./install.sh, setup.py reconcile,
                                          doctor; each step on the card (--card: the bot's machine only, the bot passes
                                          it); without --card it is said on this terminal only
  altention update save <vX.Y.Z>          keep the edits not committed as one commit ("local edits before vX.Y.Z"), so
                                          go can take them on top of the release (asked first: Claude does)
  altention update aside <vX.Y.Z>         rename the files not tracked here that have the name of one the release adds
                                          to <name>.mine (asked first: Claude does), so go can put the release's in
  altention update apply <vX.Y.Z>         take the copy Claude merged (go makes it under #setup's folder when your
                                          changes meet the release's), after the person agreed
  altention update undo                   back to before the last update (altention/before-vX, update.py marks it;
                                          the plugins that update moved go back too)
  altention update plugin <name> <vX.Y.Z> [--anyway]
                                          one plugin moved to that release, once the person saw what it brings (the
                                          bot's machine; --anyway: even though its own test fails)
  altention update follow                 this machine's plugins to the versions the instance says (lead_others runs it
                                          on each other machine; update.py's rounds do it through plugins-round)
  altention update card <vX.Y.Z>          the 🆕 card for that release with its buttons (update.py calls it on the bot's
                                          machine; exit 3 elsewhere, and update.py posts its notice in words)

The check (check()) tries the update aside and moves nothing: the newest release this machine does not have yet,
then what this machine has of its own. Nothing of its own, or commits that go on top of the release cleanly
(sync.try_rebase, in a throwaway worktree), and go goes on by itself. Anything else waits for a person: edits not
committed (asked every time, owner 2026-10-19), a file not tracked here with the name of one the release adds,
commits that do not go on top, rollbacks mixed with other commits, an engine detached from its branch, git that
cannot say; the card then says why in plain words and hands it to #setup's Claude (the "請 Claude 幫忙" button).
An engine unpacked from an archive cannot update this way: said, no button.
go: its own lock (update-go.lock), one at a time. It holds the git lock only while it checks; update.py --release
takes it as every round does, with ALT_UPDATE_CARD set so that round pushes no ✅ or ⚠️ of its own (the card says
it). The release asked for may be older than the newest by now: the newest is taken, and the card says so and lists
what was really installed. Once the engine moved, the rest (install, reconcile, doctor) runs in the new version's
own update_go (exec, --after-move; what was done so far in update-go.json). A go interrupted after the move (the
record still there) is taken up by the next go: the steps left, when the record's version is what runs here; a
record that does not match is said and removed. A step that fails: the card says which,
whether the engine moved, and offers Claude's help. Only the bot's machine writes cards (the bot judges a card's
freshness by the writer's clock: manager 2026-10-20); other machines' progress comes in U5 through the bot's.
Several machines (U5): only the bot's machine writes cards and tells a new release; once it went through, its go
takes every other machine on the list (ssh, `altention update go vX` there, no card) one after another, each a line
under the ✅; one stopping does not stop the rest, and an engine there too old for this updates the old way.
Every move to a release leaves altention/before-vX where HEAD was (update.py); undo goes back to the newest one,
only from where that move went and only when the engine does not update by itself. save keeps edits as one commit
once the person said yes (Claude asks: decision 4), and nothing is ever stashed, reset hard or thrown away (C-57 U3).
External plugins (C-61 E3, plugin_update does the moving): the 🆕 card lists every plugin with a newer release
under the engine, one card for all (its id from the whole set: the same set is not posted again); 更新 takes the
engine first, then the plugins whose check is clean (in their requires order: one failing keeps those needing it,
never the engine back), sets the instance's versions once and pushes it; the others wait under 請 Claude 幫忙
(`altention update plugin`). Plugins that update by themselves (update "release") move in update.py's rounds
(plugins-round, outside the git lock); every machine's round and lead_others take its plugins to the instance's
versions (follow). Moves are kept in plugin-moves.json for undo.
Text handed to Claude: in the button itself up to ASK_INLINE characters; longer, in a file under #setup's folder
(setup/update-work/: #setup's Claude reads there without asking, M0 2026-10-19), the button saying to read it.
"""
import fcntl
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time

ENGINE_HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ENGINE_HERE not in sys.path:
    sys.path.insert(0, ENGINE_HERE)

from altention import catalog, config, plugin_update, sync, update  # noqa: E402
from altention.dc import card as cards  # noqa: E402

GO_KINDS = ("clean", "commits")  # what go takes on by itself
ASK_INLINE = 3000                # characters handed to Claude in the button itself (the bot refuses over 6000)
NOT_THE_BOT_MACHINE = update.NOT_THE_BOT_MACHINE
LOCK_WAIT = 120                  # seconds the check waits for the git lock (a sync round holds it a while)
STEPS = (("update", "換到新版"), ("install", "安裝新版需要的東西"), ("plugins", "換插件"), ("reconcile", "補齊頻道和設定"),
         ("doctor", "系統檢查"))
TIMEOUTS = {"update": 900, "install": 1200, "reconcile": 300, "doctor": 300, "machine": 3600}


def engine():
    return update.ENGINE  # at call time (the tests point it at a rig)


def state_dir():
    return update.state_dir()


def work_dir():
    """#setup's folder's update-work/: what #setup's Claude is handed to read (its own folder, so no question)."""
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "setup", "update-work")


def git(*args, timeout=120):
    return sync.Repo(engine()).git(*args, timeout=timeout)


def lines(r):
    return [l for l in (r.stdout or "").splitlines() if l.strip()]


class git_lock(object):
    """The engine's git lock for the check: the one update.py hands over (ALT_GIT_LOCK_FD: it holds it), else taken
    here, waiting up to LOCK_WAIT. .fd is None when it could not be had."""

    def __init__(self, repo):
        self.repo, self.f, self.fd = repo, None, None

    def __enter__(self):
        given = os.environ.get("ALT_GIT_LOCK_FD", "")
        if given.isdigit():
            self.fd = int(given)
            return self
        gd = self.repo.git_dir()
        if gd is None:
            return self
        self.f = open(os.path.join(gd, update.LOCK_NAME), "a")
        until = time.time() + LOCK_WAIT
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                self.fd = self.f.fileno()
                return self
            except OSError:
                if time.time() > until:
                    return self
                time.sleep(2)

    def __exit__(self, *exc):
        if self.f is not None:
            self.f.close()
        return False


def found(kind, **more):
    d = {"kind": kind, "tag": None, "now": None, "files": [], "commits": [], "why": ""}
    d.update(more)
    return d


def check(fetch=True):
    """What this machine would do to take the newest release, nothing moved (see the module's docstring):
    {"kind", "tag" (the newest release, None when none), "now" (the release HEAD has), "files", "commits", "why"}."""
    eng = engine()
    if not os.path.lexists(os.path.join(os.path.realpath(eng), ".git")):
        return found("archive")
    repo = sync.Repo(eng)
    gd = repo.git_dir()
    if gd is None:
        return found("unreadable", why="讀不到 git 資料")
    if any(os.path.exists(os.path.join(gd, n)) for n in ("rebase-merge", "rebase-apply", "index.lock")):
        return found("busy", why="rebase 做到一半或有 index.lock")
    with git_lock(repo) as lock:
        if lock.fd is None:
            return found("busy", why="git 鎖一直被別人拿著（%d 秒）" % LOCK_WAIT)
        repo.lock_fd = lock.fd
        rnd = update.Round()
        now = update.release_in(rnd)[0]
        if repo.git("symbolic-ref", "-q", "HEAD", timeout=20).returncode != 0:
            head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
            return found("detached", now=now, why="detached HEAD %s" % head[:12])
        if fetch:
            r = repo.git("fetch", "-q", "--tags")
            if r.returncode != 0:
                return found("offline", now=now, why="fetch 失敗：%s" % (r.stderr.strip() or r.stdout.strip())[:200])
        rel = update.latest_release(rnd)
        head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
        if not rel or not head:
            return found("none", now=now, why="還沒有任何發佈版本" if not rel else "讀不到 HEAD")
        tag, up = rel
        if repo.git("merge-base", "--is-ancestor", up, head, timeout=20).returncode == 0:
            return found("none", tag=tag, now=now, why="HEAD 已經包含 %s" % tag)
        base = dict(tag=tag, now=now)
        edits = repo.git("status", "--porcelain", "--untracked-files=no", timeout=60)
        if edits.returncode != 0:
            return found("unreadable", why="git status 失敗：%s" % edits.stderr.strip()[:200], **base)
        changed = [l[3:].split(" -> ")[-1] for l in edits.stdout.splitlines() if l.strip()]
        if changed:
            return found("edits", files=changed, why="還沒 commit 的修改：%s" % "、".join(changed[:10]), **base)
        loose = in_the_way(repo, head, up)
        if loose:
            return found("untracked", files=loose, why="沒追蹤的檔跟新版要加的檔同名：%s" % "、".join(loose[:10]), **base)
        if repo.git("rev-list", "--count", head, "--not", up, timeout=20).stdout.strip() in ("", "0"):
            return found("clean", **base)
        backs, steps, others = sync.local_commits(repo, up)
        if backs is None:
            return found("unreadable", why="列不出這台在發佈版本之上的 commit：%s" % others, **base)
        if any(looks for _, looks in others) or ((backs or steps) and others):
            return found("mixed", commits=[h[:7] for h in backs + steps] + [h[:7] for h, _ in others],
                         why="退回 commit 和別的 commit 混在一起", **base)
        if not others:
            return found("clean", why="只有機器人自己退回的 commit：換版時留在舊版", **base)
        subjects = lines(repo.git("log", "--format=%h %s", "%s..HEAD" % up, timeout=20))
        code, why, stuck = sync.try_rebase(repo, up)
        if code:
            return found("conflict", files=stuck, commits=subjects, why=why, **base)
        return found("commits", commits=subjects, **base)


def in_the_way(repo, head, up):
    return update.in_the_way(repo, head, up)  # one copy, update.py's (it checks before a move of its own too)


PLAIN = {  # one line for the 🆕 card (manager 2026-10-20: plain words; the rest under 看要做什麼)
    "clean": "這台沒有改過的東西，可以直接更新。",
    "commits": "這台有你自己改過的地方，先在旁邊試過了，可以跟新版接在一起：按「更新」會直接換，你的修改會留著。",
    "edits": "這台有你自己改過的檔，按「更新」會請 Claude 帶你處理，不會直接換。",
    "untracked": "這台有一個檔跟新版要加的檔同名，按「更新」會請 Claude 帶你處理，不會直接換。",
    "conflict": "這台有你自己改過的地方，新版本也改了同一段，按「更新」會請 Claude 帶你處理，不會直接換。",
    "mixed": "這台有機器人自己退回的版本，又有你改過的地方，按「更新」會請 Claude 帶你處理，不會直接換。",
    "detached": "這台的引擎停在一個固定的版本上，按「更新」會請 Claude 帶你處理，不會直接換。",
    "unreadable": "這台的引擎現在看不出狀態，按「更新」會請 Claude 帶你處理，不會直接換。",
    "busy": "這台的引擎現在正在做別的事，等一下再按「更新」就可以。",
    "offline": "現在連不上放新版本的地方，等一下再按「更新」就可以。",
    "archive": "這台的引擎是從壓縮檔解開的，沒辦法在這裡更新：請下載新版重新解開。",
    "none": "這台已經是最新的了。",
}

EXPLAIN = {  # what stopped go, for its card: what happened, whether anything is lost, what can be done (C-57 U3;
    # the person may not be an engineer: no commit, rebase, HEAD, and no "saved", manager 2026-10-19)
    "edits": "這台有你自己改過的檔%s。為了不蓋掉你的修改，這次先不更新，現在照舊版跑，你的修改都還在。\n"
             "可以先把你的修改保留下來再更新，更新後你的修改還在：按「請 Claude 幫忙」，Claude 會先問你一次。",
    "untracked": "新版本要加的檔案%s，這台已經有同名的檔（不是引擎原本就有的）。為了不蓋掉它，這次先不更新，現在照舊版跑，"
                 "那個檔沒動。\n把那個檔改名或移走再按一次更新，或按「請 Claude 幫忙」。",
    "conflict": "你自己改過的地方%s，新版本也改了同一段，兩邊要合在一起。為了不蓋掉你的修改，這次先不更新，現在照舊版跑，"
                "你的修改都還在。\n按「請 Claude 幫忙」：Claude 會先在旁邊合好給你看，你同意了才換上。",
    "mixed": "這台有機器人自己退回的版本，又有你自己改過的地方，程式不猜哪些要留。這次先不更新，現在照舊版跑，你的東西都還在。\n"
             "按「請 Claude 幫忙」：Claude 會一項一項問你。",
    "detached": "這台的引擎停在一個固定的版本上，不在平常更新的那條線上，所以不會更新。現在照這個版本跑，你的東西都沒動。\n"
                "按「請 Claude 幫忙」：Claude 會帶你回到平常的那條線再更新。",
    "unreadable": "看不出這台的引擎現在的狀態（細節在「看要做什麼」）。這次先不更新，你的東西都沒動。\n按「請 Claude 幫忙」。",
}


def explain(f):
    words = EXPLAIN[f["kind"]]
    return words % named(f["files"]) if "%s" in words else words


def named(files):
    return "（%s）" % "、".join(files[:5] + (["…"] if len(files) > 5 else [])) if files else ""


def technical(f):
    """The check in technical words: for 看要做什麼 and for Claude."""
    out = ["預檢（altention update check）：%s" % f["kind"]]
    if f.get("now") or f.get("tag"):
        out.append("這台的發佈版本：%s；最新的：%s" % (f.get("now") or "沒有", f.get("tag") or "沒有"))
    if f.get("why"):
        out.append("原因：%s" % f["why"])
    if f.get("files"):
        out.append("檔案：" + "、".join(f["files"][:20]))
    if f.get("commits"):
        out.append("這台自己的 commit：\n" + "\n".join("  " + c for c in f["commits"][:20]))
    if f.get("copy"):
        out.append("合併用的副本（rebase 停在衝突上）：%s" % f["copy"])
    return "\n".join(out)


def for_agents(rnd, tag, versions):
    """The "For agents" part of each of `versions` in `tag`'s CHANGELOG, newest first ("" when none says anything)."""
    log = rnd.git("show", "%s:CHANGELOG.md" % tag).stdout
    parts = []
    for v in versions:
        section, inside, part, said = [], False, None, []
        for line in log.splitlines():
            if line.startswith("## "):
                if inside:
                    break
                inside = line[3:].split(" ")[0] == v
            elif inside:
                section.append(line)
        for line in section:
            if line.startswith("### "):
                part = line[4:].strip().lower()
            elif part == "for agents" and line.strip():
                said.append(line)
        if said:
            parts.append("%s 的 For agents：\n%s" % (v, "\n".join(said)))
    return "\n\n".join(parts)


def bot_machine_here():
    """This machine is the bot's, by name (config.bot_machine): only then cards and leading the others (U5). A
    machine on a list that does not know its name cannot tell: not taken for the bot's (manager 2026-10-20)."""
    try:
        return bool(config.bot_machine()[2]) and (config.known_here() or not config.inventory().get("machines"))
    except Exception:
        return False


def bot_elsewhere():
    """The machine list names another machine as the bot's, and knows this one by name (its 🆕 is told there)."""
    try:
        name, _, here = config.bot_machine()
        return bool(name) and not here and config.known_here()
    except Exception:
        return False


class Say(object):
    """Where go tells what it does: the card (card_id), else this terminal. A card that cannot be posted is said on
    the terminal (the log, under the bot) and go goes on: the update matters more than its card."""

    def __init__(self, card_id):
        self.card_id = card_id

    def __call__(self, text, buttons=(), details=None):
        print(text, flush=True)
        if not self.card_id:
            return
        try:
            cards.show(self.card_id, text, buttons, details)
        except cards.CardError as e:
            print("（卡片 %s 沒更新：%s）" % (self.card_id, e), flush=True)


def handed(card_id, text):
    """What the 請 Claude 幫忙 button carries: `text` itself, or (long) the path of a file in work_dir() with it."""
    if len(text) <= ASK_INLINE:
        return text
    os.makedirs(work_dir(), exist_ok=True)
    path = os.path.join(work_dir(), "%s-%d.md" % (card_id or "update", time.time()))
    with open(path, "w") as f:
        f.write(text)
    return "請讀 %s，照 update skill 處理（這台引擎的更新卡住了，要你一步一步帶使用者處理）。" % path


def ask_text(f, version, step=None, said=""):
    """For #setup's Claude: what happened, in technical words, and what is asked of it."""
    head = ("引擎更新卡住了，請照 update skill 帶使用者處理：先用白話講發生什麼、會不會丟東西，問過才動手；"
            "只用 altention update 的子指令和 setup.py，不自己跑 git。")
    body = ["要更新到：%s" % version]
    if step:
        body.append("停在：%s" % step)
    body.append(technical(f))
    if f["kind"] in NEXT:
        body.append("可以做的：" + NEXT[f["kind"]] % {"v": version})
    if said:
        body.append("最後的輸出：\n" + said[-2000:])
    return head + "\n\n" + "\n".join(body)


NEXT = {  # for Claude, by kind: the subcommands that take it on (U3; U4 adds the merged copy's apply)
    "edits": "先問使用者一次要不要把修改保留下來再更新（決定 4）；同意就 `altention update save %(v)s`，"
             "再 `altention update go %(v)s`。不同意就不動。",
    "untracked": "先問使用者一次，同意就 `altention update aside %(v)s`（把那幾個檔改名成 <名字>.mine，內容不動），"
                 "再 `altention update go %(v)s`。不要那個檔、或想放別的地方，照使用者說的做。",
    "conflict": "使用者自己改過的地方跟新版改到同一段：先講清楚兩邊各改了什麼，問過再合。合併用的副本在上面寫的資料夾"
                "（rebase 停在衝突上），在那裡用 Edit 把每個衝突的檔合好、拿掉 <<<<<<< ======= >>>>>>> 標記，給使用者看過、"
                "同意後跑 `altention update apply %(v)s`；它說還有下一段就再合、再跑。沒有副本（上面沒寫）就先跑 "
                "`altention update go %(v)s` 做一份。",
    "mixed": "列出每個 commit 是誰的（機器人退回的、使用者自己的），問使用者哪些要留。",
    "detached": "問使用者要不要回到平常的分支；這要 git，先說明，等 U4 的子指令或請使用者在終端機做。",
}


def help_buttons(card_id, f, version, step=None, said=""):
    return [{"id": "ask", "label": "請 Claude 幫忙", "style": "primary", "action": "ask_claude",
             "arg": handed(card_id, ask_text(f, version, step, said))},
            {"id": "details", "label": "看要做什麼", "style": "secondary", "action": "details"}]


def progress_path():
    return os.path.join(state_dir(), "update-go.json")


def write_progress(d):
    os.makedirs(state_dir(), exist_ok=True)
    with open(progress_path() + ".tmp", "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(progress_path() + ".tmp", progress_path())


def board(version, done, now=None, first="✓ 先在旁邊試過了：可以直接換", only_plugins=False):
    """The ⏳ card: the steps done (✓), the one going (…), the rest (only the plugins': no engine steps)."""
    out = ["⏳ 正在更新插件…（進度會寫在這張卡上）" if only_plugins else
           "⏳ 正在更新到 %s…（進度會寫在這張卡上；中途機器人可能會重啟一下）" % version, first]
    for key, words in STEPS:
        if only_plugins and key in ("update", "install"):
            continue
        out.append(("✓ " if key in done else "… " if key == now else "　 ") + words)
    return "\n".join(out)


def run(argv, key, env=None):
    """One step's command in the engine folder, in a process group of its own: (exit status, what it printed).
    Taking longer than TIMEOUTS[key], the whole group is killed (its git clone or pip too, as setup.py's script()), and
    then everything left in its session, as the supervisor does: install's external-plugin setup items run in groups
    of their own within it (C-61 E2) and stop with it."""
    try:
        p = subprocess.Popen(argv, cwd=engine(), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                             stderr=subprocess.STDOUT, universal_newlines=True, errors="replace", env=env,
                             start_new_session=True)
    except OSError as e:
        return 127, str(e)
    try:
        out, _ = p.communicate(timeout=TIMEOUTS[key])
        return p.returncode, out or ""
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        try:  # its session's other groups (start_new_session: the session id is its pid)
            subprocess.run(["pkill", "-KILL", "-s", str(p.pid)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        except (OSError, subprocess.TimeoutExpired):  # no pkill here (a bare container): the group went at least
            pass
        try:  # one that left the session (setsid) may still hold the pipe: not waited for
            p.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            p.stdout.close()
        return 124, "%d 秒沒有結束，已中止" % TIMEOUTS[key]


def alerts_since(t):
    """The plain words of update.py's own alerts written since `t` (its ⚠️, which it does not push under a card)."""
    try:
        with open(os.path.join(state_dir(), "alerts"), errors="replace") as f:
            rows = f.read().splitlines()
    except OSError:
        return []
    out = []
    for row in rows:
        parts = row.split("\t", 3)
        if len(parts) == 4 and parts[1] == "update":
            try:
                at = time.mktime(time.strptime(parts[0][:19], "%Y-%m-%dT%H:%M:%S"))
            except ValueError:
                continue
            if at >= int(t) - 1:
                out.append(parts[3])
    return out


GO_LOCK_HELD = "ALT_UPDATE_GO_LOCK"  # "1": a parent holds update-go.lock (go's or undo's install.sh): not taken again
BUSY = "有一次更新正在跑，等它做完再試；什麼都沒動。"


class go_lock(object):
    """`with go_lock() as held:` update-go.lock, not waited for: held False when another update has it. Every path
    that moves a plugin's folder takes it (go, plugins_round, update plugin, follow, undo; install.sh's
    core.plugins-external), so follow never takes a plugin back between a switch and set_versions (#code-review of
    c0b20b7, manager 2026-10-20). Held already: GO_LOCK_HELD in the environment, which only the holder's
    install.sh is given (holder_env), or parent=True (ops/install.py takes that variable out of its environment as
    it starts, so nothing it starts carries it on: #code-review of e73418a)."""

    def __init__(self, parent=False):
        self.parent = parent

    def __enter__(self):
        self.f = None
        if self.parent or os.environ.get(GO_LOCK_HELD) == "1":
            return True
        os.makedirs(state_dir(), exist_ok=True)
        self.f = open(os.path.join(state_dir(), "update-go.lock"), "a")
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            self.f = None
            return False
        return True

    def __exit__(self, *exc):
        if self.f is not None:
            self.f.close()
        return False


def holder_env():
    """The environment for install.sh run by a holder of update-go.lock: it moves plugins' folders under that lock."""
    return dict(os.environ, **{GO_LOCK_HELD: "1"})


def go(version, card_id=None, after_move=False):
    say = Say(card_id)
    os.makedirs(state_dir(), exist_ok=True)
    lock = open(os.path.join(state_dir(), "update-go.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        say("⏳ 已經有一次更新正在跑，這次就不重複了；那次的進度寫在它自己的卡片上。")
        return 1
    try:
        if after_move:
            return go_on(say, version, card_id)
        return go_first(say, version, card_id)
    finally:
        lock.close()


def go_first(say, version, card_id):
    resumed = resume(say, card_id)
    if resumed is not None and (resumed != 0 or update.vtuple(update.release_in(update.Round())[0]) >= update.vtuple(version)):
        return resumed  # the steps an interrupted go left are done (or stopped); nothing newer was asked
    say("⏳ 先在旁邊試一次，不動到現在在跑的…")
    f = check()
    tag = f.get("tag") or version
    if f["kind"] == "none" and f.get("now") and update.vtuple(f["now"]) >= update.vtuple(version):
        if bot_machine_here() and any(o["tag"] for o in offers(fetch=True)):  # a card for plugins only (C-61 E3)
            return only_plugins(say, card_id, f["now"], version)
        say("✅ 這台已經是 %s 了，不用再更新。" % f["now"])
        return 0
    if f["kind"] in ("busy", "offline", "archive", "none"):
        say("❌ 這次沒有更新：%s 你的東西都沒動。" % PLAIN[f["kind"]], details=technical(f))
        return 1
    if f["kind"] not in GO_KINDS:
        if f["kind"] == "conflict":  # the copy Claude merges in, under #setup's folder (U4)
            made = make_copy(tag)
            f["copy"], f["files"] = made if made[0] else (None, f["files"])
        wait = "\n插件等引擎好了再一起換。" if bot_machine_here() and any(o["tag"] for o in offers()) else ""
        say("📝 還沒更新到 %s：%s%s" % (tag, explain(f), wait), help_buttons(card_id, f, tag), technical(f))
        return 1
    newer = "（你按的是 %s；已經有更新的 %s，直接換到它。）" % (version, tag) if tag != version else ""
    first = "✓ 先在旁邊試過了：" + ("可以直接換" if f["kind"] == "clean" else "你的修改可以跟新版接在一起，會留著")
    write_progress({"card": card_id, "asked": version, "tag": tag, "start": f.get("now"), "first": first,
                    "newer": newer, "done": [], "notes": [], "at": time.time()})
    say(board(tag, [], "update", first) + ("\n" + newer if newer else ""))
    started = time.time()
    env = dict(os.environ, **{update.ON_A_CARD: card_id or "terminal"})
    env.pop("ALT_GIT_LOCK_FD", None)
    code, out = run([sys.executable, os.path.join(engine(), "altention", "update.py"), "--release"], "update", env)
    now = update.release_in(update.Round())[0]
    if code == update.LOCKED and (not now or update.vtuple(now) < update.vtuple(tag)):
        say("❌ 這次沒有更新：引擎現在正在做別的事（同步或另一個更新），等一下再按更新就可以。你的東西都沒動。", details=out[-3000:])
        return 1
    if not now or update.vtuple(now) < update.vtuple(tag):
        why = alerts_since(started)
        plain = why[-1] if why else "換版這一步沒成功，這次沒有更新，現在照舊版跑，你的東西都沒動。"
        say("❌ 還沒更新到 %s：%s" % (tag, re.sub(r"（細節：.*$", "", plain, flags=re.S)),
            help_buttons(card_id, f, tag, "換到新版（update.py --release）", out), technical(f) + "\n\n" + out[-3000:])
        return 1
    d = read_progress()
    d["done"], d["tag"] = ["update"], now
    write_progress(d)
    # the rest in the new version's own code (it may know steps this one does not)
    argv = [sys.executable, os.path.join(engine(), "altention", "update_go.py"), "go", now, "--after-move"]
    if card_id:
        argv += ["--card", card_id]
    sys.stdout.flush()
    try:
        os.execv(sys.executable, argv)
    except OSError as e:
        print("（換不到新版的程式接著做：%s；用這一版做完）" % e, flush=True)
        return go_on(say, now, card_id)


def steps_left(d):
    """The words of the steps a go's record `d` has not done, in order (setup.py status): without 換插件 when the
    instance declares no plugin with a source (plugin_update.enabled, as follow reads it); all of them when the
    settings cannot be read."""
    done = d.get("done") if isinstance(d.get("done"), list) else []
    try:
        plugins = bool(plugin_update.enabled(config.settings()))
    except Exception:
        plugins = True
    return [words for key, words in STEPS if key not in done and (plugins or key != "plugins")]


def only_plugins(say, card_id, now, asked):
    """The engine is the newest already and plugins have newer releases: go's steps from 換插件 on (the record says
    so: an interrupted one is taken up as any other)."""
    write_progress({"card": card_id, "asked": asked, "tag": now, "start": now, "newer": "", "notes": [],
                    "first": "✓ 引擎已經是最新的 %s，這次只換插件" % now, "done": ["update", "install"],
                    "only_plugins": True, "at": time.time()})
    return go_on(say, now, card_id)


def read_progress():
    try:
        with open(progress_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def close_old(d, card_id, on_a_card, on_a_terminal):
    """The card the record names, when it is not this go's, closed with a plain line: it would stay at its ⏳
    (#code-review-2 of f5fc771; the record dropped as well, #code-review of 394f260). The bot's machine only."""
    old = d.get("card")
    if old and old != card_id and bot_machine_here():
        try:
            cards.close(old, on_a_card if card_id else on_a_terminal)
        except cards.CardError as e:
            print("（被中斷的那張卡 %s 沒收掉：%s）" % (old, e), flush=True)


def resume(say, card_id):
    """A go that was interrupted after the move (update-go.json there, the update done): its remaining steps, from
    where it stopped, when the record's version is what this machine runs; else the record said and removed
    (manager 2026-10-20). The result of the steps, or None when there was nothing to take up."""
    d = read_progress()
    if not d:
        return None
    now = update.release_in(update.Round())[0]
    if not d.get("done") and d.get("tag") == now and now:  # killed between update.py moving HEAD and the record
        d["done"] = ["update"]  # saying so: the move is there, so it was done (#code-review of e3e4267)
    if "update" not in (d.get("done") or []) or d.get("tag") != now:
        say("上次沒做完的更新紀錄跟這台現在的版本對不上（紀錄是 %s，這台是 %s），已經刪掉，這次從頭來。"
            % (d.get("tag") or "?", now or "還沒有發佈版本"))
        close_old(d, card_id, "這次更新沒做完，已經從頭再更新一次，進度在新的那張卡上。",
                  "這次更新沒做完，已經在終端機從頭再更新一次。")
        try:
            os.remove(progress_path())
        except OSError:
            pass
        return None
    close_old(d, card_id, "這次更新被中斷了，接著做的進度在新的那張卡上。", "這次更新被中斷了，之後在終端機接著做完。")
    d["card"] = card_id  # carried on where this go is told (another card, or a terminal)
    d["first"] = "✓ 上次換到 %s 之後被中斷了，接著把剩下的做完" % now
    write_progress(d)
    return go_on(say, now, card_id)


def go_on(say, version, card_id):
    """After the move: install, reconcile, doctor; then ✅ (what was installed) or ❌ (where it stopped)."""
    d = read_progress()
    if d.get("card") != card_id or d.get("tag") != version or "update" not in (d.get("done") or []):
        say("❌ 更新的進度紀錄對不上（%s），剩下的步驟沒有接著做。引擎已經換到新版；"
            "請跟 Claude 說「跑 ./install.sh 和 doctor」。" % progress_path())
        return 1
    first, notes = d.get("first") or "✓ 先在旁邊試過了", d.get("notes") or []
    f = found("clean", tag=version, now=d.get("start"))
    for key, words in STEPS[1:]:
        if said_taken_back(say, version, card_id, f):
            return 1
        if key in d["done"]:  # an interrupted go taken up (resume): what it did already is not done again
            continue
        say(board(version, d["done"], key, first, d.get("only_plugins")))
        if key == "plugins":  # a plugin that does not move is a line on the card, not a stop (the engine stays)
            try:
                _, d["plugins"] = plugins_step(version, d)
            except Exception as e:  # a line, and on: the engine moved, the rest still to do (#code-review of c0b20b7)
                d["plugins"] = ["❌ 換插件這一步出錯了（%s: %s），插件沒有換；跟 Claude 說「看一下插件為什麼沒換」。"
                                % (type(e).__name__, str(e)[:200])]
            d["done"].append(key)
            write_progress(d)
            continue
        code, out = step(key)
        if code is None:  # nothing to do on this machine or version
            d["done"].append(key)
            continue
        if key == "install" and code == 2:
            notes.append("安裝還有幾件事要你動手，跟 Claude 說「跑 ./install.sh，照它說的做」。")
        elif key == "reconcile" and code == 0:
            asks = [l for l in out.splitlines() if l.startswith("問 ")]
            if asks:
                notes.append("有 %d 件事要你決定，請到 #setup 回答。" % len(asks))
        elif key == "doctor" and code != 0:
            notes.append("系統檢查有沒過的項目，跟 Claude 說「跑 doctor 看看」。")
        elif code != 0:
            d["notes"] = notes
            write_progress(d)
            say("❌ 引擎已經換到 %s，但「%s」這一步沒做完：%s\n新版已經在跑；按「請 Claude 幫忙」把剩下的做完。"
                % (version, words, last_line(out)), help_buttons(card_id, f, version, words, out), out[-3000:])
            return 1
        d["done"].append(key)
        d["notes"] = notes
        write_progress(d)
    if said_taken_back(say, version, card_id, f):
        return 1
    text = done_text(d, version, notes)
    waiting = d.get("waiting") or []
    buttons = [{"id": "ask", "label": "請 Claude 幫忙", "style": "primary", "action": "ask_claude",
                "arg": handed(card_id, plugin_ask(waiting))}] if waiting and card_id else []
    say(text, buttons)
    try:
        os.remove(progress_path())
    except OSError:
        pass
    if bot_machine_here():  # this machine done: the others, one after another, each a line on this card (U5)
        lead_others(say, text, version, buttons)
    return 0


OTHER = {"ok": "✅", "person": "📝", "fail": "❌", "unreached": "⚠️", "going": "⏳", "waiting": "　"}


def others():
    """[(name, machine)]: the other machines on the list, in its order (not this one, not one reached locally)."""
    try:
        machines = config.inventory().get("machines") or {}
        me = config.this_machine()
    except Exception:
        return []
    return [(n, m) for n, m in machines.items() if n != me and isinstance(m, dict) and m.get("ssh")
            and "local" not in m["ssh"]]


def lead_others(say, text, version, buttons=()):
    """altention update go <version> on each other machine (ssh, as ops/apply.py reaches them), after this one
    went through: each its own check, update, install and doctor; one stopping (its edits, a refused release,
    not reached) does not stop the rest. Their state as lines under this card's ✅, whose buttons stay (a card
    updated without them loses them: #code-review-2 of c0b20b7)."""
    rows = others()
    if not rows:
        return
    state = dict((n, ("waiting", "等著")) for n, _ in rows)

    def board():
        return text + "\n\n其他台：\n" + "\n".join("%s %s：%s" % (OTHER[state[n][0]], n, state[n][1]) for n, _ in rows)
    plugins = bool(plugin_update.declared())
    for n, m in rows:
        state[n] = ("going", "更新中…")
        say(board(), buttons)
        state[n] = go_there(m, version)
        if plugins and state[n][0] == "ok":  # its plugins to the instance's versions, pulled there first (C-61 E3)
            state[n] = (state[n][0], state[n][1].rstrip("。") + "；" + follow_there(m))
    say(board(), buttons)


def verdict(code, out):
    """(state, words) from what `altention update go` printed on another machine: its last card's first line."""
    heads = [l.strip() for l in out.splitlines() if l.strip()[:1] in ("✅", "📝", "❌")]
    last = heads[-1] if heads else ""
    words = re.sub(r"^\S+\s*", "", last)
    words = (words[:words.index("。") + 1] if "。" in words else words)[:160] or "（沒有說結果）"  # its result, not
    # the "這版的重點：" that follows it (#code-review-2 of 49b8896)
    if code == 0 and last.startswith("✅"):
        return "ok", words
    if last.startswith("📝"):
        return "person", words + "（到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」，說是哪一台）"
    return "fail", words if heads else "沒做完（結束碼 %s）：%s" % (code, last_line(out))


OLD_ENGINE = "one entry point for the whole system"  # bin/altention's help: an engine from before `altention update`


def go_there(m, version):
    """(state, words) of updating machine `m` to `version` the way this one was. An engine too old for
    `altention update` updates as ops/apply.py did: update.py --release, then install.sh."""
    engine = config.shell_path(config.engine_raw(m))
    code, out, alias = on_machine(m, "%s/bin/altention update go %s" % (engine, version))
    if alias is None:
        return "unreached", "連不上（%s）" % out[-120:]
    if code == 2 and OLD_ENGINE in out:
        code, out, _ = on_machine(m, "cd %s && python3 altention/update.py --release && ./install.sh" % engine)
        return ("ok", "已更新（那台的引擎較舊，照舊的方式更新）") if code in (0, 2) else \
            ("fail", "沒做完（結束碼 %s）：%s" % (code, last_line(out)))
    return verdict(code, out)


def follow_there(m):
    """Words for machine m's line: its plugins after `altention update follow` there."""
    code, out, alias = on_machine(m, "%s/bin/altention update follow" % config.shell_path(config.engine_raw(m)))
    if alias is None:
        return "插件：連不上，下一輪它自己跟上"
    if code == 2 and "altention update follow" not in out:
        return "插件：那台的引擎還不會換插件，下一輪它自己跟上"
    bad = [l.strip() for l in out.splitlines() if l.strip().startswith("❌")]
    if not bad and code != 0:
        bad = ["沒做完（結束碼 %s）：%s" % (code, last_line(out))]
    return "插件：%s" % ("；".join(re.sub(r"^❌\s*", "", l) for l in bad)[:200] if bad else "都跟上了")


def on_machine(m, cmd):
    """`cmd` on machine `m` over its SSH aliases in order: (exit status, output, alias) — alias None when none
    answered. Killed whole after TIMEOUTS["machine"]."""
    last = ""
    for alias in config.ssh_aliases(m):
        code, out = run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias, cmd], "machine")
        if code == 255:
            last = out.strip()
            continue
        return code, out, alias
    return 255, last or "沒有可以用的 ssh 位址", None


def said_taken_back(say, version, card_id, f):
    """The bot's trial of the new version failed and the supervisor went back (update.taken_back, as the ✅ of a plain
    round judges it): said instead of the ✅, the steps left undone (#code-review of a5ae915). True when so."""
    if not update.taken_back(update.Round(), version):
        return False
    say("❌ 新版 %s 裝上了，但機器人試跑沒過，現在退回舊版在跑；你的東西都沒動。按「請 Claude 幫忙」看是哪裡出問題。" % version,
        help_buttons(card_id, f, version, "機器人試跑新版（supervisor 退回）"), technical(f))
    try:
        os.remove(progress_path())
    except OSError:
        pass
    return True


def last_line(out):
    """The line that says why (install.sh's "fail <id> <words>": the words), else the last one."""
    got = [l for l in out.splitlines() if l.startswith("fail ")] or [l for l in out.splitlines() if l.strip()]
    return re.sub(r"^fail \S+ ", "", got[-1])[:300] if got else "（沒有輸出）"


def step(key):
    """(exit status, output) of one step after the move; (None, "") when it has nothing to do here."""
    eng = engine()
    if key == "install":  # under go's update-go.lock: its plugins step (core.plugins-external) does not take it again
        return run([os.path.join(eng, "install.sh")], key, holder_env())
    if key == "reconcile":
        if not os.path.exists(os.path.join(eng, "ops", "setup.py")):
            return None, ""
        return run([sys.executable, os.path.join(eng, "ops", "setup.py"), "reconcile"], key)
    if key == "doctor":
        return run([sys.executable, os.path.join(eng, "ops", "doctor.py"), config.this_machine()], key)
    raise ValueError(key)


def done_text(d, version, notes):
    plugins = d.get("plugins") or []
    if d.get("only_plugins"):
        text = "%s（引擎已經是最新的 %s）：\n%s" % ("✅ 插件更新好了" if any(l.startswith("✅") for l in plugins) else
                                            "📝 插件這次沒有換", version, "\n".join(plugins))
        if notes:
            text += "\n\n" + "\n".join(notes)
        return text + "\n\n想回到更新前的版本：到 #setup 跟 Claude 說「引擎回到更新前」。"
    rnd = update.Round()
    start = d.get("start")
    versions = update.releases_between(rnd, update.vtuple(start) if start else None, version)
    between = [v for v in versions if v != version]
    text = "✅ 引擎已更新到 %s" % version
    if between:
        text += "（中間的 %s 也一起裝上了）" % "、".join(sorted(between, key=update.vtuple))
    text += "。" + (d.get("newer") or "") + update.notes_of(rnd, version, versions)
    if plugins:
        text += "\n\n插件：\n" + "\n".join(plugins)
    if notes:
        text += "\n\n" + "\n".join(notes)
    return text + "\n\n想回到更新前的版本：到 #setup 跟 Claude 說「引擎回到更新前」。"


def new_card(tag, if_new=False):
    """The 🆕 card for `tag` (update.py, notify mode, on the bot's machine): what it brings since the release this
    machine has, a plain line of the check, the buttons; the card of an older release closed. With plugins that have
    newer releases (C-61 E3), each one's line on the same card, and 更新 takes the engine and those that can go; the
    others wait under 請 Claude 幫忙. `tag` no newer than this machine's: a card for the plugins alone (none:
    nothing posted). if_new (plugins_round, every round): not posted again while what it offers is the same."""
    if bot_elsewhere():  # the bot's machine tells it, on its own card (U5: one 🆕, not one per machine)
        print("這台不是機器人那台：有新版由機器人那台貼卡片")
        return NOT_THE_BOT_MACHINE
    if not bot_machine_here():
        print("找不到機器人在哪一台，或機器清單上沒有這台的名字：不貼卡片（update.py 改用文字）")
        return 1
    rnd = update.Round()
    now = update.release_in(rnd)[0]
    engine_new = not now or update.vtuple(tag) > update.vtuple(now)
    shown = tag if engine_new else now
    got = offers()  # newer() as the last round's fetch left it (plugins_round fetches, outside the git lock)
    card_id = card_id_of(shown, got)
    if not engine_new and not any(o["tag"] for o in got):
        print("引擎和插件都沒有新版，不用貼卡片")
        return 0
    if if_new and card_id == last_card():
        print("跟上次貼的卡片一樣（%s），不重貼" % card_id)
        return 0
    checked(got, shown)
    goes = [o for o in got if o["tag"] and o["check"]["kind"] in PLUGIN_GO]
    waiting = [o for o in got if o["tag"] and o["check"]["kind"] not in PLUGIN_GO]
    details = []
    if engine_new:
        f = check(fetch=False)  # update.py fetched just now
        versions = update.releases_between(rnd, update.vtuple(now) if now else None, tag)
        text = "🆕 有新版本 %s 可以更新" % tag + ("（這台現在是 %s）。" % now if now else "。")
        text += update.notes_of(rnd, tag, versions)
        text += "\n\n" + PLAIN.get(f["kind"], PLAIN["unreadable"])
        engine_goes = f["kind"] not in ("archive", "none")
        agents = for_agents(rnd, tag, versions)
        details.append(technical(f) + ("\n\n" + agents if agents else ""))
    else:
        text, engine_goes = "🆕 有插件的新版本可以更新（引擎已經是最新的 %s）。" % now, False
    if got:
        text += "\n\n" + "\n".join(plugin_lines(got))
        details.append(plugin_technical(got))
    buttons = [{"id": "go", "label": "更新", "style": "primary", "action": "update_go", "arg": shown}] \
        if engine_goes or goes else []
    buttons += [{"id": "later", "label": "之後再說", "style": "secondary", "action": "later"},
                {"id": "details", "label": "看要做什麼", "style": "secondary", "action": "details"}]
    if waiting:  # the plugins a person looks at first, all in one: a card takes 5 buttons at most
        buttons.append({"id": "ask", "label": "請 Claude 幫忙", "style": "secondary", "action": "ask_claude",
                        "arg": handed(card_id, plugin_ask(waiting))})
    details = "\n\n".join(details)
    try:
        cards.show(card_id, text, buttons, details[:cards.DETAILS_MAX])
    except cards.CardError as e:
        print("沒貼成：%s" % e)
        return 1
    close_older(card_id, tag)
    return 0


def last_card():
    """The id of the 🆕 card posted last (update-card), None when none."""
    try:
        with open(os.path.join(state_dir(), "update-card")) as f:
            return json.load(f).get("id")
    except (OSError, ValueError, AttributeError):
        return None


def close_older(card_id, tag):
    """The card of the release told before (update-card: {id}) loses its buttons and says a newer one is out."""
    path = os.path.join(state_dir(), "update-card")
    old = last_card()
    if old and old != card_id:
        try:
            cards.close(old, "這張卡的版本已經不是最新的了：有新版本 %s，請看下面那張新卡。" % tag
                        if card_id == "update-%s" % tag else "這張卡已經不是最新的了：要更新的內容變了，請看下面那張新卡。")
        except cards.CardError as e:
            print("舊的卡片 %s 沒收掉：%s" % (old, e))
    with open(path + ".tmp", "w") as f:
        json.dump({"id": card_id, "at": time.time()}, f)
    os.replace(path + ".tmp", path)


PLUGIN_GO = ("clean",)  # what 更新 takes on by itself (C-61 E3); the rest waits on the card for a person
PLUGIN_STOP = ("unreachable", "broken", "origin", "edits", "engine", "interface")  # `update plugin` does not take these either
PLUGIN_PLAIN = {  # why a plugin does not go with 更新, for the card (plain words)
    "unreachable": "這次拿不到這一版",
    "broken": "新版的設定檔讀不懂",
    "origin": "這台的插件資料夾不是從設定的地方來的，不動它",
    "edits": "插件資料夾裡有你自己改過的東西",
    "engine": "新版要更新的引擎，引擎換好了再換",
    "interface": "新版要的引擎介面這版引擎沒有，換了也不會啟用",
    "test": "新版自己的測試沒過",
    "perms": "新版會在這台多做一些事，要你看過、同意了才換",
}
PLUGIN_NEXT = {  # for #setup's Claude, by kind: what can be done (update skill's 插件 section)
    "perms": "把「多了」那幾項用白話講給使用者聽（有 hook 或改寫回覆的要另外再問一次：會看到你的對話內容，決定 4），同意了才 "
             "`altention update plugin %(n)s %(v)s`。",
    "test": "說明新版自己的測試沒過（下面是沒過的那幾行），預設不換；使用者知道了還是要換，才 "
            "`altention update plugin %(n)s %(v)s --anyway`。",
    "engine": "先更新引擎（卡片上的「更新」，或 `altention update go <引擎的新版>`），再 `altention update plugin %(n)s %(v)s`。",
    "interface": "新版的 plugin.json 寫的 uses 這版引擎給不了（下面寫了是哪個介面）：先更新引擎，更新完還是對不上，"
                 "就留在現在這版，等插件出跟這版引擎相合的版本。",
    "edits": "插件資料夾裡有使用者自己的修改或同名的檔：先問使用者要留還是不要，不要自己動那個資料夾。",
    "origin": "那個資料夾不是從實例寫的 source 來的：問使用者，不要自己動它。",
    "broken": "新版的 plugin.json 讀不懂：這一版先不換，等插件出修好的版本。",
    "unreachable": "拿不到這一版：等一下再試，或問使用者 source 寫得對不對。",
}
FOLLOW_LIMIT = 600   # seconds a machine's plugins get to follow the instance in one go (setup items: later)
MOVES_KEPT = 3       # plugin moves remembered for undo


def offers(fetch=False, deadline=None, backoff=False):
    """[{"p", "tags", "tag", "check"}]: the plugins the instance declares with a source that have newer releases
    (plugin_update: tags newest first; tags None when the source could not be read, kept only when fetched: without
    a fetch, a plugin not looked at yet is not known, not unreachable). check is filled by checked()."""
    try:
        declared = plugin_update.enabled()
    except Exception:
        return []
    out = []
    for p in declared:
        try:
            tags = plugin_update.newer(p, fetch=fetch, deadline=deadline, backoff=backoff)
        except Exception:
            tags = None
        if tags == [] or (tags is None and not fetch):
            continue
        out.append({"p": p, "tags": tags, "tag": tags[0] if tags else None, "check": None})
    return out


def checked(got, engine_version):
    """`got` with each one's check against `engine_version` (the engine this round ends with: it may run the new
    version's own test)."""
    for o in got:
        if o["tag"] and o["check"] is None:
            try:
                o["check"] = plugin_update.check(o["p"], o["tag"], engine_version)
            except Exception as e:
                o["check"] = {"kind": "broken", "kinds": ["broken"], "why": "檢查出錯：%s: %s" % (type(e).__name__, e),
                              "added": []}
    return got


def card_id_of(tag, got):
    """update-<tag> while no plugin moves (the card U2 posts), else update-<tag>-<hash of what all would move>: the
    same set, the same card (not posted again); another set, a new card (the older closed)."""
    moving = sorted("%s@%s" % (o["p"]["name"], o["tag"]) for o in got if o["tag"])
    if not moving:
        return "update-%s" % tag
    return "update-%s-%s" % (tag, hashlib.sha1(("%s %s" % (tag, " ".join(moving))).encode()).hexdigest()[:8])


def plugin_lines(got):
    """The card's lines for the plugins: each one's move, why it does not go with 更新 when so, its points."""
    out = []
    for o in got:
        p = o["p"]
        if not o["tag"]:
            out.append("插件 %s：這次查不到有沒有新版（連不上它的來源），下一輪再看。" % p["name"])
            continue
        kind = (o["check"] or {}).get("kind", "clean")
        out.append("插件 %s：%s → %s%s" % (p["name"], p["version"], o["tag"], "" if kind in PLUGIN_GO else
                                       "（不會跟著「更新」一起換：%s）" % PLUGIN_PLAIN.get(kind, kind)))
        try:
            notes = plugin_update.notes(p, o["tags"], update.notes_language())
        except Exception:
            notes = []
        many = len(notes) > 1
        for t, points, _cut in notes[:3]:
            out += ["・%s%s" % ("%s：" % t if many else "", point) for point in points]
        if len(notes) > 3:
            out.append("・（更早的 %d 版沒列出，完整的在它的 CHANGELOG.md）" % (len(notes) - 3))
    return out


def plugin_technical(got):
    out = []
    for o in got:
        c = o["check"] or {}
        out.append("插件 %s：%s → %s；檢查：%s%s" % (o["p"]["name"], o["p"]["version"], o["tag"] or "（查不到）",
                                             "、".join(c.get("kinds") or [c.get("kind") or "沒做"]),
                                             "；%s" % c["why"] if c.get("why") else ""))
        out += ["  多了：%s" % a for a in c.get("added") or []]
    return "\n".join(out)


def plugin_ask(got):
    """For #setup's Claude: the plugins that wait for a person, each with what can be done."""
    head = ("插件的更新要使用者看過才換，請照 update skill 的「插件」一節帶使用者處理：先用白話說新版會多做什麼、"
            "會不會丟東西，問過才動手；只用 altention update 的子指令，不自己動插件的資料夾。")
    body = []
    for o in got:
        c = o["check"]
        body.append("插件 %s：%s → %s，檢查是 %s：%s" % (o["p"]["name"], o["p"]["version"], o["tag"], c["kind"], c["why"]))
        body += ["  多了：%s" % a for a in c.get("added") or []]
        body.append("  可以做的：" + PLUGIN_NEXT.get(c["kind"], PLUGIN_NEXT["broken"]) % {"n": o["p"]["name"], "v": o["tag"]})
    return head + "\n\n" + "\n".join(body)


def push_instance():
    """The instance's new commit pushed now (under its own lock), so the other machines can follow it: "" when
    pushed or nothing to push, else why (the sync pushes it within its next round)."""
    inst = config.instance_dir()
    if not inst:
        return "這台沒有實例"
    repo = sync.Repo(inst)
    try:
        with sync.locked(repo):
            ok, why = sync.push(repo)
    except sync.Busy as e:
        return str(e)
    return "" if ok else why or "push 失敗"


def moves_path():
    return os.path.join(state_dir(), "plugin-moves.json")


def plugin_moves():
    """[{at, mark, from, to}], oldest first: the plugin moves an update made here (mark: the engine's before-mark of
    the same go, None when only plugins moved), for undo."""
    try:
        with open(moves_path()) as f:
            got = json.load(f)
        return [m for m in got if isinstance(m, dict) and isinstance(m.get("from"), dict)] if isinstance(got, list) else []
    except (OSError, ValueError):
        return []


def write_moves(moves):
    os.makedirs(state_dir(), exist_ok=True)
    with open(moves_path() + ".tmp", "w") as f:
        json.dump(moves[-MOVES_KEPT:], f, ensure_ascii=False)
    os.replace(moves_path() + ".tmp", moves_path())


def note_move(mark, was, now):
    try:
        write_moves(plugin_moves() + [{"at": time.time(), "mark": mark, "from": was, "to": now}])
    except OSError:  # moved all the same: undo then says it has nothing of theirs to go back from
        pass


def move_plugins(todo, mark=None, deadline=None):
    """Each of `todo` (offers with a clean check) moved to its tag here, in the order their requires give, one whose
    required one did not move left as it is; then the instance's versions set once and pushed (the other machines
    follow it). The instance not written: every one moved goes back, or the next round's follow would. (lines,
    moved {name: (was, tag)}): a line per plugin for the card."""
    by = dict((o["p"]["name"], o) for o in todo)
    names = plugin_update.order(list(by))
    lines, moved, failed = [], {}, []
    for n in names:
        if n in plugin_update.blocked_by(failed, names):
            failed.append(n)
            lines.append("❌ 插件 %s：沒換，它要用的插件這次沒換成。" % n)
            continue
        o = by[n]
        try:
            ok, why = plugin_update.switch(o["p"], o["tag"], deadline=deadline)
        except Exception as e:
            ok, why = False, "%s: %s" % (type(e).__name__, e)
        if ok:
            moved[n] = (o["p"]["version"], o["tag"])
            lines.append("✅ 插件 %s：%s → %s%s" % (n, o["p"]["version"], o["tag"], "（%s）" % why if why else ""))
        else:
            failed.append(n)
            lines.append("❌ 插件 %s：沒換成，還是 %s（%s）" % (n, o["p"]["version"], why))
    if not moved:
        return lines, moved
    ok, why = plugin_update.set_versions(dict((n, t) for n, (_, t) in moved.items()))
    if not ok:
        for n, (was, tag) in moved.items():
            plugin_update.switch(dict(by[n]["p"], version=tag), was)
        lines = [l for l in lines if not l.startswith("✅")] + [
            "❌ 插件 %s：放回 %s 了，實例記不下新版本（%s）。" % (n, was, why) for n, (was, _) in sorted(moved.items())]
        return lines, {}
    note_move(mark, dict((n, w) for n, (w, _) in moved.items()), dict((n, t) for n, (_, t) in moved.items()))
    unpushed = push_instance()
    if unpushed:
        lines.append("（實例還沒推出去：%s；別台等同步推出去之後才跟上。）" % unpushed)
    return lines, moved


def plugins_step(version, d):
    """go's 換插件 step (the bot's machine: the others follow the instance, lead_others). (code, lines): None when no
    plugin has anything newer."""
    if not bot_machine_here():
        return None, []
    got = checked(offers(fetch=True, deadline=time.time() + FOLLOW_LIMIT), version)
    if not got:
        return None, []
    todo = [o for o in got if o["tag"] and o["check"]["kind"] in PLUGIN_GO]
    lines = []
    for o in got:
        if o not in todo:
            lines.append("— 插件 %s：%s" % (o["p"]["name"], "這次查不到有沒有新版" if not o["tag"] else "%s 這次沒換：%s，按「請 Claude 幫忙」"
                                         % (o["tag"], PLUGIN_PLAIN.get(o["check"]["kind"], o["check"]["kind"]))))
    mark = update.BEFORE % version if not d.get("only_plugins") and (update.BEFORE % version) in update.befores() else None
    moved_lines, _ = move_plugins(todo, mark, time.time() + FOLLOW_LIMIT) if todo else ([], {})
    d["waiting"] = [o for o in got if o not in todo and o["tag"]]
    return 0, moved_lines + lines


def follow_here():
    with go_lock() as held:
        if not held:
            print("❌ " + BUSY)
            return 1
        return _follow_here()


def _follow_here():
    """altention update follow: this machine's instance pulled (as the sync does), then each plugin it should have
    taken to the instance's version (plugin_update.follow). lead_others runs it on each other machine after its
    engine; every update round does the same (plugins_round)."""
    inst = config.instance_dir()
    if inst:
        repo = sync.Repo(inst)
        try:
            with sync.locked(repo):
                code, note, held = sync.update_instance(repo)
            if code or held:
                print("實例這次沒拉到最新的（%s）：照這台現有的設定換。" % (held or note))
        except sync.Busy as e:
            print("實例這次沒拉到最新的（%s）：照這台現有的設定換。" % e)
    got = plugin_update.follow(deadline=time.time() + FOLLOW_LIMIT)
    for n, ok, why in got:
        print(("✅ 插件 %s：%s" if ok else "❌ 插件 %s：%s") % (n, why or "換好了"))
    if not got:
        print("插件都已經是實例寫的版本。")
    return 0 if all(ok for _, ok, _ in got) else 1


def plugins_round(deadline, tell=None):
    """update.py's every round, after the engine (its git lock let go): this machine's plugins follow the instance;
    on the bot's machine, plugins that update by themselves ("release") and can go move, a notice in #update says
    so, and the others with a newer release get the 🆕 card (posted only when what it offers changed). `tell`: the
    engine release update.py's notify left for this card (one card a round, manager 2026-10-20); told, a line
    update.TOLD + tag says so (else update.py tells it the old way). Not while a go runs (it moves plugins itself).
    [words] for the round's line."""
    os.makedirs(state_dir(), exist_ok=True)
    lock = open(os.path.join(state_dir(), "update-go.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        return ["插件：有一次更新正在跑，這一輪不動"]
    said = []
    try:
        for n, ok, why in plugin_update.follow(deadline=deadline, backoff=True):  # a round: a silent source waits
            said.append("插件 %s %s" % (n, "跟上實例的版本" if ok else "沒跟上：%s" % why))
        said += catalog_look(deadline)
        if not bot_machine_here():
            if tell:
                with redirect_out():
                    code = new_card(tell)
                if code == NOT_THE_BOT_MACHINE:  # the bot's machine tells it (U5)
                    said.append(update.TOLD + tell)
            return said
        said += catalog_news()
        now = update.release_in(update.Round())[0]
        got = offers(fetch=True, deadline=deadline, backoff=True)
        auto = checked([o for o in got if o["tag"] and o["p"]["update"] == "release"], now)
        go_now = [o for o in auto if o["check"]["kind"] in PLUGIN_GO]
        if go_now:
            lines, moved = move_plugins(go_now, None, deadline)
            said += [l.lstrip("✅❌ ") for l in lines]
            if moved:
                update.send("✅ 插件已更新：%s。\n\n想回到更新前的版本：到 #setup 跟 Claude 說「引擎回到更新前」。"
                            % "、".join("%s %s → %s" % (n, w, t) for n, (w, t) in sorted(moved.items())))
        if tell or any(o not in go_now and o["tag"] for o in got):
            tag = tell or available_tag() or now
            if tag:
                with redirect_out() as out:
                    code = new_card(tag, if_new=not tell)
                if tell and code == 0:
                    said.append(update.TOLD + tell)
                elif not tell:
                    said.append("插件有新版：" + " ".join(out.getvalue().split())[:160])
        return said
    finally:
        lock.close()


CATALOG_LIMIT = 90  # seconds of a round the official plugin list may take (it looks once a day; mostly nothing)


def catalog_look(deadline):
    """The official plugin list looked at when due (catalog.refresh, C-81), on every machine: [words] for the
    round's line when it looked (an unchanged list says nothing). Never raises."""
    try:
        state, words = catalog.refresh(deadline=min(deadline, time.time() + CATALOG_LIMIT))
    except Exception as e:  # the list is a convenience: the round goes on
        return ["官方插件清單這一輪出錯（%s: %s）" % (type(e).__name__, str(e)[:120])]
    return [words] if words and state != "same" else []


def catalog_news():
    """On the bot's machine: the official list's plugins not told yet, one line in #update (catalog.news); [words]
    for the round's line. Never raises."""
    try:
        named = set((config.settings().get("plugins") or {}).keys())
        new = catalog.news(named)
        if not new:
            return []
        if update.send(catalog.news_text(new)):
            catalog.mark_told(n for n, _ in new)
            return ["官方插件清單多了：%s（已在 #update 說）" % "、".join(n for n, _ in new)]
        return ["官方插件清單多了：%s（#update 沒送出去，下一輪再說）" % "、".join(n for n, _ in new)]
    except Exception as e:
        return ["官方插件清單的新插件這一輪沒說（%s: %s）" % (type(e).__name__, str(e)[:120])]


def available_tag():
    """The engine's release update.py saw and did not take (notify: update-available), else None."""
    try:
        with open(os.path.join(state_dir(), "update-available")) as f:
            tag = json.load(f).get("tag")
        return tag if isinstance(tag, str) and update.RELEASE_TAG.match(tag) else None
    except (OSError, ValueError, AttributeError):
        return None


class redirect_out(object):
    """with redirect_out() as out: what is printed inside kept in `out` (a StringIO), not on stdout."""

    def __enter__(self):
        self.was, self.out = sys.stdout, io.StringIO()
        sys.stdout = self.out
        return self.out

    def __exit__(self, *exc):
        sys.stdout = self.was
        return False


def one_plugin(name, version, anyway=False):
    with go_lock() as held:
        if not held:
            print(BUSY)
            return 1
        return _one_plugin(name, version, anyway)


def _one_plugin(name, version, anyway=False):
    """altention update plugin <name> <vX>: one plugin moved, once the person saw what it brings (Claude asks first:
    perms is theirs to say, decision 4; a failed test only with --anyway, manager 2026-10-20). The bot's machine
    only (it writes the instance's version); the others follow it."""
    if not bot_machine_here():
        print("這要在機器人那台跑（實例的版本由那台改，別台會跟著換）；這台什麼都沒動。")
        return 2
    p = next((x for x in plugin_update.enabled() if x["name"] == name), None)
    if p is None:
        print("實例沒有用 source 寫的插件 %s；什麼都沒動。" % name)
        return 1
    if p["version"] == version:
        print("插件 %s 已經是 %s 了，不用換。" % (name, version))
        return 0
    plugin_update.newer(p, fetch=True, deadline=time.time() + FOLLOW_LIMIT)
    c = plugin_update.check(p, version, update.release_in(update.Round())[0])
    if c["kind"] in PLUGIN_STOP:
        print("插件 %s 這次沒換到 %s：%s。你的東西都沒動。（細節：%s）" % (name, version, PLUGIN_PLAIN[c["kind"]], c["why"]))
        return 1
    if "test" in c["kinds"] and not anyway:
        print("插件 %s 的 %s 自己的測試沒過，這次不換；你的東西都沒動。\n%s\n知道了還是要換：altention update plugin %s %s --anyway"
              % (name, version, c["why"], name, version))
        return 1
    ok, said = plugin_update.switch(p, version, deadline=time.time() + FOLLOW_LIMIT)
    if not ok:
        print("插件 %s 沒換到 %s：%s" % (name, version, said))
        return 1
    ok, why = plugin_update.set_versions({name: version})
    if not ok:
        plugin_update.switch(dict(p, version=version), p["version"])
        print("插件 %s 放回 %s 了：實例記不下新版本（%s）。" % (name, p["version"], why))
        return 1
    note_move(None, {name: p["version"]}, {name: version})
    unpushed = push_instance()
    print("插件 %s 已經換到 %s%s。%s想回到換之前：到 #setup 跟 Claude 說「引擎回到更新前」。" % (
        name, version, "（%s）" % said if said else "",
        "別台會在下一輪自己跟上。" if not unpushed else "實例還沒推出去（%s），別台等同步推出去之後才跟上。" % unpushed))
    return 0


def undo_plugins(move):
    """The plugins of one recorded move taken back to where they were (switch, then the instance's versions);
    words for the person."""
    by = dict((p["name"], p) for p in plugin_update.enabled())
    back, kept = {}, []
    for n, was in sorted(move["from"].items()):
        p = by.get(n)
        if p is None or p["version"] != move["to"].get(n):
            kept.append("%s（實例寫的已經不是 %s 了）" % (n, move["to"].get(n)))
            continue
        ok, why = plugin_update.switch(p, was)
        if ok:
            back[n] = was
        else:
            kept.append("%s（%s）" % (n, why))
    if back:
        ok, why = plugin_update.set_versions(back)
        if not ok:
            for n, was in back.items():
                plugin_update.switch(dict(by[n], version=was), move["to"][n])
            return "插件沒退回：實例記不下（%s），插件都還是新版。" % why
        push_instance()
    write_moves([m for m in plugin_moves() if m != move])
    return "%s%s" % ("插件退回了：%s。" % "、".join("%s %s" % kv for kv in sorted(back.items())) if back else "",
                     "沒退回的插件：%s。" % "、".join(kept) if kept else "")


def aside(version):
    """altention update aside <v>: the files not tracked here that have the name of one `version` adds (the check's
    "untracked", nothing else) renamed to <name>.mine beside them, <name>.mine.2 and on when that is taken (never
    over anything, nor onto a name the release adds), so go can put the release's in (C-66, manager 2026-10-20;
    asked first: Claude does). Their contents are not touched."""
    repo = sync.Repo(engine())
    with git_lock(repo) as lock:
        if lock.fd is None:
            print("引擎現在正在做別的事，等一下再試；你的檔都沒動。")
            return 1
        repo.lock_fd = lock.fd
        head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
        up = repo.git("rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=20).stdout.strip()
        if not up:  # not fetched here yet (the 🆕 came from another round): fetched now, nothing else
            repo.git("fetch", "-q", "--tags")
            up = repo.git("rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=20).stdout.strip()
        if not head or not up:
            print("讀不到 %s（或這台的版本），什麼都沒動。先跑 altention update check。" % version)
            return 1
        loose = in_the_way(repo, head, up)
        if not loose:
            print("沒有擋住 %s 的同名檔，不用做。" % version)
            return 0
        added = set(lines(repo.git("ls-tree", "-r", "--name-only", up, timeout=60)))
        eng, moved = engine(), []
        for rel in loose:
            src = os.path.join(eng, rel)
            n, new = 1, rel + ".mine"
            while os.path.lexists(os.path.join(eng, new)) or new in added:
                n += 1
                new = "%s.mine.%d" % (rel, n)
            try:
                os.rename(src, os.path.join(eng, new))  # a name nothing has (looked at just above, under the lock)
            except OSError as e:
                print("%s 改不了名（%s）；%s" % (rel, e, ("已經改好的：%s。" % "、".join("%s → %s" % m for m in moved)) if moved
                                                 else "什麼都沒動。"))
                return 1
            moved.append((rel, new))
    print("已經把擋住新版的檔改名了，內容都沒動：%s。接著更新：altention update go %s"
          % ("、".join("%s → %s" % m for m in moved), version))
    return 0


def save(version):
    """altention update save <v>: the edits not committed kept as one commit, "local edits before <v>" (owner
    2026-10-19, decision 4: asked once first, by Claude), so go can take them on top of the release. Only files
    git tracks; nothing else touched."""
    repo = sync.Repo(engine())
    with git_lock(repo) as lock:
        if lock.fd is None:
            print("引擎現在正在做別的事，等一下再試；你的修改都沒動。")
            return 1
        repo.lock_fd = lock.fd
        if repo.git("symbolic-ref", "-q", "HEAD", timeout=20).returncode != 0:
            print("這台的引擎停在一個固定的版本上，這樣保留不了；你的修改都沒動。（細節：detached HEAD）")
            return 1
        edits = repo.git("status", "--porcelain", "--untracked-files=no", timeout=60)
        files = [l[3:].split(" -> ")[-1] for l in edits.stdout.splitlines() if l.strip()]
        if edits.returncode != 0:
            print("看不出這台改過哪些檔，沒有動。（細節：git status：%s）" % edits.stderr.strip()[:200])
            return 1
        if not files:
            print("這台沒有你自己改過、還沒保留的檔，不用做。")
            return 0
        r = repo.git(*(sync.ident_args(repo) + ["commit", "-q", "-a", "-m", "local edits before %s" % version]), timeout=120)
        if r.returncode != 0:
            print("保留不了你的修改，什麼都沒動，你的修改都還在。（細節：git commit：%s）"
                  % (r.stderr.strip() or r.stdout.strip())[:300])
            return 1
    print("已經把你改過的檔%s保留下來了，更新後你的修改還在。接著更新：altention update go %s" % (named(files), version))
    return 0


def undo():
    with go_lock() as held:
        if not held:
            print(BUSY)
            return 1
        return _undo()


def _undo():
    """altention update undo: back to the newest altention/before-vX mark (made just before a move to a release),
    only from where that move went (anything since would be lost: refused, said) and only when the engine does not
    update by itself (release or branch would take it again next round: said what to do first). reset --keep: edits
    not committed stay, or nothing moves. Then as after any move: the bot's restart flag or its packages, install.sh.
    Plugins (C-61 E3): the plugins that go moved with that mark go back too; when the last update moved plugins only
    (after the newest mark), only those go back."""
    moves = plugin_moves()
    last = moves[-1] if moves else None
    if last and last.get("mark") is None and last.get("at", 0) > max(
            [r.get("at") or 0 for r in update.befores().values() if isinstance(r, dict)] or [0]):
        by = dict((p["name"], p) for p in plugin_update.enabled())
        auto = sorted(n for n in last["from"] if (by.get(n) or {}).get("update") == "release")
        if auto:
            print("插件 %s 設成有新版就自己更新，回去之後下一輪又會換上新版。要停在更新前：先跟 Claude 說「插件 %s 改成只通知」，再回去。"
                  % ("、".join(auto), auto[0]))
            return 1
        print(undo_plugins(last))
        return 0
    mode = update.update_mode()[0]
    if mode in ("release", "branch"):
        print("這台設成有新版就自己更新，回去之後下一輪又會換上新版。要停在更新前：先跟 Claude 說「引擎改成只通知」，再回去。")
        return 1
    repo = sync.Repo(engine())
    with git_lock(repo) as lock:
        if lock.fd is None:
            print("引擎現在正在做別的事，等一下再試；你的東西都沒動。")
            return 1
        repo.lock_fd = lock.fd
        rnd = update.Round()
        rnd.lock = lock.f
        marks, there = update.befores(), set(repo.git("tag", "-l", update.BEFORE % "v*", timeout=20).stdout.split())
        known = sorted((update.vtuple(n[len(update.BEFORE % ""):]), n) for n in marks if n in there
                       and update.RELEASE_TAG.match(n[len(update.BEFORE % ""):]))
        if not known:
            print("沒有更新前的記號可以回去（換版時才會留，最多留 %d 個）；你的東西都沒動。" % update.BEFORE_KEPT)
            return 1
        name = known[-1][1]
        rec = marks[name]
        head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
        if repo.git("symbolic-ref", "-q", "HEAD", timeout=20).returncode != 0:
            print("這台的引擎停在一個固定的版本上，這樣回不去；你的東西都沒動。（細節：detached HEAD）")
            return 1
        if head != rec.get("after"):
            print("更新之後這台的引擎又有變動，直接回去會丟掉那些變動，這次先不回去，你的東西都沒動。"
                  "請 Claude 帶你處理。（細節：%s 之後是 %s，現在是 %s）" % (name, str(rec.get("after"))[:12], head[:12]))
            return 1
        repo.git("update-index", "-q", "--refresh", timeout=60)  # a file rewritten as it was is no edit (stat only)
        r = repo.git("reset", "-q", "--keep", name, timeout=120)
        if r.returncode != 0:
            print("你自己改過的檔，跟要回去的版本改到同一個檔，這次先不回去，你的修改都還在。（細節：%s）"
                  % (r.stderr.strip() or r.stdout.strip())[:300])
            return 1
        back = update.release_in(rnd)[0]
        update.drop_before(rnd, name)
        restart = update.after_move(rnd, head, rec["before"])
    update.install_pending()
    code, out = run([os.path.join(engine(), "install.sh")], "install", holder_env())
    print("已經回到更新前的版本%s，你自己改過的檔都還在。%s%s%s" % (
        "（%s）" % back if back else "", "機器人會自己重啟換回這個版本。" if restart else "",
        "" if code in (0, 2) else "但安裝這一步沒做完：%s；跟 Claude 說「跑 ./install.sh」。" % last_line(out),
        undo_plugins(last) if last and last.get("mark") == name else ""))
    return 0


def copy_path(version):
    return os.path.join(work_dir(), "engine-%s" % version)


def copy_record_path():
    return os.path.join(state_dir(), "update-copy.json")


def in_copy(path, *args, timeout=300, env=None):
    try:
        return subprocess.run(["git", "-C", path] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout, stdin=subprocess.DEVNULL,
                              env=dict(os.environ, GIT_EDITOR="true", **(env or {})))
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "git %s：%d 秒沒有回應" % (args[0], timeout))


def remove_copy(repo, path, version):
    """The copy of `version` removed, and its record. Only the folder copy_path(version) is ever removed: a record
    that names another (edited by hand, broken) has nothing deleted (#code-review of ea426b3). True when removed."""
    if os.path.realpath(path) != os.path.realpath(copy_path(version)):
        print("合併副本的紀錄指到別的資料夾（%s），不是 %s：那個資料夾不動，也不刪。" % (path, copy_path(version)))
        return False
    if os.path.isdir(path):
        in_copy(path, "rebase", "--abort")
        repo.git("worktree", "remove", "--force", path, timeout=120)
        shutil.rmtree(path, ignore_errors=True)
    repo.git("worktree", "prune", timeout=60)
    try:
        os.remove(copy_record_path())
    except OSError:
        pass
    return True


def stuck_in(path):
    return lines(in_copy(path, "diff", "--name-only", "--diff-filter=U"))


def make_copy(version):
    """A copy of the engine under #setup's folder (U4; M0: #setup's Claude edits there without asking), this
    machine's commits being put on top of `version` and stopped where they meet it, for Claude to merge and
    `apply` to take. (path, the files to merge), or (None, why). Never touches the engine's own tree or branch."""
    repo = sync.Repo(engine())
    with git_lock(repo) as lock:
        if lock.fd is None:
            return None, "git 鎖一直被別人拿著"
        repo.lock_fd = lock.fd
        path = copy_path(version)
        remove_copy(repo, path, version)
        os.makedirs(work_dir(), exist_ok=True)
        head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
        up = repo.git("rev-parse", "-q", "--verify", "%s^{commit}" % version, timeout=20).stdout.strip()
        r = repo.git("worktree", "add", "-q", "--detach", path, head, timeout=120)
        if r.returncode != 0 or not up:
            return None, "做不了副本：%s" % (r.stderr.strip() or "讀不到 %s" % version)[:200]
        in_copy(path, *(sync.ident_args(repo) + ["rebase", "-q", up]))
        with open(copy_record_path() + ".tmp", "w") as f:
            json.dump({"version": version, "path": path, "head": head, "up": up, "at": time.time()}, f)
        os.replace(copy_record_path() + ".tmp", copy_record_path())
        return path, stuck_in(path)


def rebasing(path):
    p = in_copy(path, "rev-parse", "--git-path", "rebase-merge").stdout.strip()
    return bool(p) and os.path.isdir(p if os.path.isabs(p) else os.path.join(path, p))


MARKERS = re.compile(r"^(<{7}|>{7})( |$)", re.M)


def apply(version):
    """altention update apply <v>: the copy Claude merged (make_copy), once the person agreed, taken: what is
    merged goes on, a commit whose part is not merged yet says the next files; when all of it is on `version`,
    checked (syntax, load) as update.py checks, marked (before-vX) and taken with reset --keep, the copy removed,
    then the rest of go (install, reconcile, doctor; on the card when on the bot's machine). Under go's own lock:
    a button pressed meanwhile waits its turn."""
    os.makedirs(state_dir(), exist_ok=True)
    lock = open(os.path.join(state_dir(), "update-go.lock"), "a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        lock.close()
        print("已經有一次更新正在跑，等它做完再換上；什麼都還沒換。")
        return 1
    try:
        return applied(version)
    finally:
        lock.close()


def applied(version):
    try:
        with open(copy_record_path()) as f:
            rec = json.load(f)
    except (OSError, ValueError):
        rec = {}
    path = rec.get("path")
    if rec.get("version") != version or not path or not os.path.isdir(path):
        print("沒有 %s 的合併副本：先跑 altention update go %s（撞到你改過的地方時會做一份）。你的東西都沒動。" % (version, version))
        return 1
    if os.path.realpath(path) != os.path.realpath(copy_path(version)):  # a record edited or broken: nothing taken from there
        print("合併副本的紀錄指到別的資料夾（%s），不是 %s：沒有換，那個資料夾也不動。重新跑 altention update go %s 再做一份。"
              % (path, copy_path(version), version))
        return 1
    repo = sync.Repo(engine())
    with git_lock(repo) as lock:
        if lock.fd is None:
            print("引擎現在正在做別的事，等一下再試；你的東西都沒動。")
            return 1
        repo.lock_fd = lock.fd
        rnd = update.Round()
        rnd.lock = lock.f
        head = repo.git("rev-parse", "HEAD", timeout=20).stdout.strip()
        if head != rec.get("head"):
            remove_copy(repo, path, version)
            print("副本做好之後，這台的引擎又有變動，這份副本不能用了（已經刪掉）；你的東西都沒動。"
                  "重新跑 altention update go %s 再做一份。" % version)
            return 1
        while rebasing(path):
            left = []
            for name in stuck_in(path):
                try:
                    with open(os.path.join(path, name), errors="replace") as f:
                        if MARKERS.search(f.read()):
                            left.append(name)
                except OSError:
                    pass  # deleted on purpose: add -A takes that
            if left:
                print("這些檔還沒合好（還有 <<<<<<< 或 >>>>>>> 標記）：%s。合好再跑一次；什麼都還沒換。" % "、".join(left))
                return 1
            in_copy(path, "add", "-A")
            r = in_copy(path, *(sync.ident_args(repo) + ["rebase", "--continue"]))
            if r.returncode != 0 and rebasing(path) and not stuck_in(path) \
                    and in_copy(path, "diff", "--cached", "--quiet").returncode == 0:
                r = in_copy(path, "rebase", "--skip")  # merged to what the new version has already: nothing left of it
            if rebasing(path) and stuck_in(path):
                print("這一段合好了；下一段還有要合的檔：%s。合好再跑一次；什麼都還沒換。" % "、".join(stuck_in(path)))
                return 1
            if r.returncode != 0 and rebasing(path):
                print("副本裡的合併接不下去，什麼都還沒換。（細節：%s）" % (r.stderr.strip() or r.stdout.strip())[:300])
                return 1
        if lines(in_copy(path, "status", "--porcelain")):  # changed after its commits were made: a fix after a
            # failed check (#code-review-2 of ea426b3; manager: amend), taken into the last one, then checked
            in_copy(path, "add", "-A")
            on_release = in_copy(path, "rev-parse", "HEAD").stdout.strip() == rec.get("up")  # all of it merged down to
            # the release (skipped): the release's own commit is never amended, the fix goes on top (#code-review of 1cb4b97)
            r = in_copy(path, *(sync.ident_args(repo) + (["commit", "-q", "-m", "local edits merged into %s" % version]
                                                         if on_release else ["commit", "-q", "--amend", "--no-edit"])))
            if r.returncode != 0:
                print("副本裡改好的地方收不進去，什麼都還沒換。（細節：%s）" % (r.stderr.strip() or r.stdout.strip())[:300])
                return 1
        new = in_copy(path, "rev-parse", "HEAD").stdout.strip()
        up = rec.get("up")
        if not new or in_copy(path, "merge-base", "--is-ancestor", up, new).returncode != 0:
            print("副本不在 %s 上面，沒有換；你的東西都沒動。" % version)
            return 1
        changed = lines(repo.git("diff", "--name-only", "--diff-filter=d", head, new, timeout=60))
        why = update.check_tree(rnd, new, sorted(set(changed) | set(update.ALWAYS)))
        if why:
            print("合好的版本沒通過檢查，沒有換，你的東西都沒動；副本留著：在副本裡改好再跑一次 apply。（細節：%s）" % why)
            return 1
        start = update.release_in(rnd)[0]
        before = update.mark_before(rnd, head, version)
        repo.git("update-index", "-q", "--refresh", timeout=60)  # a file rewritten as it was is no edit (stat only)
        r = repo.git("reset", "-q", "--keep", new, timeout=120)
        if r.returncode != 0:
            update.drop_before(rnd, before)
            print("換不上：你自己改過、還沒保留的檔跟合好的版本改到同一個檔，這次先不換，你的修改都還在。（細節：%s）"
                  % (r.stderr.strip() or r.stdout.strip())[:300])
            return 1
        update.note_after(before, head, new)
        update.after_move(rnd, head, new)
        remove_copy(repo, path, version)
    update.install_pending()
    card_id = "update-%s" % version if bot_machine_here() else None
    write_progress({"card": card_id, "asked": version, "tag": version, "start": start, "done": ["update"], "notes": [],
                    "first": "✓ 你的修改和新版合好了，已經換上", "newer": "", "at": time.time()})
    return go_on(Say(card_id), version, card_id)


def main(argv):
    if argv[:1] == ["check"] and set(argv[1:]) <= {"--json"}:
        f = check()
        if "--json" in argv:
            print(json.dumps(f, ensure_ascii=False, indent=1))
        else:
            print(PLAIN.get(f["kind"], PLAIN["unreadable"]))
            print(technical(f))
        return 0 if f["kind"] in GO_KINDS + ("none",) else 1
    if argv[:1] == ["card"] and len(argv) == 2 and update.RELEASE_TAG.match(argv[1]):
        return new_card(argv[1])
    if argv[:1] == ["aside"] and len(argv) == 2 and update.RELEASE_TAG.match(argv[1]):
        return aside(argv[1])
    if argv[:1] == ["save"] and len(argv) == 2 and update.RELEASE_TAG.match(argv[1]):
        return save(argv[1])
    if argv == ["undo"]:
        return undo()
    if argv[:1] == ["plugin"] and len(argv) in (3, 4) and update.RELEASE_TAG.match(argv[2]) and argv[3:] in ([], ["--anyway"]):
        return one_plugin(argv[1], argv[2], anyway=argv[3:] == ["--anyway"])
    if argv == ["follow"]:
        return follow_here()
    if argv[:1] == ["plugins-round"] and len(argv) in (2, 4) and argv[1].isdigit() and (
            len(argv) == 2 or (argv[2] == "--tell" and update.RELEASE_TAG.match(argv[3]))):
        for line in plugins_round(int(argv[1]), argv[3] if len(argv) == 4 else None):
            print(line)
        return 0
    if argv[:1] == ["apply"] and len(argv) == 2 and update.RELEASE_TAG.match(argv[1]):
        return apply(argv[1])
    if argv[:1] == ["go"] and len(argv) >= 2 and update.RELEASE_TAG.match(argv[1]):
        rest, card_id, after = argv[2:], None, False
        while rest:
            if rest[0] == "--card" and len(rest) >= 2 and cards.ID.match(rest[1]):
                card_id, rest = rest[1], rest[2:]
            elif rest[0] == "--after-move":
                after, rest = True, rest[1:]
            else:
                break
        if not rest:
            if card_id and not bot_machine_here():
                print("這台不是機器人那台：--card 只在機器人那台用（同一張卡只由一台寫）。不帶 --card 就只印在這裡。")
                return 2
            return go(argv[1], card_id, after)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
