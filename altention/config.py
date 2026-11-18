"""Where things are: the instance (the owner's private settings and data), the machines, this machine, the bot.

Every part of the engine asks here instead of hard-coding paths (DESIGN.md 4.2, 5.3). Standard library only,
Python 3.8: the system python of every machine runs it, from cron, hooks and tools/dcremote.

- The instance: $ALT_INSTANCE, else the path in ~/.config/altention/instance, else $ALT_ROOT/instance
  (default ~/altention/instance). Cron jobs and hooks have no environment of the owner's, hence the file.
- No machine list at all: one machine, this one. Nothing here needs SSH or a second machine.
- A file that is there but cannot be read (half written, edited badly), or a pointer to a folder that is not
  there, raises ConfigError: falling back quietly would turn a broken list into "this machine alone" and leave
  every other machine's channels silent without a word.
- A file whose "format" is newer than FORMAT is still read; a warning goes to stderr (an engine rolled back
  by the supervisor must keep working with the settings of a newer one).
"""
import json
import os
import socket
import sys
import time

ENGINE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class ConfigError(Exception):
    """A settings file or pointer that exists but is broken."""
FORMAT = 1
_warned = set()
_failed = {}  # path -> (its mtime, when): a broken file is not read again (nor waited for) for FAILED_FOR seconds
FAILED_FOR = 5


def config_dir():
    return os.path.expanduser("~/.config/altention")


def root():
    return os.path.expanduser(os.environ.get("ALT_ROOT") or "~/altention")


def instance_dir():
    """The instance folder, or None while this machine has none."""
    named = os.environ.get("ALT_INSTANCE")
    pointer = os.path.join(config_dir(), "instance")
    if not named and os.path.exists(pointer):
        try:
            with open(pointer) as f:
                named = f.readline().strip()
        except OSError as e:
            raise ConfigError("讀不了 %s：%s" % (pointer, e))
    if named == "-":  # this machine has left its instance (ops/instance.py leave): none, whatever is in the root
        return None
    if named:  # said where it is: it has to be there
        path = os.path.expanduser(named)
        if not os.path.isdir(path):
            raise ConfigError("實例資料夾不存在：%s（由 %s 指定）" % (path, "ALT_INSTANCE" if os.environ.get("ALT_INSTANCE") else pointer))
        return os.path.realpath(path)
    default = os.path.join(root(), "instance")
    return os.path.realpath(default) if os.path.isdir(default) else None


def read_json(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def load(name):
    """(data, source) of the instance's settings file `name`. source is "instance", or "none" (data None)."""
    inst = instance_dir()
    for path, source in ((os.path.join(inst, name) if inst else None, "instance"),):
        if path and os.path.exists(path):
            seen = _failed.get(path)
            if seen and seen[0] == _mtime(path) and time.time() - seen[1] < FAILED_FOR:
                raise ConfigError("%s 存在但讀不出來（不是 JSON 物件）；不會改用別的設定" % path)
            data = read_json(path)
            for _ in range(2):  # caught while git swaps the file: read again before calling it broken
                if isinstance(data, dict):
                    break
                time.sleep(0.2)
                data = read_json(path)
            if not isinstance(data, dict):
                _failed[path] = (_mtime(path), time.time())
                raise ConfigError("%s 存在但讀不出來（不是 JSON 物件）；不會改用別的設定" % path)
            _failed.pop(path, None)
            fmt = data.get("format")
            if isinstance(fmt, int) and fmt > FORMAT and path not in _warned:
                _warned.add(path)
                sys.stderr.write("altention: %s 的格式是 %d，比這版引擎認得的 %d 新；照樣讀，新的欄位會被忽略\n"
                                 % (path, fmt, FORMAT))
            return data, source
    return None, "none"


def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return None


def inventory():
    """The machine list ({"machines": {...}}), {} without one."""
    return load("machines.json")[0] or {}


def machines():
    """{name: machine} from the list; one machine (this one, reached locally) when there is no list."""
    found = inventory().get("machines") or {}
    return found if found else {this_machine(): {"ssh": ["local"], "roles": []}}


def stated_name():
    """The name this machine was given (macOS hostnames change with the network), or None."""
    for path in (os.path.join(config_dir(), "host"),):
        try:
            with open(path) as f:
                name = f.readline().strip()
            if name:
                return name
        except OSError:
            continue
    return None


def hostname():
    return socket.gethostname().split(".")[0]


def name_of(host):
    """The list's name for a machine that calls itself `host` (its own name, or its `hostname`)."""
    host = (host or "").lower()
    for name, m in (inventory().get("machines") or {}).items():
        if host in [x.lower() for x in (name, m.get("hostname") or "") if x]:
            return name
    return None


def this_machine():
    """This machine's name: the one it was given, as the list knows it; else its hostname."""
    given = stated_name()
    return name_of(given) or name_of(hostname()) or given or hostname()


BOT_TOKEN = "~/.config/altention/secrets/core/discord-token"  # the bot's token (C-110; dcbot/bot/envfile.py reads it)
BOT_OLD_SETTINGS = "~/.config/dcbot/env"  # COMPAT: ~/.config/dcbot/env 裡還有機器人設定的機器；0.x 期間都還讀，1.0 之前拿掉


def bot_here():
    """Does the Discord bot run on this machine (its token is here, in the new place or the old settings file)?
    Whether a file is there only: never opened."""
    return any(os.path.exists(os.path.expanduser(p)) for p in (BOT_TOKEN, BOT_OLD_SETTINGS))


def bot_token_there():
    """Is the bot's token on this machine: the core's secret there and not empty, or (COMPAT) the old settings file
    with a DISCORD_TOKEN line that has a value? Nothing of the value is kept or said. The one answer for install and
    the supervisor (C-163: a standby without it only tells; #code-review-2 on f47de47)."""
    try:
        if os.path.getsize(os.path.expanduser(BOT_TOKEN)) > 0:
            return True
    except OSError:
        pass
    try:
        with open(os.path.expanduser(BOT_OLD_SETTINGS), encoding="utf-8", errors="replace") as f:
            for line in f:
                key, eq, value = line.strip().partition("=")
                if eq and key.strip() in ("DISCORD_TOKEN", "export DISCORD_TOKEN") and value.strip().strip("\"'"):
                    return True
    except OSError:
        pass
    return False


def bot_machine():
    """(name, machine, here) of the machine that runs the bot: the list's entry with the role dcbot, here when its
    name is this machine's (this_machine()). "local" in an entry's ssh only says it is reached from itself, never
    who it is: with the list the examples teach, every machine would otherwise take itself for the bot's (C-57 U5,
    manager 2026-10-20). With no such entry: this one if the bot's settings are here; (None, None, False) if
    neither. A machine whose name the list does not know (known_here() false) cannot tell by name: an entry reached
    "local" is taken as here, so that its posts still go (U5's cards and leading are not done then)."""
    try:
        me = this_machine()
        inventory()
    except ConfigError:
        if bot_here():  # the list is broken but the bot is plainly here: posts still go (doctor reports the list)
            return stated_name() or hostname(), {"ssh": ["local"], "roles": ["dcbot"]}, True
        raise
    moved = bot_host()
    if moved and moved in (inventory().get("machines") or {}):  # C-163: the bot runs elsewhere since a takeover
        entry = (inventory().get("machines") or {})[moved]
        return moved, ({"ssh": ["local"], "roles": entry.get("roles", [])} if moved == me else entry), moved == me
    listed = [(name, m) for name, m in (inventory().get("machines") or {}).items() if "dcbot" in m.get("roles", [])]
    if listed:
        if any(name == me for name, _ in listed):
            return me, {"ssh": ["local"], "roles": ["dcbot"]}, True
        if not known_here():
            mine = [(name, m) for name, m in listed if "local" in m.get("ssh", [])]
            if mine:
                return mine[0][0], {"ssh": ["local"], "roles": ["dcbot"]}, True
        return listed[0][0], listed[0][1], False  # the list says it is another machine: believe it (the bot's
        # settings found here as well are reported by doctor)
    if bot_here():
        return me, {"ssh": ["local"], "roles": ["dcbot"]}, True
    return None, None, False


def bot_host_path():
    return os.path.join(os.path.expanduser("~"), ".local", "share", "altention", "bot-host")  # (at call time: HOME)


def bot_host():
    """C-163 section 4.4: the machine that runs the bot since a takeover, as the bot that took over wrote it on this
    machine ({"machine", "epoch"}); None without one (the machine list says, as always). Never in the machine list
    itself: that is in git, and goes everywhere."""
    try:
        with open(bot_host_path()) as f:
            got = json.load(f)
    except (OSError, ValueError):
        return None
    return got.get("machine") if isinstance(got, dict) and isinstance(got.get("machine"), str) else None


BOT_HOST_NEW = 600  # C-188: a bot-host written this recently (seconds) means the bot is changing machines


def bot_host_moving(now=None):
    """C-188 (manager 11-16): this machine's bot-host was written within BOT_HOST_NEW: the bot is changing machines
    (a takeover, a failback, or one waiting for posts on their way), so a post queued here and not answered yet is
    not "the bot is down"."""
    try:
        return (time.time() if now is None else now) - os.path.getmtime(bot_host_path()) <= BOT_HOST_NEW
    except OSError:
        return False


def write_bot_host(machine, epoch):
    """Write this machine's bot-host file ({"machine", "epoch"}; dcremote bot-host does the same on other machines).
    An older epoch than the one there does not overwrite it: False then."""
    path = bot_host_path()
    try:
        with open(path) as f:
            now = json.load(f)
    except (OSError, ValueError):
        now = {}
    if isinstance(now, dict) and isinstance(now.get("epoch"), int) and now["epoch"] > int(epoch):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path + ".tmp", "w") as f:
        json.dump({"machine": machine, "epoch": int(epoch)}, f)
    os.replace(path + ".tmp", path)
    return True


def dcremote_owner_path():
    """dcremote's record of the biggest bot epoch this machine has seen (C-163 1.4: fencing); the same file as
    dcbot/tools/dcremote owner_path()."""
    return os.path.join(os.path.expanduser("~"), ".local", "share", "altention", "dcremote-owner")  # (at call time)


def raise_dcremote_owner(epoch):
    """The bot became the bot here with `epoch`: this machine's own dcremote-owner says at least that much, so a
    request of an older bot to this machine is refused at once, not only after another machine's request with the
    new epoch came by (C-163 drill 11-08, item 10: the machine that took over still had the old bot's epoch for
    five minutes; manager). Never lowered. True when written."""
    path = dcremote_owner_path()
    try:
        with open(path) as f:
            now = int(f.read().strip() or 0)
    except (OSError, ValueError):
        now = 0
    if now >= int(epoch):
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = "%s.%d" % (path, os.getpid())
    with open(tmp, "w") as f:
        f.write(str(int(epoch)))
    os.replace(tmp, path)
    return True


def known_here():
    """Does the machine list know this machine by name (its given name or hostname)? False without a list entry."""
    try:
        return this_machine() in (inventory().get("machines") or {})
    except ConfigError:
        return False


def stray_bot_settings():
    """This machine has the bot's settings although the list puts the bot elsewhere (for doctor)."""
    name, _, here = bot_machine()
    return bot_here() and name is not None and not here


def ssh_aliases(machine):
    return [a for a in (machine or {}).get("ssh", []) if a != "local"]


def settings():
    """The instance's settings (plugins.json), {} without one."""
    return load("plugins.json")[0] or {}


def setting(path, default=None):
    """One setting by its dotted path, e.g. setting("core.notify_channel", "notifications")."""
    node = settings()
    for part in path.split("."):
        if not isinstance(node, dict) or part not in node:
            return default
        node = node[part]
    return node


DEFAULT_ENGINE = "~/altention/engine"  # where the engine is on a machine whose entry in the list does not say


def engine_raw(m):
    """The engine's folder on machine `m` (its entry in the machine list) as written there ("engine"; a leading ~
    is that machine's home), else DEFAULT_ENGINE. For a shell over there use engine_on()."""
    path = m.get("engine") if isinstance(m, dict) else None
    return path if isinstance(path, str) and path.strip() else DEFAULT_ENGINE


def shell_quote(word):
    """`word` as one word for a shell on another machine, always in single quotes. Not shlex.quote: it leaves bare
    what it thinks safe, and zsh reads a bare word starting with = as the path of a command ("=name" -> "zsh: name
    not found"; a remote tmux session name, 2026-10-07)."""
    return "'" + str(word).replace("'", "'\"'\"'") + "'"


def shell_path(path):
    """`path` as a shell on that machine should be given it: quoted, but a leading ~/ left out of the quotes so
    that the shell there expands it to its own home ("~/my engine" -> ~/'my engine')."""
    if path == "~":
        return "~"
    if path.startswith("~/"):
        return "~/" + shell_quote(path[2:])
    return shell_quote(path)


def engine_on(m):
    """The engine's folder on machine `m`, ready for a shell command run there (engine_raw, shell_path)."""
    return shell_path(engine_raw(m))

