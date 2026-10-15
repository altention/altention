"""Opening a conversation of Claude Code: the command line, its settings file, and which permission modes there are.
The names of the flags are here and nowhere else. Nothing here starts anything, reads a screen or knows of tmux: the
caller runs what command() gives, wherever it keeps its agents. (The dialogs Claude Code opens with, and the key that
answers them: screen.startup_key().)"""
import json
import os

from . import hooks

# The values of --permission-mode, as `claude --help` of 2.1.296 lists them (read on two machines, 2026-10-13).
# A version that names them otherwise is told apart here, not by the caller.
PERMISSIONS = ("acceptEdits", "auto", "bypassPermissions", "manual", "dontAsk", "plan")
# TODO(stage 6, D-03): the caller's last resort is still BYPASS (this installation's way since the first day); the
# public version must not let everything through without being told to (DESIGN section 11).
BYPASS = "bypassPermissions"


def permissions():
    """The permission modes a conversation can be opened with."""
    return PERMISSIONS


def command(binary, note, model, permission, settings=None, resume=None):
    """The command line that opens a conversation: `binary` the program, `note` what is added to its system prompt,
    `model` and `permission` as the caller chose them (permission: one of permissions(); ValueError for another),
    `settings` the file of settings() when the conversation is to report through it (None: it reports by what the
    machine has installed), `resume` the conversation to go on with (None: a new one)."""
    if permission not in PERMISSIONS:
        raise ValueError(permission)
    cmd = [binary, "--model", model]
    if settings:
        cmd += ["--settings", settings]
    cmd += ["--permission-mode", permission, "--append-system-prompt", note]
    return cmd + (["--resume", resume] if resume else [])


def settings(deny, hook):
    """What the settings file of the caller's own conversations holds: what they may never do (`deny`: permission
    rules) and the hooks that report (`hook`: the command line, quoted already)."""
    return {"permissions": {"deny": list(deny)}, "hooks": hooks.hooks(hook)}


def write_settings(path, deny, hook):
    """Write settings() to `path` (whole or not at all); a file that says the same already is left as it is.
    True when it was written."""
    text = json.dumps(settings(deny, hook), indent=1)
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
