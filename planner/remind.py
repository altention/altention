#!/usr/bin/env python3
"""Reminders from schedule files that the Claude they are for can edit itself (every 5 minutes from cron).

  python3 planner/remind.py                    send whatever is due in planner's own schedule files
  python3 planner/remind.py FILE...            the same for these files
  python3 planner/remind.py --check [FILE...]  only say what each file schedules, and whether it parses
  python3 planner/remind.py --where            the file(s) to write a reminder into, one per line (the default made)
  python3 planner/remind.py --secretary        the secretary's folder (planner.folder when set, a folder of the owner's
                                               left as it is; else {data}/planner), made on first use: 700, with
                                               CLAUDE.md (from planner/CLAUDE.template.md) and an empty plan.md; and
                                               the briefing and the reviews put into planner's own schedule file

planner's own schedule files: the default {data}/planner/reminders.json, made on first use (folder 700, file 600) so
that reminders work on a new install with nothing set (owner's install 2026-10-15: the setting was never made, so
reminders never ran), then those in the instance's planner.schedule_files (a path or a list of them); the default
is read whatever that setting lists (manager 2026-10-17). The same reading
as apply and the scheduler (altention/plugin.py schedule_files).

A schedule file is read as the scheduler reads it (altention/scheduler.py parse), for example:
  {"items": [{"name": "dentist", "when": {"once": "2026-10-20 09:00"}, "do": {"message": "…", "channel": "chat"}},
             {"name": "lesson", "when": {"at": "00:30", "days": "daily"}, "do": {"message": "…", "channel": "demo"}},
             {"name": "weekly", "when": {"at": "20:30", "days": ["sun"]}, "do": {"message": "…", "channel": "demo"}}]}
The older form is read too: {"channel": "demo", "items": [{"name", "enabled", "at", "days", "text"}]}; a bare list
of items is a schedule. Only items that send a message are served here. Times are in the instance's core.timezone
(as the schedules), else this machine's; 24-hour. An "at" item is sent once per day, the first time this runs at or
after its time; a "once" item once; neither if that was more than LATE hours ago (the machine was off).
An item with no channel goes to planner's own channel (planner.channel: the secretary's, #secretary on a new install).
What was sent is kept in ~/.cache/remind-state.json.
The secretary (#secretary, a Claude working in {data}/planner) gets the brief and the reviews; its CLAUDE.md says what
they are. They are three items of planner's own schedule file, the default one (BRIEFINGS: morning, evening, weekly;
no channel, so they go to planner.channel), put there by --secretary at the times in planner.briefings (the install asks them; each
one not set is BRIEFINGS' time), only the ones the file has no item of that name for: so the secretary changes their
times or turns them off ("enabled": false) in that file, and running --secretary again adds nothing (owner
2026-10-16: the times were fixed in plugin.json, and nobody could change them). Whenever this program makes or uses that folder, a CLAUDE.md and a plan.md that are not there are put there
(never written over: the owner may change them); so a machine installed before has them within five minutes.
"""
import datetime
import json
import os
import subprocess
import sys
import time

HOME_REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HERE = os.path.dirname(os.path.abspath(__file__))
# Kept in the first public release: the scheduler only records so far, and this sends the schedule files'
# reminders (manager 2026-10-14). When the scheduler runs them, this program and its state go.
STATE = os.path.expanduser("~/.cache/remind-state.json")
LATE = 3
EMPTY = {"items": []}
FOLDER = "{data}/planner"  # the secretary's folder: its CLAUDE.md, plan.md and the default reminders.json
PLAN_EMPTY = "# 短期計畫\n\n## 今天\n\n## 這週\n\n## 之後\n\n## 做完了\n"
BRIEFINGS = [  # (name, the time when planner.briefings does not say, the message)
    ("morning", {"at": "08:00", "days": "daily"}, "⏰ 每日早報：照 CLAUDE.md 寫今天的早報。"),
    ("evening", {"at": "22:00", "days": "daily"}, "🌙 晚間回顧：照 CLAUDE.md 問我今天做完了什麼，更新短期計畫。"),
    ("weekly", {"at": "21:00", "days": ["sun"]}, "📅 每週回顧：照 CLAUDE.md 整理這週、規劃下週。"),
]


def write_new(path, text):
    """`text` into `path` (600) when there is no such file; never written over."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def named_folder():
    """planner.folder: a folder of the owner's own the secretary works in (an installation that had one before the
    planner gave it {data}/planner; ours keeps it in ~/life), or None when not set. It is theirs: nothing is made,
    changed or written in it."""
    from altention import config
    value = config.setting("planner.folder")
    return os.path.expanduser(value.strip()) if isinstance(value, str) and value.strip() else None


def secretary_folder(folder=None, seed=None):
    """The secretary's folder, made private (700) with CLAUDE.md and plan.md put in when missing; its path. A folder
    named by planner.folder is returned as it is (it has to be there). seed(folder): called once the folder is there,
    before its first commit (--secretary: the briefings, add_briefings), returning the file it changed or None; a
    changed file in a folder that is a repo already is committed."""
    if folder is None:
        named = named_folder()
        if named is not None:
            if not os.path.isdir(named):
                raise OSError("planner.folder 指定的 %s 不是一個資料夾" % named)
            if seed:
                seed(named)  # nothing of planner's is committed in an owner's own folder
            return named
        from altention import config, plugin
        folder = os.path.expanduser(plugin.expand(FOLDER, HERE, config.settings()))
    fresh = not os.path.isdir(folder) or not os.listdir(folder)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)  # already there (made 755 by hand or by an older engine): made private
    remote = secretary_remote()
    cloned = False
    if fresh and remote and not is_repo(folder):  # a new machine: the plan back from its backup (into the 700 folder)
        cloned = clone_back(folder, remote)
    with open(os.path.join(HERE, "CLAUDE.template.md"), encoding="utf-8") as f:
        write_new(os.path.join(folder, "CLAUDE.md"), f.read())
    write_new(os.path.join(folder, "plan.md"), PLAN_EMPTY)
    changed = seed(folder) if seed else None
    make_repo(folder, cloned)
    inside = changed and os.path.dirname(os.path.abspath(changed)) == os.path.abspath(folder)
    if inside and is_repo(folder) and git_in(folder, "status", "--porcelain", "--", os.path.basename(changed)).stdout.strip():
        commit_file(folder, changed, "secretary: the briefing and the reviews")  # a repo already: make_repo did not
    attach_remote(folder, remote)
    return folder


def secretary_remote():
    """sync.dirs.planner.remote, or "" when not set (or the settings cannot be read)."""
    try:
        from altention import config
        value = config.setting("sync.dirs.planner.remote")
        return value.strip() if isinstance(value, str) else ""
    except Exception:
        return ""


def git_in(folder, *args, timeout=60):
    try:
        return subprocess.run(["git", "-C", folder] + list(args), stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                              universal_newlines=True, timeout=timeout, stdin=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError) as e:
        return subprocess.CompletedProcess(args, 1, "", str(e))


def said(r):
    lines = [l.strip() for l in (r.stderr or "").splitlines() if l.strip()]
    return " / ".join(lines)[:200] if lines else "rc=%d" % r.returncode


def is_repo(folder):
    return os.path.isdir(os.path.join(folder, ".git"))


def clone_back(folder, remote):
    """True when the backup came back into folder: cloned only when ops/remote.py checks it (never a public repo,
    the engine's or some other data's); said on stderr when not, and the folder is made new here."""
    try:
        sys.path.insert(0, os.path.join(HOME_REPO, "ops"))
        import remote as altremote
        ok, text = altremote.clone("planner", remote, folder)
    except Exception as e:
        ok, text = False, "%s: %s" % (type(e).__name__, e)
    if not ok:
        print("remind：%s，先在這台建新的" % text, file=sys.stderr)
    return ok


def make_repo(folder, cloned=False):
    """The secretary's folder as a git repo (C-26: the secretary commits what it changes, the sync commits again):
    `git init`, a .gitignore and a first commit of what is there. Nothing moved or removed. Said on stderr when
    git cannot (the folder works without it). Just cloned (an empty backup repo, or one from before the
    .gitignore): the .gitignore and a commit of what is not tracked yet, on main."""
    if is_repo(folder) and not cloned:
        return
    write_new(os.path.join(folder, ".gitignore"), "*.tmp\n")
    steps = [["init", "-q"], ["checkout", "-q", "-b", "main"], ["add", "-A"]]
    if cloned:
        empty = git_in(folder, "rev-parse", "-q", "--verify", "HEAD").returncode != 0
        steps = ([["checkout", "-q", "-B", "main"]] if empty else []) + [["add", "-A"]]
        if git_in(folder, "status", "--porcelain").stdout.strip() == "":
            return
    for args in steps:
        r = git_in(folder, *args)
        if r.returncode != 0:
            print("remind：秘書的資料夾做不成 git repo（git %s：%s）" % (args[0], said(r)), file=sys.stderr)
            return
    who = [] if git_in(folder, "config", "user.email").stdout.strip() else ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]
    r = git_in(folder, *(who + ["commit", "-q", "-m", "secretary: the folder as it was made on this machine"]))
    if r.returncode != 0:
        print("remind：秘書的資料夾第一次 commit 失敗（%s）" % said(r), file=sys.stderr)


def commit_file(folder, path, message):
    """`path` committed in the secretary's repo (C-26: the folder is kept committed); said on stderr when git cannot."""
    who = [] if git_in(folder, "config", "user.email").stdout.strip() else ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]
    for args in (["add", "--", os.path.basename(path)], who + ["commit", "-q", "-m", message, "--", os.path.basename(path)]):
        r = git_in(folder, *args)
        if r.returncode != 0:
            print("remind：%s 沒有 commit（%s）" % (os.path.basename(path), said(r)), file=sys.stderr)
            return


def attach_remote(folder, remote):
    """Its origin from the setting when it has none (checked first: ops/remote.py attach, C-25) and the pre-push
    hook; said on stderr unless all is as it should be."""
    if not remote and git_in(folder, "remote").stdout.strip() == "":
        return
    try:
        sys.path.insert(0, os.path.join(HOME_REPO, "ops"))
        import remote as altremote
        what, text = altremote.attach("planner", remote, folder)
    except Exception as e:
        what, text = "error", "%s: %s" % (type(e).__name__, e)
    if what not in ("same", "none"):
        print("remind：%s" % text, file=sys.stderr)


def own_files():
    """(planner's schedule files, the default one or None): the setting, else the default, as plugin.py reads them."""
    from altention import config, plugin
    with open(os.path.join(HERE, "plugin.json"), encoding="utf-8") as f:
        d = json.load(f)
    settings = config.settings()
    return plugin.schedule_files(d, HERE, settings), plugin.default_schedule_file(d, HERE, settings)


def make_default(path):
    """The default schedule file, made empty the first time (the file 600, in the secretary's folder made as
    secretary_folder() makes it); never written over. With planner.folder set the secretary works there, not in
    this folder: only the folder (700) and the file, no CLAUDE.md, plan.md or repo (#code-review of 044c5e8: the
    default file read whatever the settings say made a second secretary's folder on such a machine)."""
    if named_folder() is not None:
        folder = os.path.dirname(path)
        os.makedirs(folder, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    else:
        secretary_folder(os.path.dirname(path))
    write_new(path, json.dumps(EMPTY) + "\n")


def briefing_times():
    """{name: when} for BRIEFINGS from planner.briefings ({"morning": {"at": "07:30", "days": "daily"}, …}), each one
    not set taken from BRIEFINGS; a time that cannot be read is a ValueError naming it (nothing is written then)."""
    from altention import config, scheduler
    chosen = config.setting("planner.briefings")
    if chosen is None:
        chosen = {}
    if not isinstance(chosen, dict):
        raise ValueError("planner.briefings 要是物件，例如 {\"morning\": {\"at\": \"07:30\", \"days\": \"daily\"}}")
    out = {}
    for name, default, _ in BRIEFINGS:
        when = chosen.get(name, default)
        try:
            scheduler.parse({"items": [{"name": name, "when": when, "do": {"message": "x"}}]})
        except scheduler.ScheduleError as e:
            raise ValueError("planner.briefings.%s 讀不懂（%s）" % (name, e))
        if "at" not in when:
            raise ValueError("planner.briefings.%s 要寫 at 和 days（每天或每週的時間）" % name)
        out[name] = when
    return out


def add_briefings(path, default=None):
    """The BRIEFINGS items the schedule file at `path` has no item of that name for, added (600, the rest of the file
    as it was); the names added. The default file (`default`) not there yet is made as make_default makes it (as the
    5-minute round makes it, planner.folder set or not); any other file not there, or one that cannot be read, is
    left alone (ValueError). One added whose time today is already past and still within LATE hours is marked sent
    today (mark_sent): the old cron line, or nobody, had its turn, and the next round must not send it again
    (#code-review of 5dcd0a1: moved at 08:30, the morning brief came twice)."""
    times = briefing_times()
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        if path != default:
            raise ValueError("%s 不存在，沒有加早報和回顧" % path)
        if os.path.isdir(os.path.dirname(path)):  # the secretary's folder being made (--secretary): before its commit
            write_new(path, json.dumps(EMPTY) + "\n")
        else:
            make_default(path)
    with open(path, encoding="utf-8") as f:
        try:
            data = json.load(f)
        except ValueError as e:
            raise ValueError("%s 讀不懂（%s），沒有加早報和回顧" % (path, e))
    items = data if isinstance(data, list) else data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise ValueError("%s 沒有 items 清單，沒有加早報和回顧" % path)
    there = {it.get("name") for it in items if isinstance(it, dict)}
    added = [name for name, _, _ in BRIEFINGS if name not in there]
    if not added:
        return []
    items.extend({"name": name, "when": times[name], "do": {"message": text}} for name, _, text in BRIEFINGS if name in added)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write("\n")
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)
    mark_sent(path, added)
    return added


def zone_now():
    """(the zone remind goes by, the time there now), as main reads them."""
    from altention import scheduler
    zone, _, problem = scheduler.timezone()
    if zone is None:  # a setting that cannot be used: this machine's own zone, and why
        print("remind：時區設定不能用，照這台機器的時區：%s" % problem, file=sys.stderr)
        zone = scheduler.SystemZone()
    return zone, zone.local(time.time())


def mark_sent(path, names):
    """The items `names` of the file at `path` that would be due now with nothing sent: written into STATE as sent
    today, the rest of STATE as it was."""
    _, now = zone_now()
    late = [it for it in load(path)["items"] if it["name"] in names and due(it, now, None)]
    if not late:
        return
    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    for it in late:
        state["%s#%s" % (os.path.abspath(os.path.expanduser(path)), it["name"])] = now.date().isoformat()
    os.makedirs(os.path.dirname(STATE), exist_ok=True)
    with open(STATE, "w") as f:
        json.dump(state, f)


def own_channel():
    """planner.channel (the secretary's channel), for an item that names none; None when not set or unreadable."""
    try:
        from altention import config
        value = config.setting("planner.channel")
    except Exception:
        return None
    return str(value).strip() if isinstance(value, (str, int)) and not isinstance(value, bool) and str(value).strip() else None


def when_text(item):
    """An item's time as a person reads it: 每天 21:00, 每週日 21:00, 2026-10-20 09:00 (one time), 每 10 分鐘."""
    if item["kind"] == "once":
        return "%s（一次）" % item["once"].strftime("%Y-%m-%d %H:%M")
    if item["kind"] == "at":
        clock = "%02d:%02d" % (item["at"] // 60, item["at"] % 60)
        if item["days"] is None:
            return "每天 " + clock
        return "每週%s %s" % ("、".join("一二三四五六日"[d] for d in item["days"]), clock)
    return "每 %d 分鐘（這裡不送，只有訊息的定時和一次性提醒會送）" % (item["every"] // 60)


def load(path):
    from altention import scheduler
    with open(os.path.expanduser(path), encoding="utf-8") as f:
        return scheduler.parse(json.load(f))


def due(item, now, sent):
    """Is this item due now, given what was last sent for it (a date, "once", or None)?"""
    if not item.get("enabled", True) or "message" not in item["do"]:
        return False
    if item["kind"] == "once":
        return item["once"] <= now < item["once"] + datetime.timedelta(hours=LATE) and sent != "once"
    if item["kind"] != "at" or (item["days"] is not None and now.weekday() not in item["days"]):
        return False
    at = now.replace(hour=item["at"] // 60, minute=item["at"] % 60, second=0, microsecond=0)
    return at <= now < at + datetime.timedelta(hours=LATE) and sent != now.date().isoformat()


def main(argv):
    check, where = "--check" in argv, "--where" in argv
    files = [a for a in argv if a not in ("--check", "--where")]
    sys.path.insert(0, HOME_REPO)
    if argv == ["--secretary"]:
        problems = []

        def seed(folder):
            try:  # into the secretary's own file, never into one another channel keeps (manager 2026-10-17)
                files, default = own_files()
                target = default or (files[0] if files else None)
                if target and add_briefings(target, default):
                    return os.path.expanduser(target)
            except Exception as e:  # a time or a file that cannot be read: said below, nothing added
                problems.append(e)
            return None
        try:
            folder = secretary_folder(seed=seed)
        except Exception as e:  # settings not readable, a folder that cannot be made: said
            print("remind：秘書的資料夾建不起來或找不到（%s）" % e, file=sys.stderr)
            return 1
        for e in problems:  # the folder printed all the same: the secretary works without them
            print("remind：早報和回顧沒有加進排程檔（%s）" % e, file=sys.stderr)
        print(folder)
        return 1 if problems else 0
    if not files:
        try:
            files, default = own_files()
        except Exception as e:  # settings or plugin.json not readable: said, nothing sent
            print("remind：讀不到要看哪些排程檔（%s）" % e, file=sys.stderr)
            return 1
        if default and not check:  # --where makes it: the Claude asking is about to write into it
            try:
                make_default(default)
            except OSError as e:
                print("remind：建不起來預設的排程檔 %s（%s）" % (default, e), file=sys.stderr)
    if where:
        for path in files:
            print(path)
        return 0
    try:
        with open(STATE) as f:
            state = json.load(f)
    except (OSError, ValueError):
        state = {}
    from altention import scheduler
    zone, now = zone_now()
    for path in files:
        if not os.path.exists(os.path.expanduser(path)):  # nobody has written one yet
            print("%s：還沒有提醒（檔案還不存在）" % path)
            continue
        try:
            data = load(path)
        except (OSError, ValueError, scheduler.ScheduleError) as e:
            print("%s：讀不懂（%s）" % (path, e))
            continue
        for it in data["items"]:
            key = "%s#%s" % (os.path.abspath(os.path.expanduser(path)), it["name"])
            if check:
                print("%s · %s：%s %s" % (path, it["name"], "開" if it.get("enabled", True) else "關", when_text(it)))
            elif due(it, now, state.get(key)):
                channel = it["do"].get("channel") or own_channel()
                if not channel:
                    print("%s · %s：沒有寫 channel，planner.channel 也沒設，不知道送去哪個頻道" % (path, it["name"]), file=sys.stderr)
                    continue
                r = subprocess.run([os.path.join(HOME_REPO, "bin", "altention"), "dc", "post", channel, it["do"]["message"]])
                if r.returncode == 0:
                    state[key] = "once" if it["kind"] == "once" else now.date().isoformat()
    if not check:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        with open(STATE, "w") as f:
            json.dump(state, f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
