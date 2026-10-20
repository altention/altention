#!/usr/bin/env python3
"""altention update: updating the engine from #update's card (C-57 U2), or from a terminal.

  altention update check [--json]         look first: can this machine take the newest release as it is (nothing moved)
  altention update go <vX.Y.Z> [--card <id>]
                                          the whole way: check, update.py --release, ./install.sh, setup.py reconcile,
                                          doctor; each step on the card (--card: the bot's machine only, the bot passes
                                          it); without --card it is said on this terminal only
  altention update save <vX.Y.Z>          keep the edits not committed as one commit ("local edits before vX.Y.Z"), so
                                          go can take them on top of the release (asked first: Claude does)
  altention update apply <vX.Y.Z>         take the copy Claude merged (go makes it under #setup's folder when your
                                          changes meet the release's), after the person agreed
  altention update undo                   back to before the last update (altention/before-vX, update.py marks it)
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
Text handed to Claude: in the button itself up to ASK_INLINE characters; longer, in a file under #setup's folder
(setup/update-work/: #setup's Claude reads there without asking, M0 2026-10-19), the button saying to read it.
"""
import fcntl
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

from altention import config, sync, update  # noqa: E402
from altention.dc import card as cards  # noqa: E402

GO_KINDS = ("clean", "commits")  # what go takes on by itself
ASK_INLINE = 3000                # characters handed to Claude in the button itself (the bot refuses over 6000)
NOT_THE_BOT_MACHINE = update.NOT_THE_BOT_MACHINE
LOCK_WAIT = 120                  # seconds the check waits for the git lock (a sync round holds it a while)
STEPS = (("update", "換到新版"), ("install", "安裝新版需要的東西"), ("reconcile", "補齊頻道和設定"), ("doctor", "系統檢查"))
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
        added = set(lines(repo.git("diff", "--name-only", "--diff-filter=A", head, up, timeout=60)))
        loose = [f for f in lines(repo.git("ls-files", "--others", "--exclude-standard", timeout=60)) if f in added]
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
    "untracked": "問使用者那個檔要改名、移走還是不要了；處理好再 `altention update go %(v)s`。",
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


def board(version, done, now=None, first="✓ 先在旁邊試過了：可以直接換"):
    """The ⏳ card: the steps done (✓), the one going (…), the rest."""
    out = ["⏳ 正在更新到 %s…（進度會寫在這張卡上；中途機器人可能會重啟一下）" % version, first]
    for key, words in STEPS:
        out.append(("✓ " if key in done else "… " if key == now else "　 ") + words)
    return "\n".join(out)


def run(argv, key, env=None):
    """One step's command in the engine folder, in a process group of its own: (exit status, what it printed).
    Taking longer than TIMEOUTS[key], the whole group is killed (its git clone or pip too, as setup.py's script())."""
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
        p.communicate()
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
        say("✅ 這台已經是 %s 了，不用再更新。" % f["now"])
        return 0
    if f["kind"] in ("busy", "offline", "archive", "none"):
        say("❌ 這次沒有更新：%s 你的東西都沒動。" % PLAIN[f["kind"]], details=technical(f))
        return 1
    if f["kind"] not in GO_KINDS:
        if f["kind"] == "conflict":  # the copy Claude merges in, under #setup's folder (U4)
            made = make_copy(tag)
            f["copy"], f["files"] = made if made[0] else (None, f["files"])
        say("📝 還沒更新到 %s：%s" % (tag, explain(f)), help_buttons(card_id, f, tag), technical(f))
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
        say(board(version, d["done"], key, first))
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
    say(text)
    try:
        os.remove(progress_path())
    except OSError:
        pass
    if bot_machine_here():  # this machine done: the others, one after another, each a line on this card (U5)
        lead_others(say, text, version)
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


def lead_others(say, text, version):
    """altention update go <version> on each other machine (ssh, as ops/apply.py reaches them), after this one
    went through: each its own check, update, install and doctor; one stopping (its edits, a refused release,
    not reached) does not stop the rest. Their state as lines under this card's ✅."""
    rows = others()
    if not rows:
        return
    state = dict((n, ("waiting", "等著")) for n, _ in rows)

    def board():
        return text + "\n\n其他台：\n" + "\n".join("%s %s：%s" % (OTHER[state[n][0]], n, state[n][1]) for n, _ in rows)
    for n, m in rows:
        state[n] = ("going", "更新中…")
        say(board())
        state[n] = go_there(m, version)
    say(board())


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
    if key == "install":
        return run([os.path.join(eng, "install.sh")], key)
    if key == "reconcile":
        if not os.path.exists(os.path.join(eng, "ops", "setup.py")):
            return None, ""
        return run([sys.executable, os.path.join(eng, "ops", "setup.py"), "reconcile"], key)
    if key == "doctor":
        return run([sys.executable, os.path.join(eng, "ops", "doctor.py"), config.this_machine()], key)
    raise ValueError(key)


def done_text(d, version, notes):
    rnd = update.Round()
    start = d.get("start")
    versions = update.releases_between(rnd, update.vtuple(start) if start else None, version)
    between = [v for v in versions if v != version]
    text = "✅ 引擎已更新到 %s" % version
    if between:
        text += "（中間的 %s 也一起裝上了）" % "、".join(sorted(between, key=update.vtuple))
    text += "。" + (d.get("newer") or "") + update.notes_of(rnd, version, versions)
    if notes:
        text += "\n\n" + "\n".join(notes)
    return text + "\n\n想回到更新前的版本：到 #setup 跟 Claude 說「引擎回到更新前」。"


def new_card(tag):
    """The 🆕 card for `tag` (update.py, notify mode, on the bot's machine): what it brings since the release this
    machine has, a plain line of the check, the buttons; the card of an older release closed."""
    if bot_elsewhere():  # the bot's machine tells it, on its own card (U5: one 🆕, not one per machine)
        print("這台不是機器人那台：有新版由機器人那台貼卡片")
        return NOT_THE_BOT_MACHINE
    if not bot_machine_here():
        print("找不到機器人在哪一台，或機器清單上沒有這台的名字：不貼卡片（update.py 改用文字）")
        return 1
    rnd = update.Round()
    f = check(fetch=False)  # update.py fetched just now
    now = update.release_in(rnd)[0]
    versions = update.releases_between(rnd, update.vtuple(now) if now else None, tag)
    text = "🆕 有新版本 %s 可以更新" % tag + ("（這台現在是 %s）。" % now if now else "。")
    text += update.notes_of(rnd, tag, versions)
    text += "\n\n" + PLAIN.get(f["kind"], PLAIN["unreadable"])
    buttons = [] if f["kind"] in ("archive", "none") else [
        {"id": "go", "label": "更新", "style": "primary", "action": "update_go", "arg": tag}]
    buttons += [{"id": "later", "label": "之後再說", "style": "secondary", "action": "later"},
                {"id": "details", "label": "看要做什麼", "style": "secondary", "action": "details"}]
    agents = for_agents(rnd, tag, versions)
    details = technical(f) + ("\n\n" + agents if agents else "")
    card_id = "update-%s" % tag
    try:
        cards.show(card_id, text, buttons, details[:cards.DETAILS_MAX])
    except cards.CardError as e:
        print("沒貼成：%s" % e)
        return 1
    close_older(card_id, tag)
    return 0


def close_older(card_id, tag):
    """The card of the release told before (update-card: {id}) loses its buttons and says a newer one is out."""
    path = os.path.join(state_dir(), "update-card")
    try:
        with open(path) as f:
            old = json.load(f).get("id")
    except (OSError, ValueError, AttributeError):
        old = None
    if old and old != card_id:
        try:
            cards.close(old, "這張卡的版本已經不是最新的了：有新版本 %s，請看下面那張新卡。" % tag)
        except cards.CardError as e:
            print("舊的卡片 %s 沒收掉：%s" % (old, e))
    with open(path + ".tmp", "w") as f:
        json.dump({"id": card_id, "at": time.time()}, f)
    os.replace(path + ".tmp", path)


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
    """altention update undo: back to the newest altention/before-vX mark (made just before a move to a release),
    only from where that move went (anything since would be lost: refused, said) and only when the engine does not
    update by itself (release or branch would take it again next round: said what to do first). reset --keep: edits
    not committed stay, or nothing moves. Then as after any move: the bot's restart flag or its packages, install.sh."""
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
    code, out = run([os.path.join(engine(), "install.sh")], "install")
    print("已經回到更新前的版本%s，你自己改過的檔都還在。%s%s" % (
        "（%s）" % back if back else "", "機器人會自己重啟換回這個版本。" if restart else "",
        "" if code in (0, 2) else "但安裝這一步沒做完：%s；跟 Claude 說「跑 ./install.sh」。" % last_line(out)))
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
    if argv[:1] == ["save"] and len(argv) == 2 and update.RELEASE_TAG.match(argv[1]):
        return save(argv[1])
    if argv == ["undo"]:
        return undo()
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
