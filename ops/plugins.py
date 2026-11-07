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
  python3 ops/plugins.py list --catalog      # also the official list's left-out repositories and why (C-81)
  python3 ops/plugins.py list --catalog-now  # look at the official list now (at most once every 10 minutes), then list
  python3 ops/plugins.py add <插件> [vX.Y.Z] [--yes [--hooks-ok]]
                                            # a plugin from the official list (C-81 K2): without --yes, what it
                                            # would do on this computer (nothing written); with --yes, written into
                                            # the instance (source, version), committed and pushed; one with hooks
                                            # or a rewrite of replies also needs --hooks-ok, asked on its own
  python3 ops/plugins.py lose <插件>          # what turning it off loses (its plugin.json "lose", what needs it,
                                            # its schedules, hooks and channels), to say before turning it off (C-85)
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
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config, plugin, text as alt_text  # noqa: E402
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
            words = PLACE_WORDS["blocked_plugin"] if where == "blocked" and not plugin.blocked_by_engine(reason) \
                else PLACE_WORDS.get(where, "")
            out.write("%-8s %-9s%s%s\n" % (name, where, words, "：" + reason if reason else ""))
        for p in problems:
            out.write("問題：%s\n" % p)
        for u in unknown:
            out.write("%s\n" % u)
    return 1 if problems or unknown else 0


LABEL = {"same": "一致", "time differs": "時間不同", "paused": "暫停中", "missing": "少了", "extra": "多的",
         "unclaimed": "沒認領", "duplicate": "重複", "short": "不足", "elsewhere": "不在這台", "replaced": "換掉舊的",
         "moved": "搬家前的舊路徑", "gone": "舊資料夾不在", "turned off": "實例關掉", "held": "設定不能用，不動",
         "gated": "等安裝步驟做完",
         "hook off": "插件在這台關掉了"}
PLACE_WORDS = {"on": "開著", "off": "不在這台跑（放在別台）", "disabled": "在這台刻意關掉（disabled_on）",
               "draft": "草稿（實例還沒寫）", "blocked": "不啟用（這版引擎沒有它要的介面）",
               "blocked_plugin": "不啟用（提供它所需介面的插件沒開，或版本不對）", "unknown": "不知道"}  # C-85 (f): one word each, as doctor says them


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
        theirs = plancore.external_hooks(rows, settings or {})  # C-61 E5: the external plugins' own hooks
        hooks = plancore.installed_hooks(cc, engine, theirs)
    except (plancore.Unknown, OSError) as e:  # an unreadable settings.json is not "no hooks"
        problems.append("%s：hook 不比對（不知道）" % e)
        cc, text, hooks, want, theirs = None, None, [], [w for w in want if w["kind"] != "hook"], {}
    try:
        known = set(config.inventory().get("machines") or {})
    except Exception:
        known = set()
    away = plancore.elsewhere(rows, machine, settings, bot, known)
    gather.theirs = theirs  # read by compared(): not in the tuple, which every caller unpacks
    return machine, rows, problems, want, unknown, raw, cron, paused, text, cc, hooks, engine, away


def compared(want, cron, hooks, paused, away, engine, cc, rows, moved_from=()):
    """plancore.compare with what gather() worked out: the hooks of an engine moved elsewhere, and (C-61 E5) the
    external plugins' own hooks with the plugins whose hooks go (applycore.removable)."""
    theirs = getattr(gather, "theirs", {})
    return plancore.compare(want, cron, hooks, paused, away, engine, plancore.other_hooks(cc, engine, theirs) if cc else (),
                            moved_from, theirs, applycore.removable(rows))


def plan(out=sys.stdout):
    """This machine (plan reads its own crontab and settings): each cron line and hook, same, time differs, paused,
    missing, extra or unclaimed. Exit 1 when anything differs, or something could not be read or placed."""
    machine, rows, problems, want, unknown, lines, cron, paused, text, cc, hooks, engine, away = gather()
    result = compared(want, cron, hooks, paused, away, engine, cc, rows)
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
        if status == "gated":
            out.write("     %s\n" % alt_text.t("plugin.gated", step=w["step"]))
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
    differ = sum(v for k, v in counts.items() if k not in ("same", "paused", "unclaimed", "held", "gated"))  # on purpose / not ours / unknown / waiting
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
    result = compared(want, cron, hooks, paused, away, engine, cc, rows)
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
    lines_here = set(h[1] for h in cron) | set(p[1] for p in paused)
    stopped = [(n, f) for n, f, _d, where, _r in rows if n in off and f]
    out["leftovers"] = []
    for status, w, installed, _note in result:
        if status == "same":
            continue
        if status == "unclaimed":
            out["unclaimed"] += 1
            owner = next((n for n, f in stopped if installed in lines_here and (
                plancore.marked(installed) == n or f.rstrip("/") + "/" in installed)), None)
            if owner:  # C-85 (f): a line of a plugin turned off here that apply leaves (commented out, or the old format)
                out["leftovers"].append({"plugin": owner, "installed": installed[:300]})
        elif status == "gone":  # an old engine folder gone, not named: kept by apply, said by doctor (plugins.old-engine)
            out["gone"].append(item(status, w, installed))
        elif status == "paused":
            out["paused"].append(item(status, w, installed))
        elif (w["plugin"] if w else plancore.marked(installed or "")) == "core":
            out["core"].append(item(status, w, installed))
        elif status in ("missing", "replaced"):  # replaced: the new line in the old one's place
            out["add"].append(item(status, w, installed))
        elif status in ("held", "gated"):  # an override that cannot be used (in unknown already), or waiting for a step
            continue  # (C-77 G5): its line is never touched
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
    out["blocked"] = sorted([n, r] for n, _f, _d, where, r in rows if where == "blocked")  # C-94: [name, why]
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


PLACED = "placements.json"  # C-85 (c): what each plugin was here at the last apply --write, for its channels' notices
TURNED = {"disabled": "關掉了", "off": "不在這台跑了（放在別台）", "draft": "變成草稿了（實例沒寫它）",
          "blocked": "停了（這版引擎沒有它要的介面）"}


def notify(channel, text):
    """altention dc notify -c CHANNEL TEXT: shown in the channel, never handed to its Claude; the bot keeps one it
    cannot post yet. True when it went out; never raises, never waits more than a minute (apply goes on regardless)."""
    try:
        r = subprocess.run([os.path.join(plugin.ENGINE, "bin", "altention"), "dc", "notify", "-c", str(channel), text],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def announce(rows, machine, settings, out=sys.stdout):
    """C-85 (c): a plugin that this apply finds on here and was not at the last one (or the other way) says so once
    in each channel its plugin.json declares ("channels"[].setting: the channel's ID in the instance), or in the
    notification channel (@notify) when it has none set, naming this machine; with machines "all", the other machines still running it are named too. The first apply here (nothing
    noted yet) notes without saying. A plugin whose notice did not go out keeps its old note, so the next apply says
    it again; nothing here ever fails apply."""
    path = os.path.join(applycore.state_root(machine), PLACED)
    try:
        with open(path, encoding="utf-8") as f:
            before = json.load(f)
        before = before if isinstance(before, dict) else None
    except (OSError, ValueError):
        before = None
    now = {n: w for n, _f, _d, w, _r in rows if w in ("on", "off", "disabled", "draft", "blocked")}
    keep = dict(before or {}, **now)  # one not known this time (unknown) keeps what it was
    if before is not None:
        for name, folder, d, where, _r in rows:
            was = before.get(name)
            if d is None or name not in now or was is None or (was == "on") == (where == "on"):
                continue
            channels = [config.setting(c["setting"]) for c in d.get("channels", []) if isinstance(c, dict) and c.get("setting")]
            channels = list(dict.fromkeys(str(c) for c in channels if c))
            title = d.get("title") or name
            own = bool(channels)  # none to say it in (a rewrite of every channel's replies, say): the notification
            channels = channels or ["@notify"]  # channel says it, in words without 「這裡」 (L3 off/on run)
            if where == "on":
                text = "%s 在 %s 打開了，這裡會再開始更新。" % (title, machine) if own else \
                    alt_text.t("plugins.notify_on", title=title, machine=machine)
            else:
                others = []
                if d.get("machines", "all") == "all":
                    try:
                        others = [m for m in sorted(config.machines()) if m != machine and
                                  {n: w for n, _f2, _d2, w, _r2 in plugin.on_this_machine(m)[0]}.get(name) == "on"]
                    except Exception:
                        others = []
                turned = TURNED.get(where, "停了")
                if not own:
                    text = alt_text.t("plugins.notify_off_others", title=title, machine=machine, turned=turned,
                                      others="、".join(others)) if others else \
                        alt_text.t("plugins.notify_off", title=title, machine=machine, turned=turned)
                else:
                    text = "%s 在 %s %s，%s" % (title, machine, turned,
                                              "它在 %s 的部分不會再更新；%s 照常。" % (machine, "、".join(others)) if others
                                              else "這裡不會再更新。")
            sent = all([notify(c, text) for c in channels])
            out.write("- %s：%s（%s）\n" % ("說了" if sent else "沒說出去，下次 apply 再說", text, "、".join(channels)))
            if not sent:
                keep[name] = was
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        applycore.private_write(path, json.dumps(keep, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    except Exception as e:
        out.write("記不下這次各插件的狀態（%s）：下次 apply 會再比一次\n" % e)


def apply(write=False, allow_time=(), out=sys.stdout, hold=(), moved_from=()):
    """Without `write`: what would change, nothing written. With it: plan's result written into this machine's crontab
    and settings.json under applycore's rules and guards, each step said on its own (one may be written and the
    other not); nothing is written when anything could not be read."""
    machine, rows, problems, want, unknown, raw, cron, paused, text, cc, hooks, engine, away = gather()
    applycore.MACHINE[0] = machine
    applycore.clean_if_free()
    result = compared(want, cron, hooks, paused, away, engine, cc, rows, moved_from)
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
            announce(rows, machine, settings, out)
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
                if wrote_cc:
                    applycore.note_hooks_removed(result)
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
    announce(rows, machine, settings, out)  # after the lock: a notice may take a while, apply's writes are done
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


NOTHING = "不會在這台多做什麼（沒有排程、hook、改寫回覆、skill、安裝指令、資料、頻道、要的系統工具）"


def does_here(d):
    """[plain words]: what plugin.json `d` does on this computer once it is on (E3's added(), from nothing), and the
    system tools it needs (never installed)."""
    from altention import plugin_update
    said = plugin_update.added({}, d)
    tools = [n.get("name") for n in d.get("needs") or [] if isinstance(n, dict) and n.get("name")]
    if tools:
        said.append("要這些系統工具（不會自己裝）：%s" % "、".join(tools))
    return said


def engine_release():
    """This engine's release (vX.Y.Z), None when it cannot be told."""
    try:
        from altention import update
        return update.release_in(update.Round())[0]
    except Exception:
        return None


NONE_YET = "目前還沒有可以裝的官方插件"  # the list was read and has none: not a fault


def catalog_part(listed, settings, out, left_out=False):
    """「可以裝的」: the official list's plugins the instance does not name (C-81 K2), each with what it would do here
    and how to put it on; or why there is no list."""
    from altention import plugin_update
    named = set(((settings or {}).get("plugins") or {}).keys()) if isinstance((settings or {}).get("plugins"), dict) else set()
    if listed is None:
        out.write("官方插件清單還沒抓到（要連得上 GitHub；python3 ops/plugins.py list --catalog-now 現在抓）\n")
        return
    at = float(listed.get("at") or 0)
    old = int((time.time() - at) // 86400) if at else None
    rows = [(n, e) for n, e in sorted(listed["plugins"].items()) if n not in named]
    out.write("可以裝的（官方清單，%s%s；實例還沒寫的才列）：%s\n" % (
        time.strftime("%m-%d", time.localtime(at)) + " 抓的" if at else "還沒抓完",
        "，%d 天前的清單" % old if old and old >= 30 else "",
        "" if rows else "沒有" if listed["plugins"] else NONE_YET))
    now = engine_release()
    for n, e in rows:
        d = e.get("plugin") or {}
        out.write("  %-8s %-7s %s：%s\n" % (n, e.get("version"), d.get("title", n), d.get("pitch") or d.get("description", "")))
        said = does_here(d)
        out.write("    會做的事：%s\n" % ("；".join(said) if said else NOTHING))
        if not plugin_update._engine_ok(d.get("engine"), now):
            out.write("    要引擎 %s，這台是 %s：先更新引擎\n" % (d.get("engine"), now))
        else:
            out.write("    裝：到 #setup 跟 Claude 說「裝 %s」\n" % n)
    if left_out:
        for x in listed.get("left_out") or []:
            out.write("  沒收：%s %s：%s\n" % (x.get("repo"), x.get("version", ""), x.get("why")))


machines_for = plugin.machines_for  # C-77 G3a: moved to the core, where secret_store needs it too
users_elsewhere = plugin.users_elsewhere


def plugin_json_of(name):
    """An external plugin's plugin.json: its folder's here, else the official list's; None when neither reads."""
    from altention import catalog, plugin_update
    try:
        with open(os.path.join(plugin_update.folder_of(name), "plugin.json"), encoding="utf-8") as f:
            return plugin.check(json.load(f), "%s/plugin.json" % name)
    except (OSError, ValueError, plugin.PluginError):
        pass
    e = ((catalog.load() or {}).get("plugins") or {}).get(name)
    return e.get("plugin") if isinstance(e, dict) else None


def add(name, version=None, yes=False, hooks_ok=False, out=sys.stdout, again=None, apply_cmd=None):
    """C-81 K2: put a plugin from the official list on. Without `yes`, what it would do here and the command that
    puts it on (nothing written); with `yes`, written into the instance as {"source": <its public https address>,
    "version"}, committed and pushed. A plugin with hooks also needs `hooks_ok` (asked on its own: C-61 decision 4).
    Refused (1) when it is not on the list, the instance already names it, the version is not one of its tags, its
    plugin.json there is not usable, or it needs a newer engine. 0 done (or told), 1 refused, 2 a step failed.
    `again`: the command that puts it on, as the one asking runs it (setup.py plugin-add in #setup); `apply_cmd`:
    the one that brings another machine in line for it (setup.py plugin-apply, given the plugin and the machine;
    else ops/apply.py, given the machine). The other machines
    are never done from here (manager 2026-10-21): they are named, with that command."""
    from altention import catalog, plugin_update
    listed = catalog.load()
    if listed is None:
        out.write("官方插件清單還沒抓到（要連得上 GitHub）：python3 ops/plugins.py list --catalog-now\n")
        return 1
    e = listed["plugins"].get(name)
    if not e:
        out.write("官方清單沒有 %s%s\n" % (name, "（清單裡有：%s）" % "、".join(sorted(listed["plugins"])) if listed["plugins"] else "（%s）" % NONE_YET))
        return 1
    try:
        block = config.settings().get("plugins")
    except Exception as ex:
        out.write("實例設定讀不出來（%s）\n" % ex)
        return 2
    if isinstance(block, dict) and name in block:
        out.write("實例已經寫了插件 %s：不用裝（要換版本，在 #update 或跟 #setup 說）\n" % name)
        return 1
    tag = version or e["version"]
    if tag != e["version"]:  # another version than the list's newest: it must be one of the tags
        if not plugin_update._tag_key(version):
            out.write("版本要寫成 vX.Y.Z，收到 %r\n" % version)
            return 1
        try:
            there = catalog.tags(name)
        except catalog.Unreached as ex:
            out.write("連不上 %s（%s）：等一下再試\n" % (e["repo"], ex))
            return 2
        if version not in there:
            out.write("%s 沒有 %s 這一版（有：%s）\n" % (name, version, "、".join(there[:8]) or "沒有"))
            return 1
    d = e.get("plugin") or {}
    if tag != e["version"]:
        try:
            got, why = catalog.entry(name, tag)
        except catalog.Unreached as ex:
            out.write("連不上 GitHub（%s）：等一下再試\n" % ex)
            return 2
        if not got:
            out.write("%s %s 不能裝：%s\n" % (name, tag, why))
            return 1
        d = got["plugin"]
    now = engine_release()
    if not plugin_update._engine_ok(d.get("engine"), now):
        out.write("%s %s 要引擎 %s，這台是 %s：先更新引擎再裝\n" % (name, tag, d.get("engine"), now))
        return 1
    hooks = plugin_update.watchers({}, d)  # its hooks and a rewrite of replies: they see the conversations
    if not yes:
        said = does_here(d)
        out.write("官方插件 %s（%s %s）：%s\n" % (d.get("title", name), name, tag, d.get("pitch") or d.get("description", "")))
        out.write("裝了之後會在用它的機器上：\n" if said else "裝了之後%s\n" % NOTHING)
        for s in said:
            out.write("  - %s\n" % s)
        out.write("用它的機器：%s\n" % ("每一台" if d.get("machines", "all") == "all" else "機器人那台"))
        if hooks:
            out.write("它會看到你的對話內容（%s）：這一點要另外問過，同意了才加 --hooks-ok\n" % "、".join(hooks))
        out.write("要裝：%s %s %s --yes%s\n" % (again or "python3 %s add" % os.path.abspath(__file__), name, tag,
                                               " --hooks-ok" if hooks else ""))
        return 0
    if hooks and not hooks_ok:
        out.write("%s 會看到你的對話內容（%s），要另外問過使用者，同意了再加 --hooks-ok：沒有裝\n" % (name, "、".join(hooks)))
        return 1
    source = re.sub(r"\.git$", "", e["repo"]) + ".git"
    ok, why = plugin_update.declare(name, source, tag)
    if not ok:
        out.write("沒有裝：%s\n" % why)
        return 1
    from altention import update_go
    unpushed = update_go.push_instance()
    out.write("寫進實例了：plugins.%s = {source: %s, version: %s}%s\n" % (
        name, source, tag, "" if not unpushed else "（實例還沒推出去：%s；同步下一輪會推）" % unpushed))
    if again is None:
        out.write("接著：這台跑 ./install.sh，最後 python3 ops/doctor.py\n")
    others = users_elsewhere(d)
    if others:
        out.write("別台用到它的：%s。它們下一輪更新時會 clone，但排程和 hook 要那台跑 apply 才會裝上；要現在裝：%s\n" % (
            "、".join(others), "；".join(("%s %s %s" % (apply_cmd, name, m)) if apply_cmd else
                                        "python3 %s %s" % (os.path.join(plugin.ENGINE, "ops", "apply.py"), m) for m in others)))
    return 0


def lose(name, out=sys.stdout):
    """C-85 (e): what turning plugin `name` off on a machine loses, in plain words, for Claude to say before asking:
    its plugin.json "lose", the plugins that need it (they go off with it), its schedules, hooks (they stop: the
    conversations are no longer seen) and channels (they stop being updated). Its data stays. 1 when there is no
    such plugin; the core cannot be turned off."""
    plugins, problems = plugin.discover()
    found = {n: (f, d) for n, f, d in plugins}
    if name == "core":
        out.write("核心不能關。\n")
        return 1
    if name not in found:
        out.write("沒有叫 %s 的插件（有：%s）\n" % (name, "、".join(sorted(n for n in found if n != "core"))))
        return 1
    _f, d = found[name]
    title = d.get("title") or name
    out.write("關掉%s（%s）會少掉：%s\n" % (title, name, d.get("lose") or "（plugin.json 沒寫）"))
    users = sorted(found[n][1].get("title") or n for n in found if any(
        isinstance(x, dict) and x.get("plugin") == name for x in found[n][1].get("requires", [])))
    if users:
        out.write("要靠它的也會停：%s\n" % "、".join(users))
    try:
        settings = config.settings()
    except Exception:
        settings = {}
    items, _said = plugin.effective_schedules(name, d, settings or {})
    if items and name != "core":
        out.write("停掉的排程：%s\n" % "、".join("%s（%s）" % (it.get("name"), schedule_words(it)) for it in items))
    if d.get("hooks"):
        out.write("拿掉的 hook：%s（它不再看到 Claude 的對話）\n" % "、".join(
            "%s%s" % (h.get("event"), "（%s）" % h["matcher"] if h.get("matcher") else "") for h in d["hooks"]))
    if d.get("channels"):
        out.write("不再更新的頻道：%s\n" % "、".join("#%s" % c.get("name") for c in d["channels"] if isinstance(c, dict)))
    out.write("它的資料留著，再打開就接著用。\n")
    return 0


def list_all(out=sys.stdout, left_out=False):
    """One line per plugin, in _ordered's order; under each, its "pitch" (what it does for the user, for the install's
    questions) and the plugins it needs. Drafts (external plugins the instance does not declare, C-61 E1) next, under
    「草稿」, with what to write to turn one on; last the official list's plugins the instance does not name (C-81),
    with `left_out` the list's repositories left out and why."""
    from altention import catalog
    listed = catalog.load()
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
            there = ((listed or {}).get("plugins") or {}).get(name)
            out.write("  %-8s %s（%s）%s\n" % (name, d["title"], folder,
                                              "：官方清單有這個插件（%s），寫 source 就能裝" % there["version"] if there else ""))
        out.write("  要開：實例 plugins.json 寫 \"plugins\": {\"<名稱>\": {\"source\": \"<git 網址>\", \"version\": \"vX.Y.Z\"}}"
                  "（只在這台、不 clone 的寫 {\"local\": true}）\n")
    try:
        rows = plugin.on_this_machine()[0]
    except Exception:
        rows = []
    for name, words in plugin.external_notes(rows, settings):
        out.write("提醒：%s：%s\n" % (name, words))
    for name, folder, d, where, why in rows:
        if where == "blocked":  # C-94
            out.write(alt_text.t("plugins.list_blocked", plugin=name, why=why) + "\n")
        elif d is not None and where == "on" and plugin.external(folder) and "uses" not in d:  # on here only (manager)
            out.write(alt_text.t("plugins.list_no_uses", plugin=name) + "\n")
    for name, folder, d in _ordered(plugins) + drafts:
        for where, todo in plugin.retired(d):
            out.write("提醒：%s 的 plugin.json 還寫著 %s，引擎已經不讀了（%s）\n" % (name, where, todo))
    catalog_part(listed, settings, out, left_out)
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
            "機器人要先停下來問你要不要繼續？它會標註你，手機會跳通知。之後每個頻道可以各自設定。"
            "要別的次數，選「其他」打數字，0 是不限。",
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


PLANS = (("Pro", 1, "Pro"), ("Max", 5, "Max（5 倍 Pro）"), ("Ultra", 10, "Ultra（10 倍 Pro）"))  # usage.plan as
# [name, how many Pro allowances a week], as claude.com/pricing puts it (2026-11: Max "5x Pro usage", Ultra "10x Pro
# usage" a week; Max 20x no longer listed). At most 4 options with "none", so older names are typed (PLANS_TYPED).
PLANS_TYPED = {"Max 5x": ("Max", 5), "Max 20x": ("Max 20x", 20)}  # an old subscriber not moved yet types these
PLAN_WEEK = {"Pro": "Pro 的一週額度", "Max": "一週是 Pro 的 5 倍", "Ultra": "一週是 Pro 的 10 倍"}


def plan_question():
    """The plan, for the usage board's subscription limits (the install skill's own words until C-48); values are
    the plan's name, "none" for not reading the limits. Another plan typed in: "<name>=<Pro allowances>"."""
    return {"key": "usage.plan", "values": [n for n, _, _ in PLANS] + ["none"], "header": "方案", "multiSelect": False,
            "question": "你的 Claude 訂閱是哪個方案？用量看板會顯示訂閱額度用了多少：為了讀額度，每小時用 haiku 送一個最小的請求，"
                        "會算進 5 小時的額度。不想花這個額度就選最後一個。",
            "options": [{"label": l, "description": "%s；顯示訂閱額度（每小時一個最小的 haiku 請求）" % PLAN_WEEK[n]}
                        for n, _, l in PLANS]
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
    theirs = [(n, "插件 %s 自己的資料（%s），它寫明是私人的" % (pl, f)) for n, f, _p, pl in remote.plugin_data(private_only=True)]
    items += theirs  # C-61 E4: the private data of the external plugins on here
    return [
        {"key": "backup", "values": ["now", "later"], "header": "備份", "multiSelect": False,
         "question": "備份：你的記憶、設定、對話紀錄、進度%s現在都只存在這台電腦，電腦壞了就救不回來。"
                     "要不要建立 private（不公開）的 repo，大約每 15 分鐘自動備份一次？" % ("、秘書的計畫和插件的資料" if theirs else "和秘書的計畫"),
         "options": [{"label": "現在設定（建議）", "description": "我帶你建 private 的 GitHub repo（或用你自己的 git 主機），之後自動備份"},
                     {"label": "之後再說", "description": "先只存在這台；系統檢查會一直提醒還沒有備份"}]},
        {"key": "backup.items", "values": [n for n, _ in items], "header": "備份哪些", "multiSelect": True,
         "question": "要備份哪幾樣？建議全部勾選。每一樣各用一個 private 的 repo。",
         "options": [{"label": remote.label(n), "description": d} for n, d in items]},
    ]


def off_here():
    """The plugins plainly turned off (or placed elsewhere, drafts, blocked) on this machine; an unknown one counts as on."""
    try:
        rows, _ = plugin.on_this_machine()
    except Exception:
        return set()
    return {n for n, _, _, w, _ in rows if w in ("off", "disabled", "draft", "blocked")}


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


def main(argv, again=None, apply_cmd=None):
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
    if argv[:1] == ["list"] and set(argv[1:]) <= {"--catalog", "--catalog-now"}:
        if "--catalog-now" in argv:
            from altention import catalog
            state, words = catalog.refresh(force=True)
            print(words or ("官方插件清單 10 分鐘內看過了，用剛才的" if state == "kept" else "官方插件清單：%s" % state))
        return list_all(sys.stdout, left_out="--catalog" in argv)
    if argv[:1] == ["add"] and 2 <= len([a for a in argv if not a.startswith("--")]) <= 3 \
            and set(a for a in argv if a.startswith("--")) <= {"--yes", "--hooks-ok"} \
            and ("--hooks-ok" not in argv or "--yes" in argv):
        rest = [a for a in argv[1:] if not a.startswith("--")]
        return add(rest[0], rest[1] if len(rest) > 1 else None, "--yes" in argv, "--hooks-ok" in argv, out=sys.stdout, again=again, apply_cmd=apply_cmd)
    if argv[:1] == ["lose"] and len(argv) == 2:
        return lose(argv[1], sys.stdout)
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
