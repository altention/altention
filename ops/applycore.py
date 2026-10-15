"""Write what plan found missing or old-marked into the crontab and ~/.claude/settings.json (stage 3a.7). Only
`plugins.py apply --write` writes; without --write it shows what it would do.

What it changes, and only that:
- our cron lines that plan found the same: " # altention:<plugin>" is put at the end (an old marker at the end is
  replaced); the command itself, time fields included, stays byte for byte. An old-format line is claimed this way
  only when its words are the declared ones (quoting and ~/$HOME aside: manager 2026-10-06);
- missing lines and hooks: added (lines at the end, a hook into the group with its matcher), marked
  "# altention:<plugin>/<name>";
- a line plan found replaced (an item's earlier line, ours for sure: our marker, the same plugin and item): the new
  line written in its place, so one item never has two lines (owner 2026-10-15: #chat and #secretary both woken);
- a line whose time differs: its five time fields changed only when named with --allow-time (today only the Mac's
  chatlog minute, by decision);
- extra lines: removed only when they carry our new marker AND that plugin's plugin.json was read and it is plainly
  off or disabled here (manager 2026-10-06). Hooks carry no marker: apply never removes a hook.
Never touched: other people's lines, old-looking lines that match nothing exactly ("unclaimed"), our lines commented
out by hand (paused), everything of a plugin whose place is unknown. A crontab or settings.json that cannot be read
is not written.

The crontab is handled as bytes from `crontab -l` to `crontab -` (stdin): split at b"\\n" only, a line decoded only to
tell whether it is ours (one that is not UTF-8 is someone else's), every line not meant to change written back as
the very bytes read. (A text read splits at form feeds, lone carriage returns, U+2028 and more, and the write would
quietly change someone else's line.)

Hard guards for every write (manager 2026-10-06), crontab and settings.json alike:
  a. every line this run did not mean to change is, after writing and reading back, the same bytes in the same order;
     else the backup is put back at once and it fails;
  b. the new content loses no more lines than the ones it means to remove;
  c. the backup is written and read back equal to what was read first, or nothing is written;
  and both are read once more just before writing: changed since (someone, or Claude Code, writing) means not this
  time. One apply at a time (a lock). Backups: ~/.local/state/altention/backup/<time>/ (700, files 600; the newest
  BACKUP_KEEP kept); `plugins.py restore <time>` puts one back. Standard library only, Python 3.8.
"""
import copy
import fcntl
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time

import plancore

MARK = "# altention:"
BACKUP_KEEP = 20


class Refused(Exception):
    """A guard said no: nothing was written (or what was written has been put back)."""


def state_root(machine=None):
    """apply's own folder: per machine, so that machines sharing a home never write over each other's backups."""
    base = os.path.join(os.path.expanduser("~/.local/state/altention"), "apply")
    return os.path.join(base, machine or MACHINE[0])


MACHINE = ["this-machine"]  # set by plugins.py to the machine's name in the list before anything is written


def backup_root():
    return os.path.join(state_root(), "backup")


def clean_leftovers():
    """Temporary crontab files a killed apply left (they hold the whole crontab): removed under the apply lock."""
    try:
        for name in os.listdir(state_root()):
            if name.startswith("tmp") and name.endswith(".crontab"):
                os.remove(os.path.join(state_root(), name))
    except OSError:
        pass


class home_lock(object):
    """`with home_lock():` a lock on this home's ~/.claude/settings.json, shared by every machine using this home
    (the per-machine apply lock is not enough there). Waits up to 60 seconds, then Refused."""

    def __enter__(self):
        base = os.path.dirname(state_root())  # ~/.local/state/altention/apply: the home's, not a machine's
        os.makedirs(base, mode=0o700, exist_ok=True)
        self.f = open(os.path.join(base, "settings.lock"), "w")
        until = time.time() + 60
        while True:
            try:
                fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError:
                if time.time() > until:
                    self.f.close()
                    raise Refused("settings.json 的鎖 60 秒內拿不到（共用這個家目錄的別台正在 apply？），這次沒寫")
                time.sleep(1)

    def __exit__(self, *exc):
        self.f.close()
        return False


def clean_if_free():
    """At the start of every apply, writing or not: leftovers a killed apply left are removed, when no other apply is
    running (its temporary file is in use then). Makes no folder."""
    path = os.path.join(state_root(), "apply.lock")
    if not os.path.exists(path):
        return
    try:
        with open(path, "a") as f:
            fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            clean_leftovers()
    except OSError:
        pass


class one_at_a_time(object):
    """`with one_at_a_time():` the apply lock (one per machine); Refused if another apply holds it. Leftover
    temporary files are cleaned up once it is held."""

    def __enter__(self):
        try:
            os.makedirs(state_root(), mode=0o700, exist_ok=True)
            self.f = open(os.path.join(state_root(), "apply.lock"), "w")
        except OSError as e:  # read-only, disk full: said, not a traceback
            raise Refused("狀態資料夾 %s 寫不進去（%s），什麼都沒寫" % (state_root(), e))
        try:
            fcntl.flock(self.f, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise Refused("另一個 apply 正在跑，這次沒做")
        clean_leftovers()
        return self

    def __exit__(self, *exc):
        self.f.close()
        return False


# COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
def mark_line(raw, plugin_name, mark=None):
    """Our marker at the end of a raw line: an old marker or our own at the very end is replaced, anything else is
    kept as it is and the marker follows (the command's bytes never change). A line already marked for this plugin
    is left as it is (a name added later is no reason to rewrite it)."""
    t = plancore.text(raw)
    if plancore.marked(t) == plugin_name:
        return raw
    mark = mark or plugin_name
    for pattern in (plancore.NEW_MARK, plancore.OLD_MARK):
        m = pattern.search(t)
        if m:
            t = t[:m.start()].rstrip()
            break
    else:
        t = t.rstrip()
    return ("%s  %s%s" % (t, MARK, mark)).encode("utf-8")


def retime(raw, new_time):
    """A raw line with its five time fields (or one @word) replaced by `new_time`, the rest kept byte for byte."""
    t = plancore.text(raw)
    rest = t.lstrip()
    lead = t[:len(t) - len(rest)]
    for _ in range(1 if rest.startswith("@") else 5):
        i = 0
        while i < len(rest) and rest[i] not in " \t":
            i += 1
        rest = rest[i:].lstrip(" \t")
    return ("%s%s %s" % (lead, new_time, rest)).encode("utf-8")


def removable(rows):
    """Plugins whose marked lines may be removed here: plugin.json read, and plainly off or disabled."""
    return {n for n, f, d, w, r in rows if d is not None and w in ("off", "disabled")}


def new_crontab(lines, result, rows, allow_time=()):
    """(new raw lines, [action], removed, kept): plan's result applied to the raw crontab lines under the rules
    above. `kept` is the raw lines this run did not mean to change, in order (guard a)."""
    out, actions, removed, kept = [], [], 0, []
    by_text = {}
    for status, w, txt, note in result:
        if txt and status in ("same", "time differs", "extra", "elsewhere", "replaced") and (w is None or w["kind"] == "cron"):
            by_text.setdefault(txt, []).append((status, w))
    off = removable(rows)
    for raw in lines:
        t = plancore.text(raw)
        todo = by_text.get(t.strip()) if t is not None else None
        if not todo:
            out.append(raw)  # other people's, unclaimed, paused, unknown, not UTF-8: as it is
            kept.append(raw)
            continue
        status, w = todo.pop(0)
        if status == "replaced":
            new = ("%s  %s%s" % (w["text"], MARK, plancore.marker(w))).encode("utf-8")
            out.append(new)
            actions.append("換掉（%s/%s 的設定改了，同一項只留新的一行）\n     前：%s\n     後：%s" % (w["plugin"], w["name"], t, new.decode("utf-8")))
        elif status == "same":
            new = mark_line(raw, w["plugin"], plancore.marker(w))
            out.append(new)
            if new != raw:
                actions.append("標記：%s/%s\n     前：%s\n     後：%s" % (w["plugin"], w["name"], t, new.decode("utf-8")))
            else:
                kept.append(raw)
        elif status == "time differs":
            if "%s/%s" % (w["plugin"], w["name"]) in allow_time:
                new = mark_line(retime(raw, w["key"][0]), w["plugin"], plancore.marker(w))
                out.append(new)
                actions.append("改時間：%s/%s\n     前：%s\n     後：%s" % (w["plugin"], w["name"], t, new.decode("utf-8")))
            else:
                out.append(raw)
                kept.append(raw)
                actions.append("沒改（時間不同，要改請加 --allow-time %s/%s）" % (w["plugin"], w["name"]))
        elif status == "elsewhere":  # plancore.elsewhere: the item's own "on" plainly leaves this machine out
            removed += 1
            actions.append("拿掉（%s/%s 不在這台跑）：%s" % (w["plugin"], w["name"], t.strip()))
        else:  # extra: only a line with our new marker gets here
            name = plancore.marked(t)
            if name in off:
                removed += 1
                actions.append("拿掉（帶 %s 的標記，%s 在這台是 off）：%s" % (name, name, t.strip()))
            else:
                out.append(raw)
                kept.append(raw)
                actions.append("留著（帶我們的標記但沒有插件要，%s 不是明確 off）：%s" % (name, t.strip()))
    for status, w, txt, note in result:
        if status == "missing" and w["kind"] == "cron":
            out.append(("%s  %s%s" % (w["text"], MARK, plancore.marker(w))).encode("utf-8"))
            actions.append("加上：%s/%s\n     後：%s  %s%s" % (w["plugin"], w["name"], w["text"], MARK, plancore.marker(w)))
    return out, actions, removed, kept


def hook_list(settings):
    return [(event, g.get("matcher") or "", h) for event, groups in ((settings or {}).get("hooks") or {}).items()
            for g in groups or [] for h in g.get("hooks") or []]


def new_settings(settings, result):
    """(new settings, [action], added): missing hooks added; nothing is ever removed, nothing else changes."""
    new = copy.deepcopy(settings)
    actions, added = [], []
    for status, w, txt, note in result:
        if status == "missing" and w["kind"] == "hook":
            event, matcher, _, timeout = w["key"]
            groups = new.setdefault("hooks", {}).setdefault(event, [])
            group = next((g for g in groups if (g.get("matcher") or "") == matcher), None)
            if group is None:
                group = {"matcher": matcher, "hooks": []} if matcher else {"hooks": []}
                groups.append(group)
            entry = {"type": "command", "command": w["text"]}
            if timeout is not None:
                entry["timeout"] = timeout
            group["hooks"].append(entry)
            added.append(w["text"])
            actions.append("加上 hook：%s/%s" % (w["plugin"], w["name"]))
        elif status == "unclaimed" and w is None and any(txt == h.get("command") for _, _, h in hook_list(settings)):
            actions.append("留著（沒有插件要的 hook；hook 沒有標記，apply 不拿掉 hook）：%s" % txt)
    return new, actions, added


def raise_settings(settings, short):
    """(new settings, [action], raised keys): each key plancore.settings_short found short set to its at_least."""
    new = copy.deepcopy(settings)
    actions, raised = [], []
    for name, key, least, now in short:
        new[key] = least
        raised.append(key)
        actions.append("設定 %s：%s → %d（%s）" % (key, "沒有" if now is None else now, least, name))
    return new, actions, raised


def without(settings, commands):
    """settings with the hooks running `commands` taken out, and groups, events or a "hooks" left empty dropped."""
    s = copy.deepcopy(settings)
    for event, groups in list((s.get("hooks") or {}).items()):
        for g in groups:
            g["hooks"] = [h for h in g.get("hooks") or [] if h.get("command") not in commands]
        s["hooks"][event] = [g for g in groups if g.get("hooks")]
        if not s["hooks"][event]:
            del s["hooks"][event]
    if "hooks" in s and not s["hooks"]:
        del s["hooks"]
    return s


def private_write(path, data):
    """Write bytes to `path` as 600, flushed to disk."""
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(path, 0o600)


def note_added(plugins, now=None):
    """When apply last added a cron line of each of `plugins` here, in added.json beside its backups ({plugin: seconds
    since 1970}): doctor tells a line that has not had its first turn yet from one that never runs. Never raises
    (the crontab is written already; without the note doctor only knows less)."""
    if not plugins:
        return
    path = os.path.join(state_root(), "added.json")
    try:
        with open(path, encoding="utf-8") as f:
            seen = json.load(f)
    except (OSError, ValueError):
        seen = {}
    seen = seen if isinstance(seen, dict) else {}
    at = time.time() if now is None else now
    seen.update((p, at) for p in plugins)
    tmp = path + ".tmp-%d" % os.getpid()
    try:
        os.makedirs(state_root(), exist_ok=True)
        private_write(tmp, json.dumps(seen, sort_keys=True).encode("utf-8"))
        os.replace(tmp, path)
    except OSError:
        try:
            os.remove(tmp)
        except OSError:
            pass


FIRST = "-first"  # the backup taken before apply ever wrote here: never pruned


def make_backup(crontab_raw, settings_raw, stamp=None):
    """Write the backup (the crontab exactly as `crontab -l` printed it, settings.json as read; a file that was not
    there is noted as such) and read it back; (folder). A new folder every time, never one written over (two
    applies in the same second). The very first one is kept for good. Refused when it cannot be written or reads
    back different."""
    try:
        os.makedirs(backup_root(), mode=0o700, exist_ok=True)
        os.chmod(backup_root(), 0o700)
        first = not any(d for d in os.listdir(backup_root()) if os.path.isdir(os.path.join(backup_root(), d)))
        base = (stamp or time.strftime("%Y%m%d-%H%M%S")) + (FIRST if first else "")
        for n in range(100):
            folder = os.path.join(backup_root(), base if n == 0 else "%s.%d" % (base, n))
            try:
                os.mkdir(folder, 0o700)
                break
            except FileExistsError:
                continue
        else:
            raise Refused("備份資料夾的名字用完了")
        os.chmod(folder, 0o700)
        if crontab_raw == b"":  # no crontab (or an empty one): said so, restore gives back an empty one
            private_write(os.path.join(folder, "crontab.absent"), b"")
        for name, data in (("crontab", crontab_raw), ("settings.json", settings_raw)):
            if data is None:
                private_write(os.path.join(folder, name + ".absent"), b"")
                continue
            path = os.path.join(folder, name)
            private_write(path, data)
            with open(path, "rb") as f:
                if f.read() != data:
                    raise Refused("備份讀回來不一樣：%s" % path)
    except OSError as e:
        raise Refused("備份寫不進去（%s），什麼都沒寫" % e)
    prune()
    return folder


def prune():
    try:
        stamps = sorted(d for d in os.listdir(backup_root()) if os.path.isdir(os.path.join(backup_root(), d)))
    except OSError:
        return
    for d in [d for d in stamps if FIRST not in d][:-BACKUP_KEEP]:
        shutil.rmtree(os.path.join(backup_root(), d), ignore_errors=True)


def install_crontab(raw, run=subprocess.run):
    """The whole table through stdin (crontab -): no file path to get cut short or to leave behind (owner's move to
    #secretary 2026-10-15: a long path failed)."""
    try:
        r = run(["crontab", "-"], input=raw, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
        if r.returncode != 0:
            raise Refused("crontab 寫不進去：%s" % ((r.stderr or b"").decode("utf-8", "replace").strip() or r.returncode))
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Refused("crontab 寫不進去：%s" % e)


def untouched(lines, kept):
    """Guard a: the lines this run did not mean to change are all in `lines`, the same bytes, in the same order."""
    it = iter(lines)
    return all(any(line == k for line in it) for k in kept)


def write_crontab(raw_before, after, removed, kept, backup, run=subprocess.run):
    """Guards a and b around writing the raw lines `after` (guard c is the backup already made), and a fresh read
    first: a crontab changed since it was read is not written over. True if written."""
    before = plancore.raw_lines(raw_before)
    if not untouched(after, kept):
        raise Refused("新的 crontab 動到了不打算改的行，沒寫")
    if len(before) - len(after) > removed:
        raise Refused("新的 crontab 少了 %d 行，但只打算拿掉 %d 行，沒寫" % (len(before) - len(after), removed))
    if before == after:
        return False
    try:
        now = plancore.read_crontab_raw(run)
    except plancore.Unknown as e:
        raise Refused("寫之前再讀 crontab 讀不到（%s），沒寫" % e)
    if now != raw_before:
        raise Refused("crontab 在讀了之後被改過，這次沒寫，再跑一次")
    install_crontab(plancore.join_lines(after), run)
    why = "內容跟要寫的不同"
    try:
        again = plancore.raw_lines(plancore.read_crontab_raw(run))
    except plancore.Unknown as e:
        again, why = None, str(e)
    if again is None or again != after or not untouched(again, kept):
        try:
            install_crontab(raw_before, run)
        except Refused as e:
            raise Refused("寫了但重讀不對（%s），**還原也失敗**（%s）：現在的 crontab 可能是寫壞的那份，請手動照備份還原："
                          "crontab - < %s/crontab" % (why, e, backup))
        try:
            back = plancore.read_crontab_raw(run) == raw_before
        except plancore.Unknown:
            back = False
        raise Refused("寫完重讀不對（%s），%s" % (why, "已用備份 %s 還原、重讀確認過" % backup if back else
                                            "**還原後重讀還是不對**，請手動照備份還原：%s/crontab" % backup))
    return True


def settings_path():
    return os.path.expanduser("~/.claude/settings.json")


def replace_keeping_mode(path, data, mode):
    tmp = path + ".altention-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.chmod(tmp, mode)
    os.replace(tmp, path)


def write_settings(raw_before, before, after, added, backup, raised=()):
    """Guards a and b around writing `after` (only hooks added and the `raised` keys made larger: taking those out
    must give `before` back exactly); not written if the file changed since it was read (Claude Code may be writing
    it). The file keeps its mode (it may hold keys). True if written."""
    def rest(s):
        s = without(s, set(added))
        for k in raised:
            s.pop(k, None)
        return s
    grew = all(isinstance(after.get(k), int) and not isinstance(after.get(k), bool) and
               (before.get(k) is None or (isinstance(before.get(k), (int, float)) and after[k] > before[k])) for k in raised)
    if rest(after) != rest(before) or len(hook_list(after)) != len(hook_list(before)) + len(added) or not grew:
        raise Refused("新的 settings.json 動到了不打算改的設定，沒寫")
    if before == after:
        return False
    path = os.path.realpath(settings_path())  # a link stays a link
    try:
        with open(path, "rb") as f:
            now = f.read()
        mode = stat.S_IMODE(os.stat(path).st_mode)
    except FileNotFoundError:
        now, mode = None, 0o600
    except OSError as e:
        raise Refused("寫之前再讀 settings.json 讀不到（%s），沒寫" % e)
    if now != raw_before:
        raise Refused("settings.json 在讀了之後被改過（Claude Code 可能正在寫），這次沒寫，再跑一次")
    data = (json.dumps(after, ensure_ascii=False, indent=2) + "\n").encode("utf-8")
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        replace_keeping_mode(path, data, mode)
    except OSError as e:  # nothing reached the file (the temporary one never replaced it)
        try:
            os.remove(path + ".altention-tmp")
        except OSError:
            pass
        raise Refused("settings.json 沒寫進去（%s），檔案沒動" % e)
    why = "內容不同"
    try:
        with open(path, encoding="utf-8") as f:
            again = json.load(f)
    except (OSError, ValueError) as e:
        again, why = None, str(e)
    if again == after:
        return True
    try:  # written but wrong: put back what was there, and read it back before saying so
        if raw_before is None:
            os.remove(path)
            back = not os.path.exists(path)
        else:
            replace_keeping_mode(path, raw_before, mode)
            with open(path, "rb") as f:
                back = f.read() == raw_before
    except OSError as e:
        raise Refused("settings.json 寫了但重讀不對（%s），**還原也失敗**（%s）：現在的檔可能是寫壞的那份，備份在 %s/settings.json"
                      % (why, e, backup))
    if not back:
        raise Refused("settings.json 寫了但重讀不對（%s），**還原後重讀還是不對**，備份在 %s/settings.json" % (why, backup))
    raise Refused("settings.json 寫了但重讀不對（%s），已用備份 %s 還原、重讀確認過" % (why, backup))


def default_schedule_files(rows, settings):
    """The paths among schedule_files() that are a plugin's own default (nothing set): create_missing makes their
    folder too."""
    from altention import plugin
    out = set()
    for name, folder, d, where, reason in rows:
        if where != "on" or not d:
            continue
        try:
            default = plugin.default_schedule_file(d, folder, settings)
        except plugin.PluginError:
            continue
        if default:
            out.add(default)
    return out


def schedule_files(rows, settings):
    """[(plugin, path)] of the schedule files the plugins that are on here declare ("schedule_files": a list, or a
    {setting:…} that gives one), ~ expanded."""
    from altention import plugin
    out = []
    for name, folder, d, where, reason in rows:
        if where != "on" or not d or "schedule_files" not in d:
            continue
        try:
            out += [(name, p) for p in plugin.schedule_files(d, folder, settings)]  # the one reading (plugin.py)
        except plugin.PluginError:
            continue
    return out


def create_missing(files, machine, dry_run=False, defaults=()):
    """Make each declared schedule file that does not exist an empty schedule ("[]", mode 600) (manager 2026-10-06),
    only when its folder is there (no folders are made: a path mistyped in the settings, or a share not mounted yet,
    must not grow folders or a stand-in file) and only when the scheduler has never noted anything from it (a file
    that had notes and is gone is something wrong: said, not covered up). Never written over. defaults: a plugin's
    own default file (plugin.default_schedule_file, nothing set): its folder is made too (700); it is the engine's
    own place, not a path someone typed (planner's reminders on a new install, 2026-10-15)."""
    from altention import scheduler
    try:
        noted = set(scheduler.read_state(machine)) | set(scheduler.read_state(machine, dry=True))
    except Exception as e:  # cannot tell whether it had notes: make nothing
        return ["沒建任何排程檔：排程器的狀態讀不出來（%s）" % e]
    done = []
    for name, path in files:
        if os.path.lexists(path):
            continue
        real = os.path.realpath(path)
        if any(k.startswith(real + "#") for k in noted):
            done.append("沒建：%s 的排程檔 %s 不見了，但排程器記得它的項目（被刪了？），請查" % (name, path))
            continue
        if not os.path.isdir(os.path.dirname(path)) and path not in defaults:
            done.append("沒建：%s 的排程檔 %s 所在的資料夾不存在（打錯了，或還沒掛上？）" % (name, path))
            continue
        if not dry_run:
            try:
                if path in defaults:
                    os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
                    os.chmod(os.path.dirname(path), 0o700)  # there already and 755: made private too
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(fd, "w") as f:
                    f.write("[]\n")
            except FileExistsError:
                continue
            except OSError as e:
                done.append("建不起來 %s 的排程檔 %s：%s" % (name, path, e))
                continue
        done.append("建空的排程檔（%s）：%s" % (name, path))
    return done


def restore(stamp, engine, run=subprocess.run):
    """Put a backup back as it was ([what was put back]); the backup folder is kept. A settings.json that was not
    there before: the hooks of this engine (the only ones apply adds) are taken out of it again, and the file is
    removed if nothing else is left in it."""
    folder = os.path.join(backup_root(), stamp)
    if not os.path.isdir(folder):
        raise Refused("沒有這個備份：%s" % stamp)
    done = []
    path = os.path.join(folder, "crontab")
    if os.path.exists(path):
        with open(path, "rb") as f:
            raw = f.read()
        install_crontab(raw, run)
        if plancore.read_crontab_raw(run) != raw:
            raise Refused("crontab 還原後重讀不一樣")
        done.append("crontab")
    if os.path.exists(os.path.join(folder, "settings.json.absent")):
        target = os.path.realpath(settings_path())
        if os.path.exists(target):
            with open(target, encoding="utf-8") as f:
                now = json.load(f)
            ours = {h.get("command") for _, _, h in hook_list(now) if engine in (h.get("command") or "")}
            left = without(now, ours)
            if not left:
                os.remove(target)
                if os.path.exists(target):
                    raise Refused("settings.json 原本不存在，刪不掉")
                done.append("settings.json（原本不存在，已刪掉）")
            else:
                mode = stat.S_IMODE(os.stat(target).st_mode)
                replace_keeping_mode(target, (json.dumps(left, ensure_ascii=False, indent=2) + "\n").encode("utf-8"), mode)
                done.append("settings.json（原本不存在；拿掉了 apply 加的 hook，其他留著）")
    path = os.path.join(folder, "settings.json")
    if os.path.exists(path):
        target = os.path.realpath(settings_path())
        with open(path, "rb") as f:
            data = f.read()
        try:
            mode = stat.S_IMODE(os.stat(target).st_mode)
        except OSError:
            mode = 0o600
        replace_keeping_mode(target, data, mode)
        with open(target, "rb") as f:
            if f.read() != data:
                raise Refused("settings.json 還原後重讀不一樣")
        done.append("settings.json")
    return done
