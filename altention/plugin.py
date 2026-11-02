"""Plugins: read and check plugin.json files, and work out which plugins this machine runs (stage 3a.1).

A plugin is a folder with a plugin.json (DESIGN.md section 6). Where they are looked for:
- the engine: altention/core.plugin.json (the core itself), <engine>/<folder>/plugin.json, <engine>/skills/*/plugin.json,
  <engine>/plugins/*/plugin.json (built-in plugins, once stage 4 moves them there);
- external plugins: <root>/plugins/*/plugin.json (~/altention/plugins/my-plugin …); one counts only when the
  instance's plugins.json says where it comes from ("source" and "version", or "local": true; C-61 E1): any other
  is a draft, listed by plugins.py but never installed, scheduled or asked about;
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

A plugin may declare "rewrite" (C-71b): a command the Discord bot hands Claude's final reply to before posting it,
which gives it back as Discord messages with pictures; its fields and its input and output are in
dcbot/bot/rewrite.py (check() checks the shape).

A plugin may declare "uses" (C-94): {"<interface>": <version>}, what it needs of the engine, against the engine's
altention/interfaces.json. One that asks for an interface or a version this engine does not offer is "blocked" here:
like "disabled", nothing of it is installed or scheduled and what was is taken away, and plugins.py list, doctor and
the update card say why. A plugin without "uses" is taken as before. When interfaces.json cannot be read, a plugin
that declares "uses" is "unknown" (nothing added or removed). A plugin may offer interfaces of its own, declared in
its plugin.json "interfaces" as in interfaces.json (manager 2026-10-24): those of the plugins on here join the
engine's, so a plugin using one is blocked where its provider is not on; a name the engine or another plugin
already offers is not taken (a problem said, the rest of that plugin as before).

A plugin may declare "activity" (C-54a): {"pages": [{"id", "title", "access": "owner" | "guild"}, …]}, the pages of a
Discord Activity it serves. `altention dc post --activity <plugin>/<page>` posts a button that opens one; the bot lets
only the owner press it for an "owner" page (the default), and writes what was asked for where the plugin reads it
(dcbot/bot/activity.py). Fields of the plugin's own (where a page's files are, what it may call) are left to it.

What a plugin needs before it is set up (C-77): needs[] (system tools; "install" per system, "why", "size",
"optional", "for", "ask") and guide[] (steps the person takes outside, each with a check or an ask; a "secret" names
one of secrets[]), and "offer" (when and at what cost it is offered); check() checks the shape, plugin_steps.py works
out where a plugin stands.

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

from . import config, text

FORMAT = 1
ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYNC = ("timer", "on-request", "none")
CORE_ONLY = ("cron", "settings")  # blocks only the core may declare
# Keys the engine no longer reads (C-95): still accepted, so an older plugin keeps working; plugins.py list and
# release.py check-plugin remind its author to delete them. Each: (where, what to do instead).
RETIRED = (("commands", "沒有程式讀，刪掉"), ("requires[].data", "只看 plugin，刪掉 data"),
           ("secrets[].path", "路徑固定在 ~/.config/altention/secrets/<插件>/<名字>，刪掉 path"))  # the last: C-77 G3
ON_WORDS = ("all", "bot", "one", "sources")  # what a step's "on" may say besides machine names
# The engine update and the engine/instance sync never go into the scheduler (DESIGN 8): they must keep running when
# the time zone, plugins.json or the scheduler's state are broken, or a fix pushed to every machine never arrives.
# (only what is written out: a wrapper script that pulls inside is not seen; the guarantee is the sync's own cron line)
SYNC_RUN = re.compile(r"altention[./]sync\b|altention[./]update\b|\bhome-pull\b"
                      r"|\bgit\b[^;&|]*\s(pull|fetch)(\s|$)")
OS_NAMES = ("debian", "fedora", "arch", "macos")  # the keys of needs[].install (C-77)
OFFER_WHEN = ("install", "asked")  # C-64: at setup, when something asks for it (owner 10-21: a new plugin on the list is not asked about)
ACCESS = ("owner", "guild")  # who may open an Activity page (C-54a): the owner alone, or anyone in the server
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
    _need(check_name(d["name"]), path, "name 只能用小寫英文、數字和 -")
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
    for i, s in enumerate(_list_of(d, "setup", path, ["name"], [("optional", bool), ("today", str), ("lazy", bool)])):
        _need("run" in s and (s["run"] is None or isinstance(s["run"], str)), path,
              "setup[%d].run 要是指令，或 null（還只是記錄、不能執行）" % i)
    for i, s in enumerate(_list_of(d, "data", path, ["name", "path"], [("private", bool)])):
        _need(s.get("sync", "timer") in SYNC, path, "data[%d].sync 只能是 %s" % (i, "、".join(SYNC)))
    _list_of(d, "skills", path, ["name", "path"])
    for i, n in enumerate(_list_of(d, "needs", path, ["name", "check"], [  # C-61 E2: system tools, looked for, never installed
            ("hint", str), ("why", str), ("size", str), ("optional", bool), ("ask", str), ("install", dict), ("for", list)])):
        _need(all(isinstance(k, str) and isinstance(v, str) for k, v in n.get("install", {}).items()), path,
              "needs[%d].install 要寫成 {作業系統: 指令}（%s）" % (i, "、".join(OS_NAMES)))
        _need(all(isinstance(x, str) for x in n.get("for", [])), path, "needs[%d].for 要是文字的清單（少了它不能用的功能）" % i)
    secrets = d.get("secrets") if isinstance(d.get("secrets"), list) else []
    secret_names = [s.get("name") for s in secrets if isinstance(s, dict)]
    ids = []
    for i, g in enumerate(_list_of(d, "guide", path, ["id", "title"], [  # C-77: steps the user takes outside (plugin_steps)
            ("why", str), ("url", str), ("do", list), ("ask", str), ("run", str), ("optional", bool), ("secret", dict),
            ("after", list), ("gate", list)])):
        _need(check_name(g["id"]) and g["id"] not in ids, path, "guide[%d].id 要是小寫英數和 - 的名字，而且不重複" % i)
        ids.append(g["id"])
        _need(g.get("check") is None or isinstance(g["check"], str), path, "guide[%d].check 要是指令，或 null（檢查不了，用 ask 問）" % i)
        _need(g.get("check") is not None or isinstance(g.get("ask"), str), path, "guide[%d] 的 check 是 null，要寫 ask（問使用者的那一句）" % i)
        for key in ("do", "after", "gate"):
            _need(all(isinstance(x, str) for x in g.get(key) or []), path, "guide[%d].%s 要是文字的清單" % (i, key))
        _need(all(x in ids[:-1] for x in g.get("after") or []), path, "guide[%d].after 只能寫它前面的步驟的 id" % i)
        sec = g.get("secret")
        if sec is not None:
            _need(isinstance(sec.get("name"), str) and isinstance(sec.get("label"), str), path,
                  "guide[%d].secret 要寫 name 和 label" % i)
            _need(sec["name"] in secret_names, path, "guide[%d].secret.name（%s）要是 secrets[] 裡的名字" % (i, sec["name"]))
    if "offer" in d:  # C-64: when and at what cost it is offered; the words are "pitch"; the answers are the instance's
        o = d["offer"]
        _need(isinstance(o, dict) and isinstance(o.get("cost", ""), str), path, "offer 要是物件，cost 要是文字")
        _need(o.get("when", "install") in OFFER_WHEN, path, "offer.when 只能是 %s" % "、".join(OFFER_WHEN))
    _list_of(d, "requires", path, ["plugin"])
    for i, c in enumerate(_list_of(d, "channels", path, ["name"], [("kind", str), ("setting", str), ("name_setting", str)])):
        _need(c.get("category", "main") in ("main", "system"), path, "channels[%d].category 只能是 main 或 system" % i)
    for i, a in enumerate(_list_of(d, "asks", path, ["key", "question"])):  # C-57 S3: a setting the reconcile asks in #setup
        _need(isinstance(a.get("unless", {}), dict), path, "asks[%d].unless 要是物件（設定 → 那個值就不用問）" % i)
    for i, s in enumerate(_list_of(d, "secrets", path, ["name"], [("path", str), ("label", str)])):  # C-77 G3: the path is fixed
        _need(check_name(s["name"]), path, "secrets[%d].name 只能用小寫英文、數字和 -" % i)
    if core:
        for i, c in enumerate(_list_of(d, "cron", path, ["name", ("when", (str, dict)), "run"])):
            w = c["when"]
            if isinstance(w, dict):  # 3a.10: every/offset/after, for this machine's minute
                _need(set(w) <= {"every", "offset", "after"} and "every" in w and _length(w["every"]), path,
                      "cron[%d].when 只能寫 every（加 offset、after）" % i)
                _need(w.get("offset") in (None, "machine") or _length(w["offset"]) is not None, path,
                      "cron[%d].when.offset 要是 machine 或一段時間" % i)
                _need("after" not in w or _length(w["after"]) is not None, path, "cron[%d].when.after 要是一段時間" % i)
    _need(re.match(r"^>=\s*v?[0-9]+\.[0-9]+\.[0-9]+$", str(d.get("engine", ">=0.0.0")).strip()) is not None, path,
          "engine 要寫成 \">=X.Y.Z\"（這個插件要的引擎版本）")  # C-61 E3
    _need(isinstance(d.get("test", ""), str), path, "test 要是指令（在新版的副本裡跑，結束碼 0 才換）")  # C-61 E3
    if "rewrite" in d:  # C-71b: the bot hands Claude's final reply to this command before posting it (dcbot/bot/rewrite.py)
        r = d["rewrite"]
        _need(isinstance(r, dict), path, "rewrite 要是物件（command、timeout、when）")
        _need(isinstance(r.get("command"), str) and r["command"].strip(), path, "rewrite.command 要是指令")
        t = r.get("timeout", 30)
        _need(isinstance(t, int) and not isinstance(t, bool) and 0 < t <= 60, path, "rewrite.timeout 要是 1 到 60 的整數（秒）")
        when = r.get("when", "")
        _need(isinstance(when, str), path, "rewrite.when 要是文字（regex）")
        try:
            re.compile(when)
        except re.error as e:
            _need(False, path, "rewrite.when 不是正確的 regex：%s" % e)
    if "activity" in d:  # C-54a: pages a Discord Activity opens; the bot's 打開 button checks `access` (dcbot/bot/activity.py)
        a = d["activity"]
        _need(isinstance(a, dict) and isinstance(a.get("pages", []), list), path, "activity 要是物件，pages 要是清單")
        ids = []
        for i, page in enumerate(a.get("pages", [])):
            _need(isinstance(page, dict), path, "activity.pages[%d] 要是物件" % i)
            _need(isinstance(page.get("id"), str) and check_name(page["id"]), path,
                  "activity.pages[%d].id 要是小寫英文字母開頭、只有小寫英數和 - 的名字" % i)
            _need(page["id"] not in ids, path, "activity.pages 有兩頁都叫 %s" % page["id"])
            ids.append(page["id"])
            _need(isinstance(page.get("title", ""), str), path, "activity.pages[%d].title 要是文字" % i)
            _need(page.get("access", "owner") in ACCESS, path,
                  "activity.pages[%d].access 要是 owner（只有擁有者）或 guild（伺服器裡的人都能開）" % i)
    uses = d.get("uses", {})
    _need(isinstance(uses, dict), path, "uses 要是物件（介面名 → 版本）")
    for k, v in uses.items():
        _need(isinstance(v, int) and not isinstance(v, bool) and v >= 1, path, "uses.%s 要是 1 以上的整數（介面的版本）" % k)
    offers = d.get("interfaces", {})
    _need(isinstance(offers, dict), path, "interfaces 要是物件（介面名 → {version, what, paths, also}）")
    for k, e in offers.items():
        _need(re.match(r"^[a-z][a-z0-9.-]*$", k) is not None, path, "interfaces 的名字 %s 只能用小寫英文、數字、. 和 -" % k)
        _need(isinstance(e, dict) and isinstance(e.get("version"), int) and not isinstance(e["version"], bool)
              and e["version"] >= 1, path, "interfaces.%s.version 要是 1 以上的整數" % k)
        _need(isinstance(e.get("what", ""), str), path, "interfaces.%s.what 要是文字" % k)
        for key in ("paths", "also"):
            _need(isinstance(e.get(key, []), list), path, "interfaces.%s.%s 要是清單" % (k, key))
    _need(isinstance(d.get("provides", ""), str), path, "provides 要是文字")
    _need(isinstance(d.get("pitch", ""), str), path, "pitch 要是文字")
    _need(isinstance(d.get("schedule_files_default", ""), str), path, "schedule_files_default 要是路徑")
    _need(isinstance(d.get("keep", ""), str), path, "keep 要是文字")
    _need(isinstance(d.get("lose", ""), str), path, "lose 要是文字")
    _need(isinstance(d.get("order", 0), int) and not isinstance(d.get("order", 0), bool), path, "order 要是整數")
    return d


def retired(d):
    """The RETIRED keys plugin.json `d` (already checked) still has: [(where, what to do)]."""
    out = []
    if "commands" in d:
        out.append(RETIRED[0])
    if any(isinstance(r, dict) and "data" in r for r in d.get("requires", [])):
        out.append(RETIRED[1])
    if d.get("name") != "core" and any(isinstance(s, dict) and "path" in s for s in d.get("secrets", [])):  # the core's lie elsewhere
        out.append(RETIRED[2])
    return out


def interfaces(engine=None):
    """{name: {"version", "also", …}} of altention/interfaces.json (C-94). PluginError when it cannot be read."""
    path = os.path.join(engine or ENGINE, "altention", "interfaces.json")
    try:
        with open(path, encoding="utf-8") as f:
            found = json.load(f)["interfaces"]
    except (OSError, ValueError, KeyError, TypeError) as e:
        raise PluginError("%s：讀不出來（%s）" % (path, e))
    if not isinstance(found, dict) or not all(isinstance(e, dict) and isinstance(e.get("version"), int)
                                              for e in found.values()):
        raise PluginError("%s：interfaces 的每一項都要有整數的 version" % path)
    return found


def uses_problem(d, offered, elsewhere=None):
    """Why plugin.json `d` cannot run where `offered` (interfaces(), with what plugins on there offer: those carry
    "provider") is offered, or "" when it can (or says nothing in "uses"). `elsewhere`: {interface: plugin} offered
    by a plugin not on there, to name it."""
    out = []
    for name, want in sorted((d.get("uses") or {}).items()):
        have = offered.get(name)
        if have is None and (elsewhere or {}).get(name):
            out.append(text.t("plugin.uses_provider_off", name=name, provider=elsewhere[name]))
        elif have is None:
            out.append(text.t("plugin.uses_missing", name=name))
        elif want != have["version"] and want not in (have.get("also") or []):
            versions = "、v".join(str(v) for v in [have["version"]] + list(have.get("also") or []))
            out.append(text.t("plugin.uses_version_plugin", name=name, want=want, have=versions, provider=have["provider"])
                       if have.get("provider") else text.t("plugin.uses_version", name=name, want=want, have=versions))
    return "；".join(out)


def machines_for(name, entry, d):
    """The machines on the list the plugin of plugin.json `d` runs on, with the instance's `entry` for it: every
    machine but those turned off ("disabled_on"; "on" when it names some) for "all"; "on", else the bot's, for
    "one". [] when the machine list cannot be read (doctor says why). (ops/plugins.py's until C-77 G3a)"""
    from . import plugin_update  # it imports this module
    try:
        names = sorted(config.machines())
        if d.get("machines", "all") == "all":
            return [m for m in names if plugin_update.wanted_here(name, entry, m)]
        on = _names(entry.get("on")) or [config.bot_machine()[0]]
    except config.ConfigError:
        return []
    off = _names(entry.get("disabled_on"))
    return [m for m in on if m and m in names and m not in off]


def users_elsewhere(d, name="", entry=None):
    """machines_for() but this machine: the others to bring in line (add writes no "on" or "disabled_on")."""
    try:
        here = config.this_machine()
    except config.ConfigError:
        return []
    return [m for m in machines_for(name, entry or {}, d) if m != here]


def blocked_why(d, offered, elsewhere=None):
    """The reason plugin.json `d` is blocked, or "": the engine's words when it lacks something, the provider's when
    only plugins not on here are missing."""
    problem = uses_problem(d, offered, elsewhere)
    if not problem:
        return ""
    failing = [n for n, v in sorted((d.get("uses") or {}).items()) if uses_problem({"uses": {n: v}}, offered)]
    if not all(n in (elsewhere or {}) or (offered.get(n) or {}).get("provider") for n in failing):
        return text.t("plugin.blocked", problem=problem)
    if any(n in (elsewhere or {}) and n not in offered for n in failing):  # a provider plainly not on here
        return text.t("plugin.blocked_provider", problem=problem)
    first = failing[0]  # every provider is on: only versions differ (#code-review-2 of 0e39bb6)
    return text.t("plugin.blocked_provider_version", problem=problem, provider=offered[first]["provider"],
                  want=d["uses"][first])


def blocked_by_engine(why):
    """Whether a blocked plugin's reason (on_this_machine) is the engine lacking something, rather than a plugin
    that offers what it uses being off or older: plugins.py show words the two apart."""
    return why.startswith(text.t("plugin.blocked", problem="\0").split("\0")[0])


def provided(rows, core):
    """({interface: entry with "provider"} offered by the plugins on in `rows`, {interface: plugin} declared by
    plugins plainly not on (off, disabled, blocked, draft), {interface: (plugin, why)} declared by plugins whose
    place is unknown, [problem]): a name the engine (`core`) or an earlier plugin (by name) offers is not taken."""
    on, elsewhere, unsure, problems = {}, {}, {}, []
    for n, _f, d, where, why in sorted(rows, key=lambda r: r[0]):
        for name, e in sorted(((d or {}).get("interfaces") or {}).items()):
            if name in core:
                problems.append(text.t("plugin.interface_clash_engine", plugin=n, name=name))
            elif name in on:
                problems.append(text.t("plugin.interface_clash_plugin", plugin=n, name=name, other=on[name]["provider"]))
            elif where == "on":
                on[name] = dict(e, provider=n)
            elif where == "unknown":
                unsure.setdefault(name, (n, why))
            else:
                elsewhere.setdefault(name, n)
    return on, elsewhere, unsure, problems


def unsure_why(d, offered, core, unsure, broken):
    """Why plugin.json `d`'s place cannot be told although it is on (#code-review of 0e39bb6), or "": an interface
    it uses that is missing here is declared by a plugin whose place is unknown, or is not the engine's while some
    plugin.json here cannot be read (it may be that one's). Taking its things away then would act on a guess."""
    for name, want in sorted((d.get("uses") or {}).items()):
        if not uses_problem({"uses": {name: want}}, offered):
            continue
        if name in unsure:
            return text.t("plugin.uses_provider_unknown", name=name, provider=unsure[name][0], why=unsure[name][1])
        if broken and name not in core:
            return text.t("plugin.uses_maybe_broken", name=name, plugins="、".join(sorted(broken)))
    return ""


def offered_here(machine=None, engine=None, root=None):
    """The engine's interfaces with those of the plugins on `machine` (this one). PluginError when the engine's list
    cannot be read."""
    core = interfaces(engine)
    rows, _ = on_this_machine(machine, engine, root)
    return dict(core, **provided(rows, core)[0])


def check_name(name):
    """Whether `name` may name a plugin (and its folder under <root>/plugins)."""
    return isinstance(name, str) and re.match(r"^[a-z][a-z0-9-]*$", name) is not None


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


EXTERNAL = "plugins"  # <root>/plugins: external plugins (C-61)
_READ = object()


def external(folder, root=None):
    """Whether `folder` is an external plugin's: right under <root>/plugins (links followed)."""
    return os.path.dirname(os.path.realpath(folder)) == os.path.realpath(os.path.join(root or config.root(), EXTERNAL))


def source_of(entry):
    """(declared, problem) for an external plugin's instance entry (C-61 E1): declared when it says "source" (with
    "version") or "local": true; problem, when it says one of them but not usably (then its place is unknown). An
    entry without either (or none at all) is not declared: a draft."""
    if not isinstance(entry, dict) or not any(k in entry for k in ("source", "version", "local")):
        return False, None
    if "local" in entry:
        if entry["local"] is not True:
            return True, "local 只能寫 true（不 clone、只在有這個資料夾的機器算數）"
        if "source" in entry or "version" in entry:
            return True, "local 和 source、version 只能寫一種"
        return True, None
    if not (isinstance(entry.get("source"), str) and entry["source"].strip()):
        return True, "source 要寫 git 網址（寫了 version 就要有 source）"
    version = entry.get("version")
    if not (isinstance(version, str) and version.strip()) or any(c.isspace() for c in version):
        return True, "version 要寫標籤（vX.Y.Z）或 commit"
    return True, None


def _instance_settings(instance):
    """The settings of `instance` (as candidates() takes it: None the configured one, "" none) for telling drafts:
    None when they cannot be told (no instance, or its plugins.json unreadable), {} without a plugins.json."""
    if instance is None:
        try:
            return config.settings()
        except Exception:
            return None
    if not instance:
        return None
    path = os.path.join(instance, "plugins.json")
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except (OSError, ValueError):
        return None
    return d if isinstance(d, dict) else None


def discover(engine=None, instance=None, root=None, broken=None, drafts=None, settings=_READ):
    """([(name, folder, plugin.json as a dict)], [problem]) for every plugin found. A bad file is a problem, not
    an exception; two plugins of the same name: the first found is kept (engine before external before local).
    `broken`, a list if given, receives (name, folder) of each file that could not be read (named after its folder;
    "core" for the core's). An external plugin the instance's settings (`settings`, read from `instance` when not
    given) do not declare is a draft: left out, and put in `drafts` as (name, folder, dict) when that is a list.
    Settings that cannot be read draft nothing (each plugin's place is unknown then, placement())."""
    if settings is _READ:
        settings = _instance_settings(instance)
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
        folder = os.path.dirname(os.path.realpath(path))
        if settings is not None and external(folder, root):
            entry = instance_entry(d["name"], settings)
            if entry is not None and not source_of(entry)[0]:
                if drafts is not None:
                    drafts.append((d["name"], folder, d))
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
    `machine` (default: this one); a plugin "on" here whose "uses" this engine does not offer is "blocked", the reason
    saying which (C-94); an external plugin the instance does not declare is listed last, as "draft"
    (C-61 E1: never installed or scheduled, and its marked lines are taken away like an off one's), one whose
    source is written unusably as "unknown". Anything unreadable is a problem, the plugins it decides are "unknown" with the
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
    broken, drafts = [], []
    plugins, found = discover(engine, inst or "", root, broken, drafts, settings)
    problems += found
    out = []
    for n, f, d in plugins:
        _, bad = source_of(instance_entry(n, settings)) if settings is not None and external(f, root) else (True, None)
        if bad:
            out.append((n, f, d, "unknown", "實例設定裡 plugins.%s 的來源不能用：%s" % (n, bad)))
            continue
        where = placement(n, d, machine, settings, bot, known)
        out.append([n, f, d, where, why_unknown(n, settings, bot, known, machine) if where == "unknown" else ""])
    users = [r for r in out if r[3] == "on" and r[0] != "core" and r[2].get("uses")]
    if users:
        try:
            core = interfaces(engine)
        except PluginError as e:
            core = None
            for r in users:
                r[3], r[4] = "unknown", text.t("plugin.interfaces_unreadable", error=e)
        while core is not None:  # a plugin blocked (or unknown) offers nothing: again until no other one goes with it
            mine, elsewhere, unsure, clash = provided(out + [(n, f, d, "draft", "") for n, f, d in drafts], core)
            offered, more = dict(core, **mine), False
            for r in (r for r in users if r[3] == "on"):
                why = unsure_why(r[2], offered, core, unsure, [n for n, _f in broken])
                if why:  # never blocked on a guess: unknown, nothing added or removed
                    r[3], r[4], more = "unknown", why, True
                    continue
                why = blocked_why(r[2], offered, elsewhere)
                if why:
                    r[3], r[4], more = "blocked", why, True
            if not more:
                problems += clash
                break
    else:
        try:
            problems += provided(out, interfaces(engine))[3]
        except PluginError:
            pass
    out = [tuple(r) for r in out]
    out += [(n, f, None, "unknown", "plugin.json 讀不出來") for n, f in broken]
    out += [(n, f, d, "draft", DRAFT_WHY % n) for n, f, d in drafts]
    return out, problems


def external_notes(rows, settings, root=None):
    """[(name, words)] about the external plugins (C-61 E1): one declared in the instance with no folder here (E2's
    install clones it), and one whose folder is not named as its plugin.json's "name" (for example a plugin named draw in plugins/sketch)."""
    out, here = [], set()
    for n, f, d, where, _ in rows:
        if d is None or not external(f, root):
            continue
        here.add(n)
        if os.path.basename(os.path.realpath(f)) != n:
            out.append((n, "資料夾叫 %s，plugin.json 的 name 是 %s：資料夾請改成 plugins/%s（clone 也會放在那裡）"
                        % (os.path.basename(os.path.realpath(f)), n, n)))
    block = settings.get("plugins") if isinstance(settings, dict) else None
    for n, entry in sorted(block.items()) if isinstance(block, dict) else []:
        if n not in here and source_of(entry) == (True, None):
            out.append((n, "實例寫了 %s（%s），這台沒有 %s" % (
                n, "local" if entry.get("local") else "%s %s" % (entry["source"], entry["version"]),
                os.path.join(root or config.root(), EXTERNAL, n))))
    return out


DRAFT_WHY = "草稿：實例的 plugins.json 還沒寫 plugins.%s 的來源（source 和 version，或 local: true），不裝、不排程、不問設定"


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
    ("disabled": its disabled_on names it), or blocked (its "uses" not offered: C-94). Only what the settings plainly
    say: unknown is not off (skills_unsure has those). Never raises."""
    try:
        rows, _ = on_this_machine(machine, engine)
    except Exception:
        return set()
    where = dict((r[0], r[3]) for r in rows)
    return set(sk for sk, owner in skill_owners(rows, engine).items() if where.get(owner) in ("disabled", "blocked"))


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
    pointed at this engine (install.sh), and a skill no plugin owns (tour) is linked. install.sh does the rest.
    External plugins' skills (C-61 E2: plugin.json "skills", a path in the plugin's folder) the same way: linked
    while the plugin is on or off here, taken away (only our link to that folder) when it is disabled or a draft,
    left alone when unknown; "external" lists them, "clash" (skill, plugin) one whose name an engine skill has
    (any folder under <engine>/skills, the local-only ones of #setup too: an external "update" took #setup's
    update skill, #code-review-2 of 59cb60c): the engine's stays, the plugin's is not linked; "outside" (skill,
    plugin) one whose path leaves the plugin's folder: not linked."""
    engine = engine or ENGINE
    home_skills = os.path.expanduser("~/.claude/skills")
    folder = os.path.join(engine, "skills")
    names = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))
                   and not os.path.exists(os.path.join(folder, d, LOCAL_ONLY))) if os.path.isdir(folder) else []
    out = {"names": names, "off": set(), "made": [], "removed": [], "taken": [], "kept": [], "waiting": [], "why": {},
           "external": [], "clash": [], "outside": []}
    engine_names = set(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))) if os.path.isdir(folder) else set()
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
        out["off"] = set(sk for sk, owner in owners.items() if where.get(owner) in ("disabled", "blocked"))
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
    for owner, top, d, where, _ in rows or []:
        if d is None or not external(top):
            continue
        for sk in d.get("skills", []):
            name, src = sk.get("name"), os.path.realpath(os.path.join(top, sk.get("path", "")))
            if not isinstance(name, str) or re.match(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$", name) is None:
                continue
            if src != os.path.realpath(top) and not src.startswith(os.path.realpath(top) + os.sep):  # "" or "." is the folder
                if where in ("on", "off"):
                    out["outside"].append((name, owner))
                continue
            link = os.path.join(home_skills, name)
            if where in ("disabled", "draft", "blocked"):
                if not add_only and os.path.islink(link) and os.path.realpath(link) == src:
                    os.remove(link)
                    out["removed"].append(name)
                continue
            if where not in ("on", "off") or not os.path.isdir(src):
                continue
            if name in engine_names:
                out["clash"].append((name, owner))
                continue
            out["external"].append(name)
            if os.path.islink(link):
                if not add_only and os.path.realpath(link) != src:
                    os.remove(link)
                    os.symlink(src, link)
                    out["made"].append(name)
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
