#!/usr/bin/env python3
"""Plugins' secrets (C-77 G3): one file each, ~/.config/altention/secrets/<plugin>/<name>, the folders 700 and the
file 600, never in git and never in the backups. The secrets guard (altention/secrets.py) refuses any tool call that
reads, writes or names a path in that folder: a best effort against a Claude reading a value, not isolation (any
program run as this user can read the file; the plugin's own programs do).

  altention secret set <plugin> <name>            asked for in this terminal, not echoed
  altention secret set <plugin> <name> --stdin [--via discord]
                                                  the value on stdin (the bot's box, a script); --via says where it
                                                  came from in #audit
  altention secret list [<plugin>]                which declared secrets this machine has (names and lengths only)

Only a name the plugin declares in plugin.json's secrets[] is taken. A value is never printed, logged or put on a
command line; #audit gets one line naming the plugin, the secret and where it came from. HOME is read at call time.
Exit status: 0 stored (or listed); 1 refused (the line says why); 2 wrong usage. Standard library only, Python 3.8.
"""
import getpass
import os
import subprocess
import sys

if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from altention import config, plugin  # noqa: E402
from altention.text import t  # noqa: E402

FOLDER = "~/.config/altention/secrets"  # the guard's DEFAULT names the same folder
MAX = 64 * 1024  # a key or a token, not a file
AUDIT_TIMEOUT = 60
VIA = ("terminal", "discord")


class Refused(Exception):
    pass


def root():
    return os.path.expanduser(FOLDER)


def path(plugin_name, name):
    if not (plugin.check_name(plugin_name) and plugin.check_name(name)):
        raise Refused(t("secret.bad_name"))
    return os.path.join(root(), plugin_name, name)


def plugin_json(plugin_name):
    """plugin_name's plugin.json as this machine finds it (whether or not it runs here: the bot's machine may keep a
    secret for another one). Refused when no plugin.json of that name reads."""
    rows, _ = plugin.on_this_machine()
    for n, _folder, d, _where, _why in rows:
        if n == plugin_name and d is not None:
            return d
    raise Refused(t("secret.no_plugin", plugin=plugin_name))


def declared(plugin_name):
    """The secret names plugin_name's plugin.json declares (plugin_json())."""
    return [s["name"] for s in plugin_json(plugin_name).get("secrets", [])]


def others(plugin_name):
    """The other machines that use plugin_name (plugin.users_elsewhere), [] when that cannot be told."""
    try:
        entry = (config.settings().get("plugins") or {}).get(plugin_name)
        return plugin.users_elsewhere(plugin_json(plugin_name), plugin_name, entry if isinstance(entry, dict) else {})
    except (Refused, config.ConfigError, AttributeError):
        return []


def length(plugin_name, name):
    """How long the stored value is in bytes, None when there is none (only stat: the value is never read here)."""
    try:
        size = os.stat(path(plugin_name, name)).st_size
    except (OSError, Refused):
        return None
    return size or None


def write(plugin_name, name, value):
    """Store `value` (str) as plugin_name's secret `name`: folders 700, a new file made 600 before anything is
    written into it, then moved over the old one. Returns its length. Refused for an undeclared name, an empty or
    too long value."""
    if name not in declared(plugin_name):
        raise Refused(t("secret.not_declared", plugin=plugin_name, name=name))
    value = value[:-1] if value.endswith("\n") else value
    if not value.strip():
        raise Refused(t("secret.empty"))
    data = value.encode("utf-8")
    if len(data) > MAX:
        raise Refused(t("secret.too_long", most=MAX))  # bytes, the same as characters for a key or a token
    target = path(plugin_name, name)
    for folder in (root(), os.path.dirname(target)):
        os.makedirs(folder, mode=0o700, exist_ok=True)
        os.chmod(folder, 0o700)
    tmp = target + ".new"
    if os.path.lexists(tmp):
        os.remove(tmp)
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        os.write(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)
    os.replace(tmp, target)
    return len(data)


def tell_audit(line):
    """One line in #audit (altention dc post): never the value. A failure is said on stderr, not raised."""
    channel = config.setting("dcbot.channels.audit", None) or "audit"
    tool = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "bin", "altention")
    try:
        r = subprocess.run([sys.executable, tool, "dc", "post", str(channel), line], stdin=subprocess.DEVNULL,
                           stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=AUDIT_TIMEOUT)
        ok = r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        ok = False
    if not ok:
        sys.stderr.write(t("secret.audit_failed") + "\n")


def set_cmd(args, stdin=None, ask=None, out=None):
    out = out or sys.stdout
    stdin = stdin or sys.stdin
    via = "terminal"
    if "--via" in args:
        i = args.index("--via")
        via = args[i + 1] if i + 1 < len(args) else ""
        args = args[:i] + args[i + 2:]
    if via not in VIA or len([a for a in args if a != "--stdin"]) != 2:
        return None
    plugin_name, name = [a for a in args if a != "--stdin"]
    if name not in declared(plugin_name):
        raise Refused(t("secret.not_declared", plugin=plugin_name, name=name))
    if "--stdin" in args:
        value = stdin.read()
    elif stdin.isatty():
        value = (ask or getpass.getpass)(t("secret.prompt", plugin=plugin_name, name=name))  # no echo
    else:
        raise Refused(t("secret.no_terminal"))
    size = write(plugin_name, name, value)
    out.write(t("secret.saved", plugin=plugin_name, name=name, size=size) + "\n")
    elsewhere = others(plugin_name)
    if elsewhere:  # #bot-dev of G3b (manager: the box sends nowhere by itself): said, for #setup to do on a yes
        out.write(t("secret.others", plugin=plugin_name, name=name, machines="、".join(elsewhere)) + "\n")
    try:
        machine = config.this_machine()
    except config.ConfigError:  # stored already: the line names no machine rather than saying it failed
        machine = "?"
    tell_audit(t("secret.audit", plugin=plugin_name, name=name, machine=machine, via=t("secret.via_" + via)))
    return 0


def list_cmd(args, out=None):
    out = out or sys.stdout
    rows, _ = plugin.on_this_machine()
    names = [(n, [s["name"] for s in d.get("secrets", [])]) for n, _f, d, _w, _y in rows
             if d is not None and d.get("secrets") and (not args or n in args)]
    for n, secrets in names:
        for s in secrets:
            size = length(n, s)
            out.write("%s/%s：%s\n" % (n, s, t("secret.stored", size=size) if size else t("secret.missing")))
    return 0


def main(argv):
    try:
        if argv[:1] == ["set"]:
            code = set_cmd(argv[1:])
            if code is not None:
                return code
        elif argv[:1] == ["list"]:
            return list_cmd(argv[1:])
    except Refused as e:
        print(t("secret.refused", why=e))
        return 1
    except (OSError, config.ConfigError) as e:
        print(t("secret.refused", why=e))
        return 1
    print(__doc__.split("\n\n")[1])
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
