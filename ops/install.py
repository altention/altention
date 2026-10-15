#!/usr/bin/env python3
"""The one installer (stage 3a.9), run by install.sh once it has found a python3 of 3.8 or later and git.

  ./install.sh                         # everything this machine needs, from the instance's settings
  ./install.sh --links                 # only the light part: skills, the altention command, global notes, memory
  ./install.sh --hold progress --allow-time chatlog/sync   # passed on by ops/apply.py (see plugins.py apply)
  ./install.sh --token                 # the bot's token and IDs, typed on this machine's keyboard (never via Claude)

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
scripts behind the steps printed goes to ~/.local/state/altention/install/last.log (600), never the environment.

ALT_NO_START=1: nothing that keeps running is started (the supervisor). ops/apply.py does not set it.
"""
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
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
        print("%s %s %s" % (status, ident, " ".join(str(text).split())))
        sys.stdout.flush()

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
    rc, out = r.script("instance init", [sys.executable, os.path.join(HERE, "instance.py"), "init"])
    inst = config.instance_dir() if rc == 0 else None
    if not inst:
        r.say("fail", "core.instance", "建不出本機實例（%s）" % (out.strip().splitlines() or ["沒有說明"])[-1])
        return None
    r.say("changed", "core.instance", "建了本機實例 %s（沒有遠端）" % inst)
    return inst


def skills(r):
    """~/.claude/skills/<name> linked to each skill of the engine, except those whose plugin is turned off here
    (disabled_on): their link is taken away, only when it is a link to this engine's own folder; anything else under
    that name (a folder, a link elsewhere) is left alone and said."""
    home_skills = os.path.expanduser("~/.claude/skills")
    folder = os.path.join(ENGINE, "skills")
    names = sorted(d for d in os.listdir(folder) if os.path.isdir(os.path.join(folder, d))) if os.path.isdir(folder) else []
    off = plugin.skills_off(engine=ENGINE)
    made, taken, removed, kept = [], [], [], []
    for name in names:
        src, link = os.path.join(folder, name), os.path.join(home_skills, name)
        if name in off:
            if os.path.islink(link) and os.path.realpath(link) == os.path.realpath(src):
                os.remove(link)
                removed.append(name)
            elif os.path.lexists(link):
                kept.append(link)
            continue
        if os.path.islink(link):
            if os.readlink(link) != src:
                os.remove(link)
                os.symlink(src, link)
                made.append(name)
        elif os.path.lexists(link):
            taken.append(link)
        else:
            os.makedirs(home_skills, exist_ok=True)
            os.symlink(src, link)
            made.append(name)
    for link in taken:
        r.say("todo", "core.skills", "%s 是一般檔案或資料夾，不是連結：搬走它（mv %s %s.mine）再重跑" % (link, link, link))
    for link in kept:
        r.say("skip", "core.skills-off", "%s 的插件在這台關掉了，但它不是連到這份引擎的連結，沒有動：不要的話自己移走" % link)
    if not taken:
        on = [n for n in names if n not in off]
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
    found.sort(key=lambda x: (FIRST.index(x[0]) if x[0] in FIRST else len(FIRST), x[0]))
    return found, bot_here


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


def write_schedules(r, holds, allow):
    if shutil.which("crontab") is None:  # minimal systems and containers: said as the user's to do, not a log to read
        from common import INSTALL_CRON
        r.say("todo", "core.apply", "沒有 crontab 指令，排程寫不進去：%s，再重跑 install.sh" % INSTALL_CRON)
        return
    argv = [sys.executable, os.path.join(HERE, "plugins.py"), "apply", "--write"]
    for h in holds:
        argv += ["--hold", h]
    for a in allow:
        argv += ["--allow-time", a]
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
            r.say("todo", "dcbot.token", "還沒有機器人的設定：在你自己的終端機跑 %s --token 輸入 token 和兩個 ID，再重跑 install.sh"
                  % os.path.join(ENGINE, "install.sh"))
        return
    try:  # whether it is there and not empty: never opened, never read
        there = os.path.getsize(path) > 0
    except OSError:
        there = False
    if there:
        r.say("ok", "dcbot.token", "機器人的設定檔在")
    else:
        r.say("todo", "dcbot.token", "缺機器人的 token：把 DISCORD_TOKEN、OWNER_ID、GUILD_ID 寫進 %s（chmod 600），再重跑" % path)


TOKEN_FILE = "~/.config/dcbot/env"  # the bot's settings (dcbot/bot/envfile.py), as config.bot_here() looks for them
TOKEN_KEYS = ("DISCORD_TOKEN", "OWNER_ID", "GUILD_ID")
DISCORD_ID = re.compile(r"^[0-9]{17,20}$")


def token_key(line):
    """The key a line of the settings file sets, "export " in front or not ("" for a comment or no key): a key being
    replaced goes in every spelling, so no old token stays behind in the file (#code-review 2026-10-14)."""
    key = line.split("=", 1)[0].strip() if "=" in line else ""
    return key[len("export"):].strip() if key.startswith("export") and key[len("export"):][:1].isspace() else key


def ask_token(secret=None, line=None, interactive=None):
    """install.sh --token: the bot's token and the two IDs, typed on this machine's keyboard and written to the
    bot's settings file (600), never shown and never passed through Claude: from anything but a terminal it
    refuses (Claude's Bash has none). The file's other lines are kept. 0 written or left as it was, 1 refused."""
    import getpass
    secret = secret or (lambda prompt: getpass.getpass(prompt))
    line = line or input
    interactive = sys.stdin.isatty() if interactive is None else interactive
    path = os.path.expanduser(TOKEN_FILE)
    if not interactive:
        print("fail dcbot.token 要在你自己的終端機跑 install.sh --token（token 只從鍵盤讀，不經過 Claude 或管線）；什麼都沒寫")
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
    try:
        if there and line("%s 已經有了，要換掉 token 和 ID 嗎？[y/N] " % path).strip().lower() not in ("y", "yes"):
            print("ok dcbot.token 沒有改 %s" % path)
            return 0
        print("Discord Developer Portal → 你的應用程式 → Bot → Reset Token，複製後貼上（不會顯示）。")
        tok = secret("DISCORD_TOKEN: ").strip()
        if not tok:
            print("fail dcbot.token 沒有讀到任何字（貼上可能沒成功）；什麼都沒寫，重跑 install.sh --token")
            return 1
        if any(c.isspace() for c in tok) or tok.count(".") != 2:
            # The input is not shown, so say what was read without showing it: its length, dots and spaces only.
            spaces = "、中間有空白" if any(c.isspace() for c in tok) else ""
            print("fail dcbot.token 這不像機器人的 token：讀到 %d 個字、%d 個點%s（應該是兩個點隔開的三段，中間沒有空白）；"
                  "什麼都沒寫，重跑 install.sh --token" % (len(tok), tok.count("."), spaces))
            return 1
        print("Discord 設定 → 進階 → 開啟開發者模式；右鍵你自己的名字 → 複製使用者 ID，右鍵伺服器 → 複製伺服器 ID。")
        ids = []
        for key, what in (("OWNER_ID", "你的使用者 ID"), ("GUILD_ID", "伺服器 ID")):
            v = line("%s（%s）: " % (key, what)).strip()
            if not DISCORD_ID.match(v):
                print("fail dcbot.token %s 應該是 17 到 20 位數字；什麼都沒寫，重跑 install.sh --token" % key)
                return 1
            ids.append((key, v))
    except (EOFError, KeyboardInterrupt):
        print("\nfail dcbot.token 中途停了；什麼都沒寫")
        return 1
    folder = os.path.dirname(path)
    os.makedirs(folder, mode=0o700, exist_ok=True)
    tmp = "%s.tmp-%d" % (path, os.getpid())
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write("\n".join(["DISCORD_TOKEN=%s" % tok] + ["%s=%s" % kv for kv in ids] + kept) + "\n")
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.lexists(tmp):
            os.remove(tmp)
    print("changed dcbot.token 寫好了 %s（600）；接著跑 %s" % (path, os.path.join(ENGINE, "install.sh")))
    return 0


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
    if list(argv) == ["--token"]:  # nothing else: the bot's settings typed in, before or after the install
        return ask_token()
    light, holds, allow = False, [], []
    args = list(argv)
    while args:
        a = args.pop(0)
        if a == "--links":
            light = True
        elif a == "--hold" and args and not args[0].startswith("-"):
            holds.append(args.pop(0))
        elif a == "--allow-time" and args and not args[0].startswith("-"):
            allow.append(args.pop(0))
        else:
            print("fail core.args 不認得 %s（只有 --links、--token、--hold 插件、--allow-time 插件/項目），什麼都沒做" % a)
            return 1
    try:  # a named instance that is not there is said as such, before the engine check talks of machine lists
        config.instance_dir()
    except config.ConfigError as e:
        print("fail core.instance %s；改正 ALT_INSTANCE 或 %s 再重跑" % (e, os.path.join(config.config_dir(), "instance")))
        return 1
    from altention import engine_id
    why = engine_id.refusal(ENGINE, "安裝")
    if why:  # before anything is made, the log included (code-review 2026-10-06: a worktree or a second clone)
        print("fail core.engine %s" % why)
        return 1
    folder = os.path.join(os.path.expanduser("~/.local/state/altention"), "install")
    os.makedirs(folder, mode=0o700, exist_ok=True)
    lock = open(os.path.join(folder, "lock"), "w")
    try:  # the lock before the log: a second run must not empty the log of the one running
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        print("fail core.lock 另一個安裝正在跑；等它結束再跑")
        return 1
    r = Run()
    st = Steps(r)
    st.run("core.pointer", lambda: engine_id.write_pointer(ENGINE))  # where this machine's engine is, for the supervisor
    inst = st.run("core.instance", find_instance, r, light)
    if not inst and "core.instance" not in st.failed:
        st.failed.append("core.instance")  # a todo or a skip: nothing that needs the instance can run
    st.run("core.skills", skills, r)
    st.run("core.entry", entry, r)
    st.run("core.links", links, r, inst, needs=["core.instance"])
    if light:
        done(r, st, "輕的部分")
        return r.code()
    st.run("core.ssh", ssh, r, inst, needs=["core.instance"])
    here = st.run("core.plugins", plugins_here, r, holds, needs=["core.instance"])
    found, bot_here = here if here else ([], False)
    ok = st.run("core.install", install_plugins, r, found, needs=["core.instance", "core.plugins"])
    if ok is False:
        st.failed.append("core.install")
    st.run("core.apply", write_schedules, r, getattr(plugins_here, "held", holds), allow,
           needs=["core.instance", "core.plugins", "core.install"])
    st.run("dcbot.token", token, r, bot_here, needs=["core.instance", "core.plugins"])
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
        r.say("ok", "core.done", "%s跑完了%s" % (what, "，但還有要你做的：%s（照上面那幾行做完再重跑）" % "、".join(dict.fromkeys(left))
                                              if left else ""))


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
