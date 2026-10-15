#!/usr/bin/env python3
"""Reminders from schedule files that the Claude they are for can edit itself (every 5 minutes from cron).

  python3 planner/remind.py                    send whatever is due in planner's own schedule files
  python3 planner/remind.py FILE...            the same for these files
  python3 planner/remind.py --check [FILE...]  only say what each file schedules, and whether it parses
  python3 planner/remind.py --where            the file(s) to write a reminder into, one per line (the default made)
  python3 planner/remind.py --secretary        the secretary's folder ({data}/planner), made on first use: 700, with
                                               CLAUDE.md (from planner/CLAUDE.template.md) and an empty plan.md

planner's own schedule files: the instance's planner.schedule_files (a path or a list of them), else the default
{data}/planner/reminders.json, made on first use (folder 700, file 600) so that reminders work on a new install
with nothing set (owner's install 2026-10-15: the setting was never made, so reminders never ran). The same reading
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
they are. Whenever this program makes or uses that folder, a CLAUDE.md and a plan.md that are not there are put there
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


def write_new(path, text):
    """`text` into `path` (600) when there is no such file; never written over."""
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def secretary_folder(folder=None):
    """The secretary's folder, made private (700) with CLAUDE.md and plan.md put in when missing; its path."""
    if folder is None:
        from altention import config, plugin
        folder = os.path.expanduser(plugin.expand(FOLDER, HERE, config.settings()))
    os.makedirs(folder, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)  # already there (made 755 by hand or by an older engine): made private
    with open(os.path.join(HERE, "CLAUDE.template.md"), encoding="utf-8") as f:
        write_new(os.path.join(folder, "CLAUDE.md"), f.read())
    write_new(os.path.join(folder, "plan.md"), PLAN_EMPTY)
    return folder


def own_files():
    """(planner's schedule files, the default one or None): the setting, else the default, as plugin.py reads them."""
    from altention import config, plugin
    with open(os.path.join(HERE, "plugin.json"), encoding="utf-8") as f:
        d = json.load(f)
    settings = config.settings()
    return plugin.schedule_files(d, HERE, settings), plugin.default_schedule_file(d, HERE, settings)


def make_default(path):
    """The default schedule file, made empty the first time (the file 600, in the secretary's folder made as
    secretary_folder() makes it); never written over."""
    secretary_folder(os.path.dirname(path))
    write_new(path, json.dumps(EMPTY) + "\n")


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
        try:
            print(secretary_folder())
        except Exception as e:  # settings not readable, a folder that cannot be made: said
            print("remind：秘書的資料夾建不起來或找不到（%s）" % e, file=sys.stderr)
            return 1
        return 0
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
    zone, _, problem = scheduler.timezone()
    if zone is None:  # a setting that cannot be used: this machine's own zone, and why
        print("remind：時區設定不能用，照這台機器的時區：%s" % problem, file=sys.stderr)
        zone = scheduler.SystemZone()
    now = zone.local(time.time())
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
