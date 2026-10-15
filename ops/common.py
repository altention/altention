"""Shared helpers for ops/: read the inventory and run scripts on machines (locally or over SSH)."""
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))
from altention import config as altconfig  # noqa: E402

# what to do when a machine has no crontab command: install.sh's todo and doctor's fix say the same
# and when cron is there but its daemon is not running: only the second half
START_CRON = "讓 cron 的服務開著（systemctl enable --now cron 或 cronie；WSL 等沒有 systemd 的：sudo service cron start）"
INSTALL_CRON = "先裝 cron（Debian/Ubuntu：sudo apt install cron；Fedora：sudo dnf install cronie；Arch：sudo pacman -S cronie），" + START_CRON


def cron_running(run=subprocess.run):
    """Linux: whether a cron daemon runs (pgrep -x cron, or crond for cronie and busybox): True or False. None where
    it cannot be told: not Linux (macOS's cron is started by launchd when needed), no pgrep, pgrep failing. WSL and
    containers have the crontab command with no daemon: lines written there never run. doctor's probe does the same."""
    if not sys.platform.startswith("linux"):
        return None
    for name in ("cron", "crond"):
        try:
            rc = run(["pgrep", "-x", name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10).returncode
        except (OSError, subprocess.SubprocessError):
            return None
        if rc == 0:
            return True
        if rc != 1:
            return None
    return False


def inventory():
    """The machine list (the instance's machines.json). Without one: this machine alone, marked "single"."""
    data = dict(altconfig.inventory())
    if not data.get("machines"):
        data["machines"], data["single"] = altconfig.machines(), True
    return data


def engine_raw(m):
    """Where machine `m`'s engine is: this checkout when `m` is reached locally, else its entry's "engine" (default
    ~/altention/engine; a leading ~ is that machine's home)."""
    if "local" in (m.get("ssh") or []):
        return os.path.realpath(os.path.dirname(HERE))
    return altconfig.engine_raw(m)


def engine_on(m):
    """engine_raw(m), ready for a shell command run on `m`."""
    return altconfig.shell_path(engine_raw(m))


# A shell script is read whole before it runs (`sh -c "$(cat)"`), never line by line from stdin as `sh -s` does:
# a command in it that reads stdin (ssh, an installer) would otherwise swallow the rest of the script, and a
# Mac's sh (bash) reads it in small pieces, so it did (2026-10-06). Python reads its whole script first anyway.
WHOLE = 'sh -c "$(cat)"'


def _cmd(alias, interpreter):
    if alias == "local":
        return [interpreter, "-"] if interpreter == "python3" else ["sh", "-c", WHOLE]
    return ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", alias,
            "python3 -" if interpreter == "python3" else WHOLE]


def run_on(machine, script, interpreter="python3", timeout=600):
    """Run `script` (fed on stdin) on the machine, trying its SSH aliases in order.
    Returns (alias used or None, returncode, stdout, stderr)."""
    last = (None, 255, "", "unreachable")
    for alias in machine.get("ssh", []):
        try:
            r = subprocess.run(_cmd(alias, interpreter), input=script, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, universal_newlines=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            last = (alias, 124, "", "timeout")
            continue
        if r.returncode == 255 and alias != "local":  # ssh itself failed: try the next alias
            last = (alias, 255, r.stdout, r.stderr.strip())
            continue
        return alias, r.returncode, r.stdout, r.stderr
    return last


def local_head_status():
    """(this engine's commit, short; why it could not be read). ("", "") for an engine without .git (unpacked from
    an archive: nothing to read, nothing wrong); ("", "讀不到 git：<git's first line>") when there is a .git and git
    fails all the same (no git, dubious ownership, a broken .git): never taken for an archive. git's complaint is
    never printed above doctor's report (#code-review-2, #architecture-review 2026-10-14)."""
    root = os.path.dirname(HERE)
    if not os.path.lexists(os.path.join(root, ".git")):
        return "", ""
    try:
        r = subprocess.run(["git", "-C", root, "rev-parse", "--short", "HEAD"], stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, universal_newlines=True, timeout=60)
    except (OSError, subprocess.SubprocessError) as e:
        return "", "讀不到 git：%s" % e
    if r.returncode != 0 or not r.stdout.strip():
        first = next((x.strip() for x in r.stderr.splitlines() if x.strip()), "結束碼 %d" % r.returncode)
        return "", "讀不到 git：%s" % first[:200]
    return r.stdout.strip(), ""


def local_head():
    """This engine's commit, short, or "" (local_head_status says why)."""
    return local_head_status()[0]
