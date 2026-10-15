#!/usr/bin/env python3
"""Acceptance test of the one installer (stage 3a.9): the real install.sh, run in throwaway homes.

    python3 tests/install/acceptance.py [--only s1,s4] [--keep]

install.sh is run where it is (the tree this file is in), never from a copy: it writes its own location into
cron lines and hooks. Only the surroundings are swapped: HOME and the XDG folders point into a throwaway home,
and PATH starts with stand-ins for crontab (reads and writes a file), ssh, scp, nohup, tmux, systemctl, gh, claude
and hostname, which note every call. ALT_NO_START=1 asks the installer to start nothing that keeps running.

install.sh refuses to write from a checkout that is not this machine's engine (altention/engine_id.py): a
worktree, or a second clone. Run from the tree this file is in, it would refuse here too, so the scenarios carry
ALT_TEST_ANY_ENGINE=1, which counts only because HOME is not the real home. s15 to s18 and s20 to s22 test the check
itself: they leave the variable out and run real clones of this tree (s20 to s22: the tree unpacked the way a
downloaded archive has it, without .git), made inside the throwaway home.

No scenario ever writes a bot token (~/.config/dcbot/env): with one, dcbot/install.sh starts the supervisor by
an absolute path, and the bot's state folder is in the engine, not in HOME.

One line per check: `PASS|FAIL|NOTE <scenario>.<check> <what>`; a NOTE is not counted. The last line is
`SUMMARY pass=N fail=M note=K`; exit status 1 if anything failed. It is named acceptance.py, not test_*.py, so
that no `unittest discover` (deploy.sh) picks it up: it takes about a minute and is expected to fail until
3a.9 is in. Standard library only, Python 3.8.
"""
import os as _os
import sys as _sys
_sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.dirname(_os.path.realpath(__file__)))))
from altention import testing as _testing  # noqa: E402
_testing.guard_home()  # tests never touch the real home (altention/testing.py)
import hashlib
import json
import os
import pwd
import re
import shlex
import shutil
import stat
import subprocess
import sys
import tempfile
import time

# What 3a.9 is to print for every step (agreed with #bot-dev 2026-10-06): `<status> <id> <text>`.
STEP_LINE = re.compile(r"^(ok|changed|skip|fail|todo|unknown) ([a-z0-9][a-z0-9.-]*)( .*)?$")  # unknown: manager 2026-10-06
NEEDS_USER = 2            # exit status: the user has to do something (a token is missing, a link's place is taken)
NO_START = "ALT_NO_START"  # the installer's switch: install only, start nothing that keeps running
ANY_ENGINE = "ALT_TEST_ANY_ENGINE"  # tests only: any checkout may write, while HOME is not the real home
REFUSED = 1               # exit status when this checkout is not this machine's engine
REFUSED_LINE = re.compile(r"^fail core\.engine( |$)")  # the one line it prints then, naming the machine's engine
CANNOT_TELL = "判斷不了"   # in that line when the machine list cannot be read: refused all the same
ENGINE_HOME = os.path.join("altention", "engine")  # under HOME: where a new machine's engine is cloned (install.sh's own example)
MACHINE, OTHER, ACCOUNT = "host-a", "host-b", "alice"
INSTANCE = os.path.join("altention", "instance")          # under HOME: where a local instance is made
POINTER = os.path.join(".config", "altention", "instance")
TOKEN = os.path.join(".config", "dcbot", "env")
ODD_HOME = "家 目錄 home"                                  # a home whose path has Chinese and spaces
INSTALL_SECS = 300
# Not compared between two runs: scratch, caches, logs, and what a git repo rewrites on any command.
VOLATILE = re.compile(r"(^|/)(tmp|\.cache|__pycache__)(/|$)|\.log$|(^|/)\.git/(index|logs|ORIG_HEAD|FETCH_HEAD|COMMIT_EDITMSG)")
FOREIGN_CRON = "SHELL=/bin/zsh\nPATH=/usr/local/bin:/usr/bin:/bin\n# someone else's project\n7 * * * * /usr/bin/true  # not ours\n"

CRONTAB_STUB = r"""#!/bin/sh
# stand-in for crontab: the table is the file $STUB_CRONTAB
echo "crontab $*" >> "$STUB_LOG"
case "$1" in
  -l) if [ -f "$STUB_CRONTAB" ]; then cat "$STUB_CRONTAB"; else echo "no crontab for $USER" >&2; exit 1; fi ;;
  -r) rm -f "$STUB_CRONTAB" ;;
  -|"") cat > "$STUB_CRONTAB.new" && mv "$STUB_CRONTAB.new" "$STUB_CRONTAB" ;;
  *) cat "$1" > "$STUB_CRONTAB.new" && mv "$STUB_CRONTAB.new" "$STUB_CRONTAB" ;;
esac
"""
NOTING_STUB = "#!/bin/sh\necho \"%(name)s $*\" >> \"$STUB_LOG\"\nexit %(status)d\n"
STUBS = {"ssh": 255, "scp": 255, "nohup": 0, "setsid": 0, "tmux": 1, "systemctl": 1, "gh": 1, "claude": 1}


def engine_dir():
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))


def real_home():
    return pwd.getpwuid(os.getuid()).pw_dir  # $HOME is the guard's throwaway one by now


def write(path, text, mode=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text)
    if mode is not None:
        os.chmod(path, mode)


def read(path):
    try:
        with open(path, encoding="utf-8") as f:
            return f.read()
    except (OSError, UnicodeDecodeError):
        return None


def git(cwd, *args):
    return subprocess.run(["git"] + list(args), cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          universal_newlines=True)


class Report(object):
    def __init__(self):
        self.counts = {"PASS": 0, "FAIL": 0, "NOTE": 0}

    def line(self, kind, name, text):
        self.counts[kind] += 1
        print("%s %s %s" % (kind, name, " ".join(str(text).split())))
        sys.stdout.flush()

    def check(self, name, ok, text, why=""):
        self.line("PASS" if ok else "FAIL", name, text if ok or not why else "%s：%s" % (text, why))
        return ok

    def note(self, name, text):
        self.line("NOTE", name, text)


class Home(object):
    """One throwaway home with its stand-in commands, and install.sh run in it."""

    def __init__(self, top, name, folder="home"):
        self.name = name
        self.top = os.path.join(top, name)
        self.home = os.path.join(self.top, folder)
        self.bin = os.path.join(self.top, "bin")
        self.log = os.path.join(self.top, "calls.log")
        self.crontab = os.path.join(self.top, "crontab")
        os.makedirs(os.path.join(self.home, "tmp"))
        os.makedirs(self.bin)
        write(os.path.join(self.bin, "crontab"), CRONTAB_STUB, 0o755)
        write(os.path.join(self.bin, "hostname"), "#!/bin/sh\necho %s\n" % MACHINE, 0o755)
        for stub, status in STUBS.items():
            write(os.path.join(self.bin, stub), NOTING_STUB % {"name": stub, "status": status}, 0o755)
        write(self.log, "")
        write(self.path(".gitconfig"), "[user]\n\tname = %s\n\temail = %s@example.com\n" % (ACCOUNT, ACCOUNT))
        self.runs = []

    def path(self, *parts):
        return os.path.join(self.home, *parts)

    def env(self, extra=None, any_engine=True):
        """Nothing of the caller's environment but what a login would have: no CLAUDE_CONFIG_DIR, ALT_*, DCBOT_*
        or TMUX can reach the installer, and git may only use local transports. `any_engine`: the tree under test
        is a worktree or a second checkout on this machine, which the installer refuses without it."""
        tools = [os.path.dirname(shutil.which(t) or "/usr/bin/x") for t in ("python3", "git", "bash")]
        env = {"HOME": self.home, "USER": ACCOUNT, "LOGNAME": ACCOUNT, "LANG": "C.UTF-8", "SHELL": "/bin/sh",
               "PATH": os.pathsep.join([self.bin] + sorted(set(tools) | {"/usr/local/bin", "/usr/bin", "/bin"})),
               "TMPDIR": self.path("tmp"), "STUB_LOG": self.log, "STUB_CRONTAB": self.crontab,
               "XDG_STATE_HOME": self.path(".local", "state"), "XDG_CACHE_HOME": self.path(".cache"),
               "XDG_CONFIG_HOME": self.path(".config"), "XDG_DATA_HOME": self.path(".local", "share"),
               "GIT_ALLOW_PROTOCOL": "file", "GIT_TERMINAL_PROMPT": "0", "GIT_CONFIG_NOSYSTEM": "1",
               "PYTHONDONTWRITEBYTECODE": "1", NO_START: "1"}
        if any_engine:
            env[ANY_ENGINE] = "1"
        env.update(extra or {})
        return env

    def logs_remote(self):
        """A local bare repo for chatlog's records. Without an instance, chatlog/install.sh works a remote out
        from the engine's origin and then asks it with `env -i` and its own PATH: the real ssh, the real key."""
        bare = os.path.join(self.top, "logs.git")
        if not os.path.isdir(bare):
            git(self.top, "init", "-q", "--bare", bare)
        return {"CHATLOG_REMOTE": bare}

    def instance(self, machines=None, ssh_config=None):
        """A local instance (no remote) at the default place."""
        inst = self.path(INSTANCE)
        write(os.path.join(inst, "plugins.json"), json.dumps(
            {"format": 1, "core": {"language": "zh-TW", "timezone": "Asia/Taipei"},
             "sync": {"interval": "15m", "dirs": {}}}, indent=1) + "\n")
        os.makedirs(os.path.join(inst, "memory"))
        write(os.path.join(inst, "memory", "MEMORY.md"), "")
        if machines is not None:
            write(os.path.join(inst, "machines.json"), json.dumps({"machines": machines}, indent=1) + "\n")
            write(self.path(".config", "altention", "host"), MACHINE + "\n")  # this machine's name, as the list has it
        if ssh_config is not None:
            write(os.path.join(inst, "ssh_config"), ssh_config)
        git(inst, "init", "-q")
        git(inst, "add", "-A")
        git(inst, "-c", "user.name=%s" % ACCOUNT, "-c", "user.email=%s@example.com" % ACCOUNT, "commit", "-q", "-m", "instance")
        return inst

    def install(self, extra=None, engine=None, args=()):
        """Run install.sh: the tree under test's, or (`engine`) a clone's, then without the tests' override."""
        t0 = time.time()
        try:
            p = subprocess.run([os.path.join(engine or engine_dir(), "install.sh")] + list(args), cwd=self.home,
                               env=self.env(extra, any_engine=engine is None),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               timeout=INSTALL_SECS)
            rc, out = p.returncode, p.stdout.decode("utf-8", "replace")
        except subprocess.TimeoutExpired as e:
            rc, out = -1, (e.stdout or b"").decode("utf-8", "replace") + "\n（%d 秒沒有結束）" % INSTALL_SECS
        run = {"rc": rc, "lines": [x for x in out.split("\n") if x.strip()], "secs": time.time() - t0}
        self.runs.append(run)
        write(os.path.join(self.top, "install-%d.out" % len(self.runs)), out)
        return run

    def calls(self, *names):
        return [x for x in (read(self.log) or "").split("\n") if x.split(" ")[0] in names]

    def cron(self):
        return read(self.crontab) or ""

    def snapshot(self):
        """{relative path: what is there} of the home and the crontab, without what changes on its own."""
        seen = {"<crontab>": self.cron()}
        for folder, dirs, files in os.walk(self.home):
            for name in dirs + files:
                full = os.path.join(folder, name)
                rel = os.path.relpath(full, self.home)
                if VOLATILE.search(rel):
                    continue
                st = os.lstat(full)
                if stat.S_ISLNK(st.st_mode):
                    seen[rel] = "link → %s" % os.readlink(full)
                elif stat.S_ISDIR(st.st_mode):
                    seen[rel] = "dir %o" % stat.S_IMODE(st.st_mode)
                else:
                    with open(full, "rb") as f:
                        seen[rel] = "file %o %s" % (stat.S_IMODE(st.st_mode), hashlib.sha1(f.read()).hexdigest()[:12])
            dirs[:] = [d for d in dirs if not VOLATILE.search(os.path.relpath(os.path.join(folder, d), self.home))
                       and not os.path.islink(os.path.join(folder, d))]
        return seen

    def left_running(self):
        """Processes still alive whose environment names this home (Linux; elsewhere None)."""
        if not os.path.isdir("/proc"):
            return None
        found, needle = [], self.home.encode("utf-8")
        for pid in os.listdir("/proc"):
            if not pid.isdigit() or int(pid) == os.getpid():
                continue
            try:
                with open("/proc/%s/environ" % pid, "rb") as f:
                    if needle in f.read():
                        with open("/proc/%s/cmdline" % pid, "rb") as c:
                            found.append("%s %s" % (pid, c.read().replace(b"\0", b" ").decode("utf-8", "replace")[:80]))
            except OSError:
                continue
        return found


def differences(a, b):
    out = []
    for key in sorted(set(a) | set(b)):
        if a.get(key) != b.get(key):
            out.append("%s：%s ⇒ %s" % (key, a.get(key, "（沒有）"), b.get(key, "（沒有）")))
    return out


def tail(run, n=3):
    return " ｜ ".join(run["lines"][-n:])[:300]


def paths_in(command):
    """Absolute paths a cron line or hook command names, as a shell would split it."""
    try:
        return [w for w in shlex.split(command) if w.startswith("/") and not w.startswith("/dev/")]
    except ValueError:
        return None


def cron_commands(table):
    out = []
    for row in table.split("\n"):
        row = row.strip()
        if not row or row.startswith("#") or re.match(r"^[A-Za-z_]+=", row):
            continue
        parts = row.split(None, 1) if row.startswith("@") else row.split(None, 5)
        out.append(parts[-1])
    return out


def hook_commands(settings):
    out = []
    for groups in (settings.get("hooks") or {}).values():
        for g in groups:
            out += [h.get("command", "") for h in g.get("hooks", [])]
    return out


# ---------------------------------------------------------------- checks shared by scenarios
def common(r, h, run, want_rc):
    """What holds for every run: the exit status, the line format, nothing started, nothing reached for."""
    n = h.name
    r.check(n + ".exit", run["rc"] == want_rc, "結束碼 %d" % want_rc, "實際是 %d；最後幾行：%s" % (run["rc"], tail(run)))
    bad = [x for x in run["lines"] if not STEP_LINE.match(x)]
    r.check(n + ".lines", not bad, "每一行輸出都是「狀態 代號 說明」（共 %d 行）" % len(run["lines"]),
            "%d 行不符合，例如：%s" % (len(bad), (bad or [""])[0][:120]))
    kinds = [STEP_LINE.match(x).group(1) for x in run["lines"] if STEP_LINE.match(x)]
    if want_rc == 0:
        r.check(n + ".lines-agree", not set(kinds) & {"fail", "todo", "unknown"} and bool(kinds),
                "結束碼 0 時沒有 fail、todo 或 unknown 的行", "有 %s" % (sorted(set(kinds) & {"fail", "todo", "unknown"}) or "0 行符合格式的輸出"))
    elif want_rc == NEEDS_USER:
        r.check(n + ".lines-agree", "todo" in kinds, "結束碼 2 時至少有一行 todo", "沒有 todo 的行")
    r.check(n + ".no-start", not h.calls("nohup", "setsid", "tmux", "systemctl"), "沒有啟動任何常駐程式",
            "; ".join(h.calls("nohup", "setsid", "tmux", "systemctl"))[:200])
    r.check(n + ".no-remote", not h.calls("ssh", "scp", "gh"), "沒有連到別台機器或 GitHub",
            "; ".join(h.calls("ssh", "scp", "gh"))[:200])
    left = h.left_running()
    if left is not None:
        r.check(n + ".nothing-left", not left, "結束後沒有留下行程", "; ".join(left)[:200])
    r.check(n + ".no-token-made", not os.path.exists(h.path(TOKEN)), "沒有自己產生機器人的 token 檔")


def installed_paths(r, h):
    """The cron lines and hooks it wrote name files that exist (quoting survives the home's path)."""
    n = h.name
    missing, unparsed = [], []
    for cmd in cron_commands(h.cron()):
        found = paths_in(cmd)
        if found is None:
            unparsed.append(cmd[:80])
        else:
            missing += [p for p in found if not os.path.exists(p)]
    r.check(n + ".cron-paths", not missing and not unparsed, "cron 行裡的路徑都存在（%d 行）" % len(cron_commands(h.cron())),
            "不存在：%s；拆不開：%s" % (missing[:3], unparsed[:2]))
    try:
        settings = json.loads(read(h.path(".claude", "settings.json")) or "")
    except ValueError:
        return r.check(n + ".settings", False, "~/.claude/settings.json 是合法的 JSON", "讀不出來")
    r.check(n + ".settings", (settings.get("cleanupPeriodDays") or 0) >= 36500, "settings.json 的對話保留期是永久",
            "cleanupPeriodDays = %r" % settings.get("cleanupPeriodDays"))
    missing = []
    for cmd in hook_commands(settings):
        missing += [p for p in (paths_in(cmd) or ["（拆不開）" + cmd[:60]]) if not os.path.exists(p)]
    r.check(n + ".hook-paths", not missing, "hook 指令裡的路徑都存在（%d 個）" % len(hook_commands(settings)), str(missing[:3]))


def rerun_same(r, h, extra, times=2):
    """Run it again: the same exit status, and nothing in the home or the crontab differs."""
    before = h.snapshot()
    for i in range(times):
        run = h.install(extra)
        after = h.snapshot()
        diff = differences(before, after)
        r.check("%s.rerun%d-exit" % (h.name, i + 1), run["rc"] == h.runs[0]["rc"], "重跑第 %d 次結束碼相同" % (i + 1),
                "%d ⇒ %d；%s" % (h.runs[0]["rc"], run["rc"], tail(run)))
        r.check("%s.rerun%d-same" % (h.name, i + 1), not diff, "重跑第 %d 次家目錄和 crontab 沒有任何差異" % (i + 1),
                "%d 處不同，例如：%s" % (len(diff), "；".join(diff[:3])[:300]))
        changed = [x for x in run["lines"] if x.startswith("changed ")]
        r.check("%s.rerun%d-quiet" % (h.name, i + 1), not changed, "重跑第 %d 次沒有 changed 的行" % (i + 1), "；".join(changed[:3])[:200])
        before = after


# ---------------------------------------------------------------- scenarios
def s1(r, top):
    """One machine, no instance, no machine list: exit 0, and a local instance is made."""
    h = Home(top, "s1")
    write(h.crontab, FOREIGN_CRON)
    extra = h.logs_remote()
    run = h.install(extra)
    common(r, h, run, 0)
    inst = h.path(INSTANCE)
    r.check("s1.instance", os.path.isdir(os.path.join(inst, ".git")), "自動建了本機實例 ~/altention/instance（git repo）",
            "沒有這個資料夾" if not os.path.isdir(inst) else "不是 git repo")
    r.check("s1.instance-local", os.path.isdir(inst) and not git(inst, "remote").stdout.strip(), "本機實例沒有設遠端",
            git(inst, "remote", "-v").stdout.strip()[:120] if os.path.isdir(inst) else "沒有實例")
    pointer = (read(h.path(POINTER)) or "").strip()
    r.check("s1.instance-pointer", bool(pointer) and os.path.realpath(os.path.expanduser(pointer.replace("~", h.home, 1)))
            == os.path.realpath(inst), "~/.config/altention/instance 指向它", "內容是 %r" % pointer)
    r.check("s1.memory", os.path.isdir(os.path.join(inst, "memory")), "實例裡有 memory/（單機也有共用記憶）")
    r.check("s1.no-ssh", not os.path.exists(h.path(".ssh")), "單機完全不碰 ~/.ssh", "多了 %s" % sorted(os.listdir(h.path(".ssh")))
            if os.path.exists(h.path(".ssh")) else "")
    r.check("s1.global-notes", os.path.exists(h.path(".claude", "CLAUDE.md")), "有全域說明 ~/.claude/CLAUDE.md")
    r.check("s1.foreign-cron", h.cron().startswith(FOREIGN_CRON), "crontab 裡別人的行和 SHELL、PATH 原樣留在最前面",
            "現在的開頭：%r" % h.cron()[:120])
    installed_paths(r, h)
    return h, extra


def s2(r, top):
    """Several machines, an instance with an ssh_config, no ~/.ssh/config: the file is made (600) with the Include."""
    h = Home(top, "s2")
    machines = {MACHINE: {"ssh": ["local"], "roles": []}, OTHER: {"ssh": [OTHER], "roles": []}}
    inst = h.instance(machines, "Host %s\n  HostName 192.0.2.10\n  User %s\n" % (OTHER, ACCOUNT))
    run = h.install()
    common(r, h, run, 0)
    cfg = h.path(".ssh", "config")
    text = read(cfg) or ""
    include = "Include %s" % os.path.join(os.path.realpath(inst), "ssh_config")
    r.check("s2.ssh-config", os.path.isfile(cfg), "建了 ~/.ssh/config")
    r.check("s2.ssh-mode", os.path.isfile(cfg) and stat.S_IMODE(os.stat(cfg).st_mode) == 0o600, "~/.ssh/config 的權限是 600",
            "是 %o" % stat.S_IMODE(os.stat(cfg).st_mode) if os.path.isfile(cfg) else "沒有這個檔")
    r.check("s2.ssh-include", text.count(include) == 1, "裡面有一行 Include 指向實例的 ssh_config",
            "出現 %d 次；檔案開頭：%r" % (text.count(include), text[:120]))
    installed_paths(r, h)

    # the same, on a machine whose ~/.ssh/config already holds the user's own hosts
    k = Home(top, "s2b")
    mine = "Host mine\n  HostName 192.0.2.20\n"
    write(k.path(".ssh", "config"), mine, 0o600)
    kinst = k.instance(machines, "Host %s\n  HostName 192.0.2.10\n" % OTHER)
    krun = k.install()
    ktext = read(k.path(".ssh", "config")) or ""
    kinclude = "Include %s" % os.path.join(os.path.realpath(kinst), "ssh_config")
    r.check("s2b.exit", krun["rc"] == 0, "已經有 ~/.ssh/config 時結束碼 0", "實際是 %d；%s" % (krun["rc"], tail(krun)))
    r.check("s2b.ssh-kept", mine in ktext, "使用者原有的 Host 設定原樣保留")
    r.check("s2b.ssh-include-first", ktext.count(kinclude) == 1 and ktext.find(kinclude) < ktext.find("Host mine"),
            "Include 只有一行，而且在原有的 Host 之前（放在後面不會生效）", "檔案：%r" % ktext[:160])
    return h, None


def s3(r, top, first, second):
    """Run again, twice: nothing differs."""
    for h, extra in (first, second):
        if h.runs and h.runs[0]["rc"] == 0:
            rerun_same(r, h, extra)
        else:
            r.check("%s.rerun" % h.name, False, "重跑兩次結果相同", "第一次就沒有成功，沒有東西可比")


def s4(r, top):
    """This machine is to run the bot and its token is missing: exit 2, nothing started."""
    h = Home(top, "s4")
    h.instance({MACHINE: {"ssh": ["local"], "roles": ["dcbot"]}})
    run = h.install()
    common(r, h, run, NEEDS_USER)
    todo = [x for x in run["lines"] if x.startswith("todo ")]
    r.check("s4.says-token", any("token" in x.lower() or TOKEN.split(os.sep)[-2] in x for x in todo),
            "有一行 todo 說明缺的是機器人的 token", "todo 的行：%s" % ("；".join(todo)[:200] or "沒有"))
    return h


def s5(r, top):
    """Where a link is to go there is an ordinary file: exit 2, and the file is left as it was."""
    skills = sorted(d for d in os.listdir(os.path.join(engine_dir(), "skills"))
                    if os.path.isdir(os.path.join(engine_dir(), "skills", d)))
    cases = [("s5", os.path.join(".claude", "CLAUDE.md"), "全域說明 ~/.claude/CLAUDE.md")]
    if skills:
        cases.append(("s5b", os.path.join(".claude", "skills", skills[0]), "skill 的連結 ~/.claude/skills/%s" % skills[0]))
    for name, rel, label in cases:
        h = Home(top, name)
        h.instance()
        mine = "我自己寫的，不要動\n"
        write(h.path(rel), mine)
        run = h.install()
        common(r, h, run, NEEDS_USER)
        r.check(name + ".kept", read(h.path(rel)) == mine and not os.path.islink(h.path(rel)),
                "%s 被一般檔案佔住時，那個檔原樣不動" % label,
                "變成連結了" if os.path.islink(h.path(rel)) else "內容變了")
        r.check(name + ".says-which", any(x.startswith("todo ") and os.path.basename(rel) in x for x in run["lines"]),
                "有一行 todo 指出是哪個檔", "todo 的行：%s" % ("；".join(x for x in run["lines"] if x.startswith("todo "))[:200] or "沒有"))


def s6(r, top):
    """Scenario 1 in a home whose path has Chinese and spaces."""
    h = Home(top, "s6", ODD_HOME)
    run = h.install(h.logs_remote())
    common(r, h, run, 0)
    r.check("s6.instance", os.path.isdir(os.path.join(h.path(INSTANCE), ".git")), "路徑含中文和空白的家目錄也建得出本機實例")
    installed_paths(r, h)


FAKE_TOKEN = "FAKE-TOKEN-NOT-REAL-0123456789"  # an obvious stand-in: must never show in the output or the log


def log_of(h):
    return os.path.join(h.home, ".local", "state", "altention", "install", "last.log")


def s7(r, top, first):
    """After a single-machine install, the instance's sync twice: a local commit, quiet (no error, nothing told);
    the log is 600 and holds no environment (manager 2026-10-06)."""
    if not first or not first[0].runs or first[0].runs[0]["rc"] != 0:
        return r.check("s7.sync", False, "裝完之後同步兩次", "s1 沒有成功，沒有東西可同步")
    h = first[0]
    inst = h.path(INSTANCE)
    write(os.path.join(inst, "memory", "from-s7.md"), "記一件事\n")
    code = ("import json, sys; sys.path.insert(0, %r)\nfrom altention import config, sync\n"
            "inst = config.instance_dir()\nfor i in range(2):\n"
            "    print(json.dumps(sync.sync_repo(inst, sync.INSTANCE_SHARED, 'acceptance', lambda t: print('SAID', t))))"
            % engine_dir())
    p = subprocess.run([sys.executable, "-c", code], cwd=h.home, env=h.env(), stdout=subprocess.PIPE,
                       stderr=subprocess.STDOUT, universal_newlines=True, timeout=120)
    results = [json.loads(x) for x in p.stdout.splitlines() if x.startswith("{")]
    r.check("s7.sync-ok", len(results) == 2 and all(x.get("ok") and not x.get("error") for x in results),
            "沒有遠端的實例同步兩次都成功、沒有錯誤", p.stdout[-300:])
    r.check("s7.sync-quiet", not [x for x in p.stdout.splitlines() if x.startswith("SAID")] and not h.calls("ssh", "gh"),
            "同步沒有說任何話、沒有連到別處", p.stdout[-200:])
    r.check("s7.committed", "acceptance" in git(inst, "log", "-1", "--format=%s").stdout, "記憶在本機提交了")
    log = log_of(h)
    r.check("s7.log-mode", os.path.isfile(log) and stat.S_IMODE(os.stat(log).st_mode) == 0o600, "last.log 的權限是 600")
    text = read(log) or ""
    r.check("s7.log-no-env", "STUB_LOG=" not in text and "PATH=" not in text and "XDG_STATE_HOME" not in text,
            "log 裡沒有整包的環境變數")


def s8(r, top):
    """Killed halfway, then run again: it comes to the same place, and a third run changes nothing."""
    h = Home(top, "s8")
    write(h.crontab, FOREIGN_CRON)
    extra = h.logs_remote()
    p = subprocess.Popen([os.path.join(engine_dir(), "install.sh")], cwd=h.home, env=h.env(extra), stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, start_new_session=True)
    t0 = time.time()
    while time.time() - t0 < 60 and "chatlog/install.sh" not in (read(log_of(h)) or ""):
        time.sleep(0.05)
    os.killpg(p.pid, 9)
    p.wait()
    r.check("s8.killed", "core.done" not in (p.stdout.read() or b"").decode("utf-8", "replace"), "安裝跑到一半被 kill")
    run = h.install(extra)
    common(r, h, run, 0)
    r.check("s8.instance", os.path.isdir(os.path.join(h.path(INSTANCE), ".git")), "重跑後實例在")
    r.check("s8.no-half", not os.path.lexists(h.path(INSTANCE) + ".initializing"), "沒有留下做到一半的實例")
    again = h.install(extra)
    r.check("s8.again-quiet", again["rc"] == 0 and not [x for x in again["lines"] if x.startswith("changed ")],
            "再跑一次沒有 changed", "；".join(x for x in again["lines"] if x.startswith("changed "))[:200])


def s9(r, top):
    """ALT_INSTANCE names a folder that is not there: fail, nothing made at the default place."""
    h = Home(top, "s9")
    run = h.install({"ALT_INSTANCE": h.path("nowhere")})
    r.check("s9.exit", run["rc"] == 1, "結束碼 1", "實際是 %d；%s" % (run["rc"], tail(run)))
    r.check("s9.says", any(x.startswith("fail core.instance") for x in run["lines"]), "有一行 fail core.instance")
    r.check("s9.not-made", not os.path.lexists(h.path(INSTANCE)), "沒有改在預設位置建實例")


def s10(r, top):
    """Something not an instance at the default place: todo, left as it was."""
    h = Home(top, "s10")
    mine = h.path(INSTANCE, "notes.txt")
    write(mine, "我的東西\n")
    run = h.install()
    common(r, h, run, NEEDS_USER)
    r.check("s10.kept", read(mine) == "我的東西\n" and not os.path.isdir(h.path(INSTANCE, ".git")), "那個資料夾原樣不動")
    r.check("s10.says", any(x.startswith("todo core.instance") and "mv " in x for x in run["lines"]),
            "todo 寫了要做的事（mv …）")


def s11(r, top):
    """One machine whose ~/.ssh/config is there: never touched (content and mtime)."""
    h = Home(top, "s11")
    cfg = h.path(".ssh", "config")
    write(cfg, "Host mine\n  HostName 192.0.2.30\n", 0o600)
    os.utime(cfg, (1000000000, 1000000000))
    os.utime(h.path(".ssh"), (1000000000, 1000000000))
    run = h.install(h.logs_remote())
    common(r, h, run, 0)
    r.check("s11.ssh-untouched", os.stat(cfg).st_mtime == 1000000000 and os.stat(h.path(".ssh")).st_mtime == 1000000000
            and read(cfg) == "Host mine\n  HostName 192.0.2.30\n", "單機：~/.ssh 和 config 的內容、修改時間都沒變")


def s12(r, top):
    """The bot's machine with its settings file: exit 0, the file never read out (its stand-in text shows nowhere),
    nothing started (ALT_NO_START)."""
    h = Home(top, "s12")
    h.instance({MACHINE: {"ssh": ["local"], "roles": ["dcbot"]}})
    write(h.path(TOKEN), "DISCORD_TOKEN=%s\n" % FAKE_TOKEN, 0o600)
    run = h.install()
    r.check("s12.exit", run["rc"] == 0, "結束碼 0", "實際是 %d；%s" % (run["rc"], tail(run)))
    r.check("s12.token-ok", any(x.startswith("ok dcbot.token") for x in run["lines"]), "ok dcbot.token")
    out = "\n".join(run["lines"]) + (read(log_of(h)) or "")
    r.check("s12.never-read", FAKE_TOKEN not in out, "token 的內容沒有出現在輸出或 log")
    r.check("s12.no-start", not h.calls("nohup", "setsid", "tmux", "systemctl"), "沒有啟動任何常駐程式")


def s13(r, top):
    """Several machines, ~/.ssh/config already there: backed up first (read back), its mode kept."""
    h = Home(top, "s13")
    mine = "Host mine\n  HostName 192.0.2.20\n"
    write(h.path(".ssh", "config"), mine, 0o640)
    h.instance({MACHINE: {"ssh": ["local"], "roles": []}, OTHER: {"ssh": [OTHER], "roles": []}}, "Host %s\n" % OTHER)
    run = h.install()
    r.check("s13.exit", run["rc"] == 0, "結束碼 0", "實際是 %d；%s" % (run["rc"], tail(run)))
    root = h.path(".local", "state", "altention", "install", "backup")
    saved = [os.path.join(root, d, "ssh_config") for d in (os.listdir(root) if os.path.isdir(root) else [])]
    r.check("s13.backup", any(read(x) == mine for x in saved), "寫之前備份了原本的 ~/.ssh/config", str(saved))
    r.check("s13.mode", stat.S_IMODE(os.stat(h.path(".ssh", "config")).st_mode) == 0o640, "權限沒變（640）")


def s14(r, top):
    """The instance's settings cannot be read: unknown or fail, exit 1, the crontab not touched."""
    h = Home(top, "s14")
    write(h.crontab, FOREIGN_CRON)
    inst = h.instance()
    write(os.path.join(inst, "plugins.json"), "{ not json")
    run = h.install()
    r.check("s14.exit", run["rc"] == 1, "結束碼 1", "實際是 %d；%s" % (run["rc"], tail(run)))
    r.check("s14.lines", all(STEP_LINE.match(x) for x in run["lines"]), "每一行都是「狀態 代號 說明」")
    r.check("s14.cron", h.cron() == FOREIGN_CRON, "crontab 沒被改", h.cron()[-200:])


# ---------------------------------------------------------------- s15-s17: which checkout may write
def clone_engine(dest):
    """A real clone of the tree under test at `dest`, holding what the tree holds now: its HEAD, then its
    uncommitted changes and untracked files on top (a clone alone would test the last commit). Local, no network.
    Returns how many files were laid on top."""  # lay_changes does the second half, for worktrees too
    src = engine_dir()
    head = git(src, "rev-parse", "HEAD").stdout.strip()
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    p = git(os.path.dirname(dest), "clone", "-q", "--no-checkout", src, dest)
    if p.returncode:
        raise RuntimeError("git clone: %s" % p.stderr.strip()[:200])
    git(dest, "checkout", "-q", "--detach", head)
    return lay_changes(dest)


def lay_changes(dest):
    """Onto `dest` (a checkout of the tree under test's HEAD): what that tree has uncommitted and untracked."""
    src = engine_dir()
    diff = subprocess.run(["git", "diff", "HEAD", "--binary"], cwd=src, stdout=subprocess.PIPE).stdout
    laid = len(git(src, "diff", "HEAD", "--name-only").stdout.split())
    if diff.strip():
        p = subprocess.run(["git", "apply", "--whitespace=nowarn"], cwd=dest, input=diff, stderr=subprocess.PIPE)
        if p.returncode:
            raise RuntimeError("git apply: %s" % p.stderr.decode("utf-8", "replace").strip()[:200])
    for rel in git(src, "ls-files", "-o", "--exclude-standard", "-z").stdout.split("\0"):
        if rel and os.path.isfile(os.path.join(src, rel)):
            os.makedirs(os.path.dirname(os.path.join(dest, rel)), exist_ok=True)
            shutil.copy2(os.path.join(src, rel), os.path.join(dest, rel))
            laid += 1
    return laid


def add_worktree(main, dest):
    """A worktree of the clone `main` at `dest`, with the same uncommitted changes. "" or what went wrong."""
    p = git(main, "worktree", "add", "-q", "--detach", dest)
    if p.returncode:
        return "git worktree add：%s" % p.stderr.strip()[:160]
    lay_changes(dest)
    return ""


def from_wrong_tree(paths):
    """Of `paths`, those inside the tree under test: a clone that installs must name itself, never its source."""
    src = os.path.realpath(engine_dir()) + os.sep
    return [p for p in paths if os.path.realpath(p).startswith(src)]


def refused(r, name, h, engine, official, label, must_say=None):
    """install.sh and install.sh --links from `engine` do nothing and say where this machine's engine is."""
    for tag, args in (("links", ("--links",)), ("full", ())):  # the light one first: nothing the full one did hides it
        before, wrote = h.snapshot(), len(h.calls("crontab"))
        run = h.install(h.logs_remote(), engine=engine, args=args)
        n = "%s.%s" % (name, tag)
        what = "%s跑 install.sh%s" % (label, " --links" if args else "")
        r.check(n + "-exit", run["rc"] == REFUSED, "%s：結束碼 %d" % (what, REFUSED), "實際是 %d；%s" % (run["rc"], tail(run)))
        said = [x for x in run["lines"] if REFUSED_LINE.match(x)]
        need = must_say or os.path.realpath(official)
        r.check(n + "-says", bool(said) and need in said[0],
                "%s：有一行 fail core.engine，%s" % (what, "寫著「%s」" % must_say if must_say else "寫出正式引擎在哪"),
                "沒有這一行；輸出：%s" % tail(run) if not said else "那一行沒有 %s：%s" % (need, said[0][:160]))
        did = [x for x in run["lines"] if x.split(" ")[0] in ("ok", "changed")]
        r.check(n + "-no-step", not did, "%s：沒有做任何一步" % what, "有 %d 行 ok 或 changed，例如：%s" % (len(did), (did or [""])[0][:120]))
        bad = [x for x in run["lines"] if not STEP_LINE.match(x)]
        r.check(n + "-lines", not bad, "%s：輸出仍是「狀態 代號 說明」" % what, (bad or [""])[0][:120])
        diff = differences(before, h.snapshot())
        r.check(n + "-untouched", not diff, "%s：家目錄（含兩份 checkout、settings.json、altention 指令）和 crontab 逐檔相同" % what,
                "%d 處不同，例如：%s" % (len(diff), "；".join(diff[:3])[:300]))
        r.check(n + "-no-crontab", len(h.calls("crontab")) == wrote, "%s：沒有呼叫 crontab" % what,
                "; ".join(h.calls("crontab")[wrote:])[:200])
        r.check(n + "-no-start", not h.calls("nohup", "setsid", "tmux", "systemctl"), "%s：沒有啟動任何常駐程式" % what)


def s15(r, top):
    """A new machine: the first clone, at ~/altention/engine, installs as before (the check must not stop it)."""
    h = Home(top, "s15")
    write(h.crontab, FOREIGN_CRON)
    official = h.path(ENGINE_HOME)
    laid = clone_engine(official)
    if laid:
        r.note("s15.clone", "clone 是 HEAD 加上工作樹裡還沒提交的 %d 個檔" % laid)
    run = h.install(h.logs_remote(), engine=official)
    common(r, h, run, 0)
    r.check("s15.not-refused", not any(REFUSED_LINE.match(x) for x in run["lines"]), "全新的家目錄、第一份 clone：身分檢查放行")
    r.check("s15.instance", os.path.isdir(os.path.join(h.path(INSTANCE), ".git")), "照常建了本機實例")
    cmd = h.path(".local", "bin", "altention")
    r.check("s15.command", os.path.islink(cmd) and os.path.realpath(cmd).startswith(os.path.realpath(official) + os.sep),
            "~/.local/bin/altention 指到這份 clone", "指到 %s" % (os.path.realpath(cmd) if os.path.lexists(cmd) else "（沒有這個連結）"))
    installed_paths(r, h)
    named = []
    for cmd_line in cron_commands(h.cron()):
        named += paths_in(cmd_line) or []
    try:
        for cmd_line in hook_commands(json.loads(read(h.path(".claude", "settings.json")) or "{}")):
            named += paths_in(cmd_line) or []
    except ValueError:
        pass
    wrong = from_wrong_tree(named)
    r.check("s15.own-paths", not wrong, "cron 和 hook 裡的路徑都是這份 clone 自己的，沒有來源那棵樹的", str(wrong[:3]))
    return h


def s16(r, top, h):
    """This machine has its engine (s15); a second clone must not write."""
    if h is None or not h.runs or h.runs[0]["rc"] != 0:
        return r.check("s16.setup", False, "第二份 clone 被拒", "s15 的第一次安裝沒有成功，沒有正式引擎可以對照")
    official, second = h.path(ENGINE_HOME), h.path("second")
    clone_engine(second)
    refused(r, "s16", h, second, official, "從第二份 clone ")
    before = h.snapshot()
    run = h.install(h.logs_remote(), engine=official)
    r.check("s16.official-still-works", run["rc"] == 0 and not differences(before, h.snapshot()),
            "之後從正式引擎重跑：結束碼 0，而且什麼都沒變", "結束碼 %d；%s" % (run["rc"], "；".join(differences(before, h.snapshot())[:2])[:200]))


def s17(r, top, h):
    """A worktree never writes: of this machine's engine (s15), and on a machine with nothing installed yet."""
    if h is not None and h.runs and h.runs[0]["rc"] == 0:
        official, tree = h.path(ENGINE_HOME), h.path("worktree")
        wrong = add_worktree(official, tree)
        if wrong:
            r.check("s17.setup", False, "worktree 被拒", wrong)
        else:
            refused(r, "s17", h, tree, official, "從正式引擎的 worktree ")
    else:
        r.check("s17.setup", False, "worktree 被拒", "s15 的第一次安裝沒有成功，沒有正式引擎可以對照")
    k = Home(top, "s17b")
    main, tree = k.path("src", "engine"), k.path("src", "wt")
    clone_engine(main)
    wrong = add_worktree(main, tree)
    if wrong:
        return r.check("s17b.setup", False, "全新機器上的 worktree 被拒", wrong)
    refused(r, "s17b", k, tree, main, "全新的家目錄、什麼都還沒裝，從 worktree ")
    r.check("s17b.nothing-made", not os.path.exists(k.path(INSTANCE)) and not os.path.exists(k.path(".claude"))
            and not k.cron(), "沒有建實例、沒有 ~/.claude、crontab 是空的")


def s18(r, top):
    """It cannot be told whether this is the machine's engine (the instance's machine list is not JSON): refused."""
    h = Home(top, "s18")
    official = h.path(ENGINE_HOME)
    clone_engine(official)
    inst = h.instance({MACHINE: {"ssh": ["local"], "roles": []}})
    write(os.path.join(inst, "machines.json"), "{ not json\n")
    refused(r, "s18", h, official, None, "機器清單壞掉、判斷不了時，從 ~/altention/engine 的 clone ", must_say=CANNOT_TELL)
    r.check("s18.nothing-made", not os.path.exists(h.path(".claude")) and not h.cron(), "沒有 ~/.claude、crontab 是空的")


def s19(r, top):
    """One outside plugin's plugin.json is broken: only it is left out (unknown, its line untouched), the others are
    installed and their lines written; exit 1 and the last line names it (manager 2026-10-06)."""
    h = Home(top, "s19")
    inst = h.instance()
    write(os.path.join(inst, "plugins", "extra", "plugin.json"), "{ broken")
    theirs = "5 5 * * * /usr/bin/true  # altention:extra\n"
    write(h.crontab, FOREIGN_CRON + theirs)
    run = h.install(h.logs_remote())
    r.check("s19.exit", run["rc"] == 1, "結束碼 1", "實際是 %d；%s" % (run["rc"], tail(run)))
    r.check("s19.says", any(x.startswith("unknown plugin.extra") for x in run["lines"]), "有一行 unknown plugin.extra")
    r.check("s19.others", any(x.startswith("ok plugin.chatlog") for x in run["lines"])
            and any(x.startswith(("ok core.apply", "changed core.apply")) for x in run["lines"]),
            "其他插件照裝、core.apply 照做", tail(run, 6))
    r.check("s19.theirs-kept", theirs.strip() in h.cron(), "那個插件已經有的行一行不動", h.cron()[-200:])
    r.check("s19.chatlog-line", "chatlog.py" in h.cron(), "chatlog 的排程照寫")
    r.check("s19.last", run["lines"][-1].startswith("fail core.done") and "plugin.extra" in run["lines"][-1],
            "最後一行寫出是哪個插件", run["lines"][-1])


# ---------------------------------------------------------------- s20-s22: an engine that is not a git checkout
PUBLIC_ENGINE = os.path.join("altention", "engine")  # under HOME: where the public README puts the engine


def unpack_engine(dest):
    """The tree under test as a downloaded archive would have it, at `dest`: no .git (git archive of HEAD, then
    the uncommitted changes and untracked files on top). Returns how many files were laid on top."""
    src = engine_dir()
    os.makedirs(dest)
    arc = subprocess.run(["git", "archive", "--format=tar", "HEAD"], cwd=src, stdout=subprocess.PIPE)
    if arc.returncode:
        raise RuntimeError("git archive failed")
    p = subprocess.run(["tar", "-x", "-f", "-", "-C", dest], input=arc.stdout, stderr=subprocess.PIPE)
    if p.returncode:
        raise RuntimeError("tar: %s" % p.stderr.decode("utf-8", "replace").strip()[:200])
    return lay_changes(dest)


def s20(r, top):
    """A new machine, the engine unpacked from an archive at ~/altention/engine (no .git): installs like a clone."""
    h = Home(top, "s20")
    write(h.crontab, FOREIGN_CRON)
    official = h.path(PUBLIC_ENGINE)
    laid = unpack_engine(official)
    if laid:
        r.note("s20.unpack", "壓縮檔是 HEAD 加上工作樹裡還沒提交的 %d 個檔" % laid)
    r.check("s20.no-git", not os.path.lexists(os.path.join(official, ".git")), "引擎資料夾沒有 .git")
    run = h.install(h.logs_remote(), engine=official)
    common(r, h, run, 0)
    r.check("s20.not-refused", not any(REFUSED_LINE.match(x) for x in run["lines"]), "全新的家目錄、解開的壓縮檔：身分檢查放行",
            tail(run))
    said = [x for x in run["lines"] if x.startswith("skip dcbot.token ")]
    r.check("s20.no-bot-said", bool(said) and "install.sh --token" in said[0], "沒有 token：有一行 skip dcbot.token 說怎麼輸入",
            tail(run, 4))
    r.check("s20.instance", os.path.isdir(os.path.join(h.path(INSTANCE), ".git")), "照常建了本機實例")
    pointer = read(h.path(".config", "altention", "engine")) or ""
    r.check("s20.pointer", os.path.realpath(pointer.strip() or "/nonexistent") == os.path.realpath(official),
            "~/.config/altention/engine 指到這份", "內容：%r" % pointer[:120])
    cmd = h.path(".local", "bin", "altention")
    r.check("s20.command", os.path.islink(cmd) and os.path.realpath(cmd).startswith(os.path.realpath(official) + os.sep),
            "~/.local/bin/altention 指到這份", "指到 %s" % (os.path.realpath(cmd) if os.path.lexists(cmd) else "（沒有這個連結）"))
    installed_paths(r, h)
    named = []
    for cmd_line in cron_commands(h.cron()):
        named += paths_in(cmd_line) or []
    try:
        for cmd_line in hook_commands(json.loads(read(h.path(".claude", "settings.json")) or "{}")):
            named += paths_in(cmd_line) or []
    except ValueError:
        pass
    wrong = from_wrong_tree(named)
    r.check("s20.own-paths", not wrong, "cron 和 hook 裡的路徑都是這份自己的，沒有來源那棵樹的", str(wrong[:3]))
    before = h.snapshot()
    again = h.install(h.logs_remote(), engine=official)
    diff = differences(before, h.snapshot())
    r.check("s20.again", again["rc"] == 0 and not diff, "重跑一次：結束碼 0，什麼都沒變",
            "結束碼 %d；%s" % (again["rc"], "；".join(diff[:2])[:200]))
    r.check("s20.no-git-made", not os.path.lexists(os.path.join(official, ".git")), "安裝沒有在引擎資料夾裡建 .git")
    return h


def s21(r, top, h):
    """This machine's engine is an unpacked archive (s20); a second one unpacked elsewhere must not write."""
    if h is None or not h.runs or h.runs[0]["rc"] != 0:
        return r.check("s21.setup", False, "第二份壓縮檔被拒", "s20 的第一次安裝沒有成功，沒有正式引擎可以對照")
    second = h.path("下載", "altention engine")
    unpack_engine(second)
    refused(r, "s21", h, second, h.path(PUBLIC_ENGINE), "從第二份解開的壓縮檔")


def s22(r, top):
    """The engine's .git is a link to somewhere not there (a share not mounted yet): cannot tell, refused."""
    h = Home(top, "s22")
    official = h.path(PUBLIC_ENGINE)
    unpack_engine(official)
    os.symlink(h.path("mnt", "not there", ".git"), os.path.join(official, ".git"))
    refused(r, "s22", h, official, None, ".git 指到讀不到的地方時，從 ~/altention/engine ", must_say=CANNOT_TELL)
    r.check("s22.nothing-made", not os.path.exists(h.path(".claude")) and not h.cron()
            and not os.path.exists(h.path(INSTANCE)), "沒有實例、沒有 ~/.claude、crontab 是空的")


# ---------------------------------------------------------------- the real machine stays as it was
def real_state():
    """What must not change on the real machine while this runs (read only)."""
    home, seen = real_home(), {}
    tool = next((p for p in ("/usr/bin/crontab", "/bin/crontab") if os.path.exists(p)), None)
    if tool:
        p = subprocess.run([tool, "-l"], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        seen["crontab"] = hashlib.sha1(p.stdout).hexdigest()
    seen["ssh-config"] = hashlib.sha1((read(os.path.join(home, ".ssh", "config")) or "").encode("utf-8")).hexdigest()
    try:
        hooks = json.loads(read(os.path.join(home, ".claude", "settings.json")) or "{}").get("hooks")
    except ValueError:
        hooks = None
    seen["hooks"] = hashlib.sha1(json.dumps(hooks, sort_keys=True).encode("utf-8")).hexdigest()
    try:
        folder = os.path.join(home, ".claude", "skills")
        seen["skills"] = sorted("%s → %s" % (n, os.readlink(os.path.join(folder, n)) if os.path.islink(os.path.join(folder, n)) else "")
                                for n in os.listdir(folder))
    except OSError:
        seen["skills"] = None
    seen["token"] = os.path.exists(os.path.join(home, TOKEN)) and os.stat(os.path.join(home, TOKEN)).st_mtime
    return seen


def engine_state():
    return git(engine_dir(), "status", "--porcelain").stdout


def main(argv):
    only = None
    for i, a in enumerate(argv):
        if a == "--only" and i + 1 < len(argv):
            only = set(argv[i + 1].split(","))
    r = Report()
    want = lambda name: only is None or name in only  # noqa: E731
    real, tree = real_state(), engine_state()
    top = tempfile.mkdtemp(prefix="install-")  # in the guard's throwaway home
    r.note("setup", "引擎 %s；每個情境的輸出留在 %s/<情境>/install-N.out%s" % (
        engine_dir(), top, "" if "--keep" in argv else "（結束時刪掉，要留加 --keep）"))
    try:
        first = s1(r, top) if want("s1") or want("s3") or want("s7") else None
        second = s2(r, top) if want("s2") or want("s3") else None
        if want("s3"):
            s3(r, top, first, second)
        if want("s4"):
            s4(r, top)
        if want("s5"):
            s5(r, top)
        if want("s6"):
            s6(r, top)
        if want("s7"):
            s7(r, top, first)
        for name, fn in (("s19", s19), ("s8", s8), ("s9", s9), ("s10", s10), ("s11", s11), ("s12", s12), ("s13", s13), ("s14", s14)):
            if want(name):
                fn(r, top)
        clean = s15(r, top) if want("s15") or want("s16") or want("s17") else None
        if want("s16"):
            s16(r, top, clean)
        if want("s17"):
            s17(r, top, clean)
        if want("s18"):
            s18(r, top)
        tar = s20(r, top) if want("s20") or want("s21") else None
        if want("s21"):
            s21(r, top, tar)
        if want("s22"):
            s22(r, top)
    finally:
        now = real_state()
        r.check("real.untouched", now == real, "真的 crontab、~/.ssh/config、hook、skill 連結、token 都沒有變",
                "變了：%s" % [k for k in real if real[k] != now.get(k)])
        if engine_state() != tree:
            r.note("engine.tree", "引擎工作樹的 git status 在測試期間變了（共用的工作樹可能是別的頻道改的）")
        if "--keep" in argv:
            keep = os.path.join(os.sep, "tmp", "install-acceptance-%d" % os.getpid())  # outside the throwaway home
            shutil.copytree(top, keep, symlinks=True)
            r.note("kept", keep)
    print("SUMMARY pass=%d fail=%d note=%d" % (r.counts["PASS"], r.counts["FAIL"], r.counts["NOTE"]))
    return 1 if r.counts["FAIL"] else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
