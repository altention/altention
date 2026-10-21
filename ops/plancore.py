"""What the plugin.json files ask this machine to have (cron lines, hooks, Claude Code settings), what it has, and
the two compared by content (stage 3a.6). Read only: nothing here writes a crontab or a settings file.

Comparing by content, not by text: a cron line is (when it runs, its environment, its command as words with ~ and
$HOME spelled out); the comment at its end (the old "# planner", the coming "# altention:<plugin>" markers) is not
content, only reported. A hook is (event, matcher, command as words, timeout). So the same command written with
other quoting is the same, and only a real difference shows.

A plugin whose place is unknown (plugin.on_this_machine) is not compared at all: nothing would be added or taken
away for it. A line or hook of the engine that no plugin asks for is listed as "extra". Only our lines are read:
those naming the engine's folder or carrying our markers; every other line (other projects in the same crontab,
SHELL=, PATH=, notes) is opaque text, never parsed, kept byte for byte by whatever writes later. A crontab or
settings.json that cannot be read is unknown, never empty ("no crontab for <user>" is the only empty one). A line of
ours commented out that matches a declared item is "paused" by hand: not a difference, never filled in again.
Standard library only, Python 3.8.
"""
import hashlib
import json
import os
import re
import shlex
import subprocess

from altention import config, plugin

DAYS = {"sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6}
NEW_MARK = re.compile(r"#\s*altention:([A-Za-z0-9_-]+)(?:/([A-Za-z0-9_.-]+))?\s*$")  # what apply writes at the end of a line
NAME_OK = re.compile(r"^[A-Za-z0-9_.-]+$")  # an item name that can go into the marker (<plugin>/<name>)
# COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
OLD_MARK = re.compile(r"#\s*(planner|usage)\s*$")  # what the old install scripts wrote (at the end, not anywhere)


class Unknown(Exception):
    """A crontab or settings.json that could not be read: not the same as empty."""


def minutes(text):
    m = re.match(r"^(\d+)([mh])$", str(text))
    if not m:
        raise ValueError("看不懂的長度：%r" % text)
    return int(m.group(1)) * (60 if m.group(2) == "h" else 1)


def machine_minute(every, name):
    """The spreading of machines: from the machine's stable name in the list (manager 2026-10-06), as the scheduler
    does; 0 without one."""
    if not name:
        return 0
    return int(hashlib.md5(name.encode()).hexdigest(), 16) % 60 % every


def cron_time(when, name):
    """A plugin.json "when" as cron's five fields ("@reboot" and other cron strings as they are)."""
    if isinstance(when, str):
        return when
    if "at" in when:
        if not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", str(when["at"])):
            raise ValueError("at 要寫成 HH:MM：%r" % when["at"])
        h, m = (int(x) for x in when["at"].split(":"))
        days = when.get("days", "daily")
        dow = "*" if days in (None, "daily") else ",".join(str(d) for d in sorted(DAYS[x] for x in days))
        return "%d %d * * %s" % (m, h, dow)
    if "every" in when:
        every = minutes(when["every"])
        off = when.get("offset", 0)
        off = machine_minute(every, name) if off == "machine" else (minutes(off) if off else 0)
        if when.get("after"):  # a fixed time after the machine's minute (3a.10: the sync 5 minutes after the update)
            off = (off + minutes(when["after"])) % every
        if off >= every:
            raise ValueError("offset 要比 every 小")
        if every < 60 and 60 % every == 0:
            return "*/%d * * * *" % every if off == 0 else "%s * * * *" % ",".join(str(x) for x in range(off, 60, every))
        if every % 60 == 0 and 1440 % every == 0:
            hours, (off_h, off_m) = every // 60, divmod(off, 60)
            return "%d %s * * *" % (off_m, "*" if hours == 1 else ("%d-23/%d" % (off_h, hours) if off_h else "*/%d" % hours))
        raise ValueError("every %s 寫不成 cron" % when["every"])
    raise ValueError("once 寫不成 cron")


def words(command):
    """A command as shell words, comments dropped, ~ and $HOME spelled out (also after > and >>, and in VAR=~/…)."""
    home = os.path.expanduser("~")
    out = []
    for w in shlex.split(command, comments=True):
        assign = re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", w)  # VAR=~/x: the shell spells that ~ out too
        for prefix in ((assign.group(0),) if assign else ()) + (">>", ">", ""):
            if w.startswith(prefix + "~/") or w == prefix + "~":
                w = prefix + home + w[len(prefix) + 1:]
                break
        out.append(w.replace("$HOME", home))
    return out


def comment(line):
    """The comment at the end of a cron line ("# planner"), or ""."""
    lexer = shlex.shlex(line, posix=True)
    lexer.whitespace_split, lexer.commenters = True, ""
    try:
        parts = list(lexer)
    except ValueError:
        return ""
    for i, p in enumerate(parts):
        if p.startswith("#"):
            return " ".join(parts[i:])
    return ""


def split_cron(line):
    """(time, command) of a crontab line, or None for blank lines, comments and VAR=value lines."""
    s = line.strip()
    if not s or s.startswith("#") or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", s):
        return None
    if s.startswith("@"):
        t, _, rest = s.partition(" ")
        return t, rest.strip()
    parts = s.split(None, 5)
    return (" ".join(parts[:5]), parts[5]) if len(parts) == 6 else None


def cron_key(time, command):
    return (time, tuple(words(command)))


def render_schedule(name, folder, d, s, settings, machine_name):
    """(time, command) of one schedule item as today's cron line would read."""
    do = s["do"]
    if "run" in do:
        command = plugin.expand(do["run"], folder, settings)
    else:  # a message: written as `altention dc post` to the plugin's channel
        channel = next((c for c in d.get("channels", []) if c.get("name") == do.get("channel")), None)
        target = plugin.expand("{setting:%s}" % channel["setting"], folder, settings) if channel and channel.get("setting") \
            else (do.get("channel") or "")
        command = '"%s" dc post %s %s' % (os.path.join(os.path.realpath(plugin.ENGINE), "bin", "altention"), target, shlex.quote(do["message"]))
    env = " ".join('%s="%s"' % (k, plugin.expand(v, folder, settings)) for k, v in sorted((do.get("env") or {}).items()))
    if do.get("log"):
        tail = '>>"%s" 2>&1' % plugin.expand(do["log"], folder, settings)
    elif "run" in do and do.get("output", "none") == "none":
        tail = ">/dev/null 2>&1"
    else:
        tail = ""
    line = " ".join(x for x in (env, command, tail) if x)
    return cron_time(s["when"], machine_name), line.replace("%", "\\%")  # cron turns a bare % into a newline


SETTINGS_FILE = "~/.claude/settings.json"  # the only file a "settings" item may name today


def settings_wanted(rows):
    """([(plugin, key, at_least)], [(item, why not)]): the "settings" items of the plugins on here (the core's
    cleanupPeriodDays), a number each key must reach. An item naming another file, or a value that is not a number,
    is not compared and never written."""
    items, unknown = [], []
    for name, folder, d, where, reason in rows:
        if where != "on" or d is None:
            continue
        for it in d.get("settings", []):
            what = "%s/%s" % (name, it.get("key"))
            if it.get("file") != SETTINGS_FILE or not isinstance(it.get("key"), str) or \
                    not isinstance(it.get("at_least"), int) or isinstance(it.get("at_least"), bool):
                unknown.append((what, "只認 %s 裡、at_least 是整數的設定" % SETTINGS_FILE))
                continue
            items.append((name, it["key"], it["at_least"]))
    return items, unknown


def settings_short(cc, items):
    """[(plugin, key, at_least, now)] for the items `cc` (settings.json, read) does not reach; a key holding
    something that is not a number is left alone (someone put it there) and said: (plugin, key, at_least, now, why)."""
    short = []
    for name, key, least in items:
        now = cc.get(key)
        if now is None or (isinstance(now, (int, float)) and not isinstance(now, bool) and now < least):
            short.append((name, key, least, now))
    return short


def settings_odd(cc, items):
    """The items whose key holds something that is not a number: not compared, never written."""
    return [(name, key) for name, key, _ in items
            if cc.get(key) is not None and (isinstance(cc.get(key), bool) or not isinstance(cc.get(key), (int, float)))]


def elsewhere(rows, machine, settings, bot, known):
    """The cron items of plugins that are on here whose own "on" plainly leaves this machine out (the bot's
    supervisor lines on a machine that is not the bot's): a line of ours matching one of them is "elsewhere" and may
    be removed (manager 2026-10-06). Nothing when the settings, the bot's machine or the machine list (`known`)
    cannot be told, or the item names a word that is neither a known word nor a machine on the list."""
    if settings is None or not bot or not known or machine not in known:
        return []
    out = []
    for name, folder, d, where, reason in rows:
        if where != "on" or d is None:
            continue
        for c in d.get("cron", []):
            words = set(plugin._names(c.get("on", "all")) or ["all"])
            if words & {"sources", "one"}:
                continue  # sources: the instance's list is not read here; one: where the plugin runs, so here
            if plugin.unknown_on(c, known) or plugin.step_here(c, machine, True, bot):
                continue
            try:
                command = plugin.expand(c["run"], folder, settings)
            except plugin.PluginError:
                continue
            when = cron_time(c["when"], machine)
            out.append({"plugin": name, "name": c["name"], "kind": "cron", "key": cron_key(when, command),
                        "text": "%s %s" % (when, command), "scheduler": False})
    return out


def wanted(rows, machine, settings, bot):
    """Every cron line and hook the plugins that are on here ask for, and the plugins not compared (unknown).
    Schedules as the instance overrides them (plugin.effective_schedules, C-37): an item whose override cannot be used
    is unknown, and a {"kind": "held"} entry keeps its installed line out of every change; an item the instance
    turned off ("enabled": false) asks for no line, and a {"kind": "off"} entry lets compare take its marked line
    away. A cron item whose time the instance set carries "override": its time differs is changed without
    --allow-time (applycore.new_crontab)."""
    want, unknown = [], []
    for name, folder, d, where, reason in rows:  # C-85: a plugin turned off here (disabled_on, or not its machine):
        if where not in ("off", "disabled") or d is None:  # its hooks, exactly as it declares them, are taken out
            continue
        for h in d.get("hooks", []):
            try:
                command = plugin.expand(h["command"], folder, settings)
            except plugin.PluginError:
                continue  # cannot be written out: nothing to match, nothing taken
            want.append({"plugin": name, "name": "%s%s" % (h["event"], "（%s）" % h["matcher"] if h.get("matcher") else ""),
                         "kind": "hook off", "key": (h["event"], h.get("matcher") or "", tuple(words(command)), h.get("timeout")),
                         "text": command})

    def special(kind, name, item):
        want.append({"plugin": name, "name": item.get("name"), "kind": kind, "key": None, "text": ""})
    for name, folder, d, where, reason in rows:
        if where == "unknown":
            unknown.append((name, reason))
            continue
        if where != "on" or d is None:
            continue
        items, problems = plugin.effective_schedules(name, d, settings)
        for p in problems:
            unknown.append((p.split("：", 1)[0], "這一筆不用：" + p.split("：", 1)[-1]))
        for s in (items if name != "core" else []):
            if s.get("unknown"):
                unknown.append(("%s/%s" % (name, s.get("name")), "實例的設定不能用：%s；這一項現在的行不動" % s["unknown"]))
                special("held", name, s)
                continue
            if s.get("enabled") is False:
                special("off", name, s)
                continue
            try:
                time, command = render_schedule(name, folder, d, s, settings, machine)
            except (plugin.PluginError, ValueError, KeyError) as e:  # cannot be written out: not compared, never touched
                unknown.append(("%s/%s" % (name, s.get("name")), "寫不出這一項：%s" % e))
                continue
            want.append({"plugin": name, "name": s["name"], "kind": "cron", "key": cron_key(time, command),
                         "text": "%s %s" % (time, command), "scheduler": s.get("scheduler", True),
                         "override": s.get("source") == "instance"})
        for c in (items if name == "core" else d.get("cron", [])):
            if plugin.step_here(c, machine, True, bot):
                if c.get("unknown"):
                    unknown.append(("%s/%s" % (name, c.get("name")), "實例的設定不能用：%s；這一項現在的行不動" % c["unknown"]))
                    special("held", name, c)
                    continue
                if c.get("enabled") is False:
                    special("off", name, c)
                    continue
                try:
                    command = plugin.expand(c["run"], folder, settings)
                except plugin.PluginError as e:
                    unknown.append(("%s/%s" % (name, c.get("name")), "寫不出這一項：%s" % e))
                    continue
                when = cron_time(c["when"], machine)  # a string as it is, or every/offset/after for this machine
                want.append({"plugin": name, "name": c["name"], "kind": "cron", "key": cron_key(when, command),
                             "text": "%s %s" % (when, command), "scheduler": False, "on": c.get("on", "all"),
                             "override": c.get("source") == "instance"})
        for h in d.get("hooks", []):
            try:
                command = plugin.expand(h["command"], folder, settings)
            except plugin.PluginError as e:
                unknown.append(("%s/%s" % (name, h.get("event")), "寫不出這一項：%s" % e))
                continue
            key = (h["event"], h.get("matcher") or "", tuple(words(command)), h.get("timeout"))
            want.append({"plugin": name, "name": "%s%s" % (h["event"], "（%s）" % h["matcher"] if h.get("matcher") else ""),
                         "kind": "hook", "key": key, "text": command})
    return want, unknown


def engine_forms(engine):
    """How a line may name the engine's folder: its real path, and through ~ or $HOME when it is under home."""
    home = os.path.expanduser("~")
    forms = [engine]
    if engine.startswith(home + "/"):
        rest = engine[len(home) + 1:]
        forms += ["~/" + rest, "$HOME/" + rest, "${HOME}/" + rest]
    return forms


def engine_folder_at(path, engine, moved_from=()):
    """Is `path`, a folder one of our lines or hooks names where this engine's folder would be, an engine's folder
    under another name? Plain facts only (C-17): a link to this very engine (the old name left behind when the engine
    was moved), or another checkout of the engine (it has altention/config.py). A folder no longer there only when a
    person named it (`apply --moved-from`, manager 2026-10-16): gone alone says nothing about whose it was. Anything
    else is someone else's folder."""
    try:
        if os.path.realpath(path) == os.path.realpath(engine):
            return True
        if os.path.isfile(os.path.join(path, "altention", "config.py")):
            return True
        return not os.path.lexists(path) and path.rstrip("/") in named_folders(moved_from)
    except (OSError, ValueError):
        return False


def named_folders(moved_from):
    return {os.path.expanduser(m).rstrip("/") for m in moved_from or () if m}


def word_paths(w):
    """The paths a command word may name, each starting with "/": the whole word with a leading VAR=, redirection and
    quote taken off and ~, $HOME or ${HOME} at its start spelled out (#code-review on cba4b4b: "${HOME}/…" was never
    seen as this engine's), and, as before, what follows its first "/" (a word like --log=/x)."""
    home = os.path.expanduser("~")
    p = re.sub(r"^[A-Za-z_][A-Za-z0-9_]*=", "", w).lstrip("<>").lstrip("\"'")
    for start in ("${HOME}", "$HOME", "~"):
        if p == start or p.startswith(start + "/"):
            p = home + p[len(start):]
            break
    out = [p] if p.startswith("/") else []
    i = w.find("/")
    if i >= 0 and w[i:] not in out:
        out.append(w[i:])
    return [x.rstrip(";\"'") for x in out]


def in_engine(ws, engine, moved_from=()):
    """Whether a line's words name a path in this engine (engine_forms) or in an engine folder it moved from
    (old_folder_in): only such a line of an item the instance turned off is taken away, or held, by its marker
    (#code-review on c45394d: another engine's line with the same marker went too)."""
    forms = [f.rstrip("/") for f in engine_forms(engine) if f.startswith("/")]  # ~ and $HOME are spelled out in word_paths
    if any(p.startswith(f + "/") for w in ws for p in word_paths(w) for f in forms):
        return True
    return old_folder_in(ws, engine, moved_from) is not None


def old_folder_in(ws, engine, moved_from=()):
    """The first engine folder elsewhere (engine_folder_at) that a word of `ws` names a path under, or None. A word
    naming a path in this engine names no other, even when a folder above this engine is an engine too (C-38: the
    engine inside another checkout had its own lines taken for moved)."""
    forms = [f.rstrip("/") for f in engine_forms(engine) if f.startswith("/")]  # ~ and $HOME: spelled out in word_paths
    for w in ws:
        paths = word_paths(w)
        if any(path == f or path.startswith(f + "/") for path in paths for f in forms):
            continue
        for path in paths:
            parts = path.split("/")
            for n in range(2, len(parts)):  # /a, /a/b, ...: a folder the word goes inside
                prefix = "/".join(parts[:n])
                if engine_folder_at(prefix, engine, moved_from):
                    return prefix
    return None


def relocated_from(old, new, engine):
    """The folder `old` (a line's or hook's words) names where `new` (an item's words) names this engine's folder,
    every other word alike, or None. One folder for all the words that differ; never this engine itself."""
    if len(old) != len(new):
        return None
    froms = set()
    for o, n in zip(old, new):
        if o == n:
            continue
        hit = None
        for e in engine_forms(engine):
            i = n.find(e + "/")
            if i < 0:
                continue
            pre, post = n[:i], n[i + len(e):]
            if o.startswith(pre) and o.endswith(post) and len(o) > len(pre) + len(post):
                folder = o[len(pre):len(o) - len(post)]
                if folder.startswith("/") and folder.rstrip("/") not in engine_forms(engine):
                    hit = folder.rstrip("/")
                    break
        if hit is None:
            return None
        froms.add(hit)
    return froms.pop() if len(froms) == 1 else None


def marked(line):
    """The plugin named by our new marker at the end of the line, or None."""
    m = NEW_MARK.search(line)
    return m.group(1) if m else None


def marked_name(line):
    """The item named by our marker (# altention:<plugin>/<name>), or None (a marker of the plugin alone, or none)."""
    m = NEW_MARK.search(line)
    return m.group(2) if m else None


def marker(w):
    """Our marker for item `w`: <plugin>/<name>, or <plugin> alone when the name cannot go into it."""
    return "%s/%s" % (w["plugin"], w["name"]) if NAME_OK.match(str(w.get("name") or "")) else w["plugin"]


# COMPAT: crontab 還是舊格式的機器（# planner、# usage，或沒有標記的行）；每台的 crontab 都沒有舊格式的行（plugins.py plan 沒有 old、unclaimed）之後
def ours(line, engine):
    """Could this crontab line be ours: our new marker at its end, an old marker at its end, or a file inside the
    engine (its folder followed by /)? Read as plain text; other people's lines are never parsed. Only a line with
    the new marker is ours for sure; the others are claimed only when they match a declared item exactly."""
    return bool(marked(line) or OLD_MARK.search(line) or any(f + "/" in line for f in engine_forms(engine)))


def installed_cron(lines, engine):
    """([(key, text, comment, marker plugin or None)] of the job lines that may be ours, [(key, text)] of such lines
    commented out, [text] of such lines that could not be read). Every other line is left alone, unparsed."""
    jobs, paused, unreadable = [], [], []
    for line in lines:
        if not ours(line, engine):
            continue
        s = line.strip()
        target = paused if s.startswith("#") else jobs
        body = s.lstrip("#").strip() if s.startswith("#") else s
        try:
            parts = split_cron(body)
            if parts is None:
                continue
            key = cron_key(*parts)
        except ValueError:
            unreadable.append(s)
            continue
        target.append((key, s, comment(line), marked(s)) if target is jobs else (key, s))
    return jobs, paused, unreadable


def installed_hooks(settings_json, engine):
    out = []
    for event, groups in ((settings_json or {}).get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                command = h.get("command") or ""
                if engine in " ".join(words(command)):
                    out.append(((event, g.get("matcher") or "", tuple(words(command)), h.get("timeout")), command))
    return out


def other_hooks(settings_json, engine):
    """[(key, command)] of the hooks that do not name this engine's folder, keyed as installed_hooks keys them: the
    ones an engine moved elsewhere may have left (compare() takes such a hook for an item only by relocated_from)."""
    out = []
    for event, groups in ((settings_json or {}).get("hooks") or {}).items():
        for g in groups or []:
            for h in g.get("hooks") or []:
                command = h.get("command") or ""
                try:
                    ws = words(command)
                except ValueError:
                    continue
                if engine not in " ".join(ws):
                    out.append(((event, g.get("matcher") or "", tuple(ws), h.get("timeout")), command))
    return out


def compare(want, cron_have, hook_have, paused=(), away=(), engine=None, old_hooks=(), moved_from=()):
    """[(status, item or None, installed text, marker)]:
    same       an installed line or hook with the item's content (an old-format line so matched is claimed);
    time differs  the command installed at another time (changed only when named by hand);
    paused     our line commented out by hand: not a difference, never filled in;
    missing    nothing like it is installed;
    extra      a line with our new marker that no plugin asks for (apply removes it only when the plugin is off);
    elsewhere  a line with our new marker matching an item of `away` (plancore.elsewhere: its own "on" leaves this
               machine out), the command alike, the time aside: removed;
    duplicate  a second (third…) installed line for an item already matched: the same command words, old marker or
               new (an old install script run again after the markers changed adds its lines twice);
    replaced   an item not installed as it is now, whose earlier line is still there: a line with our marker of the
               same plugin and the same item name (# altention:<plugin>/<name>) whose command is in this engine or
               in an engine folder it moved from (in_engine; C-42: another engine's line, in a folder that is
               neither, is not ours to rewrite: the item is missing and that line is left as it is), or, for a line marked with the
               plugin alone (written before names went into the marker), the only such line at the same time with
               the same program, matched by no other item. The setting behind it changed (planner.channel moved to
               #secretary, owner 2026-10-15): the new line takes the old one's place; never two lines for one item.
               Also (C-14, given `engine`): a line with our marker of the same plugin, at the same time, whose words
               are the item's but for an engine folder elsewhere (relocated_from, engine_folder_at: a link to this
               engine, another checkout of it, or a folder gone that `moved_from` names); the first such line in the
               table. A hook of `old_hooks` so alike, for an item not installed, is replaced in place the same way;
    moved      a line with our marker, of a plugin that has items here, naming a path inside such an engine folder
               (the old planner lines with no counterpart, a second old copy of an item): removed; also a hook
               twin of an item at such a folder beside the one replaced or the item's own: taken out (it would run
               twice);
    gone       a line or hook that would have been replaced but its old folder is gone and nobody named it
               (`apply --moved-from <folder>`): kept, and said, a folder gone alone saying nothing of whose it was;
    hook off   a hook exactly as a plugin turned off here declares it (C-85: wanted()'s "hook off" items; its event,
               matcher, command words and timeout): removed, as cron lines of a plugin off here are;
    turned off a line with our marker naming an item the instance turned off ("enabled": false, C-37), its command
               in this engine or a folder it moved from (in_engine): removed; another engine's line as before;
    held       such a line of an item whose instance override cannot be used: kept byte for byte;
    unclaimed  looks like an old one of ours (an old marker, or a file in the engine) but matches nothing exactly:
               someone else's as far as apply goes, kept as it is; also any hook nothing asks for (hooks carry
               no marker, so none is ever removed)."""
    special = dict(((w["plugin"], w["name"]), w) for w in want if w["kind"] in ("held", "off"))
    hooks_off = dict((w["key"], dict(w, kind="hook")) for w in want if w["kind"] == "hook off")
    want = [w for w in want if w["kind"] not in ("held", "off", "hook off")]
    result, used_c, used_h, used_p = [], set(), set(), set()
    for w in want:
        pool, used = (cron_have, used_c) if w["kind"] == "cron" else (hook_have, used_h)
        hit = next((i for i, h in enumerate(pool) if i not in used and h[0] == w["key"]), None)
        if hit is not None:
            used.add(hit)
            result.append(("same", w, pool[hit][1], pool[hit][2] if w["kind"] == "cron" else ""))
            continue
        near = next((i for i, h in enumerate(pool) if i not in used and w["kind"] == "cron" and h[0][1] == w["key"][1]), None)
        if near is not None:
            used.add(near)
            result.append(("time differs", w, pool[near][1], pool[near][2]))
            continue
        off = next((i for i, p in enumerate(paused) if i not in used_p and w["kind"] == "cron" and p[0][1] == w["key"][1]), None)
        if off is not None:
            used_p.add(off)
            result.append(("paused", w, paused[off][1], ""))
            continue
        result.append(("missing", w, "", ""))
    gone_c = replaced(result, cron_have, used_c, engine, moved_from)
    gone_dirs = {f for _w, f in gone_c.values()}
    if engine:
        gone_dirs |= replaced_hooks(result, old_hooks, engine, moved_from)
    plugins_here = {w["plugin"] for w in want if w["kind"] == "cron"}
    matched = [w for w in want if w["kind"] == "cron"]
    for i, h in enumerate(cron_have):
        if i in used_c:
            continue
        held = special.get((h[3], marked_name(h[1]))) if h[3] and engine and in_engine(h[0][1], engine, moved_from) else None
        if held is not None:  # C-37: an item the instance turned off (its line goes), or whose override is unusable (kept)
            result.append(("turned off" if held["kind"] == "off" else "held", held, h[1], h[2]))
            continue
        twin = next((w for w in matched if h[0][1] == w["key"][1]), None)
        gone = next((w for w in away if h[3] and h[0] == w["key"] and marked(h[1]) == w["plugin"]), None) or \
            next((w for w in away if h[3] and h[0][1] == w["key"][1] and marked(h[1]) == w["plugin"]), None)
        if twin is not None and any(r[0] in ("same", "time differs") and r[1] is twin for r in result):
            result.append(("duplicate", twin, h[1], h[2]))
        elif engine and h[3] and h[3] in plugins_here and old_folder_in(h[0][1], engine, moved_from):
            result.append(("moved", None, h[1], h[2]))
        elif i in gone_c:
            result.append(("gone", gone_c[i][0], h[1], h[2]))
        elif h[3] and h[3] in plugins_here and any(f + "/" in x for f in gone_dirs for x in h[0][1]):
            result.append(("gone", None, h[1], h[2]))  # the same gone folder, no item of its own (the old planner lines)
        elif gone is not None:
            result.append(("elsewhere", gone, h[1], h[2]))
        else:
            result.append(("extra" if h[3] else "unclaimed", None, h[1], h[2]))
    for i, p in enumerate(paused):
        if i not in used_p:
            result.append(("unclaimed", None, p[1], ""))
    for i, h in enumerate(hook_have):
        if i not in used_h:
            off = hooks_off.get(h[0])
            result.append(("hook off", off, h[1], "") if off is not None else ("unclaimed", None, h[1], ""))
    return result


def replaced(result, cron_have, used_c, engine=None, moved_from=()):
    """compare(): each missing cron item that an earlier line of its own takes the place of (see "replaced"),
    changed in `result` in place; the line counted as used. Returns {line index: item} of the lines that would have
    been taken but for their old folder being gone and not named (--moved-from): compare calls them "gone"; each
    with that folder ({line index: (item, folder)})."""
    def program(key):
        return key[0], key[1][:2]  # when it runs, and what runs (the tool or interpreter and its script)
    missing = [(i, r[1]) for i, r in enumerate(result) if r[0] == "missing" and r[1]["kind"] == "cron"]
    for i, w in missing:  # by name first: certain, for a line of this engine or a folder it moved from (C-42)
        hit = next((j for j, h in enumerate(cron_have) if j not in used_c and h[3] == w["plugin"]
                    and marked_name(h[1]) == w["name"] and (not engine or in_engine(h[0][1], engine, moved_from))), None)
        if hit is not None:
            used_c.add(hit)
            result[i] = ("replaced", w, cron_have[hit][1], cron_have[hit][2])
    for i, w in missing:  # a line marked with the plugin alone: only when nothing else could be meant
        if result[i][0] != "missing":
            continue
        lines = [j for j, h in enumerate(cron_have) if j not in used_c and h[3] == w["plugin"]
                 and marked_name(h[1]) is None and program(h[0]) == program(w["key"])]
        others = [x for k, x in missing if k != i and result[k][0] == "missing" and x["plugin"] == w["plugin"]
                  and program(x["key"]) == program(w["key"])]
        if len(lines) == 1 and not others:
            used_c.add(lines[0])
            result[i] = ("replaced", w, cron_have[lines[0]][1], cron_have[lines[0]][2])
    gone = {}
    if not engine:
        return gone
    for i, w in missing:  # the engine moved (C-14): the same line but for an old engine folder
        if result[i][0] != "missing":
            continue
        found = [(j, relocated_from(h[0][1], w["key"][1], engine)) for j, h in enumerate(cron_have)
                 if j not in used_c and h[3] == w["plugin"] and h[0][0] == w["key"][0]]
        found = [(j, f) for j, f in found if f]
        ok = [(j, f) for j, f in found if engine_folder_at(f, engine, moved_from)]
        if ok:  # the first in the table takes the item; any other of an engine folder is "moved" (compare)
            used_c.add(ok[0][0])
            result[i] = ("replaced", w, cron_have[ok[0][0]][1], cron_have[ok[0][0]][2])
        else:
            for j, f in found:
                if not os.path.lexists(f):
                    gone.setdefault(j, (w, f))
    return gone


def replaced_hooks(result, old_hooks, engine, moved_from=()):
    """compare(), C-17: for each hook item, its twins at old engine folders (the same event and matcher, the words
    alike but for the folder; engine_folder_at): one of them, for an item not installed, "replaced" (the item's
    command in its place, its timeout set to the item's); every other "moved" (taken out: it would run twice). A twin
    in a folder no longer there that nobody named (--moved-from) is "gone": kept, and said. Returns those folders."""
    used, gone = set(), set()
    for i, r in enumerate(list(result)):
        if r[0] not in ("missing", "same") or r[1]["kind"] != "hook":
            continue
        w = r[1]
        event, matcher, ws, _timeout = w["key"]
        found = [(j, relocated_from(list(h[0][2]), list(ws), engine)) for j, h in enumerate(old_hooks)
                 if j not in used and h[0][0] == event and h[0][1] == matcher]
        found = [(j, f) for j, f in found if f]
        ok = [(j, f) for j, f in found if engine_folder_at(f, engine, moved_from)]
        used.update(j for j, _ in ok)
        if ok and r[0] == "missing":
            result[i] = ("replaced", w, old_hooks[ok[0][0]][1], "")
            ok = ok[1:]
        for j, _ in ok:
            result.append(("moved", w, old_hooks[j][1], ""))
        for j, f in found:
            if j not in used and not os.path.lexists(f):
                used.add(j)
                gone.add(f)
                result.append(("gone", w, old_hooks[j][1], ""))
    return gone


def read_crontab_raw(run=subprocess.run):
    """This user's crontab exactly as `crontab -l` prints it, as bytes (b"" only when crontab says there is none).
    Anything else that goes wrong (failing, missing, timing out, no permission) raises Unknown: not having read it is
    not "empty". Bytes, not text: a line of someone else's may hold a form feed, a lone carriage return or U+2028,
    which a text read would split, and a write would then change."""
    try:
        r = run(["crontab", "-l"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30)
    except (OSError, subprocess.TimeoutExpired) as e:
        raise Unknown("crontab -l 跑不起來（%s）" % e)
    err = r.stderr.decode("utf-8", "replace") if isinstance(r.stderr, bytes) else (r.stderr or "")
    if r.returncode == 0:
        return r.stdout if isinstance(r.stdout, bytes) else r.stdout.encode("utf-8")
    if re.search(r"no crontab for", err, re.I):
        return b""
    raise Unknown("crontab -l 失敗（%s）" % (err.strip()[:200] or "結束碼 %s" % r.returncode))


def raw_lines(raw):
    """The lines of a raw crontab, split at b"\n" only (a trailing newline makes no extra line)."""
    lines = raw.split(b"\n")
    return lines[:-1] if lines and lines[-1] == b"" else lines


def join_lines(lines):
    return b"".join(l + b"\n" for l in lines)


def text(line):
    """A raw line as text for reading it (never for writing it back); a line that is not UTF-8 is not ours."""
    try:
        return line.decode("utf-8")
    except UnicodeDecodeError:
        return None


def read_crontab(run=subprocess.run):
    """The crontab's lines as text, for reading only (plan): split at \n only; lines that are not UTF-8 dropped
    (they cannot be ours)."""
    return [t for t in (text(l) for l in raw_lines(read_crontab_raw(run))) if t is not None]


def read_settings_raw():
    """(bytes or None, ~/.claude/settings.json parsed from those very bytes): one read, so nothing Claude Code writes
    in between can be missed. None and {} when the file does not exist; unreadable or not a JSON object raises
    Unknown."""
    path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except FileNotFoundError:
        return None, {}
    except OSError as e:
        raise Unknown("%s 讀不出來（%s）" % (path, e))
    try:
        data = json.loads(raw.decode("utf-8"))
    except ValueError as e:
        raise Unknown("%s 讀不出來（%s）" % (path, e))
    if not isinstance(data, dict):
        raise Unknown("%s 不是 JSON 物件" % path)
    return raw, data


def read_settings():
    """~/.claude/settings.json; {} only when it does not exist. Unreadable or not JSON raises Unknown (Claude Code may
    be writing it)."""
    path = os.path.expanduser("~/.claude/settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError) as e:
        raise Unknown("%s 讀不出來（%s）" % (path, e))
    if not isinstance(data, dict):
        raise Unknown("%s 不是 JSON 物件" % path)
    return data
