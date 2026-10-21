#!/usr/bin/env python3
"""alt plugins: the plugins this engine knows and where each one runs (stage 3a; read only for now).

  python3 ops/plugins.py list               # every plugin found, its schedules as they run (the instance's
                                            # plugins.<name>.schedules overrides, C-37), any plugin.json that cannot be read
  python3 ops/plugins.py questions [--json] # the install's question for each optional plugin, word for word
  python3 ops/plugins.py questions --setup [--json]  # the install's first questions (time zone … talk limit, secretary)
  python3 ops/plugins.py questions --times [--json]  # the secretary's three times, once they chose to open it
  python3 ops/plugins.py questions --backup [--json] # back up now or later, and which ones (C-25, after the instance)
  python3 ops/plugins.py show [machine]     # where each plugin stands on a machine (default: this one)
  python3 ops/plugins.py show --json        # the same as JSON
  python3 ops/plugins.py apply [--write] [--allow-time 插件/項目]… [--moved-from 舊引擎資料夾]…
                                            # without --write: what would change (nothing is written);
                                            # with --write: plan's result written into this machine's crontab and
                                            # settings.json (ops/applycore.py: markers, missing items; backup first)
  python3 ops/plugins.py setup <插件> <項目>  # one setup item of an external plugin on here, by hand (C-61 E2: a
                                            # lazy one, or one given up on after failing twice); written down
  python3 ops/plugins.py report             # 3a.12: what apply --write would change here, as JSON; reads only
  python3 ops/plugins.py backups | restore <時間> # the backups apply made, and putting one back
  python3 ops/plugins.py plan               # what plugin.json asks this machine to have (cron lines, hooks; run it
                                            # from the engine that is installed, not a worktree)
                                            # next to what it has, compared by content (ops/plancore.py)

Placements: on, off (not its machine), disabled (turned off there on purpose), draft (an external plugin the
instance does not declare: C-61 E1), unknown (what decides it could not
be read; nothing is ever added or removed for an unknown one). Where a plugin runs is plugin.json's and the
instance's plugins.json (plugins.<name>.on); the machine list's roles only say where the bot runs (dcbot) and which
machines' panes may be attached (dcattach).
Exit status: 0, or 1 when there is a problem or an unknown placement (a machine the list does not have is an
error). Standard library only, Python 3.8.
"""
import json
import re
import subprocess
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config, plugin  # noqa: E402
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import applycore  # noqa: E402
import plancore  # noqa: E402

def placements(machine):
    """(rows, problems, unknown) for `machine` as this engine and instance see it: where each plugin runs, and the
    plugins that cannot be placed (ops/apply.py does nothing on a machine with any of those: manager 2026-10-06)."""
    rows, problems = plugin.on_this_machine(machine)
    unknown = ["%s：不知道（%s）" % (n, r) for n, _, _, w, r in rows if w == "unknown"]
    return rows, problems, unknown


def show(machine=None, as_json=False, out=sys.stdout):
    named = []
    machine = machine or plugin.this_machine(named)
    try:
        listed = config.inventory().get("machines") or {}
    except Exception:
        listed = {}
    if listed and machine not in listed:
        out.write("機器清單上沒有 %s（有：%s）\n" % (machine, "、".join(sorted(listed))))
        return 1
    rows, problems, unknown = placements(machine)
    problems = named + problems
    if as_json:
        json.dump({"machine": machine,
                   "plugins": [{"name": n, "folder": f, "placement": w, "reason": r} for n, f, _, w, r in rows],
                   "problems": problems, "unknown": unknown}, out, ensure_ascii=False, indent=1)
        out.write("\n")
    else:
        out.write("== %s\n" % machine)
        for name, _, _, where, reason in rows:
            out.write("%-8s %-9s%s\n" % (name, where, "  " + reason if reason else ""))
        for p in problems:
            out.write("問題：%s\n" % p)
        for u in unknown:
            out.write("%s\n" % u)
    return 1 if problems or unknown else 0


LABEL = {"same": "一致", "time differs": "時間不同", "paused": "暫停中", "missing": "少了", "extra": "多的",
         "unclaimed": "沒認領", "duplicate": "重複", "short": "不足", "elsewhere": "不在這台", "replaced": "換掉舊的",
         "moved": "搬家前的舊路徑", "gone": "舊資料夾不在", "turned off": "實例關掉", "held": "設定不能用，不動",
         "hook off": "插件在這台關掉了"}


def unlisted(machine):
    """Why nothing is compared or written here when this machine's name is not on a machine list that was read (it
    could not work out its own name: hostname changed, host file wrong), or "" (code-review 2026-10-06)."""
    try:
        known = set(config.inventory().get("machines") or {})
    except Exception:
        return ""  # an unreadable list: each plugin is unknown already
    if known and machine not in known:
        return ("這台（%s）不在機器清單上（%s）：認不出自己的名字時什麼都不比對、不寫；檢查 ~/.config/altention/host 或清單的"
                " hostname 欄位" % (machine, "、".join(sorted(known))))
    return ""


def gather():
    """What plan and apply both need: (machine, rows, problems, want, unknown, crontab lines or None, cron, paused,
    settings text or None, settings or None, hooks, engine). None marks what could not be read."""
    problems = []
    machine = plugin.this_machine(problems)
    rows, found = plugin.on_this_machine(machine)
    problems += found
    try:
        settings = config.settings()
    except Exception:
        settings = None
    try:
        bot = config.bot_machine()[0]
    except Exception:
        bot = None
    want, unknown = plancore.wanted(rows, machine, settings or {}, bot)
    why = unlisted(machine)
    if why:
        problems.append(why)
        want = []
    engine = os.path.realpath(plugin.ENGINE)
    try:
        raw = plancore.read_crontab_raw()
        lines = [t for t in (plancore.text(l) for l in plancore.raw_lines(raw)) if t is not None]
        cron, paused, unreadable = plancore.installed_cron(lines, engine)
        for line in unreadable:
            problems.append("crontab 裡我們的一行讀不懂（引號不成對？），這一行不比對：%s" % line)
    except plancore.Unknown as e:  # not read is not "no lines": nothing about cron is compared, or ever written
        problems.append("%s：cron 不比對（不知道）" % e)
        raw, cron, paused, want = None, [], [], [w for w in want if w["kind"] != "cron"]
    try:
        text, cc = plancore.read_settings_raw()  # one read: parsed and compared from the same bytes
        hooks = plancore.installed_hooks(cc, engine)
    except (plancore.Unknown, OSError) as e:  # an unreadable settings.json is not "no hooks"
        problems.append("%s：hook 不比對（不知道）" % e)
        cc, text, hooks, want = None, None, [], [w for w in want if w["kind"] != "hook"]
    try:
        known = set(config.inventory().get("machines") or {})
    except Exception:
        known = set()
    away = plancore.elsewhere(rows, machine, settings, bot, known)
    return machine, rows, problems, want, unknown, raw, cron, paused, text, cc, hooks, engine, away


def plan(out=sys.stdout):
    """This machine (plan reads its own crontab and settings): each cron line and hook, same, time differs, paused,
    missing, extra or unclaimed. Exit 1 when anything differs, or something could not be read or placed."""
    machine, rows, problems, want, unknown, lines, cron, paused, text, cc, hooks, engine, away = gather()
    result = plancore.compare(want, cron, hooks, paused, away, engine, plancore.other_hooks(cc, engine) if cc else ())
    out.write("== %s 的 plan（只讀，什麼都沒改）\n" % machine)
    counts = {}
    for status, w, text_, note in result:
        counts[status] = counts.get(status, 0) + 1
        what = "%s/%s %s" % (w["plugin"], w["name"], w["kind"]) if w else (
            "（看起來像舊的，但對不上任何宣告的項目：當成別人的，不會動）" if status == "unclaimed" else
            "（引擎搬家前的舊路徑：apply 會拿掉，這個插件的新行另外寫上）" if status == "moved" else
            "（像是引擎搬家前的行，但舊資料夾已經不在：apply 加 --moved-from <資料夾> 才會換）" if status == "gone" else
            "（帶我們的標記，但沒有插件要它）")
        out.write("%-4s %s%s\n" % (LABEL[status], what, "  標記：%s" % note if note else ""))
        if status == "time differs" and w and w.get("override"):
            out.write("     （時間是實例設定的 plugins.%s.schedules.%s：apply 會改）\n" % (w["plugin"], w["name"]))
        if status != "same":
            if w and w["text"]:
                out.write("     要的：%s\n" % w["text"])
            if text_:
                out.write("     現在：%s\n" % text_)
    items, odd_items = plancore.settings_wanted(rows)
    unknown = unknown + odd_items
    if cc is not None:
        for name, key, least, now in plancore.settings_short(cc, items):
            counts["short"] = counts.get("short", 0) + 1
            out.write("%-4s %s/%s setting  要的：至少 %d，現在：%s\n" % (LABEL["short"], name, key, least,
                                                                     "沒有" if now is None else now))
        unknown = unknown + [("%s/%s" % (n, k), "settings.json 裡的值不是數字，不比對") for n, k in plancore.settings_odd(cc, items)]
    for name, reason in unknown:
        out.write("不知道 %s：%s（不比對，不會加也不會拿掉）\n" % (name, reason))
    for p in problems:
        out.write("問題：%s\n" % p)
    differ = sum(v for k, v in counts.items() if k not in ("same", "paused", "unclaimed", "held"))  # on purpose / not ours / unknown
    out.write("合計：%s\n" % "、".join("%s %d" % (LABEL[k], v) for k, v in sorted(counts.items())))
    if unlisted(machine):
        verdict = "不知道（這台不在機器清單上；上面列的不比對）"
    elif problems and not differ:
        verdict = "不知道（有讀不出來或判斷不了的，見上面的問題）"
    elif not differ and not unknown:
        verdict = "零差異"
    else:
        verdict = "有 %d 項不同%s" % (differ, "、%d 個插件不知道" % len(unknown) if unknown else "")
    out.write("內容：%s\n" % verdict)
    return 1 if differ or unknown or problems else 0


REMOVALS_MAX = 1  # more would-be removals than this in one report: refused as a mass removal (manager 2026-10-09:
MARKED_SHARE = 3  # two or more, or over a third of this machine's marked lines; rather too often than once too few)


def _head(folder):
    try:
        r = subprocess.run(["git", "-C", folder, "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                           universal_newlines=True, timeout=20)
        return r.stdout.strip()[:12] or None if r.returncode == 0 else None
    except (OSError, subprocess.SubprocessError):
        return None


def report():
    """3a.12, report only: what `apply --write` would change on this machine, as a dict, from what plan reads and
    compares (gather, plancore.compare). Nothing is written here and nothing that writes is called: not a
    crontab, not settings.json, no backup, no cleaning of leftovers (the dry `apply` does that one).
    add / remove / retime: the plugins' own items, as apply would add, remove (counted widely: every line with
    our marker that nothing asks for) and, when named by hand, retime. core: the core's lines and hooks that are
    not as asked: listed, and never changed by any automatic apply (a person's to put right). disabled, unknown,
    unclaimed, paused: said, nothing follows from them. refuse: why this report must never be written out
    automatically, a mass removal (REMOVALS_MAX, MARKED_SHARE), every plugin off or unknown at once, or the
    instance or the machine list not read: most likely this machine not knowing what it is, not a real change."""
    machine, rows, problems, want, unknown, raw, cron, paused, text, cc, hooks, engine, away = gather()
    applycore.MACHINE[0] = machine  # apply's folder of this machine: overrides.json is read from there (nothing written)
    result = plancore.compare(want, cron, hooks, paused, away, engine, plancore.other_hooks(cc, engine) if cc else ())
    out = {"format": 1, "machine": machine, "engine": _head(engine), "instance": None, "add": [], "remove": [], "retime": [],
           "core": [], "paused": [], "unclaimed": 0, "problems": list(problems), "refuse": None, "gone": [], "kept": []}
    try:
        out["instance"] = _head(config.instance_dir())
    except Exception:
        pass

    off = applycore.removable(rows)
    recorded = applycore.read_overrides()

    def item(status, w, installed):
        d = {"status": status, "plugin": w["plugin"] if w else plancore.marked(installed or "") or None,
             "name": w["name"] if w else None, "kind": w["kind"] if w else "cron"}
        if installed:
            d["installed"] = installed[:300]
        if w and status != "same":
            d["wanted"] = w["text"][:300]
        return d
    for status, w, installed, _note in result:
        if status == "same":
            continue
        if status == "unclaimed":
            out["unclaimed"] += 1
        elif status == "gone":  # an old engine folder gone, not named: kept by apply, said by doctor (plugins.old-engine)
            out["gone"].append(item(status, w, installed))
        elif status == "paused":
            out["paused"].append(item(status, w, installed))
        elif (w["plugin"] if w else plancore.marked(installed or "")) == "core":
            out["core"].append(item(status, w, installed))
        elif status in ("missing", "replaced"):  # replaced: the new line in the old one's place
            out["add"].append(item(status, w, installed))
        elif status == "held":  # an override that cannot be used: in unknown already; its line is never touched
            continue
        elif status == "time differs":
            d = item(status, w, installed)
            if applycore.auto_time(w, installed, recorded):
                d["auto"] = True  # the instance set this time (C-37): apply changes it without --allow-time
            out["retime"].append(d)
        elif status == "duplicate" or (status == "extra" and plancore.marked(installed or "") not in off):
            out["kept"].append(item(status, w, installed))  # apply leaves these (applycore.new_crontab): said, not removed
        else:  # extra of a plugin plainly off, elsewhere, moved: a line with our marker that apply takes away
            out["remove"].append(item(status, w, installed))
    items, odd_items = plancore.settings_wanted(rows)
    if cc is not None:
        for name, key, least, now in plancore.settings_short(cc, items):
            (out["core"] if name == "core" else out["add"]).append(
                {"status": "short", "plugin": name, "name": key, "kind": "setting", "wanted": "至少 %d" % least,
                 "installed": "沒有" if now is None else str(now)})
        odd_items = odd_items + [("%s/%s" % (n, k), "settings.json 裡的值不是數字") for n, k in plancore.settings_odd(cc, items)]
    out["unknown"] = [[n, r] for n, r in unknown + odd_items]
    out["disabled"] = sorted(n for n, _f, _d, where, _r in rows if where == "disabled")
    marked = len([h for h in cron if h[3]])
    out["marked_lines"] = marked
    why = []
    # the guard counts widely, as before: kept ones too (manager 2026-10-09); an item the instance turned off is not loose
    loose = len([r for r in out["remove"] if r["status"] != "turned off"]) + len(out["kept"])
    if loose > REMOVALS_MAX or (marked and loose * MARKED_SHARE > marked):
        why.append("沒有插件要的我們的行有 %d 項（apply 會拿掉 %d、留著 %d；這台帶記號的有 %d 行）"
                   % (loose, len(out["remove"]), len(out["kept"]), marked))
    placed = [where for n, _f, _d, where, _r in rows if n != "core"]
    if placed and all(w in ("off", "unknown") for w in placed):
        why.append("這台所有的插件都是 off 或不知道")
    try:
        config.settings()
    except Exception as e:
        why.append("實例設定讀不到（%s）" % type(e).__name__)
    try:
        inv = config.inventory()
        listed = inv.get("machines") or {}
        if inv and machine not in listed:  # no machines.json at all is the one-machine install (config.machines())
            why.append("機器清單讀不到這台（%s）" % machine)
    except Exception as e:
        why.append("機器清單讀不到（%s）" % type(e).__name__)
    if raw is None or cc is None:
        why.append("crontab 或 settings.json 讀不出來")
    if why:
        out["refuse"] = "mass-removal：" + "；".join(why)
    return out


def apply(write=False, allow_time=(), out=sys.stdout, hold=(), moved_from=()):
    """Without `write`: what would change, nothing written. With it: plan's result written into this machine's crontab
    and settings.json under applycore's rules and guards, each step said on its own (one may be written and the
    other not); nothing is written when anything could not be read."""
    machine, rows, problems, want, unknown, raw, cron, paused, text, cc, hooks, engine, away = gather()
    applycore.MACHINE[0] = machine
    applycore.clean_if_free()
    result = plancore.compare(want, cron, hooks, paused, away, engine, plancore.other_hooks(cc, engine) if cc else (), moved_from)
    out.write("== %s 的 apply%s\n" % (machine, "" if write else "（只看，不寫；要寫加 --write）"))
    # --hold: not a byte of these changes, neither added nor marked nor retimed (#code-review-2 2026-10-06), until
    # ops/apply.py finds no other machine runs it or the installer can place the plugin again
    held = lambda w: w is not None and (w["plugin"] in hold or (  # noqa: E731
        "bot" in hold and "bot" in plugin._names(w.get("on", "all"))))  # bot: only the items whose own on is bot
    held_back = [r for r in result if r[0] in ("missing", "same", "time differs", "replaced", "turned off") and held(r[1])]
    result = [r for r in result if r not in held_back]  # left out: its installed line stays exactly as it is
    for r in held_back:
        if r[0] != "same" or r[3] not in (applycore.MARK + r[1]["plugin"], applycore.MARK + plancore.marker(r[1])):  # what would have changed
            out.write("- 先不動（--hold）：%s/%s\n" % (r[1]["plugin"], r[1]["name"]))
    for p in problems:
        out.write("問題：%s\n" % p)
    if raw is None or cc is None:
        out.write("crontab 或 settings.json 讀不出來：什麼都不寫\n")
        return 1
    if unlisted(machine):
        out.write("這台不在機器清單上：什麼都不寫\n")
        return 1
    lines = plancore.raw_lines(raw)
    new_lines, cron_actions, removed, kept = applycore.new_crontab(lines, result, rows, allow_time, applycore.read_overrides())
    new_cc, hook_actions, added = applycore.new_settings(cc, result)
    items, odd_items = plancore.settings_wanted(rows)
    new_cc, setting_actions, raised = applycore.raise_settings(new_cc, plancore.settings_short(cc, items))
    hook_actions += setting_actions
    unknown = unknown + odd_items + [("%s/%s" % (n, k), "settings.json 裡的值不是數字") for n, k in plancore.settings_odd(cc, items)]
    try:
        settings = config.settings()
    except Exception:
        settings = None
    files = applycore.schedule_files(rows, settings)
    defaults = applycore.default_schedule_files(rows, settings)
    preview = applycore.create_missing(files, machine, dry_run=True, defaults=defaults)
    for a in cron_actions + hook_actions + preview:
        out.write("- %s\n" % a)
    for name, reason in unknown:
        out.write("不動 %s：不知道（%s）\n" % (name, reason))
    if new_lines == lines and new_cc == cc and not [p for p in preview if p.startswith("建空")]:
        out.write("沒有要改的\n")
        if write:  # the crontab is as asked: its intervals and overrides noted all the same (#code-review-2 on bcc099a:
            try:   # intervals.json gone or stale was never written back while nothing had to change)
                with applycore.one_at_a_time():
                    applycore.note_overrides(result, lines, engine)
                    applycore.note_intervals(lines, engine)
            except Exception as e:
                out.write("記不下間隔（%s）：update.py 和同步照 45 分鐘算\n" % e)
        return 0
    if not write:
        return 0
    try:
        with applycore.one_at_a_time():
            backup = applycore.make_backup(raw, text)
            out.write("備份：%s\n" % backup)
            try:
                wrote = applycore.write_crontab(raw, new_lines, removed, kept, backup)
                if wrote:
                    applycore.note_added({w["plugin"] for st, w, _t, _n in result if st in ("missing", "replaced") and w["kind"] == "cron"})
                applycore.note_overrides(result, new_lines, engine)  # also when nothing changed: the lines are as asked
                applycore.note_intervals(new_lines, engine)  # what update.py and the sync count rounds by (C-37 C)
                out.write("crontab：%s\n" % ("寫好了（重讀確認過）" if wrote else "沒有要改的"))
            except applycore.Refused as e:
                out.write("crontab：沒寫（%s）；settings.json 也就不寫了\n" % e)
                return 1
            try:
                with applycore.home_lock():
                    wrote_cc = applycore.write_settings(text, cc, new_cc, added, backup, raised)
                out.write("settings.json：%s\n" % ("寫好了（重讀確認過）" if wrote_cc else "沒有要改的"))
            except (applycore.Refused, OSError) as e:
                out.write("settings.json：沒寫（%s）\n" % e)
                if wrote:
                    out.write("注意：crontab 已經改了、settings.json 沒改。要退回 crontab：python3 ops/plugins.py restore %s\n"
                              % os.path.basename(backup))
                return 1
            for line in applycore.create_missing(files, machine, defaults=defaults):
                out.write("- %s\n" % line)
    except applycore.Refused as e:  # the lock, or the backup
        out.write("沒寫：%s\n" % e)
        return 1
    return 0


# COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
def held(names, out=sys.stdout):
    """Whether this machine's crontab still has lines of the plugins `names` ("bot" for the bot's supervisor lines):
    one JSON object {name: false, "marked" (every such line carries our new marker: plugins.py apply can take them
    out) or "old" (some line is in an old format: apply never removes those)}, or {"unknown": why} when the crontab
    cannot be read. ops/apply.py asks before it adds a single-machine plugin anywhere (manager 2026-10-06). A line is
    the plugin's when it carries its marker, new or old, or runs a file in its folder of this engine (the bot's: its
    supervisor lines, new marker "core"); commented-out lines do not run and do not count."""
    engine = os.path.realpath(plugin.ENGINE)
    try:
        lines = [t for t in (plancore.text(l) for l in plancore.raw_lines(plancore.read_crontab_raw())) if t is not None]
    except plancore.Unknown as e:
        json.dump({"unknown": str(e)}, out, ensure_ascii=False)
        out.write("\n")
        return 2
    forms = plancore.engine_forms(engine) + plancore.engine_forms(os.path.abspath(plugin.ENGINE))
    found = {}
    for n in names:
        folder = "dcbot" if n == "bot" else n + "/"  # the supervisor's lines: cd "<engine>/dcbot" && … supervisor.py
        mark = re.compile(r"#\s*(altention:)?%s(/[A-Za-z0-9_.-]+)?\s*$" % re.escape(n)) if n != "bot" else None
        mine = [l for l in lines if not l.lstrip().startswith("#") and
                ((mark is not None and mark.search(l)) or (any(f + "/" + folder in l for f in forms)
                                                             and (n != "bot" or "supervisor.py" in l)))]
        new = "core" if n == "bot" else n
        found[n] = False if not mine else ("marked" if all(plancore.marked(l) == new for l in mine) else "old")
    json.dump(found, out, ensure_ascii=False)
    out.write("\n")
    return 0


def backups(out=sys.stdout):
    applycore.MACHINE[0] = plugin.this_machine([])
    root = applycore.backup_root()
    try:
        stamps = sorted(os.listdir(root))
    except OSError:
        stamps = []
    for s in stamps:
        out.write("%s：%s\n" % (s, "、".join(sorted(os.listdir(os.path.join(root, s))))))
    if not stamps:
        out.write("沒有備份\n")
    return 0


def _ordered(plugins):
    """The core first, then by each plugin.json's "order" (none: last, by name)."""
    return sorted(plugins, key=lambda p: (p[0] != "core", p[2].get("order") is None, p[2].get("order") or 0, p[0]))


def schedule_words(it):
    """How plugins.py list says one schedule item: its time now and where that comes from (C-37)."""
    where = "plugin.json" if it.get("source") != "instance" else "實例設定 %s" % (it.get("from") or ("core.schedules" if it.get("core") else
                                                                                     "plugins.<名>.schedules"))
    if it.get("unknown"):
        return "實例的設定不能用（%s），apply 不動它現在的行" % it["unknown"]
    if it.get("enabled") is False:
        return "關掉了（%s）" % where
    w = it.get("when")
    if isinstance(w, dict) and "every" in w:
        text = "每 %s" % w["every"] + ("（各機器錯開）" if w.get("offset") == "machine" else "")
    elif isinstance(w, dict) and "at" in w:
        days = w.get("days", "daily")
        text = "%s %s" % ("每天" if days in ("daily", None) else "每週 " + ",".join(days), w["at"]) + (
            "（機器的時區）" if it.get("timezone") == "machine" else "")
    else:
        text = str(w)
    return "%s（%s）" % (text, where)


def list_all(out=sys.stdout):
    """One line per plugin, in _ordered's order; under each, its "pitch" (what it does for the user, for the install's
    questions) and the plugins it needs. Drafts (external plugins the instance does not declare, C-61 E1) last, under
    「草稿」, with what to write to turn one on."""
    drafts = []
    plugins, problems = plugin.discover(drafts=drafts)
    try:
        settings = config.settings()
    except Exception as e:
        settings = None
        problems = problems + ["實例設定讀不出來（%s）：排程只列 plugin.json 寫的" % e]
    for name, folder, d in _ordered(plugins):
        out.write("%-8s %-4s %s（%s）\n" % (name, d.get("machines", "all"), d["title"], folder))
        needs = [x["plugin"] for x in d.get("requires", [])]
        if d.get("pitch") or needs:
            out.write("  %s%s\n" % (d.get("pitch", ""), "（需要：%s）" % "、".join(needs) if needs else ""))
        items, said = plugin.effective_schedules(name, d, settings or {})
        for it in items:
            if name == "core" and it.get("name") not in plugin.CORE_OVERRIDABLE:
                continue  # the core's other lines cannot be changed from the instance
            out.write("  排程 %s：%s\n" % (it.get("name"), schedule_words(dict(it, core=name == "core"))))
            if it.get("unknown"):
                said = said + ["%s（apply 不動 %s/%s 現在的行）" % (it["unknown"], name, it.get("name"))]
        problems = problems + said
    if drafts:
        out.write("草稿（實例還沒寫它：不裝、不排程、不問設定）：\n")
        for name, folder, d in drafts:
            out.write("  %-8s %s（%s）\n" % (name, d["title"], folder))
        out.write("  要開：實例 plugins.json 寫 \"plugins\": {\"<名稱>\": {\"source\": \"<git 網址>\", \"version\": \"vX.Y.Z\"}}"
                  "（只在這台、不 clone 的寫 {\"local\": true}）\n")
    try:
        rows = plugin.on_this_machine()[0]
    except Exception:
        rows = []
    for name, words in plugin.external_notes(rows, settings):
        out.write("提醒：%s：%s\n" % (name, words))
    for p in problems:
        out.write("問題：%s\n" % p)
    return 1 if problems else 0


SECRETARY = "planner"  # asked in setup_questions with its times, not among the per-plugin questions (owner 2026-10-16)


def questions(plugins):
    """The install's question for each plugin but the core and the secretary (SECRETARY), in _ordered's order, ready for AskUserQuestion: the install
    skill passes them on word for word (Claude left out progress's need when it wrote them itself, 2026-10-15).
    Words from plugin.json: "title", "pitch", "keep" (what keeping it gives) and "lose" (what is missing without it);
    the sentences on needs are written here, with titles, from "requires"."""
    title = {n: d.get("title") or n for n, _, d in plugins}
    needs = {n: [x["plugin"] for x in d.get("requires", [])] for n, _, d in plugins}
    asked = []
    for name, _, d in _ordered(plugins):
        if name in ("core", SECRETARY):
            continue
        mine = "、".join(title.get(x, x) for x in needs[name])
        users = "、".join(title[n] for n, _, _ in _ordered(plugins) if name in needs[n])
        pitch = d.get("pitch", "").strip()
        q = "%s：%s" % (title[name], pitch) if pitch else title[name]
        q += "" if q.endswith("。") else "。"
        if mine:
            q += "注意：要靠%s；保留%s，%s也會一起保留。" % (mine, title[name], mine)
        q += "要保留嗎？之後想開，跟 Claude 說一聲就好。"
        keep = d.get("keep") or "%s照常運作" % title[name]
        lose = "少了%s" % title[name] + ("，" + d["lose"] if d.get("lose") else "")
        asked.append({"plugin": name, "header": title[name], "question": q, "multiSelect": False, "options": [
            {"label": "保留", "description": keep + ("（%s也會一起保留）" % mine if mine else "")},
            {"label": "關掉", "description": lose + ("；%s也要靠它" % users if users else "")}]})
    return asked


def first_question(plugins):
    """The question asked before questions(): keep every optional plugin or turn some off, naming them by title in
    the same order (Claude wrote the list itself once, mixing names and titles, 2026-10-15). Single choice: a
    multi-select left empty counts as not answered."""
    titles = "、".join(q["header"] for q in questions(plugins))
    return {"key": "plugins", "values": ["keep", "some"],
            "header": "選用功能", "question": "選用的功能（%s）要全部保留，還是關掉一些？" % titles, "multiSelect": False,
            "options": [{"label": "全部保留（建議）", "description": "所有功能都裝上"},
                        {"label": "關掉一些", "description": "接著一個一個問"}]}


def secretary_question(plugins, again=False, gone=False):
    """Open the secretary or not, from planner's plugin.json; None when planner is not there. Asked with the other
    setup questions, so that once the bot is up its channel is opened without asking again. again: they chose to open
    it before and its channel was never made (setup.py done did not get to it): asked as that, not as new (manager
    2026-10-20, Z trial). gone: its channel was there and is not any more (deleted): asked as that (before again)."""
    d = next((d for n, _, d in plugins if n == SECRETARY), None)
    if d is None:
        return None
    title = d.get("title") or SECRETARY
    pitch = d.get("pitch", "").strip()
    lose = "少了%s" % title + ("，" + d["lose"] if d.get("lose") else "")
    if gone:
        return {"key": "plugins.%s" % SECRETARY, "values": ["on", "off"], "header": "秘書", "multiSelect": False,
                "question": "#secretary 已經不在了，要重新建一個嗎？",
                "options": [{"label": "重新建（建議）", "description": "照之前選的時間再開一個 #secretary"},
                            {"label": "不開了", "description": lose}]}
    if again:
        return {"key": "plugins.%s" % SECRETARY, "values": ["on", "off"], "header": "秘書", "multiSelect": False,
                "question": "上次你選了開%s，可是 #secretary 還沒建好（那時設定沒做完）。要現在建好嗎？" % title,
                "options": [{"label": "建好它（建議）", "description": "照上次選的時間開 #secretary"},
                            {"label": "不開了", "description": "少了%s" % title + ("，" + d["lose"] if d.get("lose") else "")}]}
    return {"key": "plugins.%s" % SECRETARY, "values": ["on", "off"], "header": "秘書", "multiSelect": False,
            "question": "%s：%s%s要開嗎？開的話接著問時間。" % (
                title, pitch, "" if pitch.endswith("。") or not pitch else "。"),
            "options": [{"label": "開（建議）", "description": (d.get("keep") or title) + "；接著選時間"},
                        {"label": "不開", "description": "少了%s" % title + ("，" + d["lose"] if d.get("lose") else "")}]}


def briefing_questions():
    """The secretary's three times, asked once they chose to open it; "values" are planner.briefings.<key> (each a
    schedule's "when", as planner/remind.py BRIEFINGS writes it: the first option is its default)."""
    def ask(key, header, question, options):
        return {"key": "planner.briefings.%s" % key, "values": [w for _, _, w in options], "header": header,
                "question": question, "multiSelect": False,
                "options": [{"label": l, "description": d} for l, d, _ in options]}
    daily = lambda at: {"at": at, "days": "daily"}
    return [
        ask("morning", "早報", "秘書每天幾點寫早報（今天最重要的事、提醒和期限）？要別的時間，選「其他」打 24 小時制的時間，例如 07:30。",
            [("08:00（建議）", "每天早上 8 點", daily("08:00")), ("07:00", "每天早上 7 點", daily("07:00")),
             ("09:00", "每天早上 9 點", daily("09:00"))]),
        ask("evening", "晚間回顧", "秘書每天幾點問你今天做完了什麼、更新短期計畫？要別的時間，選「其他」打 24 小時制的時間，例如 21:30。",
            [("22:00（建議）", "每天晚上 10 點", daily("22:00")), ("21:00", "每天晚上 9 點", daily("21:00")),
             ("23:00", "每天晚上 11 點", daily("23:00"))]),
        ask("weekly", "每週回顧", "秘書每週什麼時候整理這週、規劃下週？要別的時間，選「其他」打星期幾和時間，例如 週六 10:00。",
            [("週日 21:00（建議）", "每週日晚上 9 點", {"at": "21:00", "days": ["sun"]}),
             ("週日 20:00", "每週日晚上 8 點", {"at": "20:00", "days": ["sun"]}),
             ("週五 18:00", "每週五下午 6 點", {"at": "18:00", "days": ["fri"]})]),
    ]


def setup_questions(plugins, zone, unpacked):
    """The install's first questions (five, and the secretary's when planner is there), in #setup's order (time zone,
    permission, talk limit, update, the optional parts: C-48), word for word as the skill passes them on (Claude wrote them itself once
    and mixed in English and setting names, 2026-10-15). The permission modes keep Claude Code's own names, the ones
    its screen shows, with their meaning in front of each description. "key" and "values" are for the skill: the
    setting each answer goes into and the value of each option, in order; the person sees neither. zone: this machine's time zone
    (None: nothing says); unpacked: the engine has no .git (it never updates itself, nor says a release is out)."""
    def ask(key, header, question, options, values):
        return {"key": key, "values": values, "header": header, "question": question, "multiSelect": False,
                "options": [{"label": l, "description": d} for l, d in options]}
    if zone is None:  # nothing on this machine says
        zones = [("UTC（建議）", "世界標準時間；讀不到這台機器的時區設定"), ("Asia/Taipei", "台北時間")]
    elif zone == "UTC":
        zones = [("UTC（建議）", "世界標準時間，也是這台機器目前的時區"), ("Asia/Taipei", "台北時間")]
    else:
        zones = [("%s（建議）" % zone, "這台機器目前的時區"), ("UTC", "世界標準時間")]
    return [
        ask("core.timezone", "時區", "提醒和排程要用哪個時區？要用別的時區，選「其他」打它的名稱，例如 Europe/Berlin。",
            zones, [z.replace("（建議）", "") for z, _ in zones]),
        ask("dcbot.default_permission", "權限", "機器人裡的 Claude 可以不問你就做多少事？這關係到安全，也關係到你要按幾次「允許」。"
            "之後每個頻道可以各自設定。",
            [("auto（建議）", "自動判斷：一般的事直接做，不用一直按「允許」；危險的事（例如刪掉重要的檔案、改動整台電腦的設定、"
                                "執行從網路下載的程式）會被擋下，Claude 改用別的方法，或停下來跟你說。被擋好幾次之後，才會在 Discord "
                                "跳按鈕問你。被擋下時，Discord 目前不會另外通知你"),
             ("manual", "每次都問：Claude 每次要改檔案或執行指令，都先在 Discord 跳按鈕，等你按「允許」。最安全，但要按很多次"),
             ("bypassPermissions", "從不問：不問你也不擋，危險的事也直接做（只剩機器人自己的幾條禁止規則，和保護密碼、金鑰的檢查還在）。"
                        "誰拿到你的 Discord 帳號，幾乎就能控制這台電腦。不建議")],
            ["auto", "manual", "bypassPermissions"]),
        ask("dcbot.talk_limit", "來回上限", "各頻道的 Claude 可以互相交辦工作、回覆對方。連續來回幾次、中間你都沒說話時，"
            "機器人要先停下來問你要不要繼續？它會標註你，手機會跳通知。之後每個頻道可以各自設定。",
            [("10 次（建議）", "一般的交辦和回覆都夠用"), ("30 次", "讓長一點的來回不被打斷"),
             ("不限", "一直來回到做完為止，可能用掉很多訂閱額度")], [10, 30, 0]),
        ask("core.update", "引擎更新", "有新版本時要怎麼做？" + (
            "這份引擎是從壓縮檔解開的，不管選哪個都不會自己更新，也不會通知新版；要新版請重新下載。" if unpacked else ""),
            [("只通知（建議）", "有新版時在狀態看板和健康檢查裡告訴你，不會自己更新"),
             ("自動更新", "每次出新版就自己更新，更新前先檢查程式能不能正常載入")], ["notify", "release"]),
        first_question(plugins),
    ] + [q for q in [secretary_question(plugins)] if q]


def times_question():
    """C-48: the secretary's suggested times, or choose each one (briefing_questions); asked once it is opened."""
    return {"key": "planner.times", "values": ["suggested", "custom"], "header": "秘書時間", "multiSelect": False,
            "question": "秘書什麼時候叫你？建議的時間是：早報每天 08:00、晚間回顧每天 22:00、每週回顧週日 21:00。"
                        "要用這些時間，還是自己選？之後跟秘書說一聲就能改。",
            "options": [{"label": "用建議的時間（建議）", "description": "早報 08:00、晚間回顧 22:00、每週回顧週日 21:00"},
                        {"label": "自己選", "description": "接著三個時間各問一次"}]}


PLANS = (("Pro", 1), ("Max 5x", 5), ("Max 20x", 20))  # usage.plan as [name, how many Pro allowances]


def plan_question():
    """The plan, for the usage board's subscription limits (the install skill's own words until C-48); values are
    the plan's name, "none" for not reading the limits. Another plan typed in: "<name>=<Pro allowances>"."""
    return {"key": "usage.plan", "values": [n for n, _ in PLANS] + ["none"], "header": "方案", "multiSelect": False,
            "question": "你的 Claude 訂閱是哪個方案？用量看板會顯示訂閱額度用了多少：為了讀額度，每小時用 haiku 送一個最小的請求，"
                        "會算進 5 小時的額度。不想花這個額度就選最後一個。",
            "options": [{"label": n, "description": "顯示訂閱額度（每小時一個最小的 haiku 請求）"} for n, _ in PLANS]
            + [{"label": "不顯示訂閱額度（不花額度）", "description": "看板不顯示額度用了多少，也不送讀額度的請求；之後想看再開"}]}


PROGRESS_EVERY = ("10m", "30m", "1h")  # C-47: until the measurement (10/26), the default first (manager 2026-10-19)


def progress_question():
    """How often progress sums up the projects (C-47): plugins.progress.schedules.run.when.every."""
    words = {"10m": "10 分鐘", "30m": "30 分鐘", "1h": "1 小時"}
    return {"key": "progress.every", "values": list(PROGRESS_EVERY), "header": "進度頻率", "multiSelect": False,
            "question": "進度多久整理一次？越常整理，每個專案的進度越新，用的訂閱額度也越多。之後跟 Claude 說一聲就能改。",
            "options": [{"label": "%s%s" % (words[v], "（建議）" if i == 0 else ""),
                         "description": d} for i, (v, d) in enumerate(zip(PROGRESS_EVERY, (
                             "每 10 分鐘整理一次，進度最新", "每 30 分鐘一次，額度用得比較少", "每小時一次，額度用得最少")))]}


def install_question():
    """#setup's first question (C-57 S2): a new installation, or back from the backups (the install skill's words)."""
    return {"key": "install", "values": ["new", "restore"], "header": "安裝", "multiSelect": False,
            "question": "這台要怎麼裝？",
            "options": [{"label": "新安裝（建議）", "description": "第一次用 Altention，或想從頭開始"},
                        {"label": "從備份還原", "description": "換了電腦，或舊的壞了：從之前備份的 private repo 拿回記憶、設定和紀錄"}]}


def machine_question(names, listed=False):
    """Which old machine this one takes the place of, after a restore (the install skill's words; C-45). names:
    [(name, [where])] as instance.names() gives them; the last option keeps this machine's own name, offered only
    when the instance has no machine list (listed): a machine the list does not have installs nothing (C-57 S2;
    adding a machine to a set that is still in use is another thing, not a restore: manager 2026-10-20)."""
    only = ["plugins.json"]  # instance.ONLY_SETTINGS
    shown, rest = (names[:4], names[4:]) if listed else (names[:3], names[3:])  # four options at most: the others typed
    return {"key": "machine", "values": [n for n, _ in shown] + ([] if listed else [""]) + [n for n, _ in rest],
            "header": "接替哪台", "multiSelect": False,
            "question": "這台要接替哪一台舊機器？照機器名字記的設定（哪台關掉了哪些功能、對話紀錄寫進哪個資料夾）會變成這台的。"
                        + ("其他的名字（%s）選「其他」打出來。" % "、".join(n for n, _ in rest) if rest else "")
                        + ("從備份還原就是接替其中一台。舊機器還在用、想多加一台的話，這不是還原：在原本那套的 Discord "
                           "跟 Claude 說「加一台機器」。" if listed else ""),
            "options": [{"label": n, "description": "只出現在設定裡：可能是打錯的，或是一台沒寫進機器清單的機器"
                         if w == only else "出現在：%s" % "、".join(w)} for n, w in shown]
            + ([] if listed else [{"label": "當成新的一台",
                                   "description": "用這台自己的名字：照舊名字記的設定不會套到這台，之後要自己重設"}])}


def dashboard_question(again=False, gone=False, only=None):
    """C-57 S3: an installation from before #dashboard (or whose board channel is gone): make it, or turn the boards
    off. Asked, never made unasked: it is one more channel in the main area (owner's decision 7, 2026-10-19).
    again: they chose to make it before and it was never made: asked as that. gone: it was there and was deleted.
    only: (the board without a channel, whether its channel was deleted, the board still in place, its channel ID):
    the boards are in two channels and one is still there (C-67): asked of that one board alone."""
    if only:
        lost, deleted, kept, where = only
        return {"key": "dashboard", "values": ["make", "off"], "header": "看板頻道", "multiSelect": False,
                "question": "%s的頻道%s（%s還在 <#%s>）。要把%s放回看板頻道嗎？" % (
                    lost, "已經不在了" if deleted else "還沒設定", kept, where, lost),
                "options": [{"label": "放回去（建議）", "description": "放進 #dashboard（沒有就開一個），看板自己更新"},
                            {"label": "不要了", "description": "只關掉%s，%s照舊" % (lost, kept)}]}
    if gone:
        return {"key": "dashboard", "values": ["make", "off"], "header": "看板頻道", "multiSelect": False,
                "question": "#dashboard 已經不在了，要重新建一個嗎？",
                "options": [{"label": "重新建（建議）", "description": "在主區再開一個 #dashboard，看板自己更新"},
                            {"label": "不要了", "description": "不開看板頻道，狀態和用量看板都關掉"}]}
    if again:
        return {"key": "dashboard", "values": ["make", "off"], "header": "看板頻道", "multiSelect": False,
                "question": "上次你選了開 #dashboard，可是它還沒建好（那時設定沒做完）。要現在建好嗎？",
                "options": [{"label": "建好它（建議）", "description": "在主區開 #dashboard，看板自己更新"},
                            {"label": "不要了", "description": "不開看板頻道，狀態和用量看板都關掉"}]}
    return {"key": "dashboard", "values": ["make", "off"], "header": "看板頻道", "multiSelect": False,
            "question": "要開一個 #dashboard 頻道放看板嗎？看板會顯示各頻道在忙什麼、機器的狀況、要注意的事，"
                        "還有訂閱額度用了多少。之後想關，跟 Claude 說一聲就好。",
            "options": [{"label": "開（建議）", "description": "在主區多一個 #dashboard，看板自己更新"},
                        {"label": "不要", "description": "不開看板頻道，狀態和用量看板都關掉"}]}


def tour_question():
    """The last question of #setup, once it is done: the tour (the tour skill's own opening, word for word)."""
    return {"key": "tour", "values": ["start", "later", "no"], "header": "導覽", "multiSelect": False,
            "question": "要我帶你試一下機器人的主要功能嗎？大約十分鐘，每一步你做一件事。會用掉一點訂閱額度，"
                        "大約跟平常聊十幾二十句差不多。",
            "options": [{"label": "開始", "description": "一步一步試幾個主要功能"},
                        {"label": "之後再說", "description": "之後任何頻道打 /tour 再叫我"},
                        {"label": "不用了", "description": "不帶導覽"}]}


BACKUPS = (("instance", None, "記憶、知識、設定和機器清單"),  # remote.py's names, the plugin each needs, what it holds
           ("logs", "chatlog", "每段對話逐字的內容（密碼、金鑰這類秘密會遮掉），是最私人的資料：只放進 private 的 repo"),
           ("progress", "progress", "每個專案做到哪裡的整理"),
           ("planner", SECRETARY, "秘書的短期計畫、提醒和排程"))


def backup_questions(off=()):
    """The install's backup questions (C-25, decisions 1a, 2a, 4a): now or later, then which ones, one private repo
    each. A part turned off here (`off`: its plugin's name) is not offered; the instance always is. The chat records
    are offered like the others, with what they hold said plainly (verbatim): "values" are ops/remote.py's names."""
    import remote
    items = [(n, d) for n, p, d in BACKUPS if p is None or p not in off]
    return [
        {"key": "backup", "values": ["now", "later"], "header": "備份", "multiSelect": False,
         "question": "備份：你的記憶、設定、對話紀錄、進度和秘書的計畫現在都只存在這台電腦，電腦壞了就救不回來。"
                     "要不要建立 private（不公開）的 repo，大約每 15 分鐘自動備份一次？",
         "options": [{"label": "現在設定（建議）", "description": "我帶你建 private 的 GitHub repo（或用你自己的 git 主機），之後自動備份"},
                     {"label": "之後再說", "description": "先只存在這台；系統檢查會一直提醒還沒有備份"}]},
        {"key": "backup.items", "values": [n for n, _ in items], "header": "備份哪些", "multiSelect": True,
         "question": "要備份哪幾樣？建議全部勾選。每一樣各用一個 private 的 repo。",
         "options": [{"label": remote.LABELS[n], "description": d} for n, d in items]},
    ]


def off_here():
    """The plugins plainly turned off (or placed elsewhere, or drafts) on this machine; an unknown one counts as on."""
    try:
        rows, _ = plugin.on_this_machine()
    except Exception:
        return set()
    return {n for n, _, _, w, _ in rows if w in ("off", "disabled", "draft")}


def questions_main(as_json, setup=False, out=None, times=False, backup=False):
    out = out or sys.stdout
    plugins, problems = plugin.discover()
    if backup:
        asked = backup_questions(off_here())
    elif times:
        asked = briefing_questions()
    elif setup:
        import instance
        asked = setup_questions(plugins, instance.system_timezone(None), not os.path.exists(os.path.join(plugin.ENGINE, ".git")))
    else:
        asked = questions(plugins)
    if as_json:
        json.dump(asked, out, ensure_ascii=False, indent=1)
        out.write("\n")
    else:
        for a in asked:
            out.write("%s\n%s\n" % (a["question"], "".join("  %s：%s\n" % (o["label"], o["description"]) for o in a["options"])))
    for p in problems:
        (sys.stderr if as_json else out).write("問題：%s\n" % p)
    return 1 if problems else 0


def refresh_report(now=None):
    """After apply --write: this machine's report of what apply would change (3a.12, the minute round writes it every
    15 minutes for doctor) is worked out again at once, so doctor never shows one older than what was just written
    (it said "會補 planner/…" for a quarter hour after install.sh had added them, and a Claude took that for "not
    installed", 2026-10-15). Never stops apply: a report that cannot be made is removed, so it is not shown stale."""
    import time
    path = None
    try:
        from altention import cron_schedule
        if cron_schedule.load():
            raise RuntimeError(cron_schedule.BROKEN)
        machine = plugin.this_machine([])
        if not machine:
            raise RuntimeError("不知道這台叫什麼")
        path = cron_schedule.report_paths(machine)[0]
        rep = report()
        rep["at"] = int(now if now is not None else time.time())
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = "%s.%d.tmp" % (path, os.getpid())
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(json.dumps(rep, ensure_ascii=False, indent=1) + "\n")
        os.replace(tmp, path)
    except Exception as e:
        if path:
            try:
                os.remove(path)
            except OSError:
                pass
        print("套用報告沒有重算（%s: %s）；下一個 15 分鐘會再算" % (type(e).__name__, str(e)[:120]))


def setup_one(name, item, out=sys.stdout):
    """plugins.py setup <plugin> <item>: one setup item of an external plugin on here run by hand (C-61 E2): a lazy
    one, or one given up on after failing twice (a run that goes through takes it up again by itself)."""
    from altention import plugin_update
    rows, _ = plugin.on_this_machine()
    row = next((r for r in rows if r[0] == name and r[2] is not None and plugin.external(r[1])), None)
    if row is None:
        out.write("這台沒有外部插件 %s（python3 ops/plugins.py show 看有哪些）\n" % name)
        return 1
    _, folder, d, where, why = row
    if where != "on":
        out.write("%s 在這台不是開著的（%s），不跑它的安裝步驟\n" % (name, why or where))
        return 1
    if item not in [i.get("name") for i in d.get("setup", [])]:
        out.write("%s 沒有叫 %s 的安裝步驟（有：%s）\n" % (name, item, "、".join(i.get("name") for i in d.get("setup", []))))
        return 1
    version = plugin_update.head(folder) if os.path.isdir(os.path.join(folder, ".git")) else ""
    ran = plugin_update.run_setup(name, folder, d, version, only=item, log=lambda t: out.write(t.rstrip("\n") + "\n"))
    for n, st, words in ran:
        out.write("%s %s：%s\n" % (n, {"ran": "跑好了"}.get(st, "沒過"), words))
    return 0 if ran and ran[0][1] in ("ran", "record") else 1


def main(argv):
    from altention import engine_id
    if (argv[:1] == ["apply"] and "--write" in argv) or argv[:1] == ["restore"]:
        why = engine_id.refusal(plugin.ENGINE, "plugins.py %s" % argv[0])
        if why:  # a worktree or a second clone would add every line and hook again (code-review 2026-10-06)
            print(why)
            return 1
    elif argv[:1] in (["plan"], ["show"], ["held"], ["apply"], ["list"], ["backups"]):
        said = engine_id.warning(plugin.ENGINE)
        if said:
            print(said)
    if argv == ["report"]:  # 3a.12: read only; the minute round takes the JSON and keeps it (cron_schedule.py)
        json.dump(report(), sys.stdout, ensure_ascii=False)
        sys.stdout.write("\n")
        return 0
    if argv[:1] == ["list"]:
        return list_all()
    if argv[:1] == ["setup"] and len(argv) == 3:
        return setup_one(argv[1], argv[2])
    if argv[:1] == ["questions"] and set(argv[1:]) <= {"--json", "--setup", "--times", "--backup"} \
            and len({"--setup", "--times", "--backup"} & set(argv)) <= 1:
        return questions_main("--json" in argv, "--setup" in argv, times="--times" in argv, backup="--backup" in argv)
    if argv[:1] == ["plan"]:
        return plan()
    if argv[:1] == ["apply"]:
        write, allow, hold, moved, rest = False, [], [], [], argv[1:]
        while rest:  # nothing unknown is ignored: a mistyped flag must not turn into a write
            a = rest.pop(0)
            if a == "--write":
                write = True
            elif a == "--allow-time" and rest and not rest[0].startswith("-"):
                allow.append(rest.pop(0))
            elif a == "--hold" and rest and not rest[0].startswith("-"):
                hold.append(rest.pop(0))
            elif a == "--moved-from" and rest and not rest[0].startswith("-"):
                moved.append(rest.pop(0))  # an old engine folder that is gone: its lines and hooks are ours (C-17)
            else:
                print("apply：不認得 %r（只有 --write、--allow-time 插件/項目、--hold 插件或 bot、--moved-from 舊引擎資料夾），什麼都沒做" % a)
                return 2
        rc = apply(write, allow, hold=hold, moved_from=moved)
        if write:
            refresh_report()
        if rc == 0 and write:
            print("ALT-STEP-OK plugins-apply")  # ops/apply.py's remote step checks for it (not just the exit status)
        return rc
    if argv[:1] == ["backups"]:
        return backups()
    if argv[:1] == ["held"] and len(argv) > 1:
        return held(argv[1:])
    if argv[:1] == ["restore"] and len(argv) == 2:
        try:
            problems = []
            applycore.MACHINE[0] = plugin.this_machine(problems)
            with applycore.one_at_a_time(), applycore.home_lock():
                try:
                    done = applycore.restore(argv[1], os.path.realpath(plugin.ENGINE))
                except (OSError, ValueError) as e:
                    raise applycore.Refused("還原時出錯（%s），請看備份資料夾手動處理" % e)
            print("還原了：%s" % "、".join(done))
            return 0
        except applycore.Refused as e:
            print("沒還原：%s" % e)
            return 1
    if argv[:1] == ["show"]:
        rest = [a for a in argv[1:] if a != "--json"]
        return show(rest[0] if rest else None, "--json" in argv)
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
