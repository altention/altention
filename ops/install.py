#!/usr/bin/env python3
"""The one installer (stage 3a.9), run by install.sh once it has found a python3 of 3.8 or later and git.

  ./install.sh                         # everything this machine needs, from the instance's settings
  ./install.sh --links                 # only the light part: skills, the altention command, global notes, memory
  ./install.sh --hold progress --allow-time chatlog/sync   # passed on by ops/apply.py (see plugins.py apply)
  ./install.sh --moved-from ~/old-engine   # the engine moved and its old folder is gone: its lines and hooks go too
  ./install.sh discord                 # Discord: the bot's token typed on this keyboard (never via Claude), the rest asked
                                       # of Discord; once written, the whole install as ./install.sh

Never asks anything: every answer comes from the instance's settings. Every step can be run again and comes to the
same place, an install cut short included. One line per step on stdout, `<status> <id> <text>`:
  ok        already as it should be
  changed   made so now
  skip      not for this machine
  todo      the user has to do something: the text says what, with the command or the file
  fail      went wrong
  unknown   could not be read or told
Exit status: 1 when anything failed or was unknown, else 2 when anything is left to the user, else 0. The last line
is `ok core.done …` whenever it ran to the end (ops/apply.py checks for it). What the
scripts behind the steps printed goes to ~/.local/state/altention/install/last.log (600), never the environment;
the step lines go there too.

ALT_NO_START=1: nothing that keeps running is started (the supervisor). ops/apply.py does not set it.
ALT_NO_PATH_CHECK=1: core.path does not start the user's shell (ops/apply.py sets it: every apply would run their rc
files again).
"""
import fcntl
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ENGINE = os.path.dirname(HERE)
sys.path.insert(0, ENGINE)
from altention import config, plugin, sync  # noqa: E402

MARK = "ALT-STEP-OK"
FIRST = ("chatlog", "dcbot")  # installed first, in this order: the others may use the logs repo or the bot's tools
CHANGED = re.compile(r"^- (加上|拿掉|標記|改時間|設定|建空)")
STATUSES = ("ok", "changed", "skip", "todo", "fail", "unknown")


class Run:
    """The lines said so far and the log the scripts' output goes to."""

    def __init__(self):
        self.seen, self.said = [], []
        folder = os.path.join(os.path.expanduser("~/.local/state/altention"), "install")
        os.makedirs(folder, mode=0o700, exist_ok=True)
        self.folder = folder
        self.log_path = os.path.join(folder, "last.log")
        fd = os.open(self.log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.chmod(self.log_path, 0o600)
        self.log = os.fdopen(fd, "w", encoding="utf-8")

    def say(self, status, ident, text):
        assert status in STATUSES
        self.seen.append(status)
        self.said.append((status, ident))
        said = "%s %s %s" % (status, ident, " ".join(str(text).split()))
        print(said)
        sys.stdout.flush()
        self.note(said)  # the lines too: after install.sh discord, Claude reads them here, not on the user's screen

    def note(self, text):
        self.log.write(text.rstrip("\n") + "\n")
        self.log.flush()

    def script(self, label, argv, env=None):
        """Run one script, its output into the log (what it printed, never the environment). (exit status, output)."""
        self.note("== %s: %s" % (label, " ".join(argv)))
        try:
            p = subprocess.run(argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               universal_newlines=True, env=env, timeout=1800)
            out, rc = p.stdout, p.returncode
        except subprocess.TimeoutExpired as e:
            out, rc = (e.stdout or "") + "\n（30 分鐘沒有結束）", 124
        except OSError as e:
            out, rc = str(e), 127
        self.note(out)
        self.note("== %s: exit %s" % (label, rc))
        return rc, out

    def code(self):
        if "fail" in self.seen or "unknown" in self.seen:
            return 1
        return 2 if "todo" in self.seen else 0


def find_instance(r, light):
    """The instance folder, made when this machine has none (not in --links); None when it cannot be had."""
    try:
        inst = config.instance_dir()
    except config.ConfigError as e:  # named (ALT_INSTANCE or the pointer) and not there: never made elsewhere instead
        r.say("fail", "core.instance", "%s；改正 ALT_INSTANCE 或 %s 再重跑" % (e, os.path.join(config.config_dir(), "instance")))
        return None
    place = os.path.join(config.root(), "instance")
    if inst and not (os.path.isdir(os.path.join(inst, ".git")) or os.path.isfile(os.path.join(inst, "plugins.json"))):
        r.say("todo", "core.instance", "%s 已經有東西，但不是實例：把它移走（mv %s %s.mine），或用 ALT_INSTANCE 指定實例，再重跑"
              % (inst, inst, inst))
        return None
    if inst:
        r.say("ok", "core.instance", "實例：%s" % inst)
        return inst
    if os.path.lexists(place):
        r.say("todo", "core.instance", "%s 已經有東西，但不是實例：把它移走（mv %s %s.mine）再重跑" % (place, place, place))
        return None
    if light:
        r.say("skip", "core.instance", "這台還沒有實例（--links 不建；跑完整的 install.sh 會建）")
        return None
    rc, out = r.script("instance init", [sys.executable, os.path.join(HERE, "instance.py"), "init",
                                         "--permission", new_permission()])
    inst = config.instance_dir() if rc == 0 else None
    if not inst:
        r.say("fail", "core.instance", "建不出本機實例（%s）" % (out.strip().splitlines() or ["沒有說明"])[-1])
        return None
    r.say("changed", "core.instance", "建了本機實例 %s（沒有遠端）" % inst)
    return inst


def setup_cmd():
    """ops/setup.py as a module (its name is no package's: loaded by its path)."""
    import importlib.util
    spec = importlib.util.spec_from_file_location("altention_setup_cmd", os.path.join(HERE, "setup.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def new_permission():
    """A new installation's permission mode (C-48): auto, or manual when this Claude Code lists modes without it.
    Not asked of Claude Code (not installed yet): auto, which core.needs then says to install."""
    modes = setup_cmd().claude_modes()
    return "manual" if modes is not None and "auto" not in modes else "auto"


def setup_pending():
    """The instance's setup.done is false: a new installation whose settings #setup has not finished (C-48)."""
    try:
        return config.setting("setup.done", True) is False
    except Exception:
        return False


def hold_until_setup(r, holds):
    """C-48: while #setup is not done, progress (it spends the subscription every round) is held like apply's
    --hold: not installed, no schedule; `setup.py done` runs install.sh again and it starts. (The usage board's
    limits wait in usage/collect.py itself.)"""
    if setup_pending() and "progress" not in holds:
        holds.append("progress")
        r.say("skip", "core.setup", "設定還沒做完（Discord 的 #setup）：先不裝、不排程會用訂閱額度的進度整理；設定做完會自己開始")


def needs(r):
    """What the bot and the install need besides python3 and git (install.sh checks those): said, never installed."""
    missing = []
    if not shutil.which("tmux"):
        missing.append("tmux（Debian/Ubuntu：sudo apt install tmux；Fedora：sudo dnf install tmux；macOS：brew install tmux）")
    if not setup_cmd().claude_binary():
        missing.append("Claude Code（照 https://docs.claude.com/claude-code 裝好，再在終端機開一次 claude 登入）")
    p = subprocess.run([sys.executable, "-c", "import ensurepip, venv"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    if p.returncode != 0:
        missing.append("python3 的 venv（Debian/Ubuntu：sudo apt install python3-venv）")
    if missing:
        r.say("todo", "core.needs", "這台還缺：%s。裝好再重跑 ./install.sh" % "；".join(missing))
    else:
        r.say("ok", "core.needs", "tmux、Claude Code、python3 的 venv 都在")


def setup_folder(r, bot_here):
    """#setup's working folder on the bot's machine (setup.py's write_setup_folder: the allow rule, the skill)."""
    if not bot_here:
        r.say("skip", "core.setup-folder", "機器人不在這台：#setup 的資料夾不用")
        return
    folder, changed, problem = setup_cmd().write_setup_folder(ENGINE)
    if problem:
        r.say("todo", "core.setup-folder", problem)
    elif changed:
        r.say("changed", "core.setup-folder", "#setup 的資料夾 %s：%s" % (folder, "、".join(changed)))
    else:
        r.say("ok", "core.setup-folder", "#setup 的資料夾 %s" % folder)


def skills(r):
    """~/.claude/skills/<name> linked to each skill of the engine, except those whose plugin is turned off here
    (disabled_on): their link is taken away, only when it is a link to this engine's own folder; anything else under
    that name (a folder, a link elsewhere) is left alone and said (plugin.link_skills; the sync makes the missing ones
    after an update). A plugin's skill whose place here cannot be read is neither made nor taken away, and said
    (C-11)."""
    got = plugin.link_skills(ENGINE)
    names, off, made, removed = got["names"], got["off"], got["made"], got["removed"]
    for link in got["taken"]:
        r.say("todo", "core.skills", "%s 是一般檔案或資料夾，不是連結：搬走它（mv %s %s.mine）再重跑" % (link, link, link))
    for link in got["kept"]:
        r.say("skip", "core.skills-off", "%s 的插件在這台關掉了，但它不是連到這份引擎的連結，沒有動：不要的話自己移走" % link)
    if got["waiting"]:  # C-11: its plugin may be turned off here; not made, and nothing taken away
        r.say("skip", "core.skills-unknown", "不知道這幾個 skill 的插件在這台開不開，先沒建：%s；弄清楚之後再重跑 install.sh" % "；".join(
            "%s（%s）" % (n, got["why"].get(n, "原因不明")) for n in got["waiting"]))
    if not got["taken"]:
        on = [n for n in names if n not in off and n not in got["waiting"]]
        r.say("changed" if made or removed else "ok", "core.skills", "skill 連結：%s%s" % (
            "、".join(on) or "（沒有）", "；關掉的：%s%s" % ("、".join(sorted(off & set(names))), "（拿掉了 %s）" % "、".join(removed)
                                                         if removed else "") if off & set(names) else ""))


def entry(r):
    paths = [os.path.expanduser("~/.local/bin/%s" % n) for n in ("altention", "alt")]
    look = lambda: [os.readlink(p) if os.path.islink(p) else os.path.lexists(p) for p in paths]
    before = look()
    rc, out = r.script("altention --link", [sys.executable, os.path.join(ENGINE, "bin", "altention"), "--link"])
    taken = [p for p in paths if os.path.lexists(p) and not os.path.islink(p)]
    if os.path.islink(paths[0]) and os.path.basename(os.readlink(paths[0])) != "altention":
        taken.insert(0, paths[0])  # a link of another program's under the name altention
    for p in taken:
        r.say("todo", "core.entry", "%s 是別人的檔，altention 指令沒裝上：搬走它（mv %s %s.mine）再重跑" % (p, p, p))
    if taken:
        return
    if rc != 0 or not os.path.islink(paths[0]):
        r.say("skip", "core.entry", "沒有建 altention 指令（%s）；其他照常" % (out.strip().splitlines() or [""])[-1][:120])
    else:
        r.say("changed" if look() != before else "ok", "core.entry", "~/.local/bin/altention")


PATH_MARK = "__altention_path__"


def path_check(r, timeout=10):
    """Whether ~/.local/bin (the altention command, and Claude Code's own) is on the PATH of the user's shell as a
    new terminal starts it (C-13: a zsh with no .zshrc did not have it, and `claude`, `altention` were not found).
    Only said, never written: the rc file is the user's own (manager 2026-10-17). The shell is $SHELL, run as a login,
    interactive one (its rc files read), stdin from /dev/null, for at most `timeout` seconds; what it prints before
    the mark is ignored, and env gives the PATH the same way whatever the shell (fish too). What appears in HOME
    meanwhile (zsh's ~/.zcompdump) is named in the line."""
    if os.environ.get("ALT_NO_PATH_CHECK") == "1":  # apply's run: no rc file of the user's run every time
        r.say("skip", "core.path", "apply 跑的安裝不檢查 PATH（會跑你的 shell 的 rc 檔）；要檢查就在那台的終端機跑 ./install.sh")
        return
    bindir = os.path.expanduser("~/.local/bin")
    shell = os.environ.get("SHELL") or ""
    name = os.path.basename(shell)
    if name == "fish":
        rc, line = "~/.config/fish/config.fish", "fish_add_path $HOME/.local/bin"
    else:
        rc = {"zsh": "~/.zshrc", "bash": "~/.bash_profile" if sys.platform == "darwin" else "~/.bashrc"}.get(name, "~/.profile")
        line = 'export PATH="$HOME/.local/bin:$PATH"'
    fix = "在 %s 加一行 %s，再開一個新的終端機" % (rc, line)
    why = None
    home = os.path.expanduser("~")

    def names():
        try:
            return set(os.listdir(home))
        except OSError:
            return None
    before = names()
    if not shell:
        why = "沒有 SHELL"
    else:
        try:  # into a file, not a pipe: what an rc file starts in the background keeps a pipe open after the shell ends
            with tempfile.TemporaryFile() as out:
                p = subprocess.run([shell, "-l", "-i", "-c", "echo %s; env" % PATH_MARK], stdin=subprocess.DEVNULL,
                                   stdout=out, stderr=subprocess.DEVNULL, timeout=timeout)
                out.seek(0)
                text = out.read().decode("utf-8", "replace")
            after = text.split(PATH_MARK + "\n", 1)
            found = [x[5:] for x in after[1].splitlines() if x.startswith("PATH=")] if len(after) == 2 else []
            if not found:
                why = "%s 沒有印出 PATH（結束碼 %d）" % (shell, p.returncode)
        except subprocess.TimeoutExpired:
            why = "%s 超過 %d 秒沒結束" % (shell, timeout)
        except OSError as e:
            why = "%s 跑不起來（%s）" % (shell, e)
    after = names()
    # what the shell's own rc files made in HOME (zsh's compinit: ~/.zcompdump) is said, never left without a word
    # (manager 2026-10-17); a new terminal makes it too, so it stays
    made = sorted(after - before) if before is not None and after is not None else []
    also = "；檢查 PATH 時 %s 建了 %s" % (name, "、".join("~/" + x for x in made)) if made else ""
    if why:
        r.say("skip", "core.path", "讀不出你的 shell 的 PATH（%s）：在新的終端機打 altention 找不到的話，%s%s" % (why, fix, also))
        return
    dirs = [os.path.normpath(os.path.expanduser(d)) for d in found[-1].split(":") if d]
    if os.path.normpath(bindir) in dirs:
        r.say("ok", "core.path", "%s 在 %s 的 PATH 裡%s" % (bindir, name, also))
    else:
        r.say("todo", "core.path", "%s 不在 %s 的 PATH 裡，終端機打 altention、claude 會找不到：%s%s" % (bindir, name, fix, also))


def links(r, inst):
    """Global notes and memory, through the sync's own code (so the first install has them, not the next round)."""
    target = os.path.expanduser("~/.claude/CLAUDE.md")
    before = _read(target)
    said = []
    out = sync.repair_links(inst, said.append, ssh=False)
    for line in said:
        r.note("links: %s" % line)
    g = out.get("global_md")
    if g == "own":
        r.say("todo", "core.global", "%s 是你自己的檔：把內容搬進 %s，再刪掉它（rm %s）後重跑"
              % (target, os.path.join(inst, "CLAUDE.global.md"), target))
    elif isinstance(g, str) and g.startswith("error"):
        r.say("fail", "core.global", "組不出全域說明（%s）" % g[6:].strip())
    elif g is None:
        r.say("skip", "core.global", "引擎和實例都沒有 CLAUDE.global.md")
    else:
        r.say("changed" if _read(target) != before else "ok", "core.global", "全域說明 %s（%s）" % (target, g))
    r.say("changed" if any("記憶" in x for x in said) else "ok", "core.memory", "記憶資料夾指到 %s" % os.path.join(inst, "memory"))
    try:  # the engine's knowledge/ and memory/ too, as the sync makes them, so doctor finds them on the first run
        moved = []
        states, _twice = sync.adopt_engine_paths(inst, moved.append)
    except Exception as e:
        r.say("unknown", "core.engine-links", "引擎裡指到實例的連結沒建好（%s: %s）；下一次同步（15 分鐘內）會再建" % (type(e).__name__, e))
        return
    for line in moved:
        r.note("links: %s" % line)
    linked = sorted(k for k, v in states.items() if v == "link")
    r.say("changed" if moved else "ok", "core.engine-links",
          "引擎裡指到實例的：%s" % ("、".join(linked) or "沒有"))


def _read(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def ssh(r, inst):
    """~/.ssh/config Includes the instance's ssh_config: only with more than one machine and an ssh_config. A backup
    first (read back), the file's mode kept, every line but the Include byte for byte as it was (else put back)."""
    try:
        machines = config.inventory().get("machines") or {}
    except Exception as e:
        r.say("unknown", "core.ssh", "機器清單讀不出來（%s），沒有動 ~/.ssh" % e)
        return
    sshc = os.path.join(inst, "ssh_config")
    if len(machines) <= 1:
        r.say("skip", "core.ssh", "單機：不碰 ~/.ssh")
        return
    if not (os.path.isfile(sshc) and os.path.getsize(sshc) > 0):
        r.say("skip", "core.ssh", "實例沒有 ssh_config：不碰 ~/.ssh")
        return
    cfg = os.path.realpath(os.path.expanduser("~/.ssh/config"))  # a link stays a link: written where it points
    line = "Include %s" % sshc
    before = _read(cfg)
    if before is not None:
        try:
            text = before.decode("utf-8")
        except UnicodeDecodeError:
            r.say("todo", "core.ssh", "%s 不是 UTF-8，沒有動：自己在最前面加一行「%s」" % (cfg, line))
            return
        rows = text.split("\n")
        if line in rows:
            r.say("ok", "core.ssh", "~/.ssh/config 已經引用 %s" % sshc)
            return
        new = "%s\n\n%s" % (line, text)
        if not new.endswith("\n"):
            new += "\n"
        mode = os.stat(cfg).st_mode & 0o777
    else:
        new, mode = line + "\n", 0o600
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = os.path.join(r.folder, "backup", stamp)
    if before is not None:  # backed up and read back before anything is written
        os.makedirs(backup, mode=0o700, exist_ok=True)
        saved = os.path.join(backup, "ssh_config")
        fd = os.open(saved, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "wb") as f:
            f.write(before)
        if _read(saved) != before:
            r.say("fail", "core.ssh", "備份 ~/.ssh/config 讀回來不一樣，沒有寫")
            return
    if _read(cfg) != before:
        r.say("fail", "core.ssh", "~/.ssh/config 在讀了之後被改過，這次沒寫；再跑一次")
        return
    os.makedirs(os.path.dirname(cfg), mode=0o700, exist_ok=True)
    tmp = cfg + ".altention-tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(new)
    os.chmod(tmp, mode)
    os.replace(tmp, cfg)
    after = _read(cfg)
    keep = lambda b: [x for x in (b or b"").decode("utf-8").split("\n") if x != line and not re.match(
        r"^Include[ \t]+\S*/ops/ssh_config[ \t]*$", x)]
    ok = after == new.encode("utf-8") and (os.stat(cfg).st_mode & 0o777) == mode
    if before is not None:
        ok = ok and [x for x in keep(after) if x] == [x for x in keep(before) if x]
    if not ok:
        if before is None:
            os.remove(cfg)
        else:
            tmp2 = cfg + ".altention-tmp"
            fd = os.open(tmp2, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, mode)
            with os.fdopen(fd, "wb") as f:
                f.write(before)
            os.replace(tmp2, cfg)
        r.say("fail", "core.ssh", "寫完重讀不對，已經放回原樣（%s）" % ("讀回確認過" if _read(cfg) == before else "請看 %s" % backup))
        return
    r.say("changed", "core.ssh", "~/.ssh/config 引用 %s%s" % (sshc, "（備份：%s）" % backup if before is not None else "（新建，600）"))


def plugins_here(r, holds):
    """[(name, folder under the engine)] whose install.sh runs here, and whether the bot runs here; None if unknown."""
    try:  # the settings or the machine list themselves unreadable: nothing is known, nothing installed or written
        settings, listed = config.settings(), config.inventory()
    except Exception as e:
        r.say("unknown", "core.plugins", "實例設定或機器清單讀不出來（%s）：插件一個都不裝、排程不寫；修好再重跑" % e)
        return None
    rows, problems = plugin.on_this_machine()
    for p in problems:
        r.note("plugins: %s" % p)
    try:
        bot_known = config.bot_machine()[0]
    except Exception:
        bot_known = None
    left_out = set()
    for n, _, d, w, why in rows:
        if w != "unknown":
            continue
        entry = plugin.instance_entry(n, settings)
        if (d is not None and d.get("machines") == "one" and not bot_known
                and isinstance(entry, dict) and not entry.get("on")):  # read, and plainly placed nowhere
            r.say("skip", "plugin.%s" % n, "%s 沒有指定在哪台跑（plugins.json 沒寫 plugins.%s.on，也沒有機器人那台）" % (n, n))
        else:  # only this one is left out; the others go on (manager 2026-10-06)
            r.say("unknown", "plugin.%s" % n, "不知道這台要不要跑 %s（%s）：它的安裝和排程都沒動，其他插件照做" % (n, why))
        left_out.add(n)
    grew = True
    while grew:  # a plugin that needs one left out waits with it, however far down (progress reads chatlog's records)
        grew = False
        for n, _, d, w, _ in rows:
            needs = [x.get("plugin") for x in (d or {}).get("requires", []) if isinstance(x, dict)]
            gone = [x for x in needs if x in left_out]
            if w == "on" and gone and n not in left_out:
                r.say("unknown", "plugin.%s" % n, "%s 要用的 %s 不知道，%s 也先不動" % (n, "、".join(gone), n))
                left_out.add(n)
                grew = True
    holds = list(holds) + sorted(left_out)
    try:
        bot, _, _ = config.bot_machine()
        bot_here = bool(bot) and bot == config.this_machine()
    except Exception:
        bot_here = config.bot_here()
    found = []
    for name, folder, d, where, _ in rows:
        if where != "on" or name in holds:
            continue
        rel = os.path.relpath(os.path.realpath(folder), os.path.realpath(ENGINE))
        if not rel.startswith("..") and os.path.isfile(os.path.join(folder, "install.sh")):
            found.append((name, rel))
    if bot_here and "bot" not in holds:
        found.append(("dcbot", "dcbot"))
    plugins_here.held = holds  # what plugins.py apply is told to leave alone too
    plugins_here.on = [n for n, _, _, w, _ in rows if w == "on" and n not in holds]  # core.backup's
    found.sort(key=lambda x: (FIRST.index(x[0]) if x[0] in FIRST else len(FIRST), x[0]))
    return found, bot_here


BACK = (("progress", "progress"), ("planner", "planner"))  # (ops/remote.py's name, its plugin): cloned here
# chatlog's install.sh clones the records itself (it runs in core.install, before this)


def backups(r, on):
    """C-25: on a new machine, the progress repo and the secretary's folder back from the remotes the instance names
    (sync.dirs.<plugin>.remote), so a machine set up again has them at once, not at the first round. Only a folder
    not there at all is made: 700, then cloned into through ops/remote.py clone (checked first: never a public repo,
    the engine's or another kind of data), and given the pre-push hook. A folder already there, even empty, is never touched (manager
    2026-10-17): its plugin attaches it. A clone refused or failed leaves nothing: the folder it made is removed."""
    sys.path.insert(0, HERE)
    import remote
    for name, plug in BACK:
        if plug not in on:
            continue
        url = config.setting("sync.dirs.%s.remote" % plug)
        url = url.strip() if isinstance(url, str) else ""
        if not url:
            continue
        label = remote.LABELS[name]
        if name == "planner" and isinstance(config.setting("planner.folder"), str) and config.setting("planner.folder").strip():
            r.say("ok", "core.backup", "%s用 planner.folder 指定的資料夾，不從備份 clone" % label)
            continue
        folder = remote.folder_of(name)
        if os.path.lexists(folder):
            r.say("ok", "core.backup", "%s：%s 已經有了，不從備份 clone" % (label, folder))
            continue
        os.makedirs(os.path.dirname(folder), mode=0o700, exist_ok=True)
        os.mkdir(folder, 0o700)
        os.chmod(folder, 0o700)  # before anything is cloned into it (the umask may take bits off, never adds them)
        ok, text = remote.clone(name, url, folder)
        if not ok:
            shutil.rmtree(folder, ignore_errors=True)  # made just now, by this step: whatever a failed clone left
            r.say("todo", "core.backup", "%s：%s。改好實例的 sync.dirs.%s.remote（python3 ops/remote.py set %s <private repo 網址> "
                  "--replace）再重跑；不改的話%s會在這台重新開始" % (label, text, plug, name, label))
            continue
        branch, have = remote.checkout_backup(folder)  # clone() checked it out already: what it found, for the line
        if name == "planner":  # as remind --secretary makes one it cloned: the .gitignore, main, what is not tracked
            import importlib.util
            spec = importlib.util.spec_from_file_location("remind_install", os.path.join(ENGINE, "planner", "remind.py"))
            remind = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(remind)
            remind.make_repo(folder, cloned=True)
        elif branch != "main":  # on main, as progress makes the repo it clones (`checkout -B main`) and pushes there
            subprocess.run(["git", "-C", folder, "checkout", "-q", "-B", "main"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        hooked, hook = remote.put_hook(folder)  # before anything pushes from it (the sync takes on only a repo with it)
        if branch and branch != "main" and name in remote.ON_MAIN:
            said = "從備份 clone 回來：%s → %s（700；遠端原本的分支是 %s，之後推到 main）" % (url, folder, branch)
        elif branch:
            said = "從備份 clone 回來：%s → %s（700）" % (url, folder)
        elif have:
            said = "從 %s clone 了，但認不出哪個分支是備份（有 %s，沒有 main）：%s（700）先從空的 main 開始，遠端的分支沒動" % (
                url, "、".join(have), folder)
        else:
            said = "從 %s clone 了，但裡面沒有內容（空的 repo）：%s（700），從這台開始" % (url, folder)
        r.say("changed" if hooked else "todo", "core.backup", "%s%s%s" % (
            label, said, "" if hooked else "；%s：在這台跑 python3 ops/remote.py hook %s" % (hook, name)))


def install_plugins(r, found):
    env = dict(os.environ, CHATLOG="0")
    for name, rel in found:
        rc, out = r.script("%s/install.sh" % rel, [os.path.join(ENGINE, rel, "install.sh")], env)
        mark = "%s %s" % (MARK, rel)
        ident = "plugin.%s" % re.sub(r"[^a-z0-9.-]", "-", name.lower())
        if rc != 0:
            tail = [x.strip() for x in out.splitlines() if x.strip()][-3:]  # a pipe's last word alone hides the cause
            r.say("fail", ident, "%s/install.sh 失敗（結束碼 %s）：%s；完整輸出看 %s"
                  % (rel, rc, " / ".join(x[:120] for x in tail) or "沒有輸出", r.log_path))
            return False
        if mark not in out.splitlines():
            r.say("fail", ident, "%s/install.sh 結束碼 0，但沒看到它跑完的記號：可能被別的同名指令頂替了" % rel)
            return False
        r.say("ok", ident, "%s 的程式裝好了" % name)
        if name == "dcbot" and "supervisor 被人停著" in out:
            r.say("todo", "dcbot.stopped", "supervisor 被人停著，沒有啟動：要啟動請照 dcbot/SUPERVISOR-RESCUE.md 刪掉 %s"
                  % os.path.join(ENGINE, "dcbot", "state", "stop"))
    return True


def write_schedules(r, holds, allow, moved=()):
    if shutil.which("crontab") is None:  # minimal systems and containers: said as the user's to do, not a log to read
        from common import INSTALL_CRON
        r.say("todo", "core.apply", "沒有 crontab 指令，排程寫不進去：%s，再重跑 install.sh" % INSTALL_CRON)
        return
    argv = [sys.executable, os.path.join(HERE, "plugins.py"), "apply", "--write"]
    for h in holds:
        argv += ["--hold", h]
    for a in allow:
        argv += ["--allow-time", a]
    for m in moved:
        argv += ["--moved-from", m]
    rc, out = r.script("plugins.py apply --write", argv)
    lines = out.splitlines()
    if rc != 0:
        why = [x for x in lines if "問題" in x or "沒寫" in x or "什麼都不寫" in x] or lines[-1:] or [""]
        r.say("fail", "core.apply", "plugins.py apply 失敗（結束碼 %s）：%s" % (rc, why[-1][:160]))
        return
    if "%s plugins-apply" % MARK not in lines:
        r.say("fail", "core.apply", "plugins.py apply 結束碼 0，但沒看到它跑完的記號：可能被別的同名指令頂替了")
        return
    changes = [x for x in lines if CHANGED.match(x)]
    r.say("changed" if changes else "ok", "core.apply", "排程、hook、設定：%s" % ("改了 %d 項（見 log）" % len(changes) if changes else "沒有要改的"))
    from common import START_CRON, cron_running
    if cron_running() is False:  # WSL, containers: the lines are in, nothing will run them (manager 2026-10-15)
        r.say("todo", "core.cron-service", "cron 服務沒在跑，排程寫進去了也不會跑：%s，再重跑 install.sh" % START_CRON)
    for x in lines:
        if x.startswith("不動 ") or x.startswith("- 先不動"):
            r.note("apply: %s" % x)


def token(r, bot_here):
    path = os.path.expanduser(TOKEN_FILE)
    if not bot_here:
        try:
            anywhere = config.bot_machine()[0]
        except Exception:
            anywhere = "?"  # the list cannot be read: core.plugins has said so
        if anywhere is None:  # no bot on any machine yet: the install is not done until the user gives it one
            r.say("todo", "dcbot.token", "還沒接 Discord：在你自己的終端機跑 %s discord（會問機器人的 token，其他的自己查），再重跑 install.sh"
                  % os.path.join(ENGINE, "install.sh"))
        return
    try:  # whether it is there and not empty: never opened, never read
        there = os.path.getsize(path) > 0
    except OSError:
        there = False
    if there:
        r.say("ok", "dcbot.token", "機器人的設定檔在")
    else:
        r.say("todo", "dcbot.token", "缺機器人的 token（%s 不在或是空的）：在你自己的終端機跑 %s discord，再重跑 install.sh"
              % (path, os.path.join(ENGINE, "install.sh")))


TOKEN_FILE = "~/.config/dcbot/env"  # the bot's settings (dcbot/bot/envfile.py), as config.bot_here() looks for them
TOKEN_KEYS = ("DISCORD_TOKEN", "OWNER_ID", "GUILD_ID")
DISCORD_ID = re.compile(r"^[0-9]{17,20}$")


def token_key(line):
    """The key a line of the settings file sets, "export " in front or not ("" for a comment or no key): a key being
    replaced goes in every spelling, so no old token stays behind in the file (#code-review 2026-10-14)."""
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    return key[len("export"):].strip() if key.startswith("export") and key[len("export"):][:1].isspace() else key


TOKEN_PROMPT = "token（貼上後按 Enter，不會顯示）："  # getpass prints it only after it has turned the echo off


def ask_discord(secret=None, line=None, interactive=None, base=None, sleep=time.sleep, clock=time.monotonic):
    """install.sh discord: the bot's token typed on this machine's keyboard (never shown, never passed through
    Claude: from anything but a terminal it refuses, and Claude's Bash has none); the rest asked of Discord with
    it: the application, its owner, the server the bot was added to by the link printed here. Written to the bot's
    settings file (600) only once all three are known; the file's other lines are kept. 0 written or left as it
    was, 1 refused or stopped. `base` is for the tests (a fake Discord on this machine); there is no other way to
    point it elsewhere, so the token cannot be sent anywhere but Discord."""
    import getpass
    import discordapp as dapp
    secret = secret or (lambda prompt: getpass.getpass(prompt))
    line = line or input
    interactive = sys.stdin.isatty() if interactive is None else interactive
    base = base or dapp.API
    path = os.path.expanduser(TOKEN_FILE)
    again = "重跑 %s discord" % os.path.join(ENGINE, "install.sh")
    if not interactive:
        print("fail dcbot.token 要在你自己的終端機跑 install.sh discord（token 只從鍵盤讀，不經過 Claude 或管線）；什麼都沒寫")
        return 1
    try:
        with open(path, encoding="utf-8") as f:
            kept = [x for x in f.read().splitlines() if token_key(x) not in TOKEN_KEYS]
        there = True
    except FileNotFoundError:
        kept, there = [], False
    except (OSError, UnicodeDecodeError) as e:
        print("fail dcbot.token %s 讀不出來（%s）：修好或移走它再重跑；什麼都沒寫" % (path, getattr(e, "strerror", None) or e))
        return 1

    def ask_app(tok):
        app = dapp.get(tok, "/oauth2/applications/@me", base, sleep)
        while dapp.problems(app):
            print("應用程式的設定還要改（https://discord.com/developers/applications/%s）：" % app.get("id", ""))
            for x in dapp.problems(app):
                print("  - %s" % x)
            if line("改好後按 Enter 再查一次（輸入 q 結束，什麼都不寫）：").strip().lower() == "q":
                return None
            app = dapp.get(tok, "/oauth2/applications/@me", base, sleep)
        return app

    try:
        if there and line("%s 已經有了，要換掉 token 和 ID 嗎？[y/N] " % path).strip().lower() not in ("y", "yes"):
            print("ok dcbot.token 沒有改 %s" % path)
            return 0
        print("接 Discord：接下來會問你機器人的 token（打字時看不到），其他的這個指令會自己查。")
        print("先到 https://discord.com/developers/applications → 你的應用程式（還沒有就按 新建應用程式（New Application））→ 機器人（Bot）頁：")
        print("  1. Privileged Gateway Intents 底下打開 Message Content Intent，按 儲存變更（Save Changes）")
        print("  2. 公開機器人（Public Bot）關掉，按 儲存變更（Save Changes）（Discord 不讓關的話：先到 安裝（Installation）頁"
              "把 安裝連結（Install Link）改成 無（None）、存檔，再回來關）")
        print("  3. 最後按 重設權杖（Reset Token），複製新的 token，馬上貼到下面")
        for tries in range(3):
            tok = secret(TOKEN_PROMPT).strip()
            if not tok:
                print("沒有讀到任何字（貼上可能沒成功）")
                continue
            if any(c.isspace() for c in tok) or tok.count(".") != 2:
                # The input is not shown, so say what was read without showing it: its length, dots and spaces only.
                spaces = "、中間有空白" if any(c.isspace() for c in tok) else ""
                print("這不像機器人的 token：讀到 %d 個字、%d 個點%s（應該是兩個點隔開的三段，中間沒有空白）"
                      % (len(tok), tok.count("."), spaces))
                continue
            try:
                app = ask_app(tok)
            except dapp.BadToken:
                print("Discord 說這個 token 不對（可能只複製到一部分，或之後又按了一次 重設權杖（Reset Token））：再按一次 重設權杖（Reset Token），貼新的")
                continue
            break
        else:
            print("fail dcbot.token 三次都沒拿到能用的 token；什麼都沒寫，%s" % again)
            return 1
        if app is None:
            print("todo dcbot.token 停了，什麼都沒寫；改好設定後%s" % again)
            return 1
        print("應用程式：%s（ID %s）" % (app.get("name", ""), app.get("id", "")))
        people = [p for p in dapp.owner_choices(app) if DISCORD_ID.match(p[0])]
        if not people:
            print("fail dcbot.token Discord 沒有說這個應用程式屬於誰；什麼都沒寫，把這段輸出給維護的人看")
            return 1
        owner = people[0]
        if app.get("team") and len(people) > 1:  # the app's owner is then the team itself: ask who is the user
            print("這個應用程式屬於一個團隊，機器人只聽一個人的。哪一個是你？")
            for i, (uid, name, top) in enumerate(people, 1):
                print("  %d. %s%s" % (i, name or uid, "（團隊擁有者）" if top else ""))
            while True:
                pick = line("輸入號碼（直接按 Enter 是 1）：").strip() or "1"
                if pick.isdigit() and 1 <= int(pick) <= len(people):
                    owner = people[int(pick) - 1]
                    break
                print("要 1 到 %d 的號碼" % len(people))
        print("機器人只聽：%s" % (owner[1] or owner[0]))
        last = [""]  # the temporary trouble said last, so a long one is said once, not every 3 seconds

        def look():
            """The bot's servers; while waiting, a network error, a 5xx or a timeout is said and waited out (only
            401, 403, a redirect and q end it: manager 2026-10-16)."""
            try:
                got = dapp.guilds(tok, base, sleep)
            except dapp.Unreachable as e:
                if str(e) != last[0]:
                    print("暫時連不上 Discord（%s），繼續等" % e)
                    last[0] = str(e)
                return []
            if last[0]:
                print("又連上 Discord 了")
                last[0] = ""
            return got
        found = look()
        if not found:
            print("用瀏覽器打開下面的連結，選你的伺服器（最好是新的、空的；你要有那個伺服器的「管理伺服器」權限），"
                  "按 繼續（Continue）、授權（Authorize）：")
            print(dapp.invite_url(app["id"]))
            while not found:
                print("等機器人進伺服器（最多 %d 分鐘；Ctrl-C 停止）…" % (dapp.WAIT // 60))
                end = clock() + dapp.WAIT
                while clock() < end:
                    sleep(dapp.POLL)
                    found = look()
                    if found:
                        break
                if not found and line("%d 分鐘了，機器人還沒進任何伺服器。連結打開了嗎？按 Enter 再等，輸入 q 結束"
                                      "（什麼都不寫）：" % (dapp.WAIT // 60)).strip().lower() == "q":
                    print("todo dcbot.token 停了，什麼都沒寫；%s" % again)
                    return 1
        guild = found[0]
        if len(found) > 1:
            print("機器人在這幾個伺服器裡，要用哪一個？")
            for i, (gid, name) in enumerate(found, 1):
                print("  %d. %s" % (i, name or gid))
            while True:
                pick = line("輸入號碼：").strip()
                if pick.isdigit() and 1 <= int(pick) <= len(found):
                    guild = found[int(pick) - 1]
                    break
                print("要 1 到 %d 的號碼" % len(found))
        if not DISCORD_ID.match(guild[0]):
            print("fail dcbot.token Discord 給的伺服器 ID 看不懂；什麼都沒寫，把這段輸出給維護的人看")
            return 1
        print("伺服器：%s" % (guild[1] or guild[0]))
    except dapp.BadToken:
        print("\nfail dcbot.token Discord 中途說 token 不對了（又按了 重設權杖（Reset Token）？）；什麼都沒寫，%s" % again)
        return 1
    except dapp.Unreachable as e:
        print("\nfail dcbot.token 連不上 Discord（%s）；什麼都沒寫，網路好了再%s" % (e, again))
        return 1
    except dapp.Refused as e:
        print("\nfail dcbot.token %s；什麼都沒寫，%s" % (e, again))
        return 1
    except (EOFError, KeyboardInterrupt):
        print("\nfail dcbot.token 中途停了；什麼都沒寫")
        return 1
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    mode = stat.S_IMODE(os.stat(folder).st_mode)
    if mode & 0o077:  # a folder made before (755): tightened to the owner's bits only, never loosened
        os.chmod(folder, mode & 0o700)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(["DISCORD_TOKEN=%s" % tok, "OWNER_ID=%s" % owner[0], "GUILD_ID=%s" % guild[0]] + kept) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)
    print("changed dcbot.token 寫好了 %s（600）" % path)
    return 0


LOG_SHOWN = "~/.local/state/altention/install/last.log"  # as the user is told it (Run makes it from HOME at call time)


def git_user(r):
    p = subprocess.run(["git", "config", "user.email"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                       universal_newlines=True)
    if p.stdout.strip():
        r.say("ok", "core.git-user", "git 的使用者已設定")
    else:
        r.say("todo", "core.git-user", "git 還沒設定使用者：git config --global user.name 你的名字 和 git config --global user.email 你的信箱")


class Steps:
    """Each step run once, in order: one that cannot run because a step it needs did not go through is said as skip
    with the reason (never left out without a word); a step that throws is a fail of its own, in words a person can
    act on (a folder that cannot be written to), never the exception's text alone (#code-review-2 2026-10-06)."""

    def __init__(self, r):
        self.r, self.failed = r, []

    def run(self, ident, fn, *args, needs=()):
        missing = [n for n in needs if n in self.failed]
        if missing:
            self.r.say("skip", ident, "沒做：前面的 %s 沒成功" % "、".join(missing))
            self.failed.append(ident)
            return None
        before = len(self.r.seen)
        try:
            out = fn(*args)
        except OSError as e:
            where = e.filename or "（不明的位置）"
            self.r.say("fail", ident, "寫不進 %s（%s）：檢查它的權限，或那裡是不是放了一個檔案而不是資料夾，修好再重跑"
                       % (where, e.strerror or e))
            self.r.note("%s: %r" % (ident, e))
            self.failed.append(ident)
            return None
        except Exception as e:  # a bug of ours: one line here, the traceback in the log
            import traceback
            self.r.note(traceback.format_exc())
            self.r.say("fail", ident, "程式出錯（%s），詳見 log" % type(e).__name__)
            self.failed.append(ident)
            return None
        # a plugin that cannot be placed is said (unknown plugin.x) and left out; the step itself went through
        if any(st == "fail" or (st == "unknown" and not i.startswith("plugin.")) for st, i in self.r.said[before:]):
            self.failed.append(ident)
        return out


def main(argv):
    if list(argv) == ["discord"]:  # nothing else: the bot's token typed in, the rest asked of Discord
        rc = ask_discord()
        if rc:  # stopped or refused: said why, nothing written, no install
            return rc
        # then the whole install at once (owner 2026-10-16: one command, Claude waits for it to finish)
        print("接著安裝這台需要的全部，跑完最後一行是「安裝跑完了」或「安裝沒跑完」")
        sys.stdout.flush()
        return install([], after_discord=True)
    return install(argv)


def install(argv, after_discord=False):
    def stopped(line):  # before the log is made: after install.sh discord, a last line as done() would say it too
        print(line)
        if after_discord:  # the user waits for 安裝跑完了/安裝沒跑完 (#code-review of 93e8008)
            print("fail core.done 安裝沒跑完：%s 沒成功（見上一行；%s 不是這次的）" % (line.split()[1], LOG_SHOWN))
        return 1
    light, holds, allow, moved = False, [], [], []
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--links":
            light = True
        elif a == "--hold" and args and not args[0].startswith("-"):
            holds.append(args.pop(0))
        elif a == "--allow-time" and args and not args[0].startswith("-"):
            allow.append(args.pop(0))
        elif a == "--moved-from" and args and not args[0].startswith("-"):
            moved.append(args.pop(0))  # an old engine folder that is gone (C-17): passed on to plugins.py apply
        else:
            print("fail core.args 不認得 %s（只有 discord、--links、--hold 插件、--allow-time 插件/項目、--moved-from 舊引擎資料夾），什麼都沒做" % a)
            return 1
    try:  # a named instance that is not there is said as such, before the engine check talks of machine lists
        config.instance_dir()
    except config.ConfigError as e:
        return stopped("fail core.instance %s；改正 ALT_INSTANCE 或 %s 再重跑" % (e, os.path.join(config.config_dir(), "instance")))
    from altention import engine_id
    why = engine_id.refusal(ENGINE, "安裝")
    if why:  # before anything is made, the log included (code-review 2026-10-06: a worktree or a second clone)
        return stopped("fail core.engine %s" % why)
    folder = os.path.join(os.path.expanduser("~/.local/state/altention"), "install")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    lock = open(os.path.join(folder, "lock"), "w")
    try:  # the lock before the log: a second run must not empty the log of the one running
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return stopped("fail core.lock 另一個安裝正在跑；等它結束再跑")
    r = Run()
    st = Steps(r)
    st.run("core.pointer", lambda: engine_id.write_pointer(ENGINE))  # where this machine's engine is, for the supervisor
    inst = st.run("core.instance", find_instance, r, light)
    if not inst and "core.instance" not in st.failed:
        st.failed.append("core.instance")  # a todo or a skip: nothing that needs the instance can run
    st.run("core.skills", skills, r)
    st.run("core.entry", entry, r)
    st.run("core.path", path_check, r)
    st.run("core.links", links, r, inst, needs=["core.instance"])
    if light:
        done(r, st, "輕的部分")
        return r.code()
    st.run("core.needs", needs, r)
    st.run("core.ssh", ssh, r, inst, needs=["core.instance"])
    if inst:
        hold_until_setup(r, holds)
    here = st.run("core.plugins", plugins_here, r, holds, needs=["core.instance"])
    found, bot_here = here if here else ([], False)
    ok = st.run("core.install", install_plugins, r, found, needs=["core.instance", "core.plugins"])
    if ok is False:
        st.failed.append("core.install")
    st.run("core.backup", backups, r, getattr(plugins_here, "on", ()), needs=["core.instance", "core.plugins", "core.install"])
    st.run("core.apply", write_schedules, r, getattr(plugins_here, "held", holds), allow, moved,
           needs=["core.instance", "core.plugins", "core.install"])
    st.run("dcbot.token", token, r, bot_here, needs=["core.instance", "core.plugins"])
    st.run("core.setup-folder", setup_folder, r, bot_here, needs=["core.instance", "core.plugins"])
    st.run("core.git-user", git_user, r)
    r.say("ok", "core.log", "詳細輸出：%s" % r.log_path)
    done(r, st, "安裝")
    return r.code()


def done(r, st, what):
    """The last line: ok core.done only when everything went through or was left to the user (ops/apply.py looks
    for it); else which steps it stopped at (#code-review-2 2026-10-06)."""
    stopped = [i for st_, i in r.said if st_ in ("fail", "unknown")]
    if r.code() == 1:
        r.say("fail", "core.done", "%s沒跑完：%s 沒成功或不知道（見上面那幾行）" % (what, "、".join(dict.fromkeys(stopped)) or "有一步"))
    else:  # "ok core.done " stays the start of the line: ops/apply.py waits for it
        left = [i for st_, i in r.said if st_ == "todo"]
        r.say("ok", "core.done", "%s跑完了%s%s" % (what, "，但還有要你做的：%s（照上面那幾行做完再重跑）" % "、".join(dict.fromkeys(left))
                                                if left else "", "：到 Discord 的 #setup 把設定做完" if setup_pending() else ""))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
