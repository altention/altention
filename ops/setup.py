#!/usr/bin/env python3
"""The settings of a new installation, asked in Discord's #setup (C-48): the only command #setup's Claude runs.

  python3 ops/setup.py status [--json]        what is answered, what comes next, whether setup is done
  python3 ops/setup.py questions [--json]     the questions still to ask, in order, word for word (ops/plugins.py's)
  python3 ops/setup.py answer KEY VALUE       write one answer into the instance, read it back, commit
  python3 ops/setup.py dashboard              #dashboard made, usage.channel and status.channel set, the boards posted
  python3 ops/setup.py secretary              #secretary opened at the chosen times, planner.channel set
  python3 ops/setup.py guide                  #guide brought in line with the guide (dcctl guide)
  python3 ops/setup.py backup-set NAME (URL | --create ACCOUNT/REPO)
                                              one backup (instance, logs, progress, planner): ops/remote.py set, after
                                              making the private repository with gh when --create
  python3 ops/setup.py repos [--json]         their GitHub repositories (gh repo list), to choose the instance's backup
  python3 ops/setup.py restore URL            the instance from the backups in place of the one install.sh just made
                                              (only that one, untouched: kept as instance.fresh-<time>, never
                                              deleted); which machine comes next, and done installs, putting it all
                                              back when that fails (C-57 S2)
  python3 ops/setup.py login-check [--json]   how this machine can reach GitHub for the backups: gh (logged in, as
                                              whom) and an SSH key; only exit statuses and the account are read
  python3 ops/setup.py done                   setup.done true, the instance committed, install.sh again (what
                                              waited for it starts), then whichever of dashboard, guide and secretary
                                              is not there yet, and last the bot asked to open #chat. Run again after
                                              a step failed: what is done is not done twice
  python3 ops/setup.py reconcile [--dry-run] [--json] [--if-changed]
                                              what this version declares (plugin.json's channels and asks) next to
                                              the instance and the server: filled in when it needs nobody (#update,
                                              #guide, #audit and #guide moved to the system category, #setup's
                                              folder), asked in #setup when it would add a channel to the main area
                                              or spend the subscription; --dry-run only says it, --if-changed does
                                              nothing when the engine is the one last compared (C-57 S3)
  python3 ops/setup.py tutorial               #tutorial opened, its Claude asked to start the tour at step 1
  python3 ops/setup.py plugin-add NAME [vX.Y.Z] [--yes [--hooks-ok]]
                                              a plugin from the official list (C-81 K2, ops/plugins.py add): without
                                              --yes what it would do here; with it written into the instance and
                                              pushed, then install.sh on this machine
  python3 ops/setup.py plugin-apply PLUGIN MACHINE
                                              after plugin-add, another machine brought in line now (ops/apply.py
                                              MACHINE: all it has not taken yet), then doctor; only for an external
                                              plugin the instance names that runs on MACHINE, not this machine
  python3 ops/setup.py plugin-steps PLUGIN [--all] [--json]
                                              what PLUGIN still needs here (C-77, altention/plugin_steps.py): its
                                              system tools and its guide[] steps, each checked again now, the next
                                              step marked; installs nothing. --all: on the other machines that use
                                              it too, each its own plan (C-77 G2). Exit 0 all done, 3 something left
  python3 ops/setup.py plugin-run PLUGIN STEP
                                              a guide[] step the plugin does itself (its "run"), once the person
                                              said yes, then the step checked again (C-77 G2)
  python3 ops/setup.py offer PLUGIN             an optional plugin of the official list whose moment came (its offer.when
                                              "asked"): asked in #setup like the reconcile's questions, when it may be
                                              (C-64: never after 「先不要」, at most three 「之後再說」)
  python3 ops/setup.py secret-send PLUGIN NAME MACHINE
                                              a plugin's secret stored here, written on MACHINE the same way (ssh,
                                              the value inside the script on stdin, never on a command line); only
                                              to a machine that uses the plugin (C-77 G3)
  python3 ops/setup.py plugin-finish PLUGIN
                                              once PLUGIN needs nothing more here: install.sh again, so what waited for
                                              its steps (guide[]'s gate) starts now (C-77 G5)
  python3 ops/setup.py plugin-tool PLUGIN NEED
                                              a system tool installed with the plugin's command for this system,
                                              only one without sudo (sudo is the person's, in their own terminal),
                                              then checked again (C-77 G2)
  python3 ops/setup.py plugin-said PLUGIN STEP yes|no
                                              the person's answer to a step that cannot be checked here (its check is
                                              null, or gave 3 just now); STEP is a guide[] id or needs:<name>

Why one command: #setup's Claude runs in auto mode with one allow rule, `Bash(python3 <engine>/ops/setup.py:*)`
(the folder install.py makes, setup_folder()); what is written (settings, the crontab through install.sh, a push)
is decided here, never by a command the classifier would have to judge (measured 2026-10-18, setup-in-discord.md 6).
So the checks here are strict: a value not offered is refused, a typed one is checked, every write is read back the
way the engine reads it.

The answers: each into its setting, nested (never a dotted key), and the instance's `setup.answered` keeps which
question got which answer, so that a conversation cut short goes on from the first question not answered
(`status`). An instance without `setup` (every installation from before C-48, and one restored from a backup) counts
as set up: nothing is asked. The schedules are written once, by `done` (install.sh): until then install.py holds
what would spend the subscription (progress; the usage board's limits).
Exit status: 0 done; 1 a value or a check refused it (the line says why: ask again); 2 wrong usage, or a step that
could not be done (said). Standard library only, Python 3.8.
"""
import io
import json
import os
import re
import subprocess
import sys
import time
from contextlib import redirect_stdout

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)
sys.path.insert(0, HERE)
from altention import config, plugin, text  # noqa: E402
import plugins  # noqa: E402

SECRETARY = plugins.SECRETARY
REMOTE_NAMES = ("instance", "logs", "progress", "planner")
BOARDS = ("頻道", "機器和機器人", "需要注意的事", "在等你的事")  # status/boards.py's four, in their order
WAIT = 60       # seconds a channel the bot opens may take to be there
BOARD_ROUNDS = 10


class Refused(Exception):
    """A value or a check that says no: exit 1, the text says why."""


class Failed(Exception):
    """A step that could not be done: exit 2."""


def dcctl(*args, inp=None, timeout=120):
    """dcctl of this engine (the bot's REST tool): CompletedProcess."""
    return subprocess.run([sys.executable, os.path.join(ENGINE, "dcbot", "tools", "dcctl")] + list(args), input=inp,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)


def altention(*args, timeout=120):
    return subprocess.run([os.path.join(ENGINE, "bin", "altention")] + list(args), stdin=subprocess.DEVNULL,
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=timeout)


def script(argv, timeout=1800):
    """Run a script in a process group of its own; taking longer than `timeout`, the whole group is killed (not
    only the script: its git clone or pip too; #code-review-2 of 244dabf) and TimeoutExpired raised."""
    import signal
    p = subprocess.Popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                         universal_newlines=True, cwd=ENGINE, start_new_session=True)
    try:
        out, _ = p.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except OSError:
            pass
        p.communicate()
        raise
    return subprocess.CompletedProcess(argv, p.returncode, out)


def sleep(seconds):
    time.sleep(seconds)


# --- the instance's settings ---------------------------------------------------------------------------------------

def settings_path():
    inst = config.instance_dir()
    if not inst:
        raise Failed("這台還沒有實例：先在終端機跑 ./install.sh")
    return os.path.join(inst, "plugins.json")


def read_settings():
    with open(settings_path(), encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict):
        raise Failed("實例的 plugins.json 不是一個物件")
    return d


def write_settings(d):
    path = settings_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(json.dumps(d, ensure_ascii=False, indent=2) + "\n")
    os.replace(tmp, path)


def node(d, dotted, make=True):
    """The object at `dotted` in d (made when missing and make); Failed when something else is in the way."""
    for part in dotted.split("."):
        if part not in d and make:
            d[part] = {}
        d = d.get(part)
        if not isinstance(d, dict):
            if d is None and not make:
                return None
            raise Failed("plugins.json 的 %s 不是物件，沒有寫" % dotted)
    return d


def put(d, dotted, value):
    head, _, last = dotted.rpartition(".")
    node(d, head)[last] = value


def drop(d, dotted):
    head, _, last = dotted.rpartition(".")
    parent = node(d, head, make=False) if head else d
    if isinstance(parent, dict):
        parent.pop(last, None)


def setup_state(d=None):
    """The instance's `setup` object, or None (no such key: set up before C-48, nothing to ask)."""
    d = read_settings() if d is None else d
    s = d.get("setup")
    return s if isinstance(s, dict) else None


def commit(message):
    """The instance's plugins.json committed (nothing to commit is fine); never pushed here (the sync does)."""
    inst = config.instance_dir()
    git = ["git", "-C", inst]
    subprocess.run(git + ["add", "plugins.json"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if subprocess.run(git + ["diff", "--cached", "--quiet"]).returncode == 0:
        return
    who = [] if subprocess.run(git + ["config", "user.email"], stdout=subprocess.PIPE).stdout.strip() else \
        ["-c", "user.name=altention", "-c", "user.email=altention@localhost"]
    r = subprocess.run(git[:1] + who + git[1:] + ["commit", "-q", "-m", message], stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True)
    if r.returncode != 0:
        raise Failed("實例 commit 失敗：%s" % r.stdout.strip()[:200])


# --- which questions, in which order ------------------------------------------------------------------------------

def optional(found):
    """The per-plugin questions (plugin: name), as plugins.questions() writes them."""
    return plugins.questions(found)


def is_on(name, answered, found):
    """Whether plugin `name` is kept by the answers so far (not asked yet: kept, as the defaults are)."""
    if not any(n == name for n, _, _ in found):
        return False
    if name == SECRETARY:
        return answered.get("plugins.%s" % SECRETARY) != "off"
    return not (answered.get("plugins") == "some" and answered.get("plugin.%s" % name) == "off")


def flow(answered, found=None):
    """Every question #setup asks, in order, for these answers: [question], each with "key" and "values"."""
    if found is None:
        found = plugin.discover()[0]
    import instance
    unpacked = not os.path.exists(os.path.join(ENGINE, ".git"))
    first = plugins.setup_questions(found, instance.system_timezone(None), unpacked)
    asked = []
    if not answered or "install" in answered:  # asked first, and only before anything else was answered (C-57 S2)
        asked.append(plugins.install_question())
        if answered.get("install") == "restore":  # the rest comes back with the instance: nothing else is asked
            return asked
    asked += [q for q in first if q["key"] != "plugins.%s" % SECRETARY]
    if answered.get("plugins") == "some":
        for q in optional(found):
            asked.append(dict(q, key="plugin.%s" % q["plugin"], values=["keep", "off"]))
    asked += [q for q in first if q["key"] == "plugins.%s" % SECRETARY]
    if is_on(SECRETARY, answered, found) and "plugins.%s" % SECRETARY in answered:
        asked.append(plugins.times_question())
        if answered.get("planner.times") == "custom":
            asked += plugins.briefing_questions()
    if is_on("usage", answered, found):
        asked.append(plugins.plan_question())
    if is_on("progress", answered, found):
        asked.append(plugins.progress_question())
    from altention import offers  # C-64: an optional plugin of the official list offered at setup
    settings = read_settings()
    asked += [offers.question(n, d) for n, d in offers.candidates(settings=settings).items()
              if n in offers.due(("install",), settings=settings)]
    off = {n for n, _, _ in found if not is_on(n, answered, found)}
    backup = plugins.backup_questions(off)
    asked.append(backup[0])
    if answered.get("backup") == "now":
        asked.append(backup[1])
    return asked


def pending(state, found=None):
    """(the questions left, the answers so far). Once setup is done, only what the reconcile put in setup.ask."""
    answered = state.get("answered") if isinstance(state.get("answered"), dict) else {}
    if state.get("done") is True:
        return [q for q in (question_for(k, found, answered) for k in asked(state)) if q], answered
    return [q for q in flow(answered, found) if q["key"] not in answered], answered


def asking(state):
    """Whether the reconcile's questions are open: setup.ask is there, even emptied by the answers, until done takes
    it away (#code-review of 42daf1b: an emptied list read as done, and done never ran)."""
    return isinstance(state.get("ask"), list)


def asked(state):
    """setup.ask: the questions the reconcile found an installation needs answered (C-57 S3), in order."""
    ask = state.get("ask")
    return [k for k in ask if isinstance(k, str)] if isinstance(ask, list) else []


def asked_channels(key, found):
    """The settings of the channels the reconcile's `key` is about (#secretary: planner.channel; #dashboard: the
    boards' status.channel, usage.channel), from the plugins' declarations, as reconcile_plan reads them."""
    want = ("board",) if key == "dashboard" else ("claude",) if key == "plugins.%s" % SECRETARY else ()
    return [c["setting"] for n, _, d in found for c in d.get("channels") or []
            if c.get("kind") in want and c.get("setting") and (key == "dashboard" or n == SECRETARY)]


def channel_gone(key, found):
    """Whether the reconcile asks `key` because its channel is gone (stale: a setting names it, the server has it
    not) rather than never made (unset). The reconcile asks only in those two cases, so a value set is stale
    (#code-review of e51ad8d: a deleted #secretary was asked as "never made")."""
    return any(set_value(config.setting(s)) for s in asked_channels(key, found))


BOARD_NAMES = {"usage.channel": "用量看板", "status.channel": "狀態看板"}


def boards_lost(found, there=None):
    """{setting: "unset" or "gone"} of the board channels (status.channel, usage.channel) that have no channel, for
    the dashboard question (C-67: two settings on two channels, one deleted, was asked as "#dashboard is gone"). None
    when Discord cannot be read (then asked as before). `there`: the server's channel IDs, when read already."""
    values = dict((s, config.setting(s)) for s in asked_channels("dashboard", found))
    lost = dict((s, "unset") for s, v in values.items() if not set_value(v))
    if len(lost) < len(values):
        if there is None:
            try:
                there = set(r[0] for r in discord_channels())
            except Failed:
                return None
        lost.update((s, "gone") for s, v in values.items() if set_value(v) and str(v) not in there)
    return lost


def set_value(value):
    return isinstance(value, (str, int)) and not isinstance(value, bool) and bool(str(value).strip())


def question_for(key, found=None, answered=None):
    """The question of `key` as #setup asks it, for the reconcile's own questions; None for a key it does not know.
    answered: the answers so far; a channel they chose to open that was never made is asked as that (Z trial)."""
    if found is None:
        found = plugin.discover()[0]
    answered = answered or {}
    if key == "dashboard":
        settings, lost = asked_channels(key, found), boards_lost(found)
        if lost and len(lost) < len(settings):  # one board's channel is still there: only the other is asked about
            k, how = next(iter(lost.items()))
            kept = next(s for s in settings if s not in lost)
            return plugins.dashboard_question(only=(BOARD_NAMES.get(k, k), how == "gone", BOARD_NAMES.get(kept, kept),
                                                    config.setting(kept)))
        return plugins.dashboard_question(again=answered.get(key) == "make", gone=channel_gone(key, found))
    if key == "usage.plan":
        return plugins.plan_question()
    if key.startswith("offer."):  # C-64: the reconcile's, or setup.py offer's
        from altention import offers
        d = offers.candidates(settings=read_settings()).get(key.split(".", 1)[1])
        return offers.question(key.split(".", 1)[1], d) if d is not None else None
    if key == "plugins.%s" % SECRETARY:
        return plugins.secretary_question(found, again=answered.get(key) == "on", gone=channel_gone(key, found))
    import instance
    q = next((q for q in plugins.setup_questions(found, instance.system_timezone(None), False) if q["key"] == key), None)
    written = (setup_state() or {}).get("permission_written")
    if q and key == PERMISSION_KEY and written and config.setting(PERMISSION_KEY) == written:  # still the mode written
        q = dict(q, question=WRITTEN_SAY + "\n\n" + q["question"])
    return q


def backups_missing(answered):
    """The backups chosen whose remote is not set yet (setup.py backup-set)."""
    if answered.get("backup") != "now":
        return []
    import remote
    return [n for n in answered.get("backup.items") or [] if remote.known(n) and not remote_set(remote, n)]


def remote_set(remote, name):
    if name == "instance":
        r = subprocess.run(["git", "-C", config.instance_dir(), "remote", "get-url", "origin"],
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, universal_newlines=True)
        return r.returncode == 0 and bool(r.stdout.strip())
    return bool(config.setting("sync.dirs.%s.remote" % remote.key_for(name)))


def update_unfinished():
    """The last altention update go that did not get to its end (its progress file, update_go.progress_path(), is
    removed at ✅): {version, moved (the engine is on it already), left: the steps not done, in words}, or None.
    For #setup's Claude after a run that ended without its last line: it reads nothing outside its folder (manager
    2026-10-20: that read's question changes a setting for the whole machine)."""
    from altention import update_go
    d = update_go.read_progress()
    if not d:
        return None
    done = d.get("done") if isinstance(d.get("done"), list) else []
    return {"version": d.get("tag"), "moved": "update" in done,
            "left": update_go.steps_left(d)}


def status(as_json, out=None):
    out = out or sys.stdout
    state = setup_state()
    restored = restore_mark()
    if restored:  # back from the backups (C-57 S2): which machine, then done, then the tour
        left = [] if "machine" in restored else ["machine"]
        report = {"setup": "restored", "done": restored.get("stage") == "done", "next": left[0] if left else None,
                  "answered": {}, "left": left, "backups_missing": [], "asking": False,
                  "machine": restored.get("machine"), "tour": restored.get("tour"),
                  "language": config.setting("core.language")}
    elif state is None:
        report = {"setup": "none", "done": True, "next": None, "answered": {}, "left": [], "backups_missing": []}
    else:
        left, answered = pending(state)
        missing = backups_missing(answered) if not left else []
        report = {"setup": "yes", "done": state.get("done") is True, "asking": asking(state),
                  "restore": answered.get("install") == "restore",
                  "language": config.setting("core.language"), "next": left[0]["key"] if left else None,
                  "answered": answered, "left": [q["key"] for q in left], "backups_missing": missing,
                  "tour": answered.get("tour") or ("not-asked" if state.get("since") == "reconcile" else None)}
    report["update_unfinished"] = update_unfinished()
    if as_json:
        json.dump(report, out, ensure_ascii=False, indent=1)
        out.write("\n")
        return 0
    if report["update_unfinished"]:
        u = report["update_unfinished"]
        out.write("上次更新到 %s 沒做完：%s還沒做：%s\n" % (u["version"], "引擎已經換到新版；" if u["moved"] else "",
                                                       "、".join(u["left"])))
    if report["setup"] == "none":
        out.write("這個實例沒有 setup 這一項：當成已經設定完（C-48 以前的安裝，或從備份還原的），不用再問\n")
        return 0
    if report["setup"] == "restored":
        out.write("從備份還原了。%s\n" % ("下一題：machine（接替哪台）" if report["left"] else
                                         "接著 python3 ops/setup.py done" if not report["done"] else
                                         "設定做完了%s" % ("" if report["tour"] else "；還沒問導覽")))
        return 0
    for k, v in report["answered"].items():
        out.write("答了 %s：%s\n" % (k, json.dumps(v, ensure_ascii=False)))
    if report.get("restore") and not report["left"]:
        out.write("選了從備份還原：接著 login-check、repos，再 restore <實例備份的網址>\n")
    elif report["left"]:
        out.write("還沒答：%s\n下一題：%s（python3 ops/setup.py questions 印出題目）\n" % ("、".join(report["left"]), report["next"]))
    elif report["backups_missing"]:
        out.write("題目都答了；選了備份但還沒接上：%s（python3 ops/setup.py backup-set …）\n" % "、".join(report["backups_missing"]))
    elif not report["done"] or report["asking"]:
        out.write("題目都答了：接著 python3 ops/setup.py done\n")
    else:
        out.write("設定做完了%s\n" % ("" if report["tour"] else "；還沒問導覽（plugins.py 的 tour 那一題）"))
    return 0


def questions_cmd(as_json, out=None):
    out = out or sys.stdout
    state = setup_state()
    restored = restore_mark()
    if restored:
        if "machine" not in restored:
            import instance
            left = [plugins.machine_question(instance.names(), listed=bool(config.inventory().get("machines")))]
            if restored.get("chosen"):
                left = [confirm_question(restored["chosen"])]
        elif restored.get("stage") == "done" and not restored.get("tour"):
            left = [plugins.tour_question()]
        else:
            left = []
        state = {}
    elif state is None:
        out.write("[]\n" if as_json else "沒有要問的：這個實例已經設定完\n")
        return 0
    else:
        left, _ = pending(state)
    if state and state.get("done") is True and not asking(state) and state.get("since") != "reconcile" \
            and "tour" not in (state.get("answered") or {}):
        left = left + [plugins.tour_question()]
    if as_json:
        json.dump(left, out, ensure_ascii=False, indent=1)
        out.write("\n")
    else:
        for q in left:
            out.write("[%s] %s\n%s" % (q["key"], q["question"], "".join("  %s：%s\n" % (o["label"], o["description"])
                                                                       for o in q["options"])))
    return 0


# --- answers ------------------------------------------------------------------------------------------------------

def claude_binary():
    """The Claude Code the bot starts: CLAUDE_BIN, else ~/.local/bin/claude (dcbot/bot/config.py's CLAUDE), else the
    one on PATH; None when there is none. (The bot's env file is never read here: it holds the token.)"""
    for path in (os.environ.get("CLAUDE_BIN"), os.path.expanduser("~/.local/bin/claude")):
        if path and os.path.isfile(path) and os.access(path, os.X_OK):
            return path
    import shutil
    return shutil.which("claude")


def claude_modes():
    """The permission modes the bot's Claude Code lists (`claude --help`), or None when it cannot be asked."""
    binary = claude_binary()
    if not binary:
        return None
    try:
        p = subprocess.run([binary, "--help"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                           stderr=subprocess.STDOUT, universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError):
        return None
    m = re.search(r"--permission-mode.*?\(choices:([^)]*)\)", p.stdout, re.S)
    return re.findall(r'"([A-Za-z]+)"', m.group(1)) if m else None


def zone_ok(name):
    return bool(re.match(r"^[A-Za-z0-9_+\-]+(/[A-Za-z0-9_+\-]+)*$", name)) and \
        os.path.isfile(os.path.join("/usr/share/zoneinfo", name))


DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")


def parse_time(key, value):
    """A briefing's time: the JSON an option has, or "HH:MM" (daily; the weekly one "sun 21:00")."""
    try:
        v = json.loads(value)
        if isinstance(v, dict):
            return v
    except ValueError:
        pass
    m = re.match(r"^\s*(?:([a-z]{3})\s+)?([01]?[0-9]|2[0-3]):([0-5][0-9])\s*$", value.lower())
    if not m or (m.group(1) and m.group(1) not in DAYS):
        raise Refused("看不懂這個時間：%s（要 24 小時制的 HH:MM%s）" % (value, "，每週回顧前面加星期幾，例如 sun 21:00" if key.endswith("weekly") else ""))
    at = "%02d:%s" % (int(m.group(2)), m.group(3))
    if key.endswith("weekly"):
        if not m.group(1):
            raise Refused("每週回顧要寫星期幾，例如 sun 21:00")
        return {"at": at, "days": [m.group(1)]}
    if m.group(1):
        raise Refused("每天的時間不用寫星期幾：%s" % value)
    return {"at": at, "days": "daily"}


def turn(d, name, on, machine):
    """plugins.<name>.disabled_on with or without this machine; an empty list (read as a mistake) is taken away."""
    entry = node(d, "plugins.%s" % name)
    off = [m for m in entry.get("disabled_on", []) if m != machine] if isinstance(entry.get("disabled_on"), list) else []
    if not on:
        off.append(machine)
    if off:
        entry["disabled_on"] = off
    else:
        entry.pop("disabled_on", None)
    if not entry:
        d["plugins"].pop(name, None)
    if not d.get("plugins"):
        d.pop("plugins", None)


def needs_kept(d, answered, found, machine, said):
    """A kept part that needs one turned off keeps that one on (progress reads chatlog's records), said once."""
    title = {n: x.get("title") or n for n, _, x in found}
    for name, _, x in found:
        if not is_on(name, answered, found):
            continue
        asked = name == SECRETARY or answered.get("plugins") == "keep" or "plugin.%s" % name in answered
        for need in [r.get("plugin") for r in x.get("requires", []) if isinstance(r, dict)]:
            if asked and need and not is_on(need, answered, found):
                answered["plugin.%s" % need] = "keep"
                turn(d, need, True, machine)
                said.append("%s要靠%s才能運作，所以%s先留著；兩個都不要的話，回答關掉%s" % (title[name], title[need], title[need], title[name]))


def answer(key, value, out=None):
    out = out or sys.stdout
    d = read_settings()
    state = setup_state(d)
    if restore_mark():  # a restored instance may have a setup of its own (backed up after C-48): this comes first
        return answer_restored(key, value, out)
    if state is None:
        raise Refused("這個實例沒有 setup 這一項（已經設定完）：要改設定照全域說明改，不經過這裡")
    found = plugin.discover()[0]
    answered = dict(state.get("answered") or {})
    if key == "tour":
        if state.get("done") is not True:
            raise Refused("導覽是設定做完之後才問的：先 python3 ops/setup.py done")
        q = plugins.tour_question()
    elif state.get("done") is True:  # after setup: only what the reconcile asks
        q = question_for(key, found, answered) if key in asked(state) else None
    else:
        q = next((q for q in flow(answered, found) if q["key"] == key), None)
    if q is None:
        raise Refused("%s 現在不用問（照前面的答案，這一題不在題目裡）" % key)
    machine = config.this_machine()
    said = []
    if key == "core.timezone":
        if not zone_ok(value):
            raise Refused("沒有這個時區：%s（例如 Asia/Taipei、Europe/Berlin）" % value)
        put(d, key, value)
    elif key == "dcbot.default_permission":
        if value not in q["values"]:
            raise Refused("權限只能是 %s" % "、".join(q["values"]))
        modes = claude_modes()
        if modes is not None and value not in modes:
            raise Refused("這台的 Claude Code 沒有 %s 這個模式（太舊了）：先在終端機跑 claude update，或選別的" % value)
        put(d, key, value)
    elif key == "dcbot.talk_limit":
        if not re.match(r"^\s*[0-9]+\s*$", value):  # any count, as /talklimit takes (0: no limit)
            raise Refused("來回上限要是 0 以上的整數，例如 5；0 是不限")
        value = int(value)
        put(d, key, value)
    elif key == "core.update":
        if value not in q["values"]:
            raise Refused("引擎更新只能是 %s" % "、".join(q["values"]))
        put(d, key, value)
    elif key == "plugins":
        if value not in q["values"]:
            raise Refused("只能是 keep（全部保留）或 some（關掉一些）")
        for o in optional(found):
            if value == "keep":
                turn(d, o["plugin"], True, machine)
                answered.pop("plugin.%s" % o["plugin"], None)
    elif key.startswith("plugin."):
        if value not in ("keep", "off"):
            raise Refused("只能是 keep（保留）或 off（關掉）")
        turn(d, key.split(".", 1)[1], value == "keep", machine)
    elif key == "plugins.%s" % SECRETARY:
        if value not in ("on", "off"):
            raise Refused("只能是 on（開）或 off（不開）")
        turn(d, SECRETARY, value == "on", machine)
    elif key == "planner.times":
        if value not in q["values"]:
            raise Refused("只能是 suggested（用建議的）或 custom（自己選）")
        if value == "suggested":
            for b in plugins.briefing_questions():
                put(d, b["key"], b["values"][0])
                answered[b["key"]] = b["values"][0]
    elif key.startswith("planner.briefings."):
        when = parse_time(key, value)
        put(d, key, when)
        value = when
    elif key == "usage.plan":
        named = {n: [n, k] for n, k, _ in plugins.PLANS}
        named.update({n: list(p) for n, p in plugins.PLANS_TYPED.items()})
        if value == "none":
            drop(d, "usage.plan")
            put(d, "usage.subscription_limits", False)
        else:
            if value in named:
                plan = named[value]
            else:
                m = re.match(r"^\s*([^=]+?)\s*=\s*([0-9]+)\s*$", value)
                if not m or int(m.group(2)) < 1:
                    raise Refused("方案是 Pro、Max、Ultra 或 none；別的方案寫成「名稱=幾個 Pro 的一週額度」，例如 Team=5")
                plan = [m.group(1), int(m.group(2))]
            put(d, "usage.plan", plan)
            put(d, "usage.subscription_limits", True)
            value = plan
    elif key == "progress.every":
        if value not in q["values"]:
            raise Refused("進度頻率只能是 %s" % "、".join(q["values"]))
        put(d, "plugins.progress.schedules.run.when", {"every": value})
    elif key == "backup":
        if value not in q["values"]:
            raise Refused("只能是 now（現在設定）或 later（之後再說）")
        if value == "later":
            answered.pop("backup.items", None)
    elif key == "backup.items":
        items = [x.strip() for x in value.split(",") if x.strip()]
        bad = [x for x in items if x not in q["values"]]
        if bad:
            raise Refused("沒有這幾樣：%s（只能是 %s，用逗號分開）" % ("、".join(bad), "、".join(q["values"])))
        if not items:  # nothing ticked: later
            answered["backup"] = "later"
            key, value = None, None
        else:
            value = items
    elif key == "dashboard":
        if value not in q["values"]:
            raise Refused("只能是 make（開）或 off（不要）")
        there = set(r[0] for r in discord_channels())
        lost = boards_lost(found, there)
        for k, p in (("usage.channel", "usage"), ("status.channel", "status")):
            if value == "off":
                if k in lost or not lost:  # only the boards without a channel (C-67: "off" took the one still there)
                    turn(d, p, False, machine)
            elif config.setting(k) and str(config.setting(k)) not in there:
                drop(d, k)  # a channel that is not there any more: made again by done
    elif key.startswith("offer."):  # C-64: the person's answer kept in the instance's offers
        from altention import offers
        if value not in offers.ANSWERS:
            raise Refused(text.t("offer.answer_values"))
        name = key.split(".", 1)[1]
        kept = offers.record(d, name, value)
        said.append(text.t("offer.said_" + kept, plugin=name, most=offers.MAX_LATER))
    elif key == "install":
        if value not in q["values"]:
            raise Refused("只能是 new（新安裝）或 restore（從備份還原）")
        if value == "new" and answered.get("install") == "restore" and restore_mark() is None:
            pass  # changed their mind before restoring: a new installation after all
    elif key == "tour":
        if value not in q["values"]:
            raise Refused("只能是 start、later 或 no")
        if value == "start":
            raise Refused("要開始導覽，跑 python3 ops/setup.py tutorial（它會記下這一題）")
    if key == "plugins.%s" % SECRETARY and value == "on" and config.setting("planner.channel") and \
            str(config.setting("planner.channel")) not in set(r[0] for r in discord_channels()):
        drop(d, "planner.channel")  # its channel is gone: opened again by done
    if key:
        answered[key] = value
    needs_kept(d, answered, found, machine, said)
    state = node(d, "setup")
    state["answered"] = answered
    if key in asked(state):
        state["ask"] = [k for k in asked(state) if k != key]
    if key == PERMISSION_KEY:
        state.pop("permission_written", None)  # chosen now: #setup's question no longer begins with WRITTEN_SAY
    state.setdefault("done", False)
    write_settings(d)
    check_read_back(key, value)
    commit("setup: %s" % (key or "backup.items"))
    for s in said:
        out.write("注意：%s\n" % s)
    out.write("記下了 %s\n" % (key or "備份：之後再說（一樣都沒勾）"))
    return 0


def check_read_back(key, value):
    """The answer read back the way the engine reads its settings; Failed when it does not come back as written."""
    simple = ("core.timezone", "dcbot.default_permission", "dcbot.talk_limit", "core.update")
    if key in simple or (key or "").startswith("planner.briefings."):
        got = config.setting(key)
        want = int(value) if key == "dcbot.talk_limit" else value
        if got != want:
            raise Failed("寫進去的 %s 讀回來是 %r，不是 %r" % (key, got, want))
    if (key or "").startswith("planner.briefings."):
        sys.path.insert(0, os.path.join(ENGINE, "planner"))
        import remind
        try:
            remind.briefing_times()
        except ValueError as e:
            raise Refused("秘書的時間讀不懂：%s" % e)
    if key == "progress.every":
        r = script([sys.executable, os.path.join(HERE, "plugins.py"), "list"])
        if "問題：" in r.stdout:
            raise Failed("進度的排程寫進去之後 plugins.py list 說有問題：%s" % " ".join(
                l for l in r.stdout.splitlines() if l.startswith("問題："))[:300])


# --- channels and boards ------------------------------------------------------------------------------------------

def discord_channels():
    """[(id, name, category name or "")] of the server's text channels (dcctl channels)."""
    r = dcctl("channels")
    if r.returncode != 0:
        raise Failed("讀不到 Discord 的頻道（dcctl channels：%s）：機器人或伺服器連不上，等一下再跑" % r.stdout.strip()[-200:])
    rows = []
    for line in r.stdout.splitlines():
        parts = line.split(None, 2)
        if len(parts) >= 2 and parts[0].isdigit() and parts[1].startswith("#"):
            rows.append((parts[0], parts[1][1:], parts[2].strip() if len(parts) > 2 else ""))
    return rows


def channel_ids(name):
    """IDs of the text channels called `name`."""
    return [cid for cid, n, _ in discord_channels() if n == name]


def free_name(base):
    """base, or base-2, base-3 … when a channel of that name is there already."""
    n = 1
    while True:
        name = base if n == 1 else "%s-%d" % (base, n)
        if not channel_ids(name):
            return name
        n += 1


def wait_for(name):
    for _ in range(WAIT // 5):
        ids = channel_ids(name)
        if ids:
            return ids[0]
        sleep(5)
    raise Failed("等了 %d 秒還沒看到 #%s：看 #audit 機器人怎麼說（altention dc read audit 5），再跑一次" % (WAIT, name))


def write_channel(key, cid):
    d = read_settings()
    put(d, key, cid)
    write_settings(d)
    if config.setting(key) != cid:
        raise Failed("%s 讀回來不是 %s" % (key, cid))


def dashboard(out=None):
    out = out or sys.stdout
    rows = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
    keys = [k for k, p in (("usage.channel", "usage"), ("status.channel", "status"))
            if rows.get(p) in ("on", "off") and not config.setting(k)]
    if not keys:
        out.write("看板頻道已經設定了，或用量和狀態看板都關掉了\n")
        return 0
    ids = channel_ids("dashboard")
    if len(ids) > 1:
        raise Refused("有 %d 個 #dashboard：問使用者用哪一個，把它的 ID 寫進 %s" % (len(ids), "、".join(keys)))
    if not ids:
        r = dcctl("ensure", "dashboard", "--topic", "系統狀態和用量看板")
        if r.returncode != 0:
            raise Failed("建不出 #dashboard：%s" % r.stdout.strip()[-200:])
        ids = [wait_for("dashboard")]
    for k in keys:  # usage first, then status: a board stays where it was first posted (the usage ones end up higher)
        write_channel(k, ids[0])
        out.write("寫進 %s：%s\n" % (k, ids[0]))
    if "usage.channel" in keys:
        r = script([sys.executable, os.path.join(ENGINE, "usage", "collect.py")])
        if r.returncode != 0:  # by its exit status: its posts print 「已送出到 #…」 and its notes go to stderr (both
            # here, merged), on a board that did go up (Z acceptance of 486fb69: every new install stopped here)
            d = read_settings()  # taken back: a run again writes it and posts the board (C-68: it waited for the hour)
            drop(d, "usage.channel")
            write_settings(d)
            raise Failed("用量看板沒貼上：%s" % (r.stdout.strip()[-300:] or "結束碼 %d" % r.returncode))
        out.write("用量看板貼了\n")
    if "status.channel" in keys:
        script([sys.executable, os.path.join(HERE, "doctor.py"), "--watch"])
        for _ in range(BOARD_ROUNDS):
            script([sys.executable, os.path.join(ENGINE, "status", "boards.py")])
            sleep(5)
            seen = altention("dc", "read", ids[0], "12").stdout
            if all(("📌 **%s" % b) in seen or ("📌 %s" % b) in seen for b in BOARDS):
                break
        else:
            out.write("狀態看板還沒全部出來（每兩分鐘的排程會接著貼）\n")
            commit("setup: dashboard")
            return 0
        out.write("狀態看板貼了\n")
    commit("setup: dashboard")
    return 0


def secretary(out=None):
    out = out or sys.stdout
    if config.setting("planner.channel"):
        out.write("秘書頻道已經設定了（planner.channel）\n")
        return 0
    r = script([sys.executable, os.path.join(ENGINE, "planner", "remind.py"), "--secretary"])
    lines = [l for l in r.stdout.splitlines() if l.strip()]
    if r.returncode != 0 or not lines or not os.path.isdir(lines[-1].strip()):
        raise Failed("秘書的資料夾沒建好：%s" % r.stdout.strip()[-300:])
    folder = lines[-1].strip()
    name = free_name("secretary")
    if name != "secretary":
        out.write("已經有 #secretary 了，秘書開在 #%s\n" % name)
    r = altention("dc", "post", name, "--new-claude", folder, "秘書：早報、回顧和提醒")
    if r.returncode != 0:
        raise Failed("開不了 #%s：%s" % (name, r.stdout.strip()[-200:]))
    cid = wait_for(name)
    write_channel("planner.channel", cid)
    commit("setup: secretary")
    check = script([sys.executable, os.path.join(ENGINE, "planner", "remind.py"), "--check"])
    out.write("秘書開在 <#%s>\n%s" % (cid, check.stdout))  # as Claude says it: a mention shows the name
    return 0


def guide(out=None):
    out = out or sys.stdout
    r = dcctl("guide", timeout=300)
    out.write(r.stdout)
    if r.returncode != 0:
        raise Failed("#guide 沒弄好（見上面）：機器人或伺服器連不上的話，等一下再跑")
    return 0


def tutorial(out=None):
    out = out or sys.stdout
    d = read_settings()
    state = setup_state(d)
    mark = restore_mark()
    if mark is not None and mark.get("stage") != "done" or mark is None and state is not None and state.get("done") is not True:
        raise Refused("設定還沒做完：先 python3 ops/setup.py done")
    name = free_name("tutorial")
    r = altention("dc", "post", name, "--new-claude", config.instance_dir(), "導覽：一步步試機器人的主要功能")
    if r.returncode != 0:
        raise Failed("開不了 #%s：%s" % (name, r.stdout.strip()[-200:]))
    cid = wait_for(name)
    r = altention("dc", "post", cid, "設定做完了。請用 tour skill 帶使用者導覽：從第 1 步開始，不用再問開場那一題。")
    if r.returncode != 0:
        raise Failed("導覽的第一句沒送出去：%s" % r.stdout.strip()[-200:])
    mark = restore_mark()
    if mark is not None:  # the restore's last question: its record goes
        os.remove(restore_mark_path())
    elif state is not None:
        d = read_settings()
        node(d, "setup").setdefault("answered", {})["tour"] = "start"
        write_settings(d)
        commit("setup: tour")
    out.write("導覽開在 <#%s>\n" % cid)
    return 0


# --- backups ------------------------------------------------------------------------------------------------------

def backup_set(name, target, create, out=None):
    out = out or sys.stdout
    import remote
    if not remote.known(name):
        raise Refused("只能是 %s，或 plugin:<插件>/<資料名>（插件的資料，C-61）" % "、".join(REMOTE_NAMES))
    if create:
        if not re.match(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$", target):
            raise Refused("--create 要寫成 帳號/repo 名稱，例如 alice/altention-%s" % re.sub(r"[^A-Za-z0-9_.-]+", "-", name))
        if subprocess.run(["gh", "repo", "view", target], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0:
            raise Refused("%s 已經有了：沒有重建、沒有清空。要用它就把網址交給 backup-set（只有之前同一種的備份過得了檢查），"
                          "不然換一個名字" % target)
        r = subprocess.run(["gh", "repo", "create", target, "--private", "--disable-issues", "--disable-wiki"],
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        if r.returncode != 0:
            raise Failed("gh 建不出 %s：%s" % (target, r.stdout.strip()[-200:]))
        subprocess.run(["gh", "auth", "setup-git"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        url = "https://github.com/%s.git" % target
        out.write("建了 private 的 %s\n" % target)
    else:
        url = target
    r = script([sys.executable, os.path.join(HERE, "remote.py"), "set", name, url])
    out.write(r.stdout)
    if r.returncode != 0:
        raise Refused("備份沒接上（見上面 ✗ 那行）")
    commit("setup: backup %s" % name)
    return 0


def login_check(as_json, out=None):
    out = out or sys.stdout
    try:
        gh = subprocess.run(["gh", "auth", "status"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                            stderr=subprocess.DEVNULL, timeout=60).returncode
    except OSError:
        gh = None  # not installed
    account = ""
    if gh == 0:
        account = subprocess.run(["gh", "api", "user", "--jq", ".login"], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                 stderr=subprocess.DEVNULL, universal_newlines=True, timeout=60).stdout.strip()
    try:
        p = subprocess.run(["ssh", "-T", "-o", "BatchMode=yes", "-o", "ConnectTimeout=10", "git@github.com"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           universal_newlines=True, timeout=60)
        # its exit status is 1 even then; a machine that never reached GitHub has no host key for it yet, and with
        # BatchMode that ends here, whatever key there is (#code-review of 0c98878)
        ssh = True if "successfully authenticated" in p.stdout else \
            "unknown-host" if "Host key verification failed" in p.stdout else False
    except (OSError, subprocess.SubprocessError):
        ssh = False
    report = {"gh": "missing" if gh is None else "in" if gh == 0 else "out", "account": account, "ssh": ssh}
    if as_json:
        json.dump(report, out)
        out.write("\n")
    else:
        out.write("gh：%s\n" % {"missing": "沒有裝", "out": "裝了，沒登入", "in": "已登入（帳號 %s）" % account}[report["gh"]])
        out.write("ssh：%s\n" % ("GitHub 認得這台的 key" if ssh is True else
                                 "這台還沒連過 GitHub：在自己的終端機跑一次 ssh -T git@github.com，看到 GitHub 的指紋回答 yes，再檢查一次"
                                 if ssh == "unknown-host" else "GitHub 不認得這台（沒有 key，或 key 沒加到 GitHub）"))
    return 0


# --- done ---------------------------------------------------------------------------------------------------------

def done(out=None):
    out = out or sys.stdout
    d = read_settings()
    state = setup_state(d)
    mark = restore_mark()
    if state is None and not mark:
        out.write("這個實例沒有 setup 這一項：本來就當成設定完了\n")
        return 0
    if mark is not None:
        if "machine" not in mark:
            raise Refused("還沒問這台接替哪一台（machine）")
        return done_restored(mark, out)
    left, answered = pending(state)
    if left:
        raise Refused("還有題目沒答：%s" % "、".join(q["key"] for q in left))
    if answered.get("install") == "restore":
        raise Refused("選了從備份還原：先 python3 ops/setup.py restore <實例備份的網址>（要改成新安裝就 answer install new）")
    missing = backups_missing(answered)
    if missing:
        raise Refused("選了備份但還沒接上：%s（python3 ops/setup.py backup-set …，或那一題改答 later）" % "、".join(missing))
    asking = "ask" in state  # the reconcile's questions of an installation set up before: taken away only at the end,
    if state.get("done") is not True:  # so that a run again after a step failed goes the same way (C-68: it went
        state["done"] = True           # the new installation's: #guide again, #chat opened, no ✅)
        write_settings(d)
        if setup_state() is None or setup_state().get("done") is not True:
            raise Failed("setup.done 讀回來不是 true")
        commit("setup: done")
    r = script([os.path.join(ENGINE, "install.sh")])
    last = [l for l in r.stdout.splitlines() if l.strip()][-1:] or ["（沒有輸出）"]
    out.write("install.sh：%s\n" % last[0])
    if r.returncode == 1:
        raise Failed("install.sh 沒跑完（log：~/.local/state/altention/install/last.log）；修好再跑一次 setup.py done")
    rows = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
    dashboard(out)  # only the board channels not set yet (none: nothing)
    if not asking:
        guide(out)
    if rows.get(SECRETARY) in ("on", "off") and not config.setting("planner.channel"):
        secretary(out)
    if asking:  # an installation set up before: #chat is there; the reconcile's questions are answered
        d = read_settings()
        if setup_state(d) is not None:
            setup_state(d).pop("ask", None)
            write_settings(d)
            commit("setup: done (the reconcile's questions)")
        say_update("✅ 你在 #setup 回答的都設定好了。")
        out.write("要你決定的都做好了\n")
        return 0
    chat = config.setting("dcbot.channels.chat") or "chat"
    p = dcctl("post", inp=json.dumps({"channel": chat, "open_chat": True, "text": ""}))
    if p.returncode != 0:
        raise Failed("請機器人開 #%s 沒送出去：%s" % (chat, p.stdout.strip()[-200:]))
    out.write("設定做完了：機器人接著開 #%s\n" % chat)
    return 0


# --- back from the backups (C-57 S2) -----------------------------------------------------------------------------

def restore_mark_path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "setup-restore.json")


def restore_mark():
    """This machine's record of a restore #setup made ({"at", "fresh", "url", "chosen", "machine", "stage", "tour"}),
    or None: a restored instance has no setup of its own, so what is left to do is kept here."""
    try:
        with open(restore_mark_path()) as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except (OSError, ValueError):
        return None


def write_mark(d):
    path = restore_mark_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump(d, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)


def confirm_question(name):
    """The install skill's confirmation before a machine takes an old one's name (C-45), as #setup asks it."""
    import instance
    only = dict(instance.names(look_remote=False)).get(name) == instance.ONLY_SETTINGS
    return {"key": "machine.confirm", "values": ["yes", "no"], "header": "確認", "multiSelect": False,
            "question": ("%s 這個名字只出現在設定裡，可能是打錯的。確定這台就是要接替 %s，" % (name, name) if only else "")
                        + "%s 那台之後不會再開來用了嗎？兩台用同一個名字，對話紀錄會寫進同一個資料夾。"
                          "連不上不代表它沒了（例如筆電只是沒開）。" % name,
            "options": [{"label": "不會再用了", "description": "它不會再用了；這台沿用它的名字"},
                        {"label": "之後還會開", "description": "這台用自己的名字"}]}


def records_under(name):
    """Whether this machine wrote conversation records under `name` (its folder there, or that folder's history)."""
    import remote
    logs = remote.folder_of("logs")
    if not logs:
        return False
    if os.path.exists(os.path.join(logs, name)):
        return True
    r = subprocess.run(["git", "-C", logs, "log", "--oneline", "-1", "--", name + "/"], stdout=subprocess.PIPE,
                       stderr=subprocess.DEVNULL, universal_newlines=True)
    return bool(r.stdout.strip())


def take_name(name, out, mark=None):
    """This machine takes `name` (instance.take_name's checks). The name install.sh wrote for it first is kept as
    host.bak, only when nothing was recorded under it; put back when the name is not taken. True when taken."""
    import instance
    host = os.path.join(config.config_dir(), "host")
    now = config.stated_name()
    moved = None
    if now and now != name:
        if records_under(now):
            out.write("沒有改：這台已經用 %s 這個名字寫過對話紀錄，換名字會讓那些紀錄分成兩邊。這台繼續用 %s。\n" % (now, now))
            return False
        moved = host + ".bak"
        n = 1
        while os.path.lexists(moved):  # never written over: an earlier one is kept too
            n += 1
            moved = "%s.bak%d" % (host, n)
        os.rename(host, moved)
        if mark is not None:
            mark["host_bak"] = moved  # put back if the install after it fails (put_back)
    only = dict(instance.names(look_remote=False)).get(name) == instance.ONLY_SETTINGS
    buf = io.StringIO()
    with redirect_stdout(buf):
        rc = instance.take_name(name, only_in_settings=only)
    said = buf.getvalue().strip()
    if rc != 0:
        if moved:
            os.rename(moved, host)  # this machine keeps the name it had
            if mark is not None:
                mark.pop("host_bak", None)
        out.write("沒有改：%s。這台繼續用自己的名字。\n" % said.replace("instance：", ""))
        return False
    out.write("這台改叫 %s：照這個名字記的設定（哪台關掉了哪些功能、對話紀錄的資料夾）現在是這台的。\n" % name)
    machine = (config.inventory().get("machines") or {}).get(name)
    old = [a for a in (machine or {}).get("ssh", []) if a != "local"] if isinstance(machine, dict) else []
    if old:
        out.write("還有一件事要你之後做：機器清單裡 %s 的連線方式還是舊的那台（%s）。這台跟其他機器互相連線、"
                  "系統檢查看各台狀況，都要靠它；不改的話會一直去連舊的那台。之後在 #chat 跟 Claude 說"
                  "「把機器清單裡的 %s 改成這台」就好。\n" % (name, "、".join(old), name))
    return True


def answer_restored(key, value, out):
    mark = restore_mark()
    if key == "machine" and "machine" not in mark:
        import instance
        q = plugins.machine_question(instance.names(), listed=bool(config.inventory().get("machines")))
        if value not in q["values"]:
            raise Refused("實例裡沒有叫 %s 的機器" % value if value else
                          "這套有機器清單，這台不在上面：從備份還原要接替其中一台")
        if value:
            mark["chosen"] = value
        else:
            mark["machine"] = ""
            out.write("好，這台用自己的名字。\n")
    elif key == "machine.confirm" and mark.get("chosen") and "machine" not in mark:
        if value not in ("yes", "no"):
            raise Refused("只能是 yes 或 no")
        name = mark.pop("chosen")
        taken = take_name(name, out, mark) if value == "yes" else False
        if taken:
            mark["machine"] = name
        elif config.inventory().get("machines"):
            write_mark(mark)  # not taken, and the list has no place for this one: asked again
            raise Refused("這台還是要接替清單上的其中一台（%s）才裝得起來；重新選一台" % "、".join(
                sorted(config.inventory().get("machines"))))
        else:
            mark["machine"] = ""
    elif key == "tour" and mark.get("stage") == "done":
        if value not in ("later", "no"):
            raise Refused("要開始導覽，跑 python3 ops/setup.py tutorial" if value == "start" else "只能是 later 或 no")
        os.remove(restore_mark_path())  # the restore's last question: its record goes
        out.write("記下了 %s\n" % key)
        return 0
    else:
        raise Refused("%s 現在不用問" % key)
    write_mark(mark)
    out.write("記下了 %s\n" % key)
    return 0


def repos(as_json, out=None):
    """Their GitHub repositories (gh repo list: name, URL, private or not), for choosing the instance's."""
    out = out or sys.stdout
    try:
        r = subprocess.run(["gh", "repo", "list", "--limit", "200", "--json", "name,url,isPrivate"], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
        rows = json.loads(r.stdout) if r.returncode == 0 else None
    except (OSError, ValueError, subprocess.SubprocessError):
        rows = None
    if not isinstance(rows, list):
        raise Failed("列不出你的 repo（gh 沒裝或沒登入：python3 ops/setup.py login-check）")
    rows = [x for x in rows if isinstance(x, dict) and x.get("url")]
    rows.sort(key=lambda x: (x.get("name") != "altention-instance", str(x.get("name"))))
    if as_json:
        json.dump(rows, out, ensure_ascii=False)
        out.write("\n")
    else:
        for x in rows:
            out.write("%s  %s%s\n" % (x.get("name"), x["url"], "" if x.get("isPrivate") else "（公開的）"))
    return 0


def fresh_why():
    """Why this machine's instance may not be swapped for a restored one, or None: only the one install.sh has just
    made and nobody used (C-57 S2): at its default place, pointed at by the pointer, no remote, nothing but its
    first commit and the install question's, nothing uncommitted, setup not done and only the install question
    answered, as restore."""
    import instance
    inst = config.instance_dir()
    if os.environ.get("ALT_INSTANCE"):
        return "實例是 ALT_INSTANCE 指定的"
    if not inst or os.path.realpath(inst) != os.path.realpath(instance.place()):
        return "這台的實例不在預設的位置（%s）" % inst
    pointer = os.path.join(config.config_dir(), "instance")
    try:
        with open(pointer) as f:
            if os.path.realpath(f.read().strip()) != os.path.realpath(inst):
                return "指向實例的設定（%s）指的不是它" % pointer
    except OSError:
        return "沒有指向實例的設定（%s）" % pointer
    git = lambda *a: subprocess.run(["git", "-C", inst] + list(a), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,  # noqa: E731
                                    universal_newlines=True)
    if git("remote").stdout.strip():
        return "實例已經有遠端（%s）：它是接上別處的，不是剛建的" % " ".join(git("remote").stdout.split())
    if git("status", "--porcelain").stdout.strip():
        return "實例裡有還沒存的修改"
    subjects = git("log", "--format=%s").stdout.splitlines()
    first = "instance: made on this machine (no remote yet)"
    if not subjects or subjects[-1] != first or any(x not in (first, "setup: install") for x in subjects):
        return "實例裡已經有其他的紀錄（%d 筆）：它被用過了" % len(subjects)
    state = setup_state()
    if state is None or state.get("done") is not False:
        return "這個實例不是新安裝等著設定的那份"
    if state.get("answered") != {"install": "restore"}:
        return "設定已經答過其他題目（或安裝方式不是「從備份還原」）"
    return None


def restore(url, out=None):
    """The instance from the backups in place of the one install.sh just made: cloned beside it and checked, the
    fresh one kept as instance.fresh-<time> (never deleted), the clone moved in. Nothing is installed here: which
    machine this one takes over comes first (a machine the backup's list does not have installs nothing), then
    done installs (install_restored), and puts everything back when that fails (put_back)."""
    out = out or sys.stdout
    import instance
    why = fresh_why()
    if why:
        raise Refused("沒有換：%s。只有剛裝好、還沒用過的實例可以換成備份" % why)
    if not url.strip() or url.startswith("-") or "\n" in url:  # any git address; never one git reads as an option
        raise Refused("看不懂這個網址：%r（要像 https://github.com/<帳號>/altention-instance.git）" % url)
    inst = config.instance_dir()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    n = 1
    while any(os.path.lexists(inst + kind + stamp) for kind in (".restoring-", ".fresh-", ".restore-failed-")):
        n += 1  # one kept from an earlier try in the same second: never written over
        stamp = "%s-%d" % (time.strftime("%Y%m%d-%H%M%S"), n)
    work = inst + ".restoring-" + stamp
    r = subprocess.run(["git", "clone", "-q", "--", url, work], stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, timeout=600)
    if r.returncode != 0:
        raise Failed("clone 不下來（%s）：這台讀得到那個 private repo 嗎？python3 ops/setup.py login-check" % r.stdout.strip()[-200:])
    head = subprocess.run(["git", "-C", work, "rev-parse", "-q", "--verify", "HEAD"], stdout=subprocess.PIPE,
                          stderr=subprocess.DEVNULL, universal_newlines=True).stdout.strip()
    got = config.read_json(os.path.join(work, "plugins.json"))
    if not head or not isinstance(got, dict):
        raise Failed("%s 不像是實例的備份（%s）；沒有換，下載下來的放在 %s" % (
            url, "是空的" if not head else "沒有讀得懂的 plugins.json", work))
    fresh = inst + ".fresh-" + stamp
    os.rename(inst, fresh)
    try:
        os.rename(work, inst)
    except OSError as e:
        os.rename(fresh, inst)
        raise Failed("換不過去（%s）；原來的實例放回去了，下載下來的在 %s" % (e, work))
    instance.private(inst)
    buf = io.StringIO()
    with redirect_stdout(buf):
        instance.hook(inst)
    write_mark({"at": time.time(), "fresh": fresh, "url": url, "stage": "restored", "stamp": stamp})
    out.write("換上了：實例換成 %s 的備份；原來剛建的那份留在 %s（沒有刪）。接著問這台接替哪一台，"
              "名字定了才裝（裝不起來會放回原來的）。\n" % (url, fresh))
    return 0


DATA = ("logs", "progress", "planner")  # the folders a plugin keeps, cloned back by the install from the instance


def fresh_data_aside(stamp, out):
    """The data folders the fresh install made, set aside so that the install clones them back from the restored
    addresses (#code-review-2 of 244dabf: an empty logs repo stayed, the records never came back). Only a plain
    one: a git repo with no commit, no remote and no file but its .git; anything else is left and said.
    {name: [folder, where it went]}; one not there at all is [folder, None]: what the install makes there is the
    restore's, taken away again by put_back (#code-review of 22f9177)."""
    import remote
    moved = {}
    for name in DATA:
        folder = remote.folder_of(name)
        if not folder:
            continue
        if not os.path.lexists(folder):
            moved[name] = [folder, None]
            continue
        git = lambda *a: subprocess.run(["git", "-C", folder] + list(a), stdout=subprocess.PIPE,  # noqa: E731
                                        stderr=subprocess.DEVNULL, universal_newlines=True)
        empty = os.path.isdir(os.path.join(folder, ".git")) and not git("rev-parse", "-q", "--verify", "HEAD").stdout.strip() \
            and not git("remote").stdout.strip() and set(os.listdir(folder)) <= {".git"}
        if not empty:
            out.write("%s 已經有東西（%s），沒有動它：還原的那份不會蓋過去\n" % (remote.LABELS.get(name, name), folder))
            continue
        aside = "%s.fresh-%s" % (folder, stamp)
        os.rename(folder, aside)
        moved[name] = [folder, aside]
    return moved


def put_back(mark, out):
    """A restore that did not install: the restored instance (and what its install cloned) kept beside, the fresh
    instance, its data folders and this machine's name back in place; the record of the restore taken out."""
    inst = config.instance_dir() or __import__("instance").place()
    stamp = mark.get("stamp") or time.strftime("%Y%m%d-%H%M%S")
    kept = []
    for folder, aside in (mark.get("aside") or {}).values():
        if os.path.lexists(folder):  # what the restore's install made or cloned there: kept beside, never deleted
            os.rename(folder, "%s.restore-failed-%s" % (folder, stamp))
            kept.append("%s.restore-failed-%s" % (folder, stamp))
        if aside and os.path.lexists(aside):
            os.rename(aside, folder)
    failed = inst + ".restore-failed-" + stamp
    os.rename(inst, failed)
    os.rename(mark["fresh"], inst)
    host = os.path.join(config.config_dir(), "host")
    if mark.get("host_bak") and os.path.exists(mark["host_bak"]):
        os.replace(mark["host_bak"], host)  # the name install.sh gave it, back
    try:
        os.remove(restore_mark_path())
    except OSError:
        pass
    return [failed] + kept


def install_restored(mark, out):
    """install.sh on the restored instance, once this machine's name is settled; failing or taking too long, all
    is put back (put_back) and installed again as it was."""
    mark["aside"] = fresh_data_aside(mark.get("stamp") or time.strftime("%Y%m%d-%H%M%S"), out)
    write_mark(mark)
    try:
        r = script([os.path.join(ENGINE, "install.sh")])
    except subprocess.TimeoutExpired:  # taking too long fails the same way (#code-review of 244dabf)
        r = subprocess.CompletedProcess([], 1, "fail core.done 安裝跑了 30 分鐘還沒結束\n")
    last = [l for l in r.stdout.splitlines() if l.strip()][-1:] or ["（沒有輸出）"]
    out.write("install.sh：%s\n" % last[0])
    if r.returncode != 1:
        return
    kept = put_back(mark, out)
    try:
        r2 = script([os.path.join(ENGINE, "install.sh")])  # the fresh one's own lines back, as before
    except subprocess.TimeoutExpired:
        r2 = subprocess.CompletedProcess([], 1, "")
    raise Failed("換上之後安裝沒跑完（%s）：原來的實例、資料夾和這台的名字都放回去了%s；還原的那份和它裝出來的留在旁邊"
                 "（%s），沒有刪；修好再從頭選一次「從備份還原」" % (last[0], "" if r2.returncode != 1 else
                                                     "（再裝一次也沒跑完，看 ~/.local/state/altention/install/last.log）",
                                                     "、".join(kept)))


# --- #setup's folder --------------------------------------------------------------------------------------------

SETUP_FOLDER = "~/.local/state/altention/setup"  # #setup's working folder (C-48; the bot opens it there)
# `altention update` subcommands #setup's Claude may run (C-57's U chain, support-3 2026-10-19): one rule each, so
# that a subcommand added later is not let through by itself. Its update-work/ folder in there is never touched here.
UPDATE_SUBCOMMANDS = ("check", "go", "save", "apply", "aside", "undo", "plugin")  # aside: C-66; plugin: C-61 E3
SETUP_SKILLS = ("setup", "update")  # linked into #setup's folder only (.local-only): the setup, and an update handed over


def setup_rules(engine=None):
    engine = engine or ENGINE
    return {"permissions": {"allow": ["Bash(python3 %s:*)" % os.path.join(engine, "ops", "setup.py")] + [
        "Bash(%s update %s:*)" % (os.path.join(engine, "bin", "altention"), sub) for sub in UPDATE_SUBCOMMANDS]}}


def write_setup_folder(engine=None, dry=False):
    """#setup's working folder: its .claude/settings.json lets this command (and the update subcommands) run without
    the auto mode's classifier, nothing else, and its skills (SETUP_SKILLS) are linked into it only (never ~/.claude/skills).
    That settings.json is written over whole whenever it differs (a rule added by hand, or a moved engine): do not
    edit it; a rule #setup needs goes here. (folder, [what changed], problem or None); dry: what would change."""
    engine = engine or ENGINE
    folder = os.path.expanduser(SETUP_FOLDER)
    skills_dir = os.path.join(folder, ".claude", "skills")
    want = setup_rules(engine)
    path = os.path.join(folder, ".claude", "settings.json")
    try:
        with open(path, encoding="utf-8") as f:
            have = json.load(f)
    except (OSError, ValueError):
        have = None
    links = [(os.path.join(engine, "skills", n), os.path.join(skills_dir, n)) for n in SETUP_SKILLS]
    for _, link in links:
        if os.path.lexists(link) and not os.path.islink(link):
            return folder, [], "%s 不是連結：搬走它（mv %s %s.mine）再重跑" % (link, link, link)
    stale = [(src, link) for src, link in links if os.path.isdir(src) and (not os.path.islink(link) or os.readlink(link) != src)]
    changed = (["允許規則"] if have != want else []) + ["%s skill" % os.path.basename(src) for src, _ in stale]
    if dry or not changed:
        return folder, changed, None
    os.makedirs(skills_dir, mode=0o700, exist_ok=True)
    os.chmod(folder, 0o700)
    if have != want:
        with open(path + ".tmp", "w", encoding="utf-8") as f:
            f.write(json.dumps(want, ensure_ascii=False, indent=2) + "\n")
        os.replace(path + ".tmp", path)
    for src, link in stale:
        if os.path.islink(link):
            os.remove(link)
        os.symlink(src, link)
    return folder, changed, None


# --- the reconcile (C-57 S3) --------------------------------------------------------------------------------------

def system_category():
    """The system category's name as the bot reads it (dcbot/bot/config.py channel_name("system", "系統")): the
    instance's dcbot.channels.system, else "系統". (The bot's env file may name another: it holds the token, never
    read here.)"""
    said = config.setting("dcbot.channels.system")
    return said.strip().lstrip("#") if isinstance(said, str) and said.strip().lstrip("#") else "系統"
UPDATE_TOPIC = "引擎更新的消息：有新版、更新好了、出了問題，都在這裡。這裡不聊天。"  # dcbot/bot/layout.py's
UPDATE_HELLO = ("👋 這個頻道是新開的，專門放引擎更新的消息：有新版時告訴你這版改了什麼、更新好了說一聲、出問題時說原因。"
                "這裡不聊天，Claude 不會在這裡回話。")


def update_name():
    said = config.setting("core.update_channel")
    return said.strip().lstrip("#") if isinstance(said, str) and said.strip().lstrip("#") else "update"


def say_audit(line):
    """One line in #audit (dcbot.channels.audit), a notice as say_update's (the system speaking, not a Claude's
    card); not said is not a failure (the instance's commit says the same)."""
    try:
        altention("dc", "notify", "-c", str(config.setting("dcbot.channels.audit") or "audit"), line)
    except (OSError, subprocess.SubprocessError):
        pass


def say_update(text):
    """One line in #update (core.update_channel), as a notice of this machine's like update.py's (altention dc
    notify): the system speaking, not a card "來自 … 上的 Claude" as altention dc post makes it; not said is not a
    failure (the alerts and doctor stay)."""
    try:
        altention("dc", "notify", "-c", update_name(), text)
    except (OSError, subprocess.SubprocessError):
        pass


def reconcile_state_path():
    return os.path.join(os.path.expanduser("~/.local/state/altention"), "reconcile.json")


def engine_head():
    r = subprocess.run(["git", "-C", ENGINE, "rev-parse", "HEAD"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       universal_newlines=True)
    return r.stdout.strip() if r.returncode == 0 else None


def declared(found, rows):
    """(channels, asks) the plugins in use declare: the core and every plugin on or placed on another machine."""
    channels, asks = [], []
    for name, _, d in found:
        if name != "core" and rows.get(name) not in ("on", "off"):
            continue
        channels += [dict(c, plugin=name) for c in d.get("channels", []) if isinstance(c, dict)]
        asks += [dict(a, plugin=name) for a in d.get("asks", []) if isinstance(a, dict)]
    return channels, asks


# C-121: an installation from before the permission mode was asked has none written, and the bot opened its
# conversations with bypassPermissions. The bot now opens nothing without one, so the first reconcile writes that
# mode out (nothing changes) and asks in #setup, the question beginning with WRITTEN_SAY.
PERMISSION_KEY = "dcbot.default_permission"
WRITTEN_MODE = "bypassPermissions"
WRITTEN_AUDIT = "把原本沒寫明的權限模式 bypassPermissions 寫進設定（行為不變）"
WRITTEN_COMMIT = "setup: write the permission mode bypassPermissions, never written before, into the settings " \
                 "(behaviour unchanged) / " + WRITTEN_AUDIT
WRITTEN_SAY = "這個安裝之前沒設權限模式，機器人一直用 bypassPermissions 在跑：Claude 做什麼都不先問你。" \
              "這次更新把它寫明了，行為不變。建議到 #setup 改成 auto。"


def permission_unwritten(state):
    """Is this an installation set up already (or from before #setup) that has no permission mode written, the
    case write_permission() is for? A new installation still in #setup is not: it is asked there, and install.sh
    wrote one already (instance.py init). Nor is one whose mode was chosen in #setup, or written here once, and
    then taken out by hand: that is no mode, and the bot opens nothing (#code-review on 85cd909: it was written
    again as bypassPermissions at the next update, said to be what it had always run with)."""
    if state is not None and state.get("done") is not True:
        return False
    if state is not None and (PERMISSION_KEY in (state.get("answered") or {}) or state.get("permission_written")):
        return False
    value = config.setting(PERMISSION_KEY)
    return value is None or value == ""


def write_permission(out):
    """The mode the bot ran with written out (WRITTEN_MODE), said in #audit with the instance's commit saying the
    same, and in #update; the setup state remembers it, so #setup's question says so (question_for)."""
    d = read_settings()
    put(d, PERMISSION_KEY, WRITTEN_MODE)
    st = d.get("setup") if isinstance(d.get("setup"), dict) else None
    if st is None:  # an installation from before #setup: set up already (as the asks below make it)
        st = d["setup"] = {"done": True, "since": "reconcile"}
    st["permission_written"] = WRITTEN_MODE
    write_settings(d)
    commit(WRITTEN_COMMIT)
    say_audit(WRITTEN_AUDIT)
    say_update("🔐 " + WRITTEN_SAY)
    out.write("%s\n" % WRITTEN_AUDIT)


def reconcile_plan(chans, before_offers=None):
    """What the reconcile would do, given the server's text channels [(id, name, category)]:
    [{"kind": "fill" | "ask" | "note", "what": short id, "say": plain words, ...}]. before_offers: called once the
    rest is known and before the install offers are read (the reconcile writes the permission mode, then looks at
    the official list there)."""
    found = plugin.discover()[0]
    rows = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
    channels, asks = declared(found, rows)
    by_name = {}
    for cid, n, cat in chans:
        by_name.setdefault(n, []).append((cid, cat))
    ids = set(cid for cid, _, _ in chans)
    system = system_category()
    plan, ask = [], []
    for c in channels:
        kind = c.get("kind")
        if kind == "bot":
            name = config.setting(c.get("name_setting") or "") if c.get("name_setting") != "core.update_channel" \
                else update_name()
            name = name.strip().lstrip("#") if isinstance(name, str) and name.strip() else c["name"]
            there = by_name.get(name, [])
            if not there:
                if c["name"] == "update":
                    plan.append({"kind": "fill", "what": "make-update", "channel": name,
                                 "say": "開 #%s（主區）：放引擎更新的消息" % name})
                elif c["name"] == "guide":
                    plan.append({"kind": "fill", "what": "make-guide", "channel": name,
                                 "say": "開 #%s（「%s」分類）：機器人的使用說明" % (name, system)})
                else:
                    plan.append({"kind": "note", "what": "missing-%s" % c["name"],
                                 "say": "#%s 不在：機器人下次啟動會自己開" % name})
            elif c.get("category") == "system" and not any(cat == system for _, cat in there):
                plan.append({"kind": "fill", "what": "move-%s" % c["name"], "channel": name,
                             "say": "把 #%s 搬到最下面的「%s」分類（現在在%s）" % (
                                 name, system, "「%s」" % there[0][1] if there[0][1] else "主區")})
        elif kind in ("board", "claude") and c.get("setting"):
            value = config.setting(c["setting"])
            stale = set_value(value) and str(value) not in ids  # channel_gone() tells these two apart the same way
            unset = not set_value(value)
            if not (unset or stale):
                continue
            key = "dashboard" if kind == "board" else "plugins.%s" % c["plugin"]
            if key not in ask:
                ask.append(key)
                plan.append({"kind": "ask", "what": key, "say": "%s：%s" % (
                    "#%s" % c["name"], "設定裡的頻道已經不在了" if stale else "還沒有這個頻道") + "（要不要開，到 #setup 問）"})
    state = setup_state()
    if permission_unwritten(state):
        plan.append({"kind": "fill", "what": "permission-written", "say": WRITTEN_AUDIT})
    elif state is not None and state.get("permission_written") and PERMISSION_KEY not in (state.get("answered") or {}) \
            and PERMISSION_KEY not in asked(state) and PERMISSION_KEY not in ask:
        ask.append(PERMISSION_KEY)  # written by an earlier reconcile that stopped before it asked
        plan.append({"kind": "ask", "what": PERMISSION_KEY, "say": "%s 是補寫的（到 #setup 問）" % PERMISSION_KEY})
    for a in asks:
        if config.setting(a["key"]) is not None:
            continue
        if any(config.setting(k) == v for k, v in (a.get("unless") or {}).items()):
            continue
        if a["question"] not in ask:
            ask.append(a["question"])
            plan.append({"kind": "ask", "what": a["question"], "say": "%s 沒設定（到 #setup 問）" % a["key"]})
    if before_offers is not None:
        before_offers(plan)
    from altention import offers  # C-64, an install offer never answered or answered 「之後再說」, after an update
    for n in offers.due(("install",), settings=config.settings()):
        if "offer.%s" % n not in ask:
            ask.append("offer.%s" % n)
            plan.append({"kind": "ask", "what": "offer.%s" % n, "say": text.t("offer.reconcile", plugin=n)})
    order = ("dcbot.default_permission", "plugins.%s" % SECRETARY, "dashboard", "usage.plan")  # #setup's order
    rank = lambda p: (p["kind"] != "fill", order.index(p["what"]) if p["what"] in order else len(order))  # noqa: E731
    plan.sort(key=rank)
    _, changed, problem = write_setup_folder(dry=True)
    if problem:
        plan.append({"kind": "note", "what": "setup-folder", "say": problem})
    elif changed:
        plan.append({"kind": "fill", "what": "setup-folder", "say": "#setup 資料夾的%s" % "、".join(changed)})
    return plan


def reconcile(dry, as_json, if_changed, out=None):
    out = out or sys.stdout
    if not config.bot_here():
        out.write("機器人不在這台：頻道和整個實例的設定只在機器人那台補\n")
        return 0
    head = engine_head()
    try:
        with open(reconcile_state_path()) as f:
            last = json.load(f).get("head")
    except (OSError, ValueError, AttributeError):
        last = None
    if if_changed and head and head == last:
        out.write("引擎沒換過（%s）：不用再比對\n" % head[:12])
        return 0
    lock = None
    if not dry:  # a dry run writes nothing, its lock included
        import fcntl
        os.makedirs(os.path.dirname(reconcile_state_path()), exist_ok=True)
        lock = open(reconcile_state_path() + ".lock", "a")
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            out.write("另一個 reconcile 正在跑\n")
            lock.close()
            return 0
    try:
        state = setup_state()
        first = []

        def before_offers(plan):
            """The install offers come from the official list: looked at first (a dry run writes nothing). The
            permission mode is written before that look, which may take up to a minute: the bot opens no
            conversation until it is written (#code-review of 1ca3c0e)."""
            if dry:
                return
            if any(p["what"] == "permission-written" for p in plan):
                write_permission(out)
                first.append("permission-written")
            from altention import catalog
            _, words = catalog.look_before_asking()
            if words:
                out.write("%s\n" % words)
        plan = reconcile_plan(discord_channels(), before_offers)
        if as_json:
            json.dump(plan, out, ensure_ascii=False, indent=1)
            out.write("\n")
        else:
            for p in plan:
                out.write("%s %s\n" % ({"fill": "補", "ask": "問", "note": "說"}[p["kind"]], p["say"]))
            if not plan:
                out.write("都齊了\n")
        if dry:
            return 0
        fills = [p for p in plan if p["kind"] == "fill"]
        if any(p["what"] == "permission-written" for p in fills) and "permission-written" not in first:
            write_permission(out)  # first: the bot opens nothing until it is written (written above already, as a rule)
        if any(p["what"] == "make-update" for p in fills):
            p = next(p for p in fills if p["what"] == "make-update")
            r = dcctl("create", p["channel"], "--topic", UPDATE_TOPIC)
            if r.returncode != 0:
                raise Failed("開不了 #%s：%s" % (p["channel"], r.stdout.strip()[-200:]))
            wait_for(p["channel"])
            say_update(UPDATE_HELLO)
        moves = [p for p in fills if p["what"].startswith("move-")]
        if moves:
            say_update("📦 整理頻道：把 %s 搬到最下面的「%s」分類，讓上面只留你平常會用的頻道。頻道裡的內容都不會變。"
                       % ("、".join("#%s" % p["channel"] for p in moves), system_category()))
            for p in moves:
                r = altention("dc", "post", p["channel"], "--move-to", system_category(), "整理頻道：系統用的頻道放在最下面")
                if r.returncode != 0:
                    out.write("沒搬成 #%s：%s\n" % (p["channel"], r.stdout.strip()[-200:]))
        if any(p["what"] == "make-guide" for p in fills):
            guide(out)
        if any(p["what"] == "setup-folder" for p in fills):
            write_setup_folder()
        asks = [p["what"] for p in plan if p["kind"] == "ask"]  # in #setup's order
        if state is not None and state.get("done") is not True:
            asks = []  # a new installation still in #setup: its questions cover these
        if asks:
            d = read_settings()
            st = d.get("setup") if isinstance(d.get("setup"), dict) else None
            if st is None:  # an installation from before #setup: set up already, asked only these (no tour)
                st = d["setup"] = {"done": True, "since": "reconcile"}
            before = asked(st)
            new = [k for k in asks if k not in before]
            st["ask"] = before + new
            if new:
                write_settings(d)
                commit("setup: the reconcile asks %s" % "、".join(new))
                heads = [q["header"] for q in (question_for(k) for k in st["ask"]) if q]  # the questions' own names
                say_update("📝 有 %d 件事要你決定：%s。到 #setup 回答，一題一題問；還沒決定之前，這些維持現在的樣子。"
                           % (len(st["ask"]), "、".join(heads) if heads else "見 #setup"))
                p = dcctl("post", inp=json.dumps({"channel": "setup", "open_setup": True, "text": ""}))
                if p.returncode != 0:
                    out.write("請機器人開 #setup 沒送出去：%s\n" % p.stdout.strip()[-200:])
        with open(reconcile_state_path() + ".tmp", "w") as f:
            json.dump({"head": head, "at": time.time()}, f)
        os.replace(reconcile_state_path() + ".tmp", reconcile_state_path())
        return 0
    finally:
        if lock:
            lock.close()


def done_restored(mark, out):
    """After a restore: install.sh again (the name may have changed), the channels the restored settings name made
    again where they are not on this server, and #chat. The restored instance is never given a setup of its own."""
    if mark.get("stage") == "restored":
        install_restored(mark, out)
        mark["stage"] = "installed"
        write_mark(mark)
    ids = set(cid for cid, _, _ in discord_channels())
    d = read_settings()
    for k in ("usage.channel", "status.channel", "planner.channel"):  # another server's IDs: made again below
        v = config.setting(k)
        if v is not None and str(v) not in ids:
            drop(d, k)
    write_settings(d)
    commit("setup: channels of another server taken out after the restore")
    rows = {n: w for n, _, _, w, _ in plugin.on_this_machine()[0]}
    dashboard(out)
    guide(out)
    if rows.get(SECRETARY) in ("on", "off") and not config.setting("planner.channel"):
        secretary(out)
    chat = config.setting("dcbot.channels.chat") or "chat"
    p = dcctl("post", inp=json.dumps({"channel": chat, "open_chat": True, "text": ""}))
    if p.returncode != 0:
        raise Failed("請機器人開 #%s 沒送出去：%s" % (chat, p.stdout.strip()[-200:]))
    mark["stage"] = "done"
    write_mark(mark)
    out.write("設定做完了：機器人接著開 #%s\n" % chat)
    return 0


def offer(name, out=None):
    """setup.py offer NAME (C-64 "asked"): something found the moment for an optional plugin of the official list
    (its offer.when "asked"); when it may be offered, it is put in setup.ask and the bot asked to open #setup, where it
    is asked like the reconcile's questions. Exit 0 offered, 1 not (said why)."""
    from altention import offers
    out = out or sys.stdout
    d = read_settings()
    p = offers.candidates(settings=d).get(name)
    if p is None or p["offer"].get("when", "install") != "asked":
        raise Refused(text.t("offer.not_listed", plugin=name))
    if not offers.open_for(name, d):
        raise Refused(text.t("offer.closed", plugin=name, most=offers.MAX_LATER))
    st = d.get("setup") if isinstance(d.get("setup"), dict) else None
    if st is not None and st.get("done") is not True:
        raise Refused(text.t("offer.in_setup", plugin=name))
    if st is None:  # set up before #setup: asked like the reconcile's (no tour)
        st = d["setup"] = {"done": True, "since": "reconcile"}
    key = "offer.%s" % name
    if key not in asked(st):
        st["ask"] = asked(st) + [key]
        write_settings(d)
        commit("setup: offer %s" % name)
        r = dcctl("post", inp=json.dumps({"channel": "setup", "open_setup": True, "text": ""}))
        if r.returncode != 0:
            out.write("請機器人開 #setup 沒送出去：%s\n" % r.stdout.strip()[-200:])
    out.write(text.t("offer.offered", plugin=name) + "\n")
    return 0


def plugin_add(rest, out=None):
    """setup.py plugin-add: ops/plugins.py add, word for word (its usage checked there); once written, install.sh
    here, as done() runs it (#setup's Claude has this command's rule only)."""
    import plugins
    out = out or sys.stdout
    me = "python3 %s" % os.path.join(ENGINE, "ops", "setup.py")
    code = plugins.main(["add"] + rest, again=me + " plugin-add", apply_cmd=me + " plugin-apply")
    if code != 0 or "--yes" not in rest:
        return code
    r = script([os.path.join(ENGINE, "install.sh")])
    last = [x for x in r.stdout.splitlines() if x.strip()][-1:] or ["（沒有輸出）"]
    out.write("install.sh：%s\n" % last[0])
    if r.returncode == 1:  # as done(): 2 is something left for the person, said by install itself
        raise Failed("install.sh 沒跑完（log：~/.local/state/altention/install/last.log）；修好再跑一次 setup.py plugin-add")
    return 0


def _plugin_here(name):
    """(folder, plugin.json) of PLUGIN as this machine runs it; Refused when it is not on here or cannot be read."""
    rows, _ = plugin.on_this_machine()
    for n, folder, d, where, why in rows:
        if n == name:
            if d is None:
                raise Refused(text.t("setup.steps_unreadable", plugin=name))
            if where != "on":
                raise Refused(text.t("setup.steps_not_here", plugin=name, why=why or where))
            return folder, d
    raise Refused(text.t("setup.steps_no_plugin", plugin=name))


ELSEWHERE_TIMEOUT = 300  # another machine's plugin-steps over ssh: its own plan is at most PLAN_LIMIT (120 s)
ELSEWHERE = """import os, subprocess, sys
engine = os.path.expanduser({engine!r})
try:
    r = subprocess.run([sys.executable, os.path.join(engine, "ops", "setup.py"), "plugin-steps", {name!r}, "--json"],
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True, timeout={limit})
except subprocess.TimeoutExpired:
    sys.exit(124)
sys.stdout.write(r.stdout)
sys.exit(r.returncode)
"""


def plans_elsewhere(name, d):
    """{machine: plan, or {"error": words}} of PLUGIN on the other machines that use it (C-77 G2: system tools are
    each machine's own), each asked over ssh to run its own plugin-steps."""
    from common import engine_raw, inventory, run_on
    from altention import plugin_steps as steps
    entry = (config.settings().get("plugins") or {}).get(name) or {}
    machines = inventory().get("machines") or {}
    out = {}
    for m in plugins.users_elsewhere(d, name, entry if isinstance(entry, dict) else {}):
        if m not in machines:
            out[m] = {"error": text.t("setup.steps_not_listed", machine=m)}
            continue
        script = ELSEWHERE.format(engine=engine_raw(machines[m]), name=name, limit=steps.PLAN_LIMIT + 60)
        alias, rc, stdout, err = run_on(machines[m], script, timeout=ELSEWHERE_TIMEOUT)
        try:
            p = json.loads(stdout) if rc in (0, 3) else None
        except ValueError:
            p = None
        last = ([x for x in (stdout or err or "").splitlines() if x.strip()] or [""])[-1][:200]
        out[m] = p if isinstance(p, dict) else {"error": text.t(
            "setup.steps_unreachable" if alias is None or rc in (124, 255) else
            "setup.steps_old_engine" if rc == 2 and "plugin-steps" not in stdout else "setup.steps_failed",
            machine=m, line=last)}
    return out


def plugin_steps(name, as_json, everywhere=False, out=None):
    """setup.py plugin-steps: the plan, as plugin_steps.lines() says it (#setup's Claude follows it as written);
    --all adds the other machines' (plans_elsewhere)."""
    from altention import plugin_steps as steps
    out = out or sys.stdout
    folder, d = _plugin_here(name)
    p = steps.plan(name, folder, d, config.settings())
    others = plans_elsewhere(name, d) if everywhere else {}
    if as_json:
        out.write(json.dumps({"here": p, "others": others} if everywhere else p, ensure_ascii=False, indent=1) + "\n")
    else:
        out.write("\n".join(steps.lines(p)) + "\n")
        for m, q in sorted(others.items()):
            out.write("\n" + (text.t("setup.steps_machine", machine=m) + "\n" + "\n".join(steps.lines(q))
                               if "error" not in q else q["error"]) + "\n")
    left = p["left"] or any(q.get("left") or "error" in q for q in others.values())
    return 3 if left else 0


def _ran_words(item, rc, output):
    """How a plugin-run or plugin-tool command ended: one line when it printed nothing (C-77 acceptance)."""
    tail = _tail(output)
    return text.t("setup.ran", item=item, code=rc, tail=tail) if tail else text.t("setup.ran_quiet", item=item, code=rc)


def _tail(output, n=5):
    return "\n".join([x for x in output.strip().splitlines() if x.strip()][-n:])[-600:]


def _checked_again(name, folder, d, kind, key, out):
    """After plugin-run or plugin-tool: that one item's state now, said; 0 when its check passes."""
    from altention import plugin_steps as steps
    item = next(x for x in steps.plan(name, folder, d, config.settings())[kind] if (x.get("id") or x["name"]) == key)
    out.write((text.t("setup.ran_check_ok", item=key) if item["state"] == "ok" else
               text.t("setup.ran_check_why", item=key, why=item["reason"]) if item["reason"] else
               text.t("setup.ran_check_todo", item=key)) + "\n")
    return 0 if item["state"] == "ok" else 1


def plugin_run(name, step_id, out=None):
    """setup.py plugin-run: a guide[] step's "run" (the plugin does that step itself), once, with SETUP_TIMEOUT; what
    it printed last, then the step checked again. Only a step plugin.json gives a "run"."""
    from altention import plugin_update
    out = out or sys.stdout
    folder, d = _plugin_here(name)
    g = next((g for g in d.get("guide", []) if g["id"] == step_id), None)
    if g is None or not g.get("run"):
        raise Refused(text.t("setup.run_none", plugin=name, item=step_id))
    try:
        cmd = plugin.expand(g["run"], folder, config.settings())
    except plugin.PluginError as e:  # a setting it names is not set: nothing run
        raise Refused(text.t("steps.cannot_run", error=e))
    rc, output, killed = plugin_update._run(cmd, folder, plugin_update.SETUP_TIMEOUT)
    out.write(_ran_words(step_id, rc, output) + "\n")
    if killed or rc != 0:
        raise Failed(text.t("setup.ran_failed", item=step_id, code=rc))
    return _checked_again(name, folder, d, "guide", step_id, out)


SECRET_SEND = """import base64, os
value = base64.b64decode({value!r})
root = os.path.expanduser("~/.config/altention/secrets")
folder = os.path.join(root, {plugin!r})
for f in (root, folder):
    os.makedirs(f, mode=0o700, exist_ok=True)
    os.chmod(f, 0o700)
target = os.path.join(folder, {name!r})
if os.path.lexists(target + ".new"):
    os.remove(target + ".new")
fd = os.open(target + ".new", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
try:
    os.write(fd, value)
    os.fsync(fd)
finally:
    os.close(fd)
os.replace(target + ".new", target)
print(len(value))
"""
SECRET_SEND_TIMEOUT = 120


def secret_send(name, secret, machine, out=None):
    """setup.py secret-send: the value stored here (secret_store) written on MACHINE as secret_store.write does,
    by a script fed on ssh's stdin (it works whatever engine that machine has); only to a machine that uses PLUGIN.
    The value is read here by this program alone, never printed; #audit gets a line."""
    import base64
    from common import inventory, run_on
    from altention import secret_store
    out = out or sys.stdout
    rows, _ = plugin.on_this_machine()
    d = next((d for n, _f, d, _w, _y in rows if n == name and d is not None), None)
    if d is None:
        raise Refused(text.t("secret.no_plugin", plugin=name))
    if secret not in [s["name"] for s in d.get("secrets", [])]:
        raise Refused(text.t("secret.not_declared", plugin=name, name=secret))
    entry = (config.settings().get("plugins") or {}).get(name) or {}
    machines = inventory().get("machines") or {}
    if machine not in plugins.users_elsewhere(d, name, entry if isinstance(entry, dict) else {}) or machine not in machines:
        raise Refused(text.t("setup.secret_not_user", plugin=name, machine=machine))
    try:
        with open(secret_store.path(name, secret), "rb") as f:
            value = f.read()
    except OSError:
        value = b""
    if not value:
        raise Refused(text.t("setup.secret_not_here", plugin=name, name=secret))
    script = SECRET_SEND.format(value=base64.b64encode(value).decode("ascii"), plugin=name, name=secret)
    alias, rc, stdout, err = run_on(machines[machine], script, timeout=SECRET_SEND_TIMEOUT)
    if rc != 0 or stdout.strip() != str(len(value)):
        raise Failed(text.t("setup.secret_send_failed", name=secret, machine=machine,
                            line=([x for x in (err or stdout or "").splitlines() if x.strip()] or ["?"])[-1][:200]))
    out.write(text.t("setup.secret_sent", name=secret, machine=machine) + "\n")
    try:
        here = config.this_machine()
    except config.ConfigError:
        here = "?"
    secret_store.tell_audit(text.t("secret.audit", plugin=name, name=secret, machine=machine,
                                   via=text.t("secret.via_sent", machine=here)))
    return 0


def plugin_finish(name, out=None):
    """setup.py plugin-finish: after the last step, install.sh here (as plugin-add runs it), so the setup items and
    schedules its gates held start; refused while the plan still has something left."""
    from altention import plugin_steps as steps
    out = out or sys.stdout
    folder, d = _plugin_here(name)
    p = steps.plan(name, folder, d, config.settings())
    if p["left"]:
        raise Refused(text.t("setup.finish_left", plugin=name, n=p["left"]))
    r = script([os.path.join(ENGINE, "install.sh")])
    last = [x for x in r.stdout.splitlines() if x.strip()][-1:] or ["（沒有輸出）"]
    out.write("install.sh：%s\n" % last[0])
    if r.returncode == 1:
        raise Failed(text.t("setup.finish_failed"))
    waiting = steps.gated(name, d)
    out.write((text.t("install.steps_gated", plugin=name, items="、".join(sorted(waiting)),
                      steps="」「".join(sorted(set(waiting.values())))) if waiting else text.t("setup.finished", plugin=name)) + "\n")
    return 0


def plugin_tool(name, need, out=None):
    """setup.py plugin-tool: one system tool installed with plugin.json's command for this system, never one starting
    with sudo (the person runs those in their own terminal: #setup's Claude never runs sudo), then checked again."""
    from altention import plugin_steps as steps, plugin_update
    out = out or sys.stdout
    folder, d = _plugin_here(name)
    n = next((n for n in d.get("needs", []) if n["name"] == need), None)
    cmd = ((n or {}).get("install") or {}).get(steps.os_name())
    if not cmd:
        raise Refused(text.t("setup.tool_none", plugin=name, item=need))
    if cmd.startswith("sudo "):
        raise Refused(text.t("setup.tool_sudo", item=need, cmd=cmd))
    rc, output, killed = plugin_update._run(cmd, folder, plugin_update.SETUP_TIMEOUT)
    out.write(_ran_words(need, rc, output) + "\n")
    if killed or rc != 0:
        raise Failed(text.t("setup.ran_failed", item=need, code=rc))
    return _checked_again(name, folder, d, "needs", need, out)


def plugin_said(name, key, answer, out=None):
    """setup.py plugin-said: kept only for an item asked about, a check null or giving 3 now (one that can be checked
    counts by its check, never by a word)."""
    from altention import plugin_steps as steps
    out = out or sys.stdout
    if answer not in ("yes", "no"):
        raise Refused(text.t("setup.said_yes_no"))
    folder, d = _plugin_here(name)
    if key.startswith("needs:"):
        item = next((n for n in d.get("needs", []) if n["name"] == key[6:]), None)
    else:
        item = next((g for g in d.get("guide", []) if g["id"] == key), None)
    if item is None:
        raise Refused(text.t("setup.said_no_item", plugin=name, item=key))
    if item.get("check") is not None:
        st, why = steps.run_check(item["check"], folder, config.settings(), time.time() + steps.CHECK_TIMEOUT)
        if st != "cannot":
            raise Refused(text.t("setup.said_checkable_ok", item=key) if st == "ok" else
                          text.t("setup.said_checkable_why", item=key, why=why) if why else text.t("setup.said_checkable_todo", item=key))
    steps.said(name, key, answer)
    out.write(text.t("setup.said_" + answer, plugin=name, item=key) + "\n")
    return 0


def plugin_apply(name, machine, out=None):
    """setup.py plugin-apply PLUGIN MACHINE: after plugin-add, another machine on the list brought in line now
    (ops/apply.py MACHINE: everything it has not taken yet, the engine and the other plugins too, then this plugin's
    folder, schedules and hooks), then doctor's last lines; once the person said yes to that machine (the setup
    skill's step 6). Only for an external plugin the instance names with a source that runs on MACHINE, never this machine
    (manager 2026-10-21): anything else is wrong usage (2)."""
    import plugins
    out = out or sys.stdout
    entry = (config.setting("plugins") or {}).get(name) if isinstance(config.setting("plugins"), dict) else None
    d = plugins.plugin_json_of(name) if isinstance(entry, dict) and "source" in entry and \
        plugin.source_of(entry) == (True, None) else None  # one with a source (as plugin-add writes), not a local one
    others = plugins.users_elsewhere(d, name, entry) if d is not None else []
    if machine not in others:
        out.write("用法：setup.py plugin-apply <插件> <機器>，只給實例寫了的外部插件、它會用到的別台（%s）\n" % (
            "%s 用到的別台：%s" % (name, "、".join(others) or "沒有") if d is not None else "實例沒有外部插件 %s" % name))
        return 2
    r = script([sys.executable, os.path.join(ENGINE, "ops", "apply.py"), machine])
    lines = [x for x in r.stdout.splitlines() if x.strip()]
    out.write("apply %s：%s\n" % (machine, " ｜ ".join(lines[-3:]) or "（沒有輸出）"))
    d = script([sys.executable, os.path.join(ENGINE, "ops", "doctor.py")])
    out.write("doctor：%s\n" % (" ｜ ".join([x for x in d.stdout.splitlines() if x.strip()][-6:]) or "（沒有輸出）"))
    if r.returncode == 1:
        raise Failed("apply %s 沒做完（它說的在上面）" % machine)
    return 0


def main(argv):
    args = [a for a in argv if a != "--json"]
    as_json = "--json" in argv
    try:
        if args == ["status"]:
            return status(as_json)
        if args == ["questions"]:
            return questions_cmd(as_json)
        if len(args) == 3 and args[0] == "answer" and not as_json:
            return answer(args[1], args[2])
        if args == ["dashboard"] and not as_json:
            return dashboard()
        if args == ["secretary"] and not as_json:
            return secretary()
        if args == ["guide"] and not as_json:
            return guide()
        if args == ["tutorial"] and not as_json:
            return tutorial()
        if args == ["repos"]:
            return repos(as_json)
        if len(args) == 2 and args[0] == "restore" and not as_json:
            return restore(args[1])
        if args == ["login-check"]:
            return login_check(as_json)
        if args == ["done"] and not as_json:
            return done()
        if args[:1] == ["reconcile"] and set(args[1:]) <= {"--dry-run", "--if-changed"}:
            return reconcile("--dry-run" in args, as_json, "--if-changed" in args)
        if len(args) == 3 and args[0] == "plugin-apply" and not as_json:
            return plugin_apply(args[1], args[2])
        if args[:1] == ["plugin-steps"] and len([a for a in args if a != "--all"]) == 2:
            return plugin_steps([a for a in args if a != "--all"][1], as_json, "--all" in args)
        if len(args) == 3 and args[0] == "plugin-run" and not as_json:
            return plugin_run(args[1], args[2])
        if len(args) == 2 and args[0] == "offer" and not as_json:
            return offer(args[1])
        if len(args) == 4 and args[0] == "secret-send" and not as_json:
            return secret_send(args[1], args[2], args[3])
        if len(args) == 2 and args[0] == "plugin-finish" and not as_json:
            return plugin_finish(args[1])
        if len(args) == 3 and args[0] == "plugin-tool" and not as_json:
            return plugin_tool(args[1], args[2])
        if len(args) == 4 and args[0] == "plugin-said" and not as_json:
            return plugin_said(args[1], args[2], args[3])
        if args[:1] == ["plugin-add"] and not as_json:
            return plugin_add(args[1:])
        if args[:1] == ["backup-set"] and not as_json and (len(args) == 3 or (len(args) == 4 and args[2] == "--create")):
            return backup_set(args[1], args[-1], len(args) == 4)
    except Refused as e:
        print("沒有寫：%s" % e)
        return 1
    except (Failed, OSError, ValueError, config.ConfigError) as e:
        print("沒做完：%s" % e)
        return 2
    except subprocess.TimeoutExpired as e:
        print("沒做完：%s 太久沒有結束" % " ".join(e.cmd[:3]))
        return 2
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
