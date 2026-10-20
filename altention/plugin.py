"""Plugins: read and check plugin.json files, and work out which plugins this machine runs (stage 3a.1).

A plugin is a folder with a plugin.json (DESIGN.md section 6). Where they are looked for:
- the engine: altention/core.plugin.json (the core itself), <engine>/<folder>/plugin.json, <engine>/skills/*/plugin.json,
  <engine>/plugins/*/plugin.json (built-in plugins, once stage 4 moves them there);
- external plugins: <root>/plugins/*/plugin.json (~/altention/plugins/my-plugin …);
- local plugins: <instance>/plugins/*/plugin.json.

plugin.json is public, so it holds no personal values; those live in the instance's plugins.json:
    "plugins": {"<name>": {"on": "host-a" | ["host-a", "host-b"], "disabled_on": ["host-b"], …}}
A plugin with "machines": "all" runs on every machine unless the instance lists the machine in disabled_on (written
"on purpose", and doctor says so rather than calling it broken). One with "machines": "one" runs where the
instance's "on" says, else on the bot's machine. The core always runs and cannot be turned off.

When what decides it cannot be read (the instance's settings or the machine list broken or unreadable, or "on"
naming a machine not in the list), a plugin's place is "unknown", never "off": plan shows it as unknown, and apply
and the self-repair add and remove nothing for it, only report (so a broken machines.json pushed to every
machine never takes anyone's cron lines or hooks away). A plugin.json that cannot be read is listed too, as
unknown. So plan, apply and the self-repair may remove an installed item only when it belongs to a plugin whose
plugin.json was read and which is plainly "off" (or "disabled") on this machine: never because a plugin is
missing from the list, never for "unknown". Settings of a plugin's own (usage.channel …) stay in the
instance's top-level sections; {setting:usage.channel} reads them.

Compatibility as everywhere in the core: missing fields take defaults, unknown fields are ignored, a newer
"format" is read with a warning. A plugin.json that cannot be read is reported and skipped; the others still load
(the bot, hooks and the scheduler must keep running on a broken file; only one-off tools stop on it).
Nothing here writes anything. Standard library only, Python 3.8.
"""
import glob
import json
import os
import re
import shlex
import shutil
import sys

from . import config

FORMAT = 1
ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = ("timer", "on-request", "none")
CORE_ONLY = ("cron", "settings", "links")  # blocks only the core may declare
ON_WORDS = ("all", "bot", "one", "sources")  # what a step's "on" may say besides machine names
# The engine update and the engine/instance sync never go into the scheduler (DESIGN 8): they must keep running when
# the time zone, plugins.json or the scheduler's state are broken, or a fix pushed to every machine never arrives.
# (only what is written out: a wrapper script that pulls inside is not seen; the guarantee is the sync's own cron line)
SYNC_RUN = re.compile(r"altention[./]sync\b|altention[./]update\b|\bhome-pull\b"
                      r"|\bgit\b[^;&|]*\s(pull|fetch)(\s|$)")
TOKEN = re.compile(r"\{(engine|plugin|python|claude|instance|data|root|setting:[A-Za-z0-9_.-]+)\}")


class PluginError(Exception):
    pass


def candidates(engine=None, instance=None, root=None):
    """Every plugin.json path, the core's first, without duplicates (a folder linked in twice counts once)."""
    engine = engine or ENGINE
    instance = instance if instance is not None else config.instance_dir()
    root = root or config.root()
    found = [os.path.join(engine, "altention", "core.plugin.json")]
    for pattern in (os.path.join(engine, "*", "plugin.json"), os.path.join(engine, "skills", "*", "plugin.json"),
                    os.path.join(engine, "plugins", "*", "plugin.json"), os.path.join(root, "plugins", "*", "plugin.json")):
        found += sorted(glob.glob(pattern))
    if instance:
        found += sorted(glob.glob(os.path.join(instance, "plugins", "*", "plugin.json")))
    seen, out = set(), []
    for path in found:
        real = os.path.realpath(path)
        if os.path.exists(real) and real not in seen:
            seen.add(real)
            out.append(path)
    return out


def _need(cond, path, what):
    if not cond:
        raise PluginError("%s：%s" % (path, what))


def _list_of(d, key, path, fields, optional=()):
    """d[key] is a list of objects having `fields` (each a str key, or (key, type))."""
    items = d.get(key, [])
    _need(isinstance(items, list), path, "%s 要是清單" % key)
    for i, item in enumerate(items):
        _need(isinstance(item, dict), path, "%s[%d] 要是物件" % (key, i))
        for f in fields:
            name, kind = f if isinstance(f, tuple) else (f, str)
            _need(isinstance(item.get(name), kind), path, "%s[%d] 少了 %s（或型別不對）" % (key, i, name))
        for name, kind in optional:
            if name in item and item[name] is not None:
                _need(isinstance(item[name], kind), path, "%s[%d].%s 型別不對" % (key, i, name))
    return items


def _length(text):
    """Minutes in "10m" or "2h", None if it is not such a length."""
    m = re.match(r"^(\d+)([mh])$", str(text))
    return int(m.group(1)) * (60 if m.group(2) == "h" else 1) if m else None


def check(d, path="plugin.json", core=False):
    """Raise PluginError naming the file and the field if `d` is not a usable plugin.json; return d. `core`: this is
    the engine's own altention/core.plugin.json, the only file that may be named core."""
    _need(isinstance(d, dict), path, "不是 JSON 物件")
    fmt = d.get("format", 1)
    _need(isinstance(fmt, int), path, "format 要是整數")
    if fmt > FORMAT:
        sys.stderr.write("altention：%s 的格式是 %d，比這版引擎認得的 %d 新；照樣讀，新的欄位會被忽略\n" % (path, fmt, FORMAT))
    for key in ("name", "title", "description"):
        _need(isinstance(d.get(key), str) and d[key].strip(), path, "少了 %s" % key)
    _need(re.match(r"^[a-z][a-z0-9-]*$", d["name"]) is not None, path, "name 只能用小寫英文、數字和 -")
    _need(d.get("machines", "all") in ("all", "one"), path, "machines 只能是 all 或 one")
    _need((d["name"] == "core") == core, path, "只有引擎的 altention/core.plugin.json 是核心，其他插件不能叫 core"
          if not core else "altention/core.plugin.json 的 name 要是 core")
    if not core:
        for key in CORE_ONLY:
            _need(key not in d, path, "%s 只有核心可以宣告" % key)
    _list_of(d, "hooks", path, ["event", "command"], [("matcher", str), ("timeout", int)])
    for i, s in enumerate(_list_of(d, "schedules", path, ["name", ("when", dict), ("do", dict)])):
        _need(isinstance(s.get("scheduler", True), bool), path, "schedules[%d].scheduler 只能是 true 或 false" % i)
        _need(not forbidden_run(s), path, "schedules[%d]（%s）是同步或引擎更新：永遠不進排程器，留在自己的 cron，"
              "標成 \"scheduler\": false" % (i, s.get("name")))
        _need(sum(k in s["when"] for k in ("at", "every", "once")) == 1, path,
              "schedules[%d].when 要剛好一種：at、every、once" % i)
        _need(sum(k in s["do"] for k in ("message", "run")) == 1, path, "schedules[%d].do 要剛好一種：message、run" % i)
        w = s["when"]
        if "at" in w:
            _need(re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(w["at"])) is not None, path,
                  "schedules[%d].when.at 要寫成 HH:MM（例如 08:05）" % i)
        if "every" in w and w.get("offset") not in (None, "machine"):
            every, off = _length(w["every"]), _length(w["offset"])
            _need(every and off is not None and off < every, path, "schedules[%d].when.offset 要比 every 小" % i)
    for i, s in enumerate(_list_of(d, "setup", path, ["name"], [("optional", bool), ("today", str)])):
        _need("run" in s and (s["run"] is None or isinstance(s["run"], str)), path,
              "setup[%d].run 要是指令，或 null（還只是記錄、不能執行）" % i)
    for i, s in enumerate(_list_of(d, "data", path, ["name", "path"], [("private", bool)])):
        _need(s.get("sync", "timer") in SYNC, path, "data[%d].sync 只能是 %s" % (i, "、".join(SYNC)))
    _list_of(d, "skills", path, ["name", "path"])
    _list_of(d, "requires", path, ["plugin"], [("data", str)])
    for i, c in enumerate(_list_of(d, "channels", path, ["name"], [("kind", str), ("setting", str), ("name_setting", str)])):
        _need(c.get("category", "main") in ("main", "system"), path, "channels[%d].category 只能是 main 或 system" % i)
    for i, a in enumerate(_list_of(d, "asks", path, ["key", "question"])):  # C-57 S3: a setting the reconcile asks in #setup
        _need(isinstance(a.get("unless", {}), dict), path, "asks[%d].unless 要是物件（設定 → 那個值就不用問）" % i)
    _list_of(d, "secrets", path, ["name", "path"])
    _list_of(d, "commands", path, ["name", "path"], [("desc", str)])
    if core:
        for i, c in enumerate(_list_of(d, "cron", path, ["name", ("when", (str, dict)), "run"])):
            w = c["when"]
            if isinstance(w, dict):  # 3a.10: every/offset/after, for this machine's minute
                _need(set(w) <= {"every", "offset", "after"} and "every" in w and _length(w["every"]), path,
                      "cron[%d].when 只能寫 every（加 offset、after）" % i)
                _need(w.get("offset") in (None, "machine") or _length(w["offset"]) is not None, path,
                      "cron[%d].when.offset 要是 machine 或一段時間" % i)
                _need("after" not in w or _length(w["after"]) is not None, path, "cron[%d].when.after 要是一段時間" % i)
    _need(isinstance(d.get("provides", ""), str), path, "provides 要是文字")
    _need(isinstance(d.get("pitch", ""), str), path, "pitch 要是文字")
    _need(isinstance(d.get("schedule_files_default", ""), str), path, "schedule_files_default 要是路徑")
    _need(isinstance(d.get("keep", ""), str), path, "keep 要是文字")
    _need(isinstance(d.get("lose", ""), str), path, "lose 要是文字")
    _need(isinstance(d.get("order", 0), int) and not isinstance(d.get("order", 0), bool), path, "order 要是整數")
    return d


def forbidden_run(item):
    """Whether a schedule item (plugin.json or a schedule file) runs the sync or the engine update and is not marked
    "scheduler": false (kept in its own cron line). The scheduler refuses such an item too."""
    run = (item.get("do") or {}).get("run") if isinstance(item, dict) else None
    return isinstance(run, str) and bool(SYNC_RUN.search(run)) and item.get("scheduler", True) is not False


def load(path, core=False):
    """One plugin.json, checked. PluginError if it cannot be read or is not usable."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except OSError as e:
        raise PluginError("%s：讀不到（%s）" % (path, e))
    except ValueError as e:
        raise PluginError("%s：不是合法的 JSON（%s）" % (path, e))
    return check(d, path, core)


def discover(engine=None, instance=None, root=None, broken=None):
    """([(name, folder, plugin.json as a dict)], [problem]) for every plugin found. A bad file is a problem, not
    an exception; two plugins of the same name: the first found is kept (engine before external before local).
    `broken`, a list if given, receives (name, folder) of each file that could not be read (named after its folder;
    "core" for the core's)."""
    plugins, problems, names = [], [], {}
    core_file = os.path.realpath(os.path.join(engine or ENGINE, "altention", "core.plugin.json"))
    for path in candidates(engine, instance, root):
        try:
            d = load(path, os.path.realpath(path) == core_file)
        except PluginError as e:
            problems.append(str(e))
            if broken is not None:
                folder = os.path.dirname(os.path.realpath(path))
                broken.append(("core" if os.path.realpath(path) == core_file else os.path.basename(folder), folder))
            continue
        if d["name"] in names:
            problems.append("%s：名稱 %s 跟 %s 重複，略過這一份" % (path, d["name"], names[d["name"]]))
            continue
        names[d["name"]] = path
        plugins.append((d["name"], os.path.dirname(os.path.realpath(path)), d))
    return plugins, problems


def instance_entry(name, settings):
    """The instance's "plugins.<name>" block: {} if there is none, None if it is there but not an object (or
    "plugins" itself is not one): unreadable, which makes the plugin's place unknown."""
    if not isinstance(settings, dict):
        return None
    block = settings.get("plugins", {})
    if not isinstance(block, dict):
        return None
    entry = block.get(name, {})
    return entry if isinstance(entry, dict) else None


# C-37: the instance may override a plugin's schedules (plugins.<name>.schedules) and, of the core's own cron lines,
# only these (core.schedules); the sync and the engine update follow in a batch of their own (manager 2026-10-17)
CORE_OVERRIDABLE = ("watch", "sync", "engine-update")
CORE_ALWAYS_ON = ("sync", "engine-update")  # the way fixes reach a machine: their time may change, they never go
OVERRIDE_KEYS = ("when", "enabled", "timezone")
DAY_NAMES = ("sun", "mon", "tue", "wed", "thu", "fri", "sat")


def _span_minutes(text):
    m = re.match(r"^([1-9][0-9]*)([mh])$", text) if isinstance(text, str) else None
    return None if m is None else int(m.group(1)) * (60 if m.group(2) == "h" else 1)


def when_problem(when, core=False):
    """Why an instance's "when" cannot be used, or "" (C-37). Only what a cron line can say: "at" HH:MM with "days"
    ("daily" or a list of sun..sat), or "every" (minutes dividing an hour, or hours dividing a day) with "offset"
    ("machine" or a length shorter than every); the core's lines also take "after"; never "once"."""
    if not isinstance(when, dict):
        return "when 要是物件"
    allowed = {"at", "days", "every", "offset"} | ({"after"} if core else set())
    odd = sorted(k for k in when if k not in allowed)
    if odd:
        return "when 不認得 %s（只能有 %s）" % ("、".join(odd), "、".join(sorted(allowed)))
    if core and "every" not in when:
        return "核心的排程只能寫 every（加 offset、after）"
    if ("at" in when) == ("every" in when):
        return "when 要剛好一種：at 或 every"
    if "at" in when:
        if "offset" in when or "after" in when:
            return "at 不能配 offset"
        if not (isinstance(when["at"], str) and re.match(r"^([01]\d|2[0-3]):[0-5]\d$", when["at"])):
            return "at 要寫成 HH:MM（例如 08:05），不是 %r" % (when["at"],)
        days = when.get("days", "daily")
        if days != "daily" and not (isinstance(days, list) and days and all(d in DAY_NAMES for d in days)
                                    and len(set(days)) == len(days)):
            return "days 要是 \"daily\" 或像 [\"mon\", \"fri\"] 的清單（sun mon tue wed thu fri sat）"
        return ""
    if "days" in when:
        return "every 不能配 days"
    every = _span_minutes(when["every"])
    if every is None:
        return "every 要寫成像 10m、2h 的長度，不是 %r" % (when["every"],)
    if not ((every < 60 and 60 % every == 0) or (every % 60 == 0 and 1440 % every == 0)):
        return "every %s 寫不成 cron：要能整除一小時（例如 5m、15m、30m）或一天（例如 1h、6h、24h）" % when["every"]
    off = when.get("offset")
    if off is not None and off != "machine":
        m = _span_minutes(off) if off != "0m" else 0
        if m is None or m >= every:
            return "offset 要是 machine，或比 every 短的長度"
    if "after" in when and _span_minutes(when["after"]) is None:
        return "after 要是像 3m 的長度"
    return ""


def schedule_overrides(name, settings):
    """({item name: the instance's override}, problem or None): plugins.<name>.schedules, core.schedules for the core.
    {} when there are none; (None, why) when the block is there but not an object (every item is then unknown)."""
    if not isinstance(settings, dict):
        return {}, None
    if name == "core":
        block, where = settings.get("core", {}), "core"
        block = block if isinstance(block, dict) else {}
    else:
        block, where = instance_entry(name, settings), "plugins.%s" % name
        if block is None:
            return None, "%s 不是物件" % where
    found = block.get("schedules", {})
    if not isinstance(found, dict):
        return None, "%s.schedules 要是物件（{\"排程名\": {\"when\": …}}）" % where
    return found, None


def with_sync_interval(found, settings, base):
    """(overrides, {item: label}): the instance's sync.interval (written by `instance.py init`, said by its README) as
    the short way of core.schedules.sync's every, the rest of the core's when kept (C-37 C). core.schedules.sync, when
    written, comes first; an interval equal to the core's own is no override."""
    block = settings.get("sync") if isinstance(settings, dict) else None
    if not isinstance(block, dict) or "interval" not in block or "sync" in (found or {}):
        return found, {}
    own = next((s.get("when") for s in base if isinstance(s, dict) and s.get("name") == "sync"), None)
    if not isinstance(own, dict) or block["interval"] == own.get("every"):
        return found, {}
    found = dict(found or {})
    found["sync"] = {"when": dict(own, every=block["interval"])}
    return found, {"sync": "sync.interval"}


def effective_schedules(name, d, settings):
    """([item], [problem]): a plugin's schedules (the core's cron lines for the core) with the instance's overrides
    put in (C-37), the one reading of them (apply, plan, the scheduler). Each item is a copy, with "source":
    "instance" when the instance changed its when, enabled or timezone, else "plugin"; an override that cannot be
    used makes its item {"unknown": why}: not compared and its installed line never touched, never quietly the
    plugin.json's time instead. A name that matches no item, or a core line not open to overriding, is a problem
    (said by plan and doctor), and changes nothing."""
    core = name == "core"
    base = (d or {}).get("cron" if core else "schedules", []) if isinstance(d, dict) else []
    found, bad = schedule_overrides(name, settings)
    where = "core.schedules" if core else "plugins.%s.schedules" % name
    items, problems = [], []
    names = [s.get("name") for s in base if isinstance(s, dict)]
    labels = {}
    if core and not bad:
        found, labels = with_sync_interval(found, settings, base)
    for s in base:
        item = dict(s, source="plugin")
        if bad:
            if not core or s.get("name") in CORE_OVERRIDABLE:
                item["unknown"] = bad
            items.append(item)
            continue
        o = found.get(s.get("name")) if found else None
        if o is None or (core and s.get("name") not in CORE_OVERRIDABLE):
            items.append(item)
            continue
        label = labels.get(s.get("name")) or "%s.%s" % (where, s.get("name"))
        why = ""
        if not isinstance(o, dict):
            why = "要是物件"
        else:
            odd = sorted(k for k in o if k not in OVERRIDE_KEYS)
            if odd:
                why = "不認得 %s（只能改 %s）" % ("、".join(odd), "、".join(OVERRIDE_KEYS if not core else ("when", "enabled")))
            elif core and "timezone" in o:
                why = "核心的排程不能改 timezone"
            elif "enabled" in o and not isinstance(o["enabled"], bool):
                why = "enabled 只能是 true 或 false"
            elif core and o.get("enabled") is False and s.get("name") in CORE_ALWAYS_ON:
                why = "不能關（引擎更新和同步是修正送到這台的路），只能改時間"
            elif "timezone" in o and o["timezone"] not in ("machine", None):
                why = "timezone 只能是 \"machine\" 或 null（照 core.timezone）"
            elif "when" in o:
                why = when_problem(o["when"], core)
        if why:
            item["unknown"] = "%s：%s" % (label, why)
        else:
            if s.get("name") in labels:
                item["from"] = labels[s.get("name")]  # where it came from, when not under its own schedules (sync.interval)
            if "when" in o:
                item["when"] = o["when"]
            if "enabled" in o:
                item["enabled"] = o["enabled"]
            if "timezone" in o:
                if o["timezone"] is None:
                    item.pop("timezone", None)
                else:
                    item["timezone"] = o["timezone"]
            item["source"] = "instance"
        items.append(item)
    for key in sorted(k for k in (found or {}) if k not in labels):
        if key not in names:
            problems.append("%s.%s：%s 沒有叫 %s 的排程（有：%s）" % (where, key, name, key, "、".join(n for n in names if isinstance(n, str)
                                                                                        and (not core or n in CORE_OVERRIDABLE)) or "沒有"))
        elif core and key not in CORE_OVERRIDABLE:
            problems.append("%s.%s：這一版還不能改（核心只開放 %s）" % (where, key, "、".join(CORE_OVERRIDABLE)))
    return items, problems


def _names(value):
    if isinstance(value, str):
        return [value]
    return [v for v in value if isinstance(v, str)] if isinstance(value, list) else []


def placement(name, d, machine, settings, bot, known=None):
    """Where plugin `name` stands on `machine`: "on"; "off" (not its machine); "disabled" (turned off there on
    purpose); "unknown" (what decides it could not be read: `settings` or `bot` is None because the instance's
    settings or the machine list are broken, or "on" names a machine not in `known`, the machine list's names, or
    `machine` itself is not in a list that was read: a machine that does not know its own name must never take its
    lines for another's). The core is always "on"."""
    if name == "core":
        return "on"
    if settings is None:
        return "unknown"
    if known is not None and machine not in known:
        return "unknown"  # not "off": its singles' lines would be removed for a name it merely failed to work out
    entry = instance_entry(name, settings)
    if entry is None:
        return "unknown"
    for key in ("on", "disabled_on"):  # there, but no machine names in it: a mistake, not "not written"
        if key in entry and not _names(entry[key]):
            return "unknown"
    if machine in _names(entry.get("disabled_on")):
        return "disabled"
    if d.get("machines", "all") == "all":
        return "on"
    where = _names(entry.get("on"))
    if where:
        if known is not None and any(w not in known for w in where):
            return "unknown"  # a typo would otherwise switch it off everywhere without a word
        return "on" if machine in where else "off"
    if not bot:
        return "unknown"
    return "on" if machine == bot else "off"


def why_unknown(name, settings, bot, known, machine=None):
    """The reason a plugin's place came out unknown, for plan and doctor to say (settings and bot as given to
    placement())."""
    if settings is None:
        return "實例設定讀不出來"
    if known is not None and machine is not None and machine not in known:
        return "這台（%s）不在機器清單上：認不出自己的名字時不增不刪（檢查 ~/.config/altention/host 或清單的 hostname 欄位）" % machine
    entry = instance_entry(name, settings)
    if entry is None:
        return "實例設定裡 plugins.%s（或 plugins）不是物件" % name
    for key in ("on", "disabled_on"):
        if key in entry and not _names(entry[key]):
            return "實例設定裡 plugins.%s.%s 寫的不是機器名或機器名清單" % (name, key)
    where = _names(entry.get("on"))
    if where and known is not None and any(w not in known for w in where):
        return "plugins.%s.on 寫了機器清單裡沒有的機器（%s）" % (name, "、".join(w for w in where if w not in known))
    if not bot:
        return "不知道機器人在哪台（機器清單讀不出來，或沒有任何一台有 dcbot 角色），只在一台跑的插件又沒寫 on"
    return "原因不明"


def this_machine(problems):
    """This machine's name, even when the machine list is broken (then the stated name or the hostname, and why
    in `problems`): the self-repair and the sync call without a name and must still get unknowns, not an error."""
    try:
        return config.this_machine()
    except Exception as e:
        problems.append("認不出這台在機器清單上的名字（%s），暫時用 %s" % (e, config.stated_name() or config.hostname()))
        return config.stated_name() or config.hostname()


def on_this_machine(machine=None, engine=None, root=None):
    """([(name, folder, dict or None, placement, reason)], [problem]) for every plugin, with where it stands on
    `machine` (default: this one). Anything unreadable is a problem, the plugins it decides are "unknown" with the
    reason, and a plugin.json that cannot be read is listed as unknown with dict None. Never raises for a broken
    setting or machine list."""
    problems = []
    try:
        inst = config.instance_dir()
    except Exception as e:  # a pointer to a folder that is not there
        inst = None
        problems.append("實例的位置讀不出來（%s）" % e)
    if not inst:
        settings = None  # not joined yet, or the folder is away for now: its settings are not "none"
        problems.append("這台沒有實例（還沒 join，或實例的資料夾暫時不在）：插件在哪台跑都當成「不知道」")
    else:
        try:
            settings = config.settings()
        except Exception as e:  # config.ConfigError: the file is there but broken
            settings = None
            problems.append("實例設定讀不出來（%s）：插件在哪台跑都當成「不知道」，不增不刪" % e)
    try:
        bot = config.bot_machine()[0]  # (name, machine, here)
        known = set(config.inventory().get("machines") or {}) or None
    except Exception as e:
        bot = known = None
        problems.append("機器清單讀不出來（%s）：只在一台跑的插件當成「不知道」，不增不刪" % e)
    machine = machine or this_machine(problems)
    broken = []
    plugins, found = discover(engine, inst or "", root, broken)
    problems += found
    out = []
    for n, f, d in plugins:
        where = placement(n, d, machine, settings, bot, known)
        out.append((n, f, d, where, why_unknown(n, settings, bot, known, machine) if where == "unknown" else ""))
    out += [(n, f, None, "unknown", "plugin.json 讀不出來") for n, f in broken]
    return out, problems


def skill_owners(rows, engine=None):
    """{folder name under <engine>/skills: the plugin whose plugin.json lists it in "skills"} (paths relative to
    the plugin's own folder), from on_this_machine()'s rows. A skill no plugin lists belongs to none."""
    top = os.path.realpath(os.path.join(engine or ENGINE, "skills"))
    out = {}
    for n, folder, d, _where, _why in rows:
        for sk in (d or {}).get("skills", []) if isinstance(d, dict) else []:
            path = sk.get("path") if isinstance(sk, dict) else None
            if isinstance(path, str):
                real = os.path.realpath(os.path.join(folder, path))
                if os.path.dirname(real) == top:
                    out[os.path.basename(real)] = n
    return out


def skills_off(machine=None, engine=None):
    """The skills under <engine>/skills whose plugin is turned off on `machine` (default: this one) on purpose
    ("disabled": its disabled_on names it). Only what the settings plainly say: unknown is not off (skills_unsure
    has those). Never raises."""
    try:
        rows, _ = on_this_machine(machine, engine)
    except Exception:
        return set()
    where = dict((r[0], r[3]) for r in rows)
    return set(sk for sk, owner in skill_owners(rows, engine).items() if where.get(owner) == "disabled")


def skills_unsure(machine=None, engine=None):
    """The skills under <engine>/skills whose plugin's place on `machine` is "unknown" (settings or a machine list
    that cannot be read): it may be turned off there, so such a skill is neither linked nor taken away (C-11), and
    not missing. All of them when the plugins cannot be told at all. Never raises."""
    try:
        rows, _ = on_this_machine(machine, engine)
    except Exception:
        folder = os.path.join(engine or ENGINE, "skills")
        try:
            return set(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d)))
        except OSError:
            return set()
    where = dict((r[0], r[3]) for r in rows)
    return set(sk for sk, owner in skill_owners(rows, engine).items() if where.get(owner) == "unknown")


LOCAL_ONLY = ".local-only"  # in a skill's folder: linked only into one working folder (install.py: setup), never ~/.claude/skills


def link_skills(engine=None, add_only=False):
    """~/.claude/skills/<name> linked to each skill folder under <engine>/skills, except those whose plugin is
    turned off here (skills_off). {"names", "off", "made", "removed", "taken", "kept", "waiting"}: made = links made
    or pointed again, removed = links of a turned-off plugin taken away (only a link resolving to this engine's own
    folder), taken = a file or folder under a name we need, kept = something under a turned-off name that is not
    our link (both left alone), waiting = skills with no link that were not made because their plugin's place here
    is unknown, why = {skill: the reason its place is unknown} for every such skill. add_only (the sync's round, after an update brought a new skill): only links that are missing are
    made; nothing is taken away or pointed elsewhere (a broken link stays broken). A plugin's skill is neither
    linked nor taken away when that plugin's place here is unknown, as with settings that cannot be read (a
    turned-off plugin must not get its skill back by a comma too many: C-11); a link it already has is still
    pointed at this engine (install.sh), and a skill no plugin owns (tour) is linked. install.sh does the rest."""
    engine = engine or ENGINE
    home_skills = os.path.expanduser("~/.claude/skills")
    folder = os.path.join(engine, "skills")
    names = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))
                   and not os.path.exists(os.path.join(folder, d, LOCAL_ONLY))) if os.path.isdir(folder) else []
    out = {"names": names, "off": set(), "made": [], "removed": [], "taken": [], "kept": [], "waiting": [], "why": {}}
    try:
        rows, _ = on_this_machine(None, engine)
    except Exception as e:
        rows = None
        out["why"] = dict((n, "插件讀不出來（%s: %s）" % (type(e).__name__, e)) for n in names)
    if rows is None:  # nothing can be told: every skill waits
        unsure = set(names)
    else:
        where = dict((r[0], r[3]) for r in rows)
        why = dict((r[0], r[4]) for r in rows)
        owners = skill_owners(rows, engine)
        out["off"] = set(sk for sk, owner in owners.items() if where.get(owner) == "disabled")
        unsure = set(sk for sk, owner in owners.items() if where.get(owner) == "unknown")
        out["why"] = dict((sk, why.get(owners[sk]) or "原因不明") for sk in unsure)
    for name in names:
        src, link = os.path.join(folder, name), os.path.join(home_skills, name)
        if name in out["off"]:
            if add_only:
                continue
            if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(src):
                os.remove(link)
                out["removed"].append(name)
            elif os.path.lexists(link):
                out["kept"].append(link)
            continue
        if os.path.islink(link):
            if not add_only and os.readlink(link) != src:
                os.remove(link)
                os.symlink(src, link)
                out["made"].append(name)
        elif name in unsure:
            if not os.path.lexists(link):
                out["waiting"].append(name)
        elif os.path.lexists(link):
            out["taken"].append(link)
        else:
            os.makedirs(home_skills, exist_ok=True)
            os.symlink(src, link)
            out["made"].append(name)
    return out


def step_here(step, machine, plugin_on, bot=None, sources=()):
    """Whether one step (a setup step, a secret, a cron line) applies on `machine`, by its "on": "all" (or none),
    "one" (where the plugin itself runs: `plugin_on`), "bot", "sources" (the instance's list for this plugin),
    machine names; a list means any of them."""
    words = _names(step.get("on", "all")) or ["all"]
    return ("all" in words or ("one" in words and plugin_on) or ("bot" in words and machine == bot)
            or ("sources" in words and machine in sources) or machine in words)


def unknown_on(step, known):
    """The words in a step's "on" that are neither ON_WORDS nor a machine in `known` (a typo like "sourcs" would
    otherwise be taken for a machine name and quietly never run). For plan and doctor to report."""
    return [w for w in _names(step.get("on", "all")) if w not in ON_WORDS and w not in (known or ())]


def schedule_files(d, folder, settings):
    """The schedule files a plugin.json names, ~ expanded: "schedule_files" is a list of paths, or text (a path,
    or "{setting:x.y}" for a setting that is a path or a list of them; spaces around it do not count). Not
    there, or a setting not set: none. A plugin's own default file (default_schedule_file) is always one of them,
    first, whatever the setting lists (manager 2026-10-17: the secretary's file is never left out, and its
    briefings never go into a file another channel keeps). The one reading, for apply (which makes missing ones),
    the scheduler and remind. PluginError when it is something else."""
    value = (d or {}).get("schedule_files")
    if isinstance(value, str):
        m = TOKEN.fullmatch(value.strip())
        if m and m.group(1).startswith("setting:"):
            node = settings or {}
            for part in m.group(1)[len("setting:"):].split("."):
                node = node.get(part) if isinstance(node, dict) else None
            value = node
        else:
            value = [expand(value.strip(), folder, settings)]
    default = default_schedule_file(d, folder, settings)
    if value is None:
        return [default] if default else []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        raise PluginError("schedule_files 要是路徑或路徑的清單，不是 %s" % json.dumps(value, ensure_ascii=False)[:80])
    listed = [os.path.expanduser(v.strip()) for v in value]
    if default is None:
        return listed
    same = os.path.realpath(default)
    return [default] + [p for p in listed if os.path.realpath(p) != same]


def default_schedule_file(d, folder, settings):
    """The schedule file a plugin.json's "schedule_files_default" names ({data} and the like filled in), when its
    "schedule_files" is a {setting:…}: the plugin's own file, read whether the setting is set or not (schedule_files
    puts it first); None when it names none. planner's reminders need nothing set on a new install (owner's install
    2026-10-15), and the secretary's briefings always live in it (manager 2026-10-17)."""
    value, default = (d or {}).get("schedule_files"), (d or {}).get("schedule_files_default")
    if not isinstance(default, str) or not default.strip() or not isinstance(value, str):
        return None
    m = TOKEN.fullmatch(value.strip())
    if not m or not m.group(1).startswith("setting:"):
        return None
    return os.path.expanduser(expand(default.strip(), folder, settings))


def expand(text, folder=None, settings=None):
    """`text` with {engine}, {plugin}, {python}, {claude}, {instance}, {data}, {root}, {setting:a.b} filled in and a
    leading ~ expanded (worked out now, never at import). A setting that is a list of strings becomes them
    shell-quoted, ~ expanded, separated by spaces (schedule files on a command line); any other non-string, JSON.
    An unknown or unset setting is a PluginError."""
    def value(m):
        key = m.group(1)
        if key == "engine":
            return os.path.realpath(ENGINE)
        if key == "plugin":
            if not folder:
                raise PluginError("{plugin} 用在沒有插件資料夾的地方")
            return folder
        if key == "python":  # the system's, not whatever a venv in the caller's PATH puts first (cron would differ)
            return "/usr/bin/python3" if os.path.exists("/usr/bin/python3") else (shutil.which("python3") or "python3")
        if key == "claude":
            local = os.path.expanduser("~/.local/bin/claude")
            return local if os.path.exists(local) else (shutil.which("claude") or local)
        if key == "instance":
            inst = config.instance_dir()
            if not inst:
                raise PluginError("{instance}：這台還沒有實例")
            return inst
        if key == "data":
            return os.environ.get("ALT_DATA") or os.path.join(config.root(), "data")
        if key == "root":
            return config.root()
        path = key.split(":", 1)[1]
        node = config.settings() if settings is None else settings
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                raise PluginError("{setting:%s}：實例設定裡沒有這一項" % path)
            node = node[part]
        if isinstance(node, str):
            return node
        if isinstance(node, list) and all(isinstance(x, str) for x in node):  # paths for a command line ([]: none)
            return " ".join(shlex.quote(os.path.expanduser(x)) for x in node)
        return json.dumps(node, ensure_ascii=False)
    out = TOKEN.sub(value, text)
    return os.path.expanduser(out) if out.startswith("~") else out
