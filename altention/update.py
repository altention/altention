#!/usr/bin/env python3
"""Engine updater (stage 3a.10): brings this machine's engine up to its upstream, behind a syntax check.

  python3 altention/update.py            # one round (its own cron line, ahead of the sync's)
  python3 altention/update.py --release  # one round now, to the newest release whatever core.update says (the
                                         # person asked to update; the same checks); then run install.sh. Exit 75:
                                         # the git lock was held (the sync, a deploy) and nothing was done: try again
                                         # (a plain round too with ALT_UPDATE_LOCKED_EXIT=1: ops/apply.py, branch mode)

Standard library only, and no other module of the engine is imported (as the supervisor): a bug anywhere else
must not stop the fixes from arriving. One round:
- not this machine's engine (its own copy of altention/engine_id.py's rules, for the same reason) → nothing;
- an engine unpacked from an archive (no .git at all) → nothing to update from: said, and the round counts as done;
- the repo's git lock (altention-git.lock, shared with the sync and deploy.sh) taken by someone else, a rebase underway,
  an index.lock → this round skipped;
- which upstream: the instance's core.update (update_mode): "branch" the checkout's upstream branch, as it always
  was; "release" the highest vX.Y.Z tag, never the branch; "notify" (the default) moves nothing and only writes the
  newer release down (update-available, for doctor). Settings that cannot be read: the mode last read, and told;
- fetch; nothing new → done. Else the files that changed, plus update.py, sync.py, config.py and engine_id.py every
  time, are taken out of the upstream tree into a folder of its own and checked with this machine's own python3
  (py_compile) and bash -n: anything that does not pass, or a check that fails or takes too long → not moved, told;
- a fast-forward when it can (edits in the working tree that do not overlap carry over); with local commits, the
  sync's update() (a rebase in a throwaway worktree, reset --keep) in a child process of its own session, killed
  whole after 300 seconds; however that fails, this round did not update: told, the old version runs on. Never
  autostash. To a release, rollback commits the supervisor made here are left behind, never put on top of it, and
  rollbacks mixed with other local commits stop the round (C-07; sync.local_commits).
Before moving, besides the syntax check, a load check: the upstream altention/ is imported (update, sync, config,
engine_id, update_go) in a child; their top-level code runs, in an isolated throwaway place (python3 -I, HOME and cwd there).
A commit that compiles but throws when loaded never arrives this way.
After a move that changes what the supervisor counts as the bot's version (the trees of dcbot/ and altention/), and
only while this engine's supervisor runs here, dcbot/state/restart-requested is written as deploy.sh writes it: the
bot restarts once nothing is half done, the new version gets the supervisor's trial and a bad one is rolled back
(the rollback commit sits on top of what was taken, so the next round finds nothing new: no loop). The other paths
that move the engine (the sync standing in, home-pull; the launcher's fallback by its own short copy) call the same
after_move (C-09). When dcbot/requirements.txt changed and the bot's venv is here, the restart waits for its packages
(C-08): deps-pending is written under the lock, and pip runs as dcbot/install.sh does once the lock is let go
(install_pending); worked → the flag; failing → no flag, the bot keeps the old code (whose packages it has), doctor
says it, told once, tried again an hour later; killed halfway → the record is there, the round after takes it up. A machine restarted meanwhile: the
supervisor runs pip itself before starting the new code (C-40), and when pip keeps failing it steps back to the old
version here, never pushed (C-44); while HEAD is such a step back, install_pending leaves the record to the
supervisor (it alone moves forward), and the sync drops the step back when the branch moves on.
A machine that moves by itself (release, branch) says once in Discord when HEAD first contains a release newer than
the one it last told, whoever moved it, with the first sentence of each point of that release's CHANGELOG section
(tell_updated, C-55); the first round with no record writes down what it runs and says nothing.
Every notice about updating (🆕, ✅, the ⚠️ of a round) goes to the instance's core.update_channel, #update when not
set (C-57; a channel not there: the default one, saying so), in plain words with the technical part in brackets; a
notice lists what each release between brought, newest first. An update the person asked for (--release) is told
too, and an engine detached from its branch, which never updates, says so once. On the bot's machine the 🆕 is a
card with the buttons (altention update card, U2), the other machines tell none of their own (U5), and a round that altention update go runs (ALT_UPDATE_CARD)
pushes no ✅ or ⚠️ of its own: the card says it. Before HEAD moves to a release, a local tag altention/before-vX on
where it was (never pushed; the newest 3 kept; before-tags.json says where each move went), for altention update
undo (C-57 U3). A file of the person's, not tracked or ignored, with the name of one the new version adds stops
the move before it (in_the_way, C-66: git would refuse one and overwrite the other without a word).
Nothing else here runs install.sh: a skill the new version brings gets its link from the sync's next round (sync.new_skills,
five minutes after this one); a new cron line or hook waits for install.sh or ops/apply.py, and doctor's
plugins.would-apply says it meanwhile.
A refused upstream stays refused (last_alive-update names its hash and why): the sync does not pull it while
update.py is alive, nor do the launchers' fallbacks; it moves on only when upstream changes (a fix pushed) or a
person runs ops/apply.py from the control machine (its home-pull --take-refused; a plain home-pull, as the bot's
stream line, the usage collector and each channel's Claude run it, does not pull it either: 2026-10-07). There is no
"refused too long, let it through". When update.py itself is broken nobody checks: the launcher's plain
fast-forward runs as it must (a known, needed trade-off; manager 2026-10-06).
cron does not run this file but altention/cron_update.py: when a round here does not come to its end (prints no
ALT-UPDATE-DONE: it did not load, it threw, it took too long), that launcher fast-forwards by plain git and writes
the reason down itself (manager 2026-10-06).
Each round that works writes ~/.local/state/altention/last_ok-update; the sync writes last_ok-sync. Each looks at
the other's: more than three of its rounds old (stale_of: the interval apply wrote to intervals.json, as the
instance may set it; 45 minutes without that file) → told; the sync then also moves the engine itself, so a fix
still arrives while this one is broken (manager 2026-10-06).
Telling: a line in ~/.local/state/altention/alerts first (one per line, the last 200 kept; doctor reads it from the
control machine), then altention dc notify, at most once in 6 hours per kind (every time when that record cannot be
written). The notification may fail exactly when there is something to tell: the alerts file is the record.
"""
import fcntl
import json
import os
import pwd
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ALWAYS = ("altention/update.py", "altention/sync.py", "altention/config.py", "altention/engine_id.py",
          "altention/cron_update.py", "altention/cron_sync.py",  # the cron entries themselves
          "altention/dc/post.py", "altention/dc/notify.py",  # every Claude's message and every notice
          "altention/update_go.py", "altention/dc/card.py",  # the update button's way (C-57 U2)
          # tools without an extension every Claude or every notice goes through (manager 2026-10-06)
          "dcbot/tools/dchook", "dcbot/tools/dcctl", "dcbot/tools/dcremote",
          "bin/home-pull", "bin/home-git", "bin/altention")
CHECK_DIR = "altention-update-check"  # the fixed name of the parent; each round makes its own folder inside
STALE = 45 * 60          # three 15-minute rounds: when apply has not written intervals.json
STALE_ROUNDS = 3         # rounds of the other's line missed before it is told (or the sync stands in)
TELL_EVERY = 6 * 3600
CHECK_TIMEOUT, CHILD_TIMEOUT, GIT_TIMEOUT = 120, 300, 120
ALERTS_KEPT = 200
LOCK_NAME = "altention-git.lock"

UPDATE_MODES = ("notify", "release", "branch")  # the instance's core.update (manager 2026-10-14); notify when not set
RELEASE_TAG = re.compile(r"^v([0-9]+)\.([0-9]+)\.([0-9]+)$")  # what the release command tags a published commit with


def state_dir():
    return os.path.join(os.path.expanduser("~/.local/state/altention"))


def now_iso():
    return time.strftime("%Y-%m-%dT%H:%M:%S%z")


def last_ok(name):
    """When `name` ("update", "sync") last worked, or None when it never has (or the file cannot be read)."""
    try:
        with open(os.path.join(state_dir(), "last_ok-%s" % name)) as f:
            return float(json.load(f)["at"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


BLOCKED = []  # (upstream hash, why) when this round refused the upstream
REACHED = []  # set when this round compared with the upstream (updated, nothing new, refused): only then alive
SKIPPED = []  # why this round did not get that far (the lock, a rebase left, a path not readable, fetch…)
SAID = []     # what this round came to, in words (updated X → Y, nothing new, refused): the summary line
ALERTED = []  # the kinds this round wrote down: mark_ok keeps their record of being told


def mark_skipped(why):
    """last_skip-update: since when rounds in a row have not compared with the upstream, and the latest reason.
    doctor shows it (a fault after 45 minutes) and --watch tells: the sync takes over, but a person has to know
    why (manager 2026-10-06). Removed by a round that does compare."""
    path = os.path.join(state_dir(), "last_skip-update")
    try:
        with open(path) as f:
            since = float(json.load(f).get("since"))
    except (OSError, ValueError, TypeError, AttributeError):
        since = time.time()
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"since": since, "at": time.time(), "why": why}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def update_mode():
    """(mode, problem): the instance's core.update, read the way altention/config.py finds the instance (the same
    reading is copied into cron_update.py, cron_sync.py, sync.py and dcbot/supervisor.py; the agreement test holds
    them together).
    plugins.json read and the key not set: "notify". No instance or no plugins.json to be seen (a share not mounted
    yet, a machine that left its instance): the mode last read. The settings cannot be read, or the value is none of
    UPDATE_MODES: the mode last read too, and `problem` says why: a comma too many in plugins.json must not stop a
    machine that follows its branch from getting fixes. The mode last read is update-mode (written by update.py),
    "notify" when there is none, and then `problem` says that too (C-04)."""
    def last(problem="", unseen=""):
        """The mode last read, with `problem`; none (or not one of UPDATE_MODES): notify, and why, `unseen` being
        what was not seen (C-04: a machine that never read its settings is told it does not update by itself)."""
        try:
            with open(os.path.expanduser("~/.local/state/altention/update-mode")) as f:
                m = json.load(f).get("mode")
        except (OSError, ValueError, AttributeError):
            m = None
        if m in UPDATE_MODES:
            return m, problem
        return "notify", problem or "%s，也沒有上次讀到的模式：照 notify，不自動更新" % (unseen or "上次讀到的模式不能用")
    core, problem, unseen = instance_core()
    if core is None:
        return last(problem, unseen)
    mode = core.get("update", "notify")
    if mode not in UPDATE_MODES:
        return last("plugins.json 的 core.update 是 %s，只能是 notify、release 或 branch" % json.dumps(mode, ensure_ascii=False)[:40])
    return mode, ""


def instance_core():
    """(the instance's core settings, "", "") the way altention/config.py finds the instance; (None, problem, "")
    when they cannot be read, (None, "", what was not seen) when there is no instance or plugins.json to be seen."""
    named = os.environ.get("ALT_INSTANCE")
    pointer = os.path.expanduser("~/.config/altention/instance")
    if not named and os.path.exists(pointer):
        try:
            with open(pointer) as f:
                named = f.readline().strip()
        except (OSError, UnicodeDecodeError) as e:
            return None, "讀不了 %s（%s）" % (pointer, e), ""
    if named == "-":  # this machine has left its instance
        return None, "", "這台已經離開實例"
    if named:
        inst = os.path.expanduser(named)
        if not os.path.isdir(inst):
            return None, "實例資料夾不存在：%s" % inst, ""
    else:
        inst = os.path.join(os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention"), "instance")
        if not os.path.isdir(inst):
            return None, "", "看不到實例（%s）" % inst
    path = os.path.join(inst, "plugins.json")
    try:
        with open(path, encoding="utf-8") as f:
            got = json.load(f)
    except FileNotFoundError:
        return None, "", "實例裡沒有 %s" % path
    except (OSError, ValueError, UnicodeDecodeError) as e:
        return None, "讀不出 %s（%s）" % (path, e), ""
    core = got.get("core", {}) if isinstance(got, dict) else None
    if not isinstance(core, dict):
        return None, "%s 的%s不是物件" % (path, " core" if isinstance(got, dict) else "內容"), ""
    return core, "", ""


UPDATE_CHANNEL = "update"  # C-57: where the notices about updating go, unless core.update_channel names another


def update_channel():
    """The instance's core.update_channel (a channel name, category/name or ID), UPDATE_CHANNEL when not set or not
    readable. A channel that is not there: altention dc notify sends to the default one, saying so."""
    core = instance_core()[0]
    ch = core.get("update_channel") if core else None
    return ch.strip() if isinstance(ch, str) and ch.strip() else UPDATE_CHANNEL


LANGUAGE = "zh-TW"  # an installation's language when core.language is not set (altention/text.py's DEFAULT)


def notes_language():
    """The instance's core.language, LANGUAGE when not set or not readable: which CHANGELOG part the notices quote
    (C-57 U8)."""
    core = instance_core()[0]
    lang = core.get("language") if core else None
    return lang.strip() if isinstance(lang, str) and lang.strip() else LANGUAGE


def send(text, channel=None):
    """altention dc notify -c `channel` (update_channel() when None; "" the default channel): True when it went out."""
    where = ["-c", update_channel() if channel is None else channel] if channel != "" else []
    try:
        r = subprocess.run([os.path.join(ENGINE, "bin", "altention"), "dc", "notify"] + where + [text],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def remember_mode(mode):
    """update-mode: the mode last read, for when the settings cannot be read (never fails the round)."""
    path = os.path.join(state_dir(), "update-mode")
    try:
        with open(path) as f:
            if json.load(f).get("mode") == mode:
                return
    except (OSError, ValueError, AttributeError):
        pass
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"mode": mode, "at": now_iso()}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def latest_release(rnd):
    """(tag, commit) of the highest vX.Y.Z tag this repo has, or None."""
    best = None
    for tag in rnd.git("tag", "-l", "v*").stdout.split():
        m = RELEASE_TAG.match(tag)
        if m and (best is None or tuple(map(int, m.groups())) > best[0]):
            best = (tuple(map(int, m.groups())), tag)
    if best is None:
        return None
    commit = rnd.git("rev-parse", "-q", "--verify", best[1] + "^{commit}").stdout.strip()
    return (best[1], commit) if commit else None


def available(found, rnd=None):
    """update-available: the release newer than HEAD that notify mode saw ({tag, commit, at, told}), for doctor;
    removed when there is none or the mode is not notify. A release not told yet is told (C-18: once per release,
    pushed out like a notice; told: the tag whose notice went out, kept while the file is). True when told now."""
    path = os.path.join(state_dir(), "update-available")
    try:
        with open(path) as f:
            told = json.load(f).get("told")
    except (OSError, ValueError, AttributeError):
        told = None
    try:
        if found:
            if told != found[0] and tell_release(rnd, found[0]):
                told = found[0]
            os.makedirs(state_dir(), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"tag": found[0], "commit": found[1], "at": time.time(), "told": told}, f)
            os.replace(path + ".tmp", path)
            return told == found[0]
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass
    return False


NOT_THE_BOT_MACHINE = 3  # altention update card's exit when the bot runs elsewhere (altention/update_go.py)


def tell_release(rnd, tag):
    """The notice that `tag` is out (to update_channel()), with what it brings since the release HEAD has (every
    version between, newest first) and how to take it, in plain words (C-57). On the bot's machine a card with the
    buttons (altention update card, U2; this round's git lock handed to it, ALT_GIT_LOCK_FD); on another machine
    while the list names the bot's, nothing (that machine tells it, U5); no bot machine known, or the card cannot
    be posted, the notice in words. True when it went out; not, and it is tried again next round."""
    if card_of(rnd, tag):
        return True  # posted as a card, or the bot's machine tells it (U5: one 🆕 for the whole installation)
    now, found = release_in(rnd)
    newer = releases_between(rnd, found[now] if now else None, tag)
    text = "🆕 有新版本 %s 可以更新" % tag
    text += "（這台現在是 %s）。" % now if now else "。"
    text += notes_of(rnd, tag, newer)
    text += ("\n\n要更新：跟 Claude 說「更新引擎」。更新前會先檢查新版能不能正常跑；沒通過就不換，你改過的東西也不會被蓋掉。"
             "\n想以後有新版就自己更新：跟 Claude 說「引擎改成自動更新」。")
    return send(text)


def card_of(rnd, tag):
    """altention update card `tag` (the 🆕 card with the buttons, posted only from the bot's machine): True when it
    is posted, or when the bot runs on another machine (exit NOT_THE_BOT_MACHINE: that machine tells it, U5)."""
    fd = rnd.lock.fileno() if rnd is not None and rnd.lock else None
    env = dict(os.environ, ALT_GIT_LOCK_FD=str(fd)) if fd is not None else dict(os.environ)
    try:
        r = subprocess.run([os.path.join(ENGINE, "bin", "altention"), "update", "card", tag], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=300, env=env, pass_fds=(fd,) if fd is not None else ())
    except (OSError, subprocess.SubprocessError):
        return False
    if r.returncode not in (0, NOT_THE_BOT_MACHINE):
        SKIPPED.append("貼不了更新卡片（%s），改用文字" % " ".join((r.stdout or "").split())[-160:])
    return r.returncode in (0, NOT_THE_BOT_MACHINE)


def releases_between(rnd, floor, tag):
    """The vX.Y.Z tags above `floor` (a version tuple, None for none) up to `tag`, newest first."""
    top = tuple(map(int, RELEASE_TAG.match(tag).groups()))
    got = {}
    for t in rnd.git("tag", "-l", "v*").stdout.split():
        m = RELEASE_TAG.match(t)
        if m:
            v = tuple(map(int, m.groups()))
            if (floor is None or v > floor) and v <= top:
                got[t] = v
    got[tag] = top
    return sorted(got, key=got.get, reverse=True)


def release_in(rnd):
    """The highest vX.Y.Z tag HEAD contains, with the lower ones it contains: (tag or None, {tag: (x, y, z)})."""
    found = {}
    for tag in rnd.git("tag", "-l", "v*", "--merged", "HEAD").stdout.split():
        m = RELEASE_TAG.match(tag)
        if m:
            found[tag] = tuple(map(int, m.groups()))
    return (max(found, key=found.get) if found else None), found


def vtuple(tag):
    """A release tag as numbers to compare; None (no release) below them all."""
    return tuple(map(int, RELEASE_TAG.match(tag).groups())) if tag else (-1, -1, -1)


def taken_back(rnd, tag):
    """Is `tag` not what runs here: a rollback or step back of the supervisor's above it in HEAD, or git cannot say
    (tell_updated; altention update go before its ✅, C-57 U2)."""
    above = rnd.git("log", "--format=%s", "%s..HEAD" % tag)
    return above.returncode != 0 or any(ROLLBACK_SUBJECT.match(l) or STEP_BACK_SUBJECT.match(l)
                                        for l in above.stdout.splitlines())


def in_the_way(rnd, head, up):
    """The files not tracked here with the name of one `up` adds over `head`: git refuses to overwrite those, and the
    ones the person ignores (.gitignore, .git/info/exclude) it overwrites without a word (C-66: #code-review-2 of
    0196526, manager), so both stop a move. `rnd`: anything with git() (a Round, a sync.Repo; altention update
    check and aside use this one copy)."""
    def names(*args):
        r = rnd.git(*args, timeout=60)
        return [l for l in (r.stdout or "").splitlines() if l.strip()]
    added = set(names("diff", "--name-only", "--diff-filter=A", head, up))
    return sorted(set(f for f in names("ls-files", "--others", "--exclude-standard") +
                      names("ls-files", "--others", "--ignored", "--exclude-standard") if f in added))


BEFORE = "altention/before-%s"  # a local tag on HEAD before it moves to a release (C-57 U3), never pushed
BEFORE_KEPT = 3


def mark_before(rnd, head, tag):
    """altention/before-<tag> on `head`, before HEAD moves to the release `tag` (altention update undo goes back to
    it); the BEFORE_KEPT newest kept. Its name, or None (not a release: branch mode; or git refused)."""
    if not tag:
        return None
    name = BEFORE % tag
    if rnd.git("tag", "-f", name, head).returncode != 0:
        return None
    marks = []
    for t in rnd.git("tag", "-l", BEFORE % "v*").stdout.split():
        m = RELEASE_TAG.match(t[len(BEFORE % ""):])
        if m:
            marks.append((tuple(map(int, m.groups())), t))
    for _, t in sorted(marks, reverse=True)[BEFORE_KEPT:]:
        rnd.git("tag", "-d", t)
        forget_before(t)
    return name


def drop_before(rnd, name):
    """The mark of a move that did not happen."""
    if name:
        rnd.git("tag", "-d", name)
        forget_before(name)


def befores_path():
    return os.path.join(state_dir(), "before-tags.json")


def befores():
    """{tag name: {before, after, at}}: where each mark's move went (undo goes back only from there)."""
    try:
        with open(befores_path()) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else {}
    except (OSError, ValueError):
        return {}


def write_befores(got):
    os.makedirs(state_dir(), exist_ok=True)
    with open(befores_path() + ".tmp", "w") as f:
        json.dump(got, f)
    os.replace(befores_path() + ".tmp", befores_path())


def note_after(name, before, after):
    if name:
        got = befores()
        got[name] = {"before": before, "after": after, "at": time.time()}
        try:
            write_befores(got)
        except OSError:  # the move is done: undo then says it has no mark to go back from
            pass


def forget_before(name):
    got = befores()
    if got.pop(name, None) is not None:
        try:
            write_befores(got)
        except OSError:
            pass


def tell_updated(rnd, start=False):
    """C-55: a machine that moves by itself (release, branch) tells once per release that it now runs it, whoever
    moved HEAD (this round, home-pull, the sync standing in, a push to main here). updated-release: {tag, at}, the
    release last told (or found when there was no record: written down, not told, so a new install or this code
    arriving pushes nothing). Several releases at once: the newest told, the ones between named. The notice not
    out: not written, tried again next round. HEAD going back tells nothing, nor a release with a rollback or step
    back of the supervisor's above it in HEAD (it is not what runs): told once that commit is gone. `start`: the
    release HEAD had before an update the person asked for (update.py --release, C-57), the start when there is no
    record (a notify machine keeps none), or when it is newer than the record (a notify machine's record stays
    where it was while apply or home-pull move HEAD), so that update is told from what really ran before it."""
    path = os.path.join(state_dir(), "updated-release")
    was = False  # no record (or one someone broke): what HEAD has now is the start
    try:
        with open(path) as f:
            rec = json.load(f)
        if isinstance(rec, dict) and "tag" in rec and (rec["tag"] is None or RELEASE_TAG.match(str(rec["tag"]))):
            was = rec["tag"]
    except (OSError, ValueError):
        pass
    if start is not False and (was is False or vtuple(start) > vtuple(was)):
        was = start  # the newer of the two: a notify machine's record can be old (HEAD moved by apply, home-pull)
    now, found = release_in(rnd)
    if was is not False:
        floor = vtuple(was)
        if now is None or found[now] <= floor:  # nothing newer (HEAD gone back included): the record stays
            return
        if taken_back(rnd, now):
            return  # the supervisor took it back (or git cannot say): not running it; told once that is gone
        between = sorted((t for t, v in found.items() if floor < v < found[now]), key=found.get)
        if os.environ.get(ON_A_CARD):  # altention update go: its card says it (C-57 U2), the record is written
            SAID.append("引擎更新到 %s（寫在更新卡片上）" % now)
        elif not tell_version(rnd, now, between):
            return
        else:
            SAID.append("已推播：引擎更新到 %s" % now)
    os.makedirs(state_dir(), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump({"tag": now, "at": time.time()}, f)
    os.replace(path + ".tmp", path)


NOTES_LINES = 6     # bullets of a release's notes in the notice
NOTES_CHARS = 1200  # and characters, at most
NOTE_CHARS = 240    # of one bullet
EACH_LINES = 3      # several releases in one notice: bullets of each (C-57, owner 2026-10-19)
ALL_CHARS = 1500    # and characters of all of them together


def release_notes(rnd, tag, text=None, most=NOTES_LINES, room=NOTES_CHARS, lang=None):
    """Each bullet of `tag`'s CHANGELOG section (its "For people (<lang>)" part when it has one, `lang` being
    notes_language() when None; else its "For people" part when it has one) cut to its bold lead ("**…**" it starts
    with) or else its first sentence, NOTE_CHARS at most, as lines (`most` of them, `room` characters together),
    and whether some were left out; None when the CHANGELOG (`tag`'s own, or `text`) has no such section. A
    release without its own words in `lang` is quoted in English (C-57 U8: releases before v0.3.0 have none)."""
    if text is None:
        text = rnd.git("show", "%s:CHANGELOG.md" % tag).stdout
    section, inside = [], False
    for line in text.splitlines():
        if line.startswith("## "):
            if inside:
                break
            inside = line[3:].split(" ")[0] == tag
        elif inside:
            section.append(line)
    if not inside:
        return None
    parts, part = {}, None
    for line in section:
        if line.startswith("### "):
            part = line[4:].strip().lower()
            continue
        parts.setdefault(part, []).append(line)

    def bullets_of(lines):
        got = []
        for line in lines:
            if line.startswith("- "):
                got.append(line[2:].strip())
            elif got and line.strip():
                got[-1] += " " + line.strip()
        return got
    own = "for people (%s)" % (notes_language() if lang is None else lang).lower()
    bullets = bullets_of(parts.get(own, []))
    # a sentence ends at "." "!" "?" before a space; in the installation's own words also at 。！？ (no space after
    # them), not in the English part, whose quoted Chinese would end its first sentence too early
    end = r"[.!?](?=\s)|[。！？]" if bullets else r"[.!?。](?=\s)"
    bullets = bullets or bullets_of(parts.get(None, []) + parts.get("for people", []))
    lines, cut, size = [], False, 0
    for b in bullets:
        lead = re.match(r"\*\*[^*]+\*\*", b) or re.search(end, b)
        line = b[:lead.end()] if lead else b
        if len(line) > NOTE_CHARS:
            line = line[:NOTE_CHARS - 1] + "…"
        cut = cut or line != b
        if len(lines) == most or size + len(line) > room:
            cut = True
            break
        lines.append(line)
        size += len(line)
    return lines, cut


def notes_of(rnd, tag, versions):
    """What `versions` (newest first, `tag` the newest) brought, from `tag`'s CHANGELOG: one release, its points
    (release_notes); several, a part for each, newest first, EACH_LINES points each and ALL_CHARS together, the
    ones that do not fit named (C-57: owner 2026-10-19). Starts with a line break when not empty."""
    if len(versions) <= 1:
        notes = release_notes(rnd, tag)
        if notes is None:
            return "CHANGELOG.md 裡沒有這版的說明。"
        if not notes[0]:
            return "這版改了什麼在 CHANGELOG.md 的 %s 一節。" % tag
        text = "這版的重點：\n" + "\n".join("- " + n for n in notes[0])
        if notes[1]:
            text += "\n（只列每項的第一句；完整的在 CHANGELOG.md 的 %s 一節）" % tag
        return text
    log = rnd.git("show", "%s:CHANGELOG.md" % tag).stdout
    parts, left, room, cut = [], [], ALL_CHARS, False
    for v in versions:
        notes = None if left or room <= 0 else release_notes(rnd, v, log, EACH_LINES, room)
        if left or room <= 0 or (notes and not notes[0] and notes[1]):  # points, but no room left for them
            left.append(v)
            continue
        if notes is None or not notes[0]:
            parts.append("**%s**：CHANGELOG.md 裡沒有這版的重點" % v)
            continue
        parts.append("**%s**\n" % v + "\n".join("- " + n for n in notes[0]))
        room -= sum(len(n) for n in notes[0])
        cut = cut or notes[1]
    text = "改了什麼（新的在前）：\n\n" + "\n\n".join(parts)
    if left:
        text += "\n\n（還有 %s 的改動沒列出來；完整的在 CHANGELOG.md）" % "、".join(left)
    elif cut:
        text += "\n\n（只列每項的第一句；完整的在 CHANGELOG.md）"
    return text


def tell_version(rnd, tag, between):
    """The notice that this machine now runs `tag` (to update_channel()), with what it and the releases `between`
    brought. True when it went out."""
    text = "✅ 引擎已更新到 %s" % tag
    if between:
        text += "（中間的 %s 也一起裝上了）" % "、".join(between)
    return send(text + "。" + notes_of(rnd, tag, [tag] + sorted(between, key=lambda t: tuple(map(int, RELEASE_TAG.match(t).groups())), reverse=True)))


def last_alive():
    """{"at", "blocked": {"hash", "why", "since"} or None} written at the end of every round that came to its end
    (updated, nothing new, or refused), or None. The sync moves the engine only when this is old (manager
    2026-10-06): a refused upstream is never pulled in by the sync just because update.py keeps refusing it."""
    try:
        with open(os.path.join(state_dir(), "last_alive-update")) as f:
            got = json.load(f)
        return got if isinstance(got, dict) and isinstance(got.get("at"), (int, float)) else None
    except (OSError, ValueError):
        return None


def blocked_hash():
    """The upstream hash update.py is refusing now, or "": the same reading as sync.refused_hash() and the
    launchers' refused() (no file or an unreadable one: no record; the latter written down)."""
    path = os.path.join(state_dir(), "last_alive-update")
    try:
        with open(path) as f:
            got = json.load(f)
    except FileNotFoundError:
        return ""
    except (OSError, ValueError) as e:
        alert("update", "alive-unreadable", "讀不出 %s（%s），當成沒有被擋的紀錄" % (path, e), notify=False)
        return ""
    if not isinstance(got, dict):  # [] or a bare value: no record either, and said like an unreadable one
        alert("update", "alive-unreadable", "%s 不是物件，當成沒有被擋的紀錄" % path, notify=False)
        return ""
    b = got.get("blocked")
    return b.get("hash", "") if isinstance(b, dict) and isinstance(b.get("hash"), str) else ""


def mark_alive():
    path = os.path.join(state_dir(), "last_alive-update")
    blocked = None
    if BLOCKED:
        h, why = BLOCKED[-1]
        before = (last_alive() or {}).get("blocked") or {}
        blocked = {"hash": h, "why": why, "since": before.get("since") if before.get("hash") == h else time.time()}
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"at": time.time(), "when": now_iso(), "blocked": blocked}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def mark_ok(name, extra=None):
    """Worked: written down, and what this one was told about is told again next time it goes wrong (a second,
    different problem within six hours must not stay silent: #code-review-2 2026-10-06)."""
    told_path = os.path.join(state_dir(), "alerts-told.json")
    gone = lambda k: k.startswith(name + "/") and k not in ALERTED  # noqa: E731 (told this round: still so)
    try:
        with open(told_path) as f:
            told = json.load(f)
        if isinstance(told, dict) and any(gone(k) for k in told):
            told = dict((k, v) for k, v in told.items() if not gone(k))
            with open(told_path + ".tmp", "w") as f:
                json.dump(told, f)
            os.replace(told_path + ".tmp", told_path)
    except (OSError, ValueError):
        pass
    path = os.path.join(state_dir(), "last_ok-%s" % name)
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump(dict({"at": time.time(), "when": now_iso()}, **(extra or {})), f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def add_alert(rows, stamp, source, kind, text, keep=200):
    """The alerts file's rows with this one added: one source and kind keeps one row, the newest, counted (an
    earlier one is taken out), so kinds repeating, even in turn, never push the others out; then the last `keep`. The same in update.py,
    sync.py and both cron launchers (manager 2026-10-07)."""
    text = " ".join(str(text).split())
    for i in range(len(rows) - 1, -1, -1):  # an earlier row of the same source and kind: this one takes its place
        row = rows[i].rstrip("\n").split("\t", 3)
        if len(row) == 4 and row[1] == source and row[2] == kind:
            head, mark, count = row[3].rpartition("（共 ")
            n = int(count[:-3]) + 1 if mark and count.endswith(" 次）") and count[:-3].isdigit() else 2
            text, rows = "%s（共 %d 次）" % (text, n), rows[:i] + rows[i + 1:]
            break
    return (rows + ["%s\t%s\t%s\t%s\n" % (stamp, source, kind, text)])[-keep:]


def alert(source, kind, text, notify=True):
    """Write it down here first (doctor reads it), then push it out, at most once in TELL_EVERY per kind."""
    ALERTED.append("%s/%s" % (source, kind))
    folder = state_dir()
    try:
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, "alerts")
        with open(path, "a+", errors="replace") as f:
            fcntl.flock(f, fcntl.LOCK_EX)
            f.seek(0)
            kept = add_alert(f.readlines(), now_iso(), source, kind, text, ALERTS_KEPT)
            f.seek(0)
            f.truncate()
            f.writelines(kept)
    except OSError:
        pass
    if not notify:
        return
    if os.environ.get(ON_A_CARD) and source == "update" and kind not in NOT_ABOUT_UPDATING:
        return  # altention update go tells it on its card (C-57 U2); the 6-hour record left for the plain rounds
    told_path = os.path.join(folder, "alerts-told.json")
    try:
        with open(told_path) as f:
            told = json.load(f)
        if not isinstance(told, dict):
            told = {}
    except (OSError, ValueError):
        told = {}
    key = "%s/%s" % (source, kind)
    try:
        last = float(told.get(key) or 0)
    except (TypeError, ValueError):  # a record someone broke: told again, and written over below
        last = 0
    if time.time() - last < TELL_EVERY:
        return
    told[key] = time.time()
    try:
        with open(told_path + ".tmp", "w") as f:
            json.dump(told, f)
        os.replace(told_path + ".tmp", told_path)
    except OSError:
        pass  # cannot write the record: then every time (better twice than never)
    if source == "update" and kind not in NOT_ABOUT_UPDATING:  # C-57: about updating, to update_channel()
        send("⚠️ 引擎更新：%s" % text)
    else:
        send("⚠️ %s：%s" % (source, text), DEFAULT_CHANNEL)


ON_A_CARD = "ALT_UPDATE_CARD"  # set by altention update go: its card tells what this round did, no notice of its own
NOT_ABOUT_UPDATING = ("sync-stale",)  # update.py's notices that go to the default channel, not update_channel()
DEFAULT_CHANNEL = ""  # send() without -c: altention dc notify's default channel (core.notify_channel)


BOT_PATHS = ("dcbot", "altention")  # dcbot/supervisor.py version(): what makes the bot's next start a trial


def bot_version(rnd, rev):
    return "+".join(rnd.git("rev-parse", "-q", "--verify", "%s:%s" % (rev, p)).stdout.strip() for p in BOT_PATHS)


def bot_running():
    """This engine's supervisor runs on this machine: its pid file names a live process that is a supervisor."""
    try:
        with open(os.path.join(ENGINE, "dcbot", "state", "supervisor.pid")) as f:
            pid = int(f.read().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return False
    try:
        with open("/proc/%d/cmdline" % pid, "rb") as f:
            return b"supervisor" in f.read()
    except OSError:  # no /proc (macOS): the live pid is all there is
        return True


REQUIREMENTS = "dcbot/requirements.txt"  # what dcbot/install.sh installs into the bot's venv
PIP_TIMEOUT = 600
DEPS_PENDING = "deps-pending"  # in the state folder: {head, why, at} while the bot's packages are not installed


def write_restart_flag(tell=None):
    """deploy.sh's flag (the bot restarts once nothing is half done). True when written."""
    flag = os.path.join(ENGINE, "dcbot", "state", "restart-requested")
    try:
        with open(flag + ".tmp", "w") as f:
            f.write('{"channel": ""}\n')  # no channel: the bot says it in #audit only
        os.replace(flag + ".tmp", flag)
        return True
    except OSError as e:
        (tell or alert)("update", "restart", "新版本已經換上，但沒辦法叫機器人自己重啟，所以機器人還在跑舊版；你的東西都沒動。"
                         "要套用新版：在 Discord 打 !restart。（細節：寫不了機器人的重啟旗標：%s）" % e)
        return False


def install_deps():
    """pip install -r dcbot/requirements.txt into the bot's venv, as dcbot/install.sh does it: "" or why not."""
    try:
        r = subprocess.run([os.path.join(ENGINE, "dcbot", ".venv", "bin", "python"), "-m", "pip", "install", "-q", "-r",
                            "requirements.txt"], cwd=os.path.join(ENGINE, "dcbot"), stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, errors="replace", timeout=PIP_TIMEOUT,
                           stdin=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        return "pip install %d 秒沒有結束" % PIP_TIMEOUT
    except OSError as e:
        return "跑不了 pip（%s）" % e
    return "" if r.returncode == 0 else "pip install 失敗（結束碼 %d）：%s" % (r.returncode, " ".join(r.stdout.split())[-300:])


# dcbot/supervisor.py's subject for a step back while packages install (altention/sync.py has the same; a test holds them)
STEP_BACK_SUBJECT = re.compile(r"^dcbot: step back to ([0-9a-f]{7}) while packages install \([0-9a-f]{7}\)$")
# and for a rollback (put_back(); the same as altention/sync.py's, a test holds them)
ROLLBACK_SUBJECT = re.compile(r"^dcbot: roll back( \(partial\))? to ([0-9a-f]{7}) \(.*\)$")
RETRY_AFTER = 3600  # a pip that failed is tried again an hour later, not every round (#code-review of df95bc2)
INSTALLING = "安裝中…"


def deps_pending():
    """The deps-pending record ({head, why, at, next}) or None."""
    try:
        with open(os.path.join(state_dir(), DEPS_PENDING)) as f:
            got = json.load(f)
        return got if isinstance(got, dict) else None
    except (OSError, ValueError):
        return None


def mark_deps(head, why, next_try=0, tries=0):
    """deps-pending written (the head it is for, why, when to try pip next, how many pips failed for it) or, with
    why "", removed. Never fails the round."""
    path = os.path.join(state_dir(), DEPS_PENDING)
    try:
        if why:
            os.makedirs(state_dir(), exist_ok=True)
            with open(path + ".tmp", "w") as f:
                json.dump({"head": head, "why": why, "at": time.time(), "next": next_try, "tries": tries}, f)
            os.replace(path + ".tmp", path)
        elif os.path.lexists(path):
            os.remove(path)
    except OSError:
        pass


def has_venv():
    return os.path.exists(os.path.join(ENGINE, "dcbot", ".venv", "bin", "python"))


def after_move(rnd, before, after, tell=None):
    """After HEAD moved from `before` to `after` by any path (this round, the sync standing in, home-pull; C-09),
    under the git lock. dcbot/requirements.txt changed and the bot's venv here, or packages still pending from an
    earlier move: deps-pending for `after`, no flag; install_pending() runs pip once the lock is let go (C-08;
    #code-review of df95bc2: pip needs no git lock, and its record is there before it runs, so a pip killed halfway
    is not forgotten). Else, the bot's version changed and its supervisor runs here: the restart flag. `rnd`:
    anything with git(). True when the flag was written."""
    tell = tell or alert
    pending = deps_pending()
    if has_venv() and (pending or rnd.git("rev-parse", "-q", "--verify", "%s:%s" % (before, REQUIREMENTS)).stdout.strip()
                       != rnd.git("rev-parse", "-q", "--verify", "%s:%s" % (after, REQUIREMENTS)).stdout.strip()):
        mark_deps(after, INSTALLING if not pending else str(pending.get("why") or INSTALLING),
                  0 if not pending else pending.get("next") or 0, 0 if not pending else tries_of(pending))
        return False
    if bot_version(rnd, before) == bot_version(rnd, after) or not bot_running():
        return False
    return write_restart_flag(tell)


def tries_of(pending):
    n = pending.get("tries")
    return n if type(n) is int and n >= 0 else 0


def install_pending(tell=None):
    """pip for a deps-pending record, outside the git lock, when its time has come ("next"; one at a time, by a lock
    of its own). The record says 安裝中… before pip starts, with a next try past PIP_TIMEOUT: killed halfway, the
    round after takes it up. Failed: why, next try in RETRY_AFTER, told the first time only (doctor keeps saying it). Worked and HEAD is still
    the head it was for: the record goes, and the restart flag if the bot runs here; HEAD moved meanwhile: the record
    stays for that HEAD, tried again at once. HEAD a step back of the supervisor's (C-44): nothing, the supervisor
    installs and moves forward. "" or what it did."""
    tell = tell or alert
    pending = deps_pending()
    if not pending:
        return ""
    if not has_venv():  # the bot is not installed here (any more): nothing to install into
        mark_deps("", "")
        return ""
    try:
        if time.time() < float(pending.get("next") or 0):
            return ""
    except (TypeError, ValueError):
        pass
    try:
        subject = subprocess.run(["git", "-C", ENGINE, "log", "-1", "--format=%s"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                 universal_newlines=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL).stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return ""  # HEAD not read: the next round
    if STEP_BACK_SUBJECT.match(subject):
        return ""  # C-44: the supervisor stepped back while packages install; moving forward is its alone
    try:
        lock = open(os.path.join(state_dir(), DEPS_PENDING + ".lock"), "a")
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return ""  # another one is installing
    try:
        head, tries = str(pending.get("head") or ""), tries_of(pending)
        mark_deps(head, INSTALLING, time.time() + PIP_TIMEOUT + 60, tries)
        why = install_deps()
        if why:
            mark_deps(head, why, time.time() + RETRY_AFTER, tries + 1)
            (tell if not tries else lambda *a: alert(*a, notify=False))("update", "deps", "新版本已經換上，但機器人需要的套件沒裝好，所以機器人還在跑舊版，一小時後會自己再試；你的東西都沒動。"
                 "要現在處理：跟 Claude 說「幫我裝機器人的套件」。（細節：引擎更新到 %s，套件沒裝上：%s；"
                 "在這台的引擎跑 dcbot/install.sh，再在 Discord 打 !restart）" % (head[:12], why))
            return "機器人要的套件沒裝上，沒有重啟（見 alerts）"
        now = subprocess.run(["git", "-C", ENGINE, "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                             universal_newlines=True, timeout=GIT_TIMEOUT, stdin=subprocess.DEVNULL).stdout.strip()
        if now != head:  # moved on while pip ran: what it needs is the next try's
            mark_deps(now, INSTALLING, 0, tries)
            return "套件裝好了，但引擎又前進了，下一輪再對新的版本裝一次"
        mark_deps("", "")
        restart = bot_running() and write_restart_flag(tell)
        return "機器人要的套件裝好了%s" % ("，機器人會自己重啟套用新版" if restart else "")
    finally:
        lock.close()


def verdict():
    """("ok", "") | ("other", why) | ("wait", why): altention/engine_id.py's rules without the machine list, as the
    supervisor has them (update.py must keep working when engine_id.py is the file that broke)."""
    if os.environ.get("ALT_TEST_ANY_ENGINE") == "1":
        try:
            if os.path.realpath(os.path.expanduser("~")) != os.path.realpath(pwd.getpwuid(os.getuid()).pw_dir):
                return ("ok", "")
        except KeyError:
            pass
    here = os.path.realpath(ENGINE)
    dotgit = os.path.join(here, ".git")
    if os.path.isfile(dotgit):
        return ("other", "%s 是 git worktree，不是這台的引擎" % here)
    if os.path.lexists(dotgit) and not os.path.isdir(dotgit):  # a link to somewhere not there: a share not mounted yet
        return ("wait", "%s 讀不到" % dotgit)
    if not os.path.lexists(dotgit) and not os.path.isfile(os.path.join(here, "altention", "engine_id.py")):
        return ("wait", "%s 讀不到" % dotgit)  # not an engine's folder (yet)
    # no .git but the engine's files: unpacked from an archive, decided by the places below like a clone
    try:
        with open(os.path.expanduser("~/.config/altention/engine")) as f:
            named = os.path.expanduser(f.readline().strip())
    except (OSError, UnicodeDecodeError):
        named = ""
    if named and os.path.isdir(named):
        return ("ok", "") if os.path.realpath(named) == here else ("other", "這台的引擎在 %s" % os.path.realpath(named))
    root = os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention")
    for place in (os.path.join(root, "engine"),):
        if os.path.islink(place) and not os.path.exists(place):
            return ("wait", "%s 指到讀不到的地方" % place)
        if os.path.isdir(place):
            return ("ok", "") if os.path.realpath(place) == here else ("other", "這台的引擎在 %s" % os.path.realpath(place))
    cmd = os.path.expanduser("~/.local/bin/altention")
    if os.path.islink(cmd):
        if not os.path.exists(cmd):
            return ("wait", "%s 指到讀不到的地方" % cmd)
        other = os.path.dirname(os.path.dirname(os.path.realpath(cmd)))
        if os.path.realpath(other) != here:
            return ("other", "這台的引擎在 %s" % os.path.realpath(other))
    return ("ok", "")


class Round(object):
    def __init__(self):
        self.lock = None

    def git(self, *args, timeout=GIT_TIMEOUT):
        try:
            return subprocess.run(["git", "-C", ENGINE] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                  universal_newlines=True, timeout=timeout, stdin=subprocess.DEVNULL,
                                  pass_fds=(self.lock.fileno(),) if self.lock else ())
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(args, 124, "", "git %s：%d 秒沒有回應" % (args[0], timeout))

    def git_dir(self):
        r = self.git("rev-parse", "--git-common-dir")
        d = r.stdout.strip()
        return (d if os.path.isabs(d) else os.path.join(ENGINE, d)) if r.returncode == 0 and d else None


def check_tree(rnd, upstream, files):
    """"" when every file in `files` (paths in the upstream tree) passes; else why not. Taken out with git archive
    into a folder of its own under the state folder (or ALT_TMP), removed afterwards; one left by a killed round is
    removed the next time."""
    # ALT_TMP may be a shared folder: only what this round made (mkdtemp, under a parent of our fixed name) is
    # ever removed, and leftovers of killed rounds only from inside that parent (manager 2026-10-06)
    parent = os.path.join(os.environ.get("ALT_TMP") or state_dir(), CHECK_DIR)
    try:
        os.makedirs(parent, exist_ok=True)
        for old in os.listdir(parent):
            if old.startswith("round-") and time.time() - os.path.getmtime(os.path.join(parent, old)) > 3600:
                shutil.rmtree(os.path.join(parent, old), ignore_errors=True)
        base = tempfile.mkdtemp(prefix="round-", dir=parent)
    except OSError as e:
        return "建不了檢查用的資料夾 %s（%s）" % (parent, e)
    try:
        listed = rnd.git("ls-tree", "-r", "--name-only", upstream, "--", *files)
        present = [f for f in listed.stdout.splitlines() if f]
        if listed.returncode != 0:
            return "列不出上游的檔案（%s）" % listed.stderr.strip()[:160]
        if not present:
            return ""
        arch = subprocess.Popen(["git", "-C", ENGINE, "archive", upstream, "--"] + present, stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        tar = subprocess.run(["tar", "-x", "-C", base], stdin=arch.stdout, stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, timeout=CHECK_TIMEOUT)
        arch.stdout.close()
        if arch.wait(timeout=CHECK_TIMEOUT) != 0 or tar.returncode != 0:
            return "拿不出上游的檔案來檢查（%s）" % tar.stderr.decode("utf-8", "replace").strip()[:160]
        def first_line(f):
            try:
                with open(os.path.join(base, f), "rb") as fh:
                    return fh.readline(200)
            except OSError:
                return b""
        # by name, or by the #! line for tools without one (dchook, dcctl, home-pull, altention…)
        load = load_check(rnd, upstream, base)
        if load:
            return load
        py = [os.path.join(base, f) for f in present if f.endswith(".py") or
              (b"python" in first_line(f) and first_line(f).startswith(b"#!"))]
        sh = [os.path.join(base, f) for f in present if f.endswith(".sh") or
              (first_line(f).startswith(b"#!") and any(w in first_line(f) for w in (b"/sh", b"bash", b" sh")))]
        if py:
            code = ("import py_compile, sys\nbad = []\nfor p in sys.argv[1:]:\n    try:\n"
                    "        py_compile.compile(p, cfile=p + 'c', doraise=True)\n    except Exception as e:\n"
                    "        bad.append('%s: %s' % (p, e))\nprint('\\n'.join(bad))\nsys.exit(1 if bad else 0)\n")
            r = subprocess.run([sys.executable, "-c", code] + py, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return "Python 語法檢查沒過（%s）：%s" % (sys.executable, r.stdout.replace(base + "/", "").strip()[:300])
        for p in sh:
            r = subprocess.run(["bash", "-n", p], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, timeout=CHECK_TIMEOUT, stdin=subprocess.DEVNULL)
            if r.returncode != 0:
                return "bash -n 沒過：%s" % r.stdout.replace(base + "/", "").strip()[:300]
        return ""
    except (OSError, subprocess.SubprocessError) as e:  # the check itself failed or took too long: not moved
        return "檢查本身出錯（%s）" % e
    finally:
        shutil.rmtree(base, ignore_errors=True)


LOADED = ("altention.update", "altention.sync", "altention.config", "altention.engine_id",
          "altention.update_go")  # the button's way finishes in the new version's update_go (C-57 U2)


def load_check(rnd, upstream, base):
    """"" when the upstream altention/ loads (each of LOADED imported in a child; their top-level code does run, but
    in an isolated throwaway place); else why not. Catches a commit that compiles but throws as soon as it is
    loaded (manager 2026-10-06)."""
    tree = os.path.join(base, "load")
    try:
        os.makedirs(tree)
        arch = subprocess.Popen(["git", "-C", ENGINE, "archive", upstream, "--", "altention"], stdout=subprocess.PIPE,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
        tar = subprocess.run(["tar", "-x", "-C", tree], stdin=arch.stdout, stdout=subprocess.DEVNULL,
                             stderr=subprocess.PIPE, timeout=CHECK_TIMEOUT)
        arch.stdout.close()
        if arch.wait(timeout=CHECK_TIMEOUT) != 0 or tar.returncode != 0:
            return "拿不出上游的 altention/ 來載入檢查"
        # the modules are loaded (their top-level code runs) in an isolated throwaway place: python3 -I, HOME and
        # cwd in this round's folder, no PYTHON* or ALT_*, no stdin; anything they write lands there and goes
        # with it (#code-review-2 2026-10-06)
        home = os.path.join(base, "home")
        os.makedirs(home)
        env = dict((k, v) for k, v in os.environ.items() if not k.startswith(("PYTHON", "ALT_")) and k != "HOME")
        env.update(HOME=home, TMPDIR=home, XDG_STATE_HOME=os.path.join(home, ".local", "state"),
                   XDG_CONFIG_HOME=os.path.join(home, ".config"), XDG_CACHE_HOME=os.path.join(home, ".cache"))
        code = ("import importlib, sys\nsys.path.insert(0, sys.argv[1])\nfor m in sys.argv[2:]:\n"
                "    importlib.import_module(m)\n")
        r = subprocess.run([sys.executable, "-I", "-B", "-c", code, tree] + list(LOADED), cwd=home,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, errors="replace",
                           timeout=60, stdin=subprocess.DEVNULL, env=env)
        if r.returncode != 0:
            return "載入檢查沒過（%s）：%s" % (sys.executable, r.stdout.strip()[-300:])
        return ""
    except (OSError, subprocess.SubprocessError) as e:
        return "載入檢查本身出錯（%s）" % e


def rebase_in_child(rnd, to=None):
    """The sync's update() (rebase in a throwaway worktree, reset --keep; to a release, this machine's rollback
    commits left behind), in a child of its own session holding the same lock (its fd passed on), killed whole after
    CHILD_TIMEOUT. (why not, "") when it did not work; ("", what it said) when it did."""
    code = ("import sys; sys.path.insert(0, %r)\n"
            "from altention import sync\nrepo = sync.Repo(%r)\nrepo.lock_fd = %d\n"
            "c, note = sync.update(repo%s)\nprint(note or '')\nsys.exit(c)\n"
            % (ENGINE, ENGINE, rnd.lock.fileno(), "" if to is None else ", to=%r" % to))  # branch: as it always was
    try:
        p = subprocess.Popen([sys.executable, "-c", code], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             universal_newlines=True, stdin=subprocess.DEVNULL, start_new_session=True,
                             pass_fds=(rnd.lock.fileno(),))
    except OSError as e:
        return "起不了更新的子行程（%s）" % e, ""
    try:
        out, _ = p.communicate(timeout=CHILD_TIMEOUT)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)  # the whole session: no git left running without the lock
        except OSError:
            pass
        p.wait()
        return "更新的子行程 %d 秒沒有結束，已中止" % CHILD_TIMEOUT, ""
    if p.returncode == 0:
        return "", " ".join((out or "").split())[:300]
    return "更新失敗（結束碼 %s）：%s" % (p.returncode, (out or "").strip()[-300:]), ""


def stale_of(part):
    """How old the last_ok of `part` ("sync", or "engine-update" for this file's own) may be: STALE_ROUNDS of the
    interval its cron line runs at, as apply wrote it (intervals.json, C-37 C: the instance may change it); STALE when
    that cannot be read."""
    try:
        with open(os.path.join(state_dir(), "intervals.json")) as f:
            v = json.load(f).get(part)
        if type(v) is int and 60 <= v <= 86400:
            return STALE_ROUNDS * v
    except (OSError, ValueError, AttributeError):
        pass
    return STALE


def watch_sync():
    """The sync's last_ok, once it has one: more than stale_of("sync") old → told."""
    t = last_ok("sync")
    if t is not None and time.time() - t > stale_of("sync"):
        alert("update", "sync-stale", "引擎和實例的同步已經 %d 分鐘沒有成功" % ((time.time() - t) // 60))


UNTRACKED_IN_THE_WAY = re.compile(r"untracked working tree files would be (?:overwritten|removed)")
CHANGES_IN_THE_WAY = re.compile(r"local changes to the following files would be overwritten")


def ff_cause(said):
    """(for the log, for the person) of a fast-forward git refused, from what git said: a file not in the engine
    with a name the new version adds, or edits not committed to files it changes (C-57: the words were wrong for the
    first; manager 2026-10-19: the person may not be an engineer, so no "saved" or commit); else that it did not work."""
    files = [l.strip() for l in said.splitlines() if l.startswith(("\t", "        "))][:5]
    names = "（%s）" % "、".join(files) if files else ""
    if UNTRACKED_IN_THE_WAY.search(said):
        return ("沒追蹤的檔跟新版要加的檔同名",
                "新版本要加的檔案%s，這台已經有同名的檔（不是引擎原本就有的）。為了不蓋掉它，這次沒有更新，現在照舊版跑。"
                "要更新：把那個檔改名或移走，或到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」。" % names)
    if CHANGES_IN_THE_WAY.search(said):
        return ("還沒 commit 的修改跟新版改到同一個檔",
                "你自己改過的檔%s，新版本也改了同一個檔。為了不蓋掉你的修改，這次先不更新，現在照舊版跑，你的修改都還在。"
                "要更新：到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」。" % names)
    return ("快轉被 git 拒絕", "換到新版本這一步沒成功，這次沒有更新，現在照舊版跑，你的東西都沒動。"
            "要處理：到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」。")


def tell_detached(head):
    """Said once per HEAD (detached-told: {head}) that an engine detached from its branch is never updated (C-57: it
    used to stay silent); told again when HEAD changes. Not out: tried again next round."""
    path = os.path.join(state_dir(), "detached-told")
    try:
        with open(path) as f:
            if json.load(f).get("head") == head:
                return
    except (OSError, ValueError, AttributeError):
        pass
    if not send("⚠️ 引擎更新：這台的引擎現在停在一個固定的版本上，沒有跟著平常更新的那條線走，所以不會自己更新，也不會通知有新版；"
                "你的東西都沒動。要恢復：到 #setup 跟 Claude 說「引擎停在固定版本了，幫我接回去」。（細節：detached HEAD %s）" % head[:12]):
        return
    try:
        os.makedirs(state_dir(), exist_ok=True)
        with open(path + ".tmp", "w") as f:
            json.dump({"head": head, "at": time.time()}, f)
        os.replace(path + ".tmp", path)
    except OSError:
        pass


def one_round(force_release=False):
    """0 fine (or skipped on purpose), 1 something to tell (told). force_release: update.py --release."""
    kind, why = verdict()
    if kind == "other":  # another checkout: not this one's business; nothing written, the reason said on stdout
        print("不是這台的正式引擎：%s；什麼都沒做" % why)
        return 0
    if kind != "ok":
        SKIPPED.append("判斷不了這份是不是這台的引擎（%s）" % why)
        return 0
    if not os.path.lexists(os.path.join(os.path.realpath(ENGINE), ".git")):  # unpacked from an archive: no upstream
        REACHED.append("not a checkout")
        SAID.append("這份引擎不是 git checkout（解開的壓縮檔），不會自動更新：新版請重新下載")
        mark_ok("update", {"note": "不是 git checkout，不會自動更新", "checkout": False})  # doctor shows it
        return 0
    rnd = Round()
    gd = rnd.git_dir()
    if gd is None:
        SKIPPED.append("讀不到 git 資料")
        alert("update", "not-a-repo", "%s 讀不到 git 資料" % ENGINE)
        return 1
    if any(os.path.exists(os.path.join(gd, n)) for n in ("rebase-merge", "rebase-apply", "index.lock")):
        SKIPPED.append("rebase 做到一半或有 index.lock（殘局）")
        return 0
    try:
        rnd.lock = open(os.path.join(gd, LOCK_NAME), "a")
        fcntl.flock(rnd.lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        SKIPPED.append(LOCK_BUSY)
        return 0  # the sync or a deploy has it: next round
    mode, start = None, False
    try:
        if force_release:  # asked for now: the newest release, this round only; the setting is left as it is
            mode, problem = "release", None
        else:
            mode, problem = update_mode()
        if problem:
            alert("update", "mode", "讀不到引擎更新的模式（%s），這一輪照 %s" % (problem, mode))
        elif not force_release:
            remember_mode(mode)
        if rnd.git("symbolic-ref", "-q", "HEAD").returncode != 0:  # detached: never updated, so said once (C-57)
            head = rnd.git("rev-parse", "HEAD").stdout.strip()
            REACHED.append("detached")
            SAID.append("引擎停在固定的版本上（detached HEAD %s），不會更新；要更新先切回分支（git checkout main）" % head[:12])
            mark_ok("update", {"note": "detached HEAD，不會更新", "head": head[:12]})
            tell_detached(head)
            return 0
        if rnd.git("rev-parse", "--abbrev-ref", "--symbolic-full-name", "@{u}").returncode != 0:
            REACHED.append("no upstream")
            SAID.append("沒有上游，不用更新")
            mark_ok("update", {"note": "沒有上游，不用更新"})
            return 0
        r = rnd.git("fetch", "-q", "--tags")  # branch too: the release tags tell_updated looks for
        if r.returncode != 0:
            SKIPPED.append("fetch 失敗")
            alert("update", "fetch", "連不上放新版本的地方，這次沒能檢查有沒有新版，下一輪會再試；你的東西都沒動。"
                  "（細節：引擎 fetch 失敗：%s）" % (r.stderr.strip() or r.stdout.strip())[:200])
            return 1
        head = rnd.git("rev-parse", "HEAD").stdout.strip()
        if force_release:
            start = release_in(rnd)[0]
        if mode == "branch":
            up = rnd.git("rev-parse", "@{u}").stdout.strip()
        else:
            rel = latest_release(rnd)
            newer = rel if rel and head and rnd.git("merge-base", "--is-ancestor", rel[1], head).returncode != 0 else None
            if mode == "notify" or not newer:
                told = available(newer if mode == "notify" else None, rnd)
                REACHED.append(mode)
                SAID.append("有新版 %s（core.update 是 notify：不自動更新%s）" % (newer[0], "；已推播" if told else "") if newer
                            else "沒有比現在新的發佈版本（%s）" % (rel[0] if rel else "還沒有任何發佈版本"))
                mark_ok("update", {"head": head[:12], "mode": mode})
                return 0
            up = newer[1]
        available(None)
        if not head or not up:
            SKIPPED.append("讀不到 HEAD 或上游的版本")
            alert("update", "rev-parse", "讀不到 HEAD 或上游的版本")
            return 1
        was = blocked_hash()
        if was and rnd.git("merge-base", "--is-ancestor", was, head).returncode == 0:  # came in while not checking
            alert("update", "took-refused", "HEAD 已經包含擋過的 %s（update 沒在檢查時被拉進來，或有人用了 --take-refused），"
                  "這一輪重新判斷、清掉紀錄" % was[:12])
        if head == up or rnd.git("merge-base", "--is-ancestor", up, head).returncode == 0:
            REACHED.append("nothing new")
            SAID.append("沒有新的（%s）" % head[:12])
            mark_ok("update", {"head": head[:12]})
            return 0
        changed = [f for f in rnd.git("diff", "--name-only", "--diff-filter=d", head, up).stdout.splitlines() if f]
        why = check_tree(rnd, up, sorted(set(changed) | set(ALWAYS)))
        if why:  # refused, and remembered: neither the sync nor a launcher pulls this hash while update.py is alive
            BLOCKED.append((up, why))
            REACHED.append("refused")
            SAID.append("擋下 %s：%s" % (up[:12], " ".join(why.split())[:150]))  # one line: the log takes one
            alert("update", "check", "新版本沒通過檢查，這次沒有更新，現在照舊版跑，你的東西都沒動；等修好的版本出來會再試。"
                  "（細節：上游 %s 沒通過檢查：%s）" % (up[:12], why))
            return 1
        note = ""
        loose = in_the_way(rnd, head, up)
        if loose:  # one of the person's own files, not tracked, would be refused or (ignored) overwritten: not moved
            names = "（%s）" % "、".join(loose[:5] + (["…"] if len(loose) > 5 else []))
            SKIPPED.append("沒追蹤或被忽略的檔跟新版要加的檔同名")
            alert("update", "in-the-way", "你自己的檔%s跟新版要加的檔同名，這次先不更新，現在照舊版跑，你的檔都還在；"
                  "到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」。（細節：更新到 %s 前，%s 不在 git 裡（沒追蹤或被忽略），"
                  "快轉會被拒或把它蓋掉）" % (names, up[:12], "、".join(loose[:10])))
            return 1
        before = mark_before(rnd, head, None if mode == "branch" else newer[0])
        if rnd.git("merge-base", "--is-ancestor", head, up).returncode == 0:
            r = rnd.git("merge", "--ff-only", "-q", up)
            if r.returncode != 0:
                drop_before(rnd, before)
                said = (r.stderr.strip() or r.stdout.strip())
                SKIPPED.append("快轉失敗（%s）" % ff_cause(said)[0])
                alert("update", "ff", "%s（細節：快轉到 %s 失敗：%s）" % (ff_cause(said)[1], up[:12], said[:200]))
                return 1
        else:
            why, note = rebase_in_child(rnd, None if mode == "branch" else up)
            if why:
                drop_before(rnd, before)
                SKIPPED.append("有本機 commit，rebase 沒成功")
                alert("update", "rebase", "這台的引擎有你自己改過的地方，新版本也改了同一段，接不起來。為了不蓋掉你的修改，這次先不更新，"
                      "現在照舊版跑，你的修改都還在。要更新：到 #setup 跟 Claude 說「引擎更新卡住了，幫我處理」。"
                      "（細節：有本機 commit，更新到 %s 沒成功：%s）" % (up[:12], why))
                return 1
        REACHED.append("updated")
        now = rnd.git("rev-parse", "HEAD").stdout.strip()
        note_after(before, head, now)
        restart = after_move(rnd, head, now)
        SAID.append("更新了 %s → %s%s%s" % (head[:12], now[:12], "；" + note if note else "",
                                         "；機器人會自己重啟套用新版（#audit 看得到）" if restart else ""))
        mark_ok("update", {"head": now[:12]})
        return 0
    finally:
        if mode in ("release", "branch"):  # whatever this round did, and whoever moved HEAD; an asked-for one too
            try:
                tell_updated(rnd, start)
            except Exception as e:
                alert("update", "updated-release", "告知引擎已更新到新版這一步出錯（%s: %s）" % (type(e).__name__, e), notify=False)
        rnd.lock.close()


LOCK_BUSY = "git 鎖被別人拿著"
LOCKED = 75  # update.py --release's exit when the git lock was held and nothing was done (EX_TEMPFAIL)


def main(argv):
    if argv not in ([], ["--release"]):
        print(__doc__)
        return 2
    del BLOCKED[:], REACHED[:], SKIPPED[:], SAID[:], ALERTED[:]  # this round's own
    try:
        code = one_round(force_release=bool(argv))
    except Exception as e:  # written down; no DONE line, so cron_update.py fast-forwards by plain git
        alert("update", "error", "引擎更新這一輪出錯（%s: %s）" % (type(e).__name__, e))
        return 1
    try:  # C-08: the bot's packages, with the git lock let go (one_round's), for this move or an earlier one
        done = install_pending()
        if done:
            SAID.append(done)
    except Exception as e:
        alert("update", "deps", "裝機器人的套件這一步出錯（%s: %s）" % (type(e).__name__, e))
    if REACHED:  # only a round that compared with the upstream; a skipped one leaves the record (and its block) as it was
        mark_alive()
        try:
            os.remove(os.path.join(state_dir(), "last_skip-update"))
        except OSError:
            pass
    elif SKIPPED:
        mark_skipped(SKIPPED[-1])
    try:
        watch_sync()
    except Exception:
        pass
    # the round in one line, just before DONE: cron_update.py writes it into cron-update.log (manager 2026-10-07)
    print(" ".join(("；".join(SAID) if SAID else "沒有更新：%s" % SKIPPED[-1] if SKIPPED else "沒有更新").split()))
    print("ALT-UPDATE-DONE")  # cron_update.py: this round came to its end
    if (argv or os.environ.get("ALT_UPDATE_LOCKED_EXIT")) and not REACHED and SKIPPED and SKIPPED[-1] == LOCK_BUSY:
        return LOCKED  # asked for (--release, or ops/apply.py's plain round) and nothing done: the asker tries again
    return code


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
