"""Other machines whose Claude Code sessions can be attached to channels, and how the bot reaches them.

Which machines: those with the role `dcattach` in the machine list (altention.config: the instance's
machines.json; none at all is just this machine). How: SSH with the machine's
aliases in the list's order, the one that last worked tried first (a laptop away from the lab answers on a reverse
tunnel listed after its usual name: 2026-10-07), one shared connection per alias (ControlMaster), so a tmux command over there costs
about as much as one here. On that machine `<its engine>/dcbot/tools/dcremote` does what needs its files; where its
engine is comes from its entry in the machine list ("engine", default ~/altention/engine: engine_of()).

The rule (manager 2026-10-07): a command sent to another machine runs there at most once; the only thing ever
tried again is the `true` that finds the way.
"""
import asyncio
import json
import os
import re

from . import config



def alt():
    """The engine's shared settings (altention/config.py, next to dcbot/)."""
    import sys
    top = os.path.dirname(config.ROOT)
    if top not in sys.path:
        sys.path.insert(0, top)
    from altention import config as altconfig
    return altconfig


problem = None  # the machine list could not be read: (when, why). The bot carries on as one machine and says so.


def broken(e):
    global problem
    import time
    problem = (time.time(), str(e))


def inventory():
    """The machine list. A broken one (altention.config.ConfigError) is not allowed to stop the bot: every
    channel would go silent. Here it is this machine alone, `problem` says why, the bot
    reports it in #audit and !status, and pump() tries again every minute."""
    global problem
    try:  # any failure, not only ConfigError: altention/ is outside dcbot/, so a rollback would not fix it
        data = alt().inventory()
    except Exception as e:
        broken(e)
        return {}
    problem = None
    return data


def this_machine():
    try:
        return alt().this_machine()
    except Exception as e:
        broken(e)
        import socket
        return socket.gethostname().split(".")[0]

DEFAULT_ENGINE = "~/altention/engine"


def altention_cmd(host=None):
    """The altention command on `host` (None or this machine: here) as a full path, for what a Claude there is told
    to run: a bare `altention` needs ~/.local/bin on PATH, which a Bash tool that is not a login shell may not have
    (manager 2026-10-14). A leading ~ is that machine's home; quoted for a shell only when it has to be."""
    if not host or host == this_machine():
        engine, home = os.path.dirname(config.ROOT), os.path.expanduser("~")
        engine = "~" + engine[len(home):] if engine == home or engine.startswith(home + "/") else engine
    else:
        try:
            engine = alt().engine_raw((inventory().get("machines") or {}).get(host) or {})
        except Exception as e:
            broken(e)
            engine = DEFAULT_ENGINE
    if re.search(r"[^\w/~.+-]", engine):
        engine = alt().shell_path(engine)
    return engine + "/bin/altention"


def engine_of(host):
    """The engine's folder on `host` (a machine's name in the list), ready for a shell over there: its "engine"
    entry, else ~/altention/engine. A list that cannot be read gives the default (and is reported like any other time)."""
    try:
        m = (inventory().get("machines") or {}).get(host) or {}
        return alt().engine_on(m)
    except Exception as e:
        broken(e)
        return DEFAULT_ENGINE


def dcremote_command(host):
    return "python3 %s/dcbot/tools/dcremote" % engine_of(host)


def stream_command(host):
    """The one SSH line tmux_bridge.stream() keeps open: bring that machine's engine up to date (and its hooks
    with it), then stream its hook events."""
    return ("cd %s && { if [ -x bin/home-pull ]; then bin/home-pull --quiet; else git pull -q --rebase --autostash; fi; } "
            ">/dev/null 2>&1; python3 dcbot/tools/dchook --update >/dev/null 2>&1; exec python3 dcbot/tools/dcremote stream"
            % engine_of(host))
SSH = ["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=8", "-o", "ServerAliveInterval=15",
       "-o", "ControlMaster=auto", "-o", "ControlPath=/tmp/dcbot-ssh-%C", "-o", "ControlPersist=600"]


def machine_of(hostname):
    """The machine list's name for a machine that calls itself `hostname` (None: this one, or unknown)."""
    try:
        a = alt()
        if (hostname or "").lower() in (a.hostname().lower(), (a.stated_name() or "").lower(), this_machine().lower()):
            return None
        return a.name_of(hostname)
    except Exception as e:
        broken(e)
        return None


LAST_GOOD = {}  # machine name -> the alias that last reached it (tried first next time)
TRIED = {}      # machine name -> the aliases the last failed attempt tried, for the message
on_good = None  # told the machine's name whenever a way to it worked (tmux_bridge: a stream waiting to reconnect
                # tries again at once; 2026-10-07, the Mac back in the lab while its stream slept 300 seconds)


def _good(host, alias):
    LAST_GOOD[host] = alias
    TRIED.pop(host, None)
    if on_good is not None:
        try:
            on_good(host)
        except Exception:
            pass


def aliases(host):
    """The ssh aliases to try for `host`, the list's order with the one that last worked first."""
    m = (inventory().get("machines") or {}).get(host) or {}
    listed = [a for a in m.get("ssh", []) if a != "local"] or [host]
    good = LAST_GOOD.get(host)
    return ([good] if good in listed else []) + [a for a in listed if a != good]


def dropped(host, alias):
    """Was `alias` taken out of `host`'s entry in the list? Only a list that can be read, and has `host`, says so: a
    broken list (pushed into the instance, being fixed) must not cut off the way that works (code-review 2026-10-07)."""
    m = (inventory().get("machines") or {}).get(host)
    return problem is None and m is not None and alias not in m.get("ssh", [])


def unreachable(host):
    """"連不上 host（試過 …）": what the user reads when no alias answered (only what was tried is named)."""
    tried = TRIED.get(host)
    return "連不上 %s（試過 %s）" % (host, "、".join(tried)) if tried else "連不上 %s" % host


def hosts():
    """{machine name: the alias to try first} of the machines sessions can be attached from (not this one)."""
    machines = inventory().get("machines") or {}
    me = this_machine()
    out = {}
    for name, m in machines.items():
        if "dcattach" in m.get("roles", []) and any(a != "local" for a in m.get("ssh", [])) and name != me:
            out[name] = aliases(name)[0]
    return out


# A non-interactive SSH shell on macOS does not have Homebrew's tmux or ~/.local/bin's claude on its PATH
PATH_FIX = 'PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/local/bin:$PATH"; export PATH; '


def argv(host, command, alias=None):
    return SSH + [alias or aliases(host)[0], PATH_FIX + command]


async def _once(host, alias, command, input, timeout):
    """(exit code, stdout, stderr) of `command` sent once by `alias`."""
    try:
        proc = await asyncio.create_subprocess_exec(*argv(host, command, alias), stdin=asyncio.subprocess.PIPE,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE)
        out, err = await asyncio.wait_for(proc.communicate(input.encode("utf-8") if input else None), timeout)
        return proc.returncode, out.decode("utf-8", "replace"), err.decode("utf-8", "replace")
    except asyncio.TimeoutError:
        try:
            proc.kill()
            await proc.wait()  # reaped here: no process left behind, nothing for a closed loop to clean up
        except Exception:
            pass
        return 124, "", "%s 秒沒有結束" % timeout
    except OSError as e:
        return 255, "", str(e)


def said(err):
    """The last lines of what a command wrote to stderr, short enough for a message."""
    lines = [l.strip() for l in (err or "").splitlines() if l.strip()]
    text = " / ".join(lines[-3:])
    return text if len(text) <= 300 else "…" + text[-300:]


async def run(host, command, input=None, timeout=30, err=None):
    """Run a shell command on `host`, sent once: by the alias that last worked, else by the one reach() finds (its
    `true` can be tried every way, a command cannot: ssh says 255 also when the line dropped after the command ran,
    and a keystroke must never go twice; code-review 2026-10-07). A 255 or a command that runs too long: not run
    again; the next command finds its way afresh. (255, "") when nothing connects (TRIED says what was tried).
    `err`, a list: why it failed is appended to it (its stderr, or the way not found), for the message."""
    good = LAST_GOOD.get(host)
    alias = good if good and not dropped(host, good) else await reach(host)
    if alias is None:
        print("remote: %s" % unreachable(host), flush=True)
        if err is not None:
            err.append(unreachable(host))
        return 255, ""
    code, out, stderr = await _once(host, alias, command, input, timeout)
    if code in (124, 255):
        LAST_GOOD.pop(host, None)
        TRIED[host] = [alias]
        print("remote: %s %s via %s; not sent again" % (host, "timed out" if code == 124 else "255", alias), flush=True)
        if err is not None:
            err.append("%s 經 %s：%s" % (host, alias, said(stderr) or ("逾時" if code == 124 else "ssh 結束碼 255")))
        return 255, ""
    _good(host, alias)
    if code != 0 and err is not None and said(stderr):
        err.append(said(stderr))
    return code, out


async def reach(host, timeout=20):
    """The alias that reaches `host` now (the one that last worked first), or None: for the long-lived stream, at
    every (re)connect. `true` can be tried again another way, so here a hanging alias also means "not this way"."""
    tried = []
    for alias in aliases(host):
        tried.append(alias)
        code = (await _once(host, alias, "true", None, timeout))[0]
        if code == 0:
            _good(host, alias)
            return alias
    TRIED[host] = tried
    LAST_GOOD.pop(host, None)
    return None


def quote(word):
    """One word for the shell over there, always in single quotes (altention.config.shell_quote): shlex.quote leaves
    "=name" bare, and zsh, the login shell of some machines, reads it as the path of a command `name`: every tmux
    command naming a session by "=name" failed with "zsh:1: name not found" (2026-10-07, #handover-0)."""
    return "'" + str(word).replace("'", "'\"'\"'") + "'"


async def dcremote(host, *args, input=None, timeout=30, err=None):
    return await run(host, " ".join([dcremote_command(host)] + [quote(a) for a in args]), input, timeout, err)


async def tmux(host, socket, args, err=None):
    """A tmux command on another machine (what tmuxio.tmux does here)."""
    return await run(host, "tmux -u -S %s %s" % (quote(socket), " ".join(quote(a) for a in args)), err=err)
