"""Opening a conversation of Claude Code: the command line, its settings file, and which permission modes there are.
The names of the flags are here and nowhere else. Nothing here starts anything, reads a screen or knows of tmux: the
caller runs what command() gives, wherever it keeps its agents. (The dialogs Claude Code opens with, and the key that
answers them: screen.startup_key().)"""
import hashlib
import json
import os

from . import hooks

# The values of --permission-mode, as `claude --help` of 2.1.296 lists them (read on two machines, 2026-10-13).
# A version that names them otherwise is told apart here, not by the caller.
PERMISSIONS = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")


def permissions():
    """The permission modes a conversation can be opened with."""
    return PERMISSIONS


def command(binary, note, model, permission, settings=None, resume=None, dirs=()):
    """The command line that opens a conversation: `binary` the program, `note` what is added to its system prompt,
    `model` and `permission` as the caller chose them (permission: one of permissions(); ValueError for another),
    `settings` the file of settings() when the conversation is to report through it (None: it reports by what the
    machine has installed), `resume` the conversation to go on with (None: a new one), `dirs` folders it may read
    besides its working directory without asking (each one there already: Claude Code drops a folder that is not).
    The folders go in its settings (permissions.additionalDirectories), never by --add-dir: from 2.1.326 Claude Code
    asks whether to trust a folder given by --add-dir that is not in a folder it trusts already, and the
    conversation waits on that question; one in the settings it reads without asking (both tried on 2.1.299 and
    2.1.326, 2026-11-17). Only one --settings counts (the last), so with a settings file the folders go into a copy
    of it (with_folders()); without one, --settings carries the folders alone (folders_only())."""
    if permission not in PERMISSIONS:
        raise ValueError(permission)
    cmd = [binary, "--model", model]
    if dirs and settings:
        try:
            settings = with_folders(settings, dirs)
        except (OSError, ValueError) as e:  # the conversation is opened all the same, and asks before it reads them
            print("launch: the folders not put in a copy of %s (%s): opened without them" % (settings, e), flush=True)
    elif dirs:
        settings = folders_only(dirs)
    if settings:
        cmd += ["--settings", settings]
    cmd += ["--permission-mode", permission]
    cmd += ["--append-system-prompt", note]
    return cmd + (["--resume", resume] if resume else [])


def settings(deny, hook):
    """What the settings file of the caller's own conversations holds: what they may never do (`deny`: permission
    rules) and the hooks that report (`hook`: the command line, quoted already)."""
    return {"permissions": {"deny": list(deny)}, "hooks": hooks.hooks(hook)}


def folders_only(dirs):
    """--settings given as JSON, the folders alone (a conversation on another machine: its hooks are the ones that
    machine has installed). One word with no space in it but the folders' own (tmux_bridge.foreign_window() tells a
    window by its --settings starting with "{")."""
    return json.dumps({"permissions": {FOLDERS: list(dirs)}}, separators=(",", ":"), ensure_ascii=False)


FOLDERS = "additionalDirectories"


def with_folders(path, dirs):
    """The path of a copy of the settings file `path` with the folders `dirs` in it, beside it and named by them
    (<name>.<12 hex>.json), written from the file as it is now each time (whole or not at all; one that says the
    same already is left as it is). OSError or ValueError when `path` cannot be read as settings, or the copy
    cannot be written."""
    with open(path, encoding="utf-8") as f:
        d = json.load(f)
    if not isinstance(d, dict) or not isinstance(d.get("permissions", {}), dict):
        raise ValueError("not settings")
    d.setdefault("permissions", {})[FOLDERS] = list(dirs)
    stem = path[:-5] if path.endswith(".json") else path
    out = "%s.%s.json" % (stem, hashlib.sha1("\0".join(dirs).encode("utf-8")).hexdigest()[:12])
    put(out, json.dumps(d, indent=1))
    return out


def put(path, text):
    """`text` into `path`, whole or not at all; True when written (False: it said that already)."""
    try:
        with open(path, encoding="utf-8") as f:
            if f.read() == text:
                return False
    except (OSError, ValueError):
        pass
    tmp = "%s.%d.tmp" % (path, os.getpid())
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    return True


def write_settings(path, deny, hook):
    """Write settings() to `path` (whole or not at all); a file that says the same already is left as it is.
    True when it was written."""
    return put(path, json.dumps(settings(deny, hook), indent=1))
