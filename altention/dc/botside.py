"""The machine connected to Discord, and how a command reaches its dcctl: for altention dc post, read and notify, and
for dcctl itself on any other machine (C-109: the token is kept only where the bot runs).

Which machine: bot_machine(), the one place that says it (altention.config's machine list today; when a standby
takes over, C-53, the answer changes here and nowhere else). How: its dcctl here, else over SSH. The way is found
first, `true` on each of the machine's aliases in turn; the command itself is then sent once and never again: ssh's
255 does not say whether it ran (the line can drop after), and a post must not go twice (manager 2026-10-30).
Standard library only: dcctl imports it with the system Python.
"""
import json
import os
import subprocess
import sys

HOME_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))  # altention/dc/ -> the engine
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8"]
WAY_SECS = 20  # one alias's `true`, at most
WAIT_MOST = 600  # --wait's most seconds (altention dc post and notify)
RUN_SECS = 900  # the command itself over SSH, at most (C-125): above --wait's most, dcctl's wait for its lock and a
# rate limit's (#code-review on 4ee2ab6), so a post still queued says so rather than being cut
HANDED = "ALTENTION_HANDED"  # set on a command sent to the bot's machine: one that arrives there is not sent on
STDIN_COMMANDS = {"post", "edit"}  # the dcctl commands that read stdin: only theirs is read and sent over


class NoBot(Exception):
    """No machine to hand the command to, no way there, or the line lost while it ran: the words say which."""


def quote(word):
    """One word for the shell on the bot's machine, always in single quotes (altention.config.shell_quote, kept
    here for the same reason as dcctl_on: zsh reads a bare word starting with = as a command's path)."""
    return "'" + str(word).replace("'", "'\"'\"'") + "'"


def dcctl_on(m):
    """dcctl on the bot's machine `m`, for a shell there: its engine is its list entry's "engine" (default
    ~/altention/engine; a leading ~ left to that shell). Worked out here, not by altention.config: the path problems are
    reported on must not depend on the settings that may be the problem."""
    engine = m.get("engine") if isinstance(m.get("engine"), str) and m.get("engine").strip() else "~/altention/engine"
    engine = "~" if engine == "~" else "~/" + quote(engine[2:]) if engine.startswith("~/") else quote(engine)
    return engine + "/dcbot/tools/dcctl"


def local_dcctl(engine=None, repo=HOME_REPO):
    """dcctl of the bot running on this machine: in its engine (`engine`, this machine's entry in the list, else
    ~/altention/engine); the checkout `repo`'s only when that is not there. Not simply this checkout's: run from a
    worktree or a copy, posts would go into a state/ no running bot reads, and say nothing."""
    for base in ([engine] if engine else []) + ["~/altention/engine"]:
        path = os.path.join(os.path.expanduser(base), "dcbot", "tools", "dcctl")
        if os.path.exists(path):
            return path
    return os.path.join(repo, "dcbot", "tools", "dcctl")


def this_engine(name):
    """This machine's "engine" in the machine list, or None."""
    try:
        from altention import config
        engine = ((config.inventory().get("machines") or {}).get(name) or {}).get("engine")
        return engine if isinstance(engine, str) and engine.strip() else None
    except Exception:
        return None


def bot_machine(cache=None, prog="altention dc"):
    """(name, machine, here) of the machine connected to Discord now (altention.config: this one if the bot's
    settings are here, else the machine list's). NoBot when there is none, or the settings cannot say. `cache`, a
    file: the answer is kept there, and used when the settings cannot be read (altention dc notify, the path problems
    are reported on, which must not depend on them)."""
    sys.path.insert(0, HOME_REPO)
    try:
        from altention import config, text
        name, m, here = config.bot_machine()
    except Exception as e:  # a broken machine list or instance pointer
        cfg = sys.modules.get("altention.config")
        if not cache and not (cfg and isinstance(e, cfg.ConfigError)):
            raise
        problem = "設定讀不出來（%s）" % e if cache else str(e)
    else:
        if name is not None:
            if cache:
                try:
                    os.makedirs(os.path.dirname(cache), exist_ok=True)
                    with open(cache + ".tmp", "w") as f:
                        json.dump({"name": name, "ssh": m.get("ssh", []), "here": bool(here)}, f)
                    os.replace(cache + ".tmp", cache)
                except OSError:
                    pass
            return name, m, here
        problem = text.t("bot.not_found")
    if not cache:
        raise NoBot(problem)
    try:
        with open(cache) as f:
            last = json.load(f)
        sys.stderr.write("%s：%s；用上次找到的機器人（%s）\n" % (prog, problem, last["name"]))
        return last["name"], {"ssh": last.get("ssh", [])}, bool(last.get("here"))
    except (OSError, ValueError, KeyError):
        raise NoBot("%s，也沒有上次找到的機器人可用" % problem)


def way(m):
    """(the first of `m`'s aliases that answers `true` now or None, the aliases tried). Finding the way may try
    again another way; the command never is."""
    tried = []
    for alias in m.get("ssh", []):
        if alias == "local":
            continue
        tried.append(alias)
        try:
            r = subprocess.run(SSH + [alias, "true"], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=WAY_SECS)
        except subprocess.TimeoutExpired:
            continue
        if r.returncode == 0:
            return alias, tried
    return None, tried


def run(args, input=None, cache=None, prog="altention dc", repo=HOME_REPO, where=None, timeout=RUN_SECS, **io):
    """dcctl `args` on the machine connected to Discord, run once: the CompletedProcess (`io`: subprocess.run's
    stdout and stderr). `input` (bytes) is its stdin; None gives it none. NoBot when there is no machine, no way
    there, the line dropped while it ran (255: maybe done, not sent again), or it ran longer than `timeout` seconds
    over SSH (cut then, said so: maybe done, never taken for done, not sent again; C-125). `where`: bot_machine()'s
    answer, when the caller asked already."""
    name, m, here = where or bot_machine(cache, prog)
    stdin = {"input": input} if input is not None else {"stdin": subprocess.DEVNULL}
    if here:
        return subprocess.run([local_dcctl(this_engine(name), repo)] + list(args), **stdin, **io)
    alias, tried = way(m)
    if alias is None:
        raise NoBot("連不上 %s（試過 %s）" % (name, "、".join(tried)) if tried else "連不上 %s（機器清單裡沒有它的 ssh 別名）" % name)
    try:
        r = subprocess.run(SSH + [alias, " ".join([HANDED + "=1", dcctl_on(m)] + [quote(a) for a in args])],
                           timeout=timeout, **stdin, **io)
    except subprocess.TimeoutExpired:  # ssh killed by subprocess.run: what it did over there is not known
        took = "%d 分鐘" % (timeout // 60) if timeout >= 60 and timeout % 60 == 0 else "%d 秒" % timeout
        raise NoBot("連到 %s 的指令 %s還沒結束（經 %s），已經停掉；指令可能已經做了，沒有重送" % (name, took, alias))
    if r.returncode == 255:
        raise NoBot("連到 %s 的線斷了（經 %s），指令可能已經做了，沒有重送" % (name, alias))
    return r


def hand_over(argv, stdin=None):
    """dcctl's first step on any machine: when the bot runs elsewhere, the whole command goes to that machine's dcctl
    and its exit status and output come back as they are; this machine's own settings (a token left here) are never
    read. Returns on the bot's machine (dcctl goes on here); exits otherwise. stdin is read only for the commands
    that read it (STDIN_COMMANDS): any other command must not wait on a pipe nobody closes."""
    if not argv or argv[0] in ("-h", "--help", "help"):
        return
    try:
        name, m, here = bot_machine(prog="dcctl")
    except NoBot as e:
        sys.exit("dcctl：%s" % e)
    if here:
        return
    if os.environ.get(HANDED):  # sent here as the bot's machine, and this machine names another: two lists disagree
        sys.exit("dcctl：這個指令是別台轉來的，但這台認為機器人在 %s；沒有再轉出去（兩台的機器清單要對一下）" % name)
    data = None
    if argv[0] in STDIN_COMMANDS:
        stdin = stdin or sys.stdin
        data = stdin.buffer.read() if hasattr(stdin, "buffer") else stdin.read().encode("utf-8")
    try:
        r = run(argv, data, prog="dcctl", where=(name, m, here))
    except NoBot as e:
        sys.exit("dcctl：%s" % e)
    sys.exit(r.returncode)


def reach():
    """For doctor on a machine that does not run the bot: can dcctl hand its commands over? {"bot", "here", "way" (the
    alias that answered, or None), "tried", "dcctl" (that machine has it)}, or {"error"} when no machine is named."""
    try:
        name, m, here = bot_machine(prog="doctor")
    except NoBot as e:
        return {"error": str(e)}
    if here:
        return {"bot": name, "here": True}
    alias, tried = way(m)
    out = {"bot": name, "here": False, "way": alias, "tried": tried}
    if alias:
        try:
            out["dcctl"] = subprocess.run(SSH + [alias, "test -f " + dcctl_on(m)], stdin=subprocess.DEVNULL,
                                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=WAY_SECS).returncode == 0
        except subprocess.TimeoutExpired:
            out["dcctl"] = None
    return out


if __name__ == "__main__":
    if sys.argv[1:] != ["--reach"]:
        sys.exit("用法：botside.py --reach（doctor 用：這台的 dcctl 轉不轉得到機器人那台，JSON）")
    print(json.dumps(reach(), ensure_ascii=False))
