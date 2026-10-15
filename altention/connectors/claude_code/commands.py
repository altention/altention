"""What a Claude Code can be asked to do with a "/": its commands and skills, asked from Claude Code itself, and
the skills' descriptions, read where it keeps them. Which of them get a menu entry, and under what words, is the
caller's (the bot's catalog.py); this came from there as it was (stage 3b step 1.9).

`claude -p --output-format stream-json` prints an "init" line naming every slash command and skill before it does
anything else; the process is killed right there, so no request is made."""
import asyncio
import glob
import json
import os
import re
import time

from . import sessions


async def ask(claude, cwd, env, timeout=60):
    """{"slash_commands": [...], "skills": [...]} for a session started in `cwd`, or None. `claude`: the program;
    `env`: the environment it is started with (the caller's: what must not reach a helper is the caller's to know)."""
    try:
        proc = await asyncio.create_subprocess_exec(
            claude, "-p", "x", "--model", "haiku", "--output-format", "stream-json", "--verbose",
            "--no-session-persistence", cwd=cwd, env=env, stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL, limit=4 * 1024 * 1024)
    except OSError:
        return None
    try:
        end = time.time() + timeout
        while time.time() < end:
            line = await asyncio.wait_for(proc.stdout.readline(), max(1, end - time.time()))
            if not line:
                return None
            try:
                d = json.loads(line.decode("utf-8", "replace"))
            except ValueError:
                continue
            if d.get("type") == "system" and d.get("subtype") == "init":
                return {"slash_commands": d.get("slash_commands") or [], "skills": d.get("skills") or []}
    except asyncio.TimeoutError:
        return None
    finally:
        try:
            proc.kill()  # before it asks the model anything
        except ProcessLookupError:
            pass
        await proc.wait()
    return None


def skill_descriptions(cwd):
    """{skill name: description} from the SKILL.md files Claude Code would load for `cwd`."""
    home = sessions.config_dir()
    paths = []
    for pattern in ("skills/*/SKILL.md", "skills/*/*/SKILL.md", "plugins/*/*/*/skills/*/SKILL.md",
                    "plugins/*/*/skills/*/SKILL.md"):
        paths += glob.glob(os.path.join(home, pattern))
    paths += glob.glob(os.path.join(cwd, ".claude", "skills", "*", "SKILL.md"))
    out = {}
    for path in paths:
        if "/.trash/" in path:
            continue
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                head = f.read(6000)
        except OSError:
            continue
        if not head.startswith("---"):
            continue
        front = head.split("---", 2)[1] if head.count("---") >= 2 else ""
        name = re.search(r"^name:\s*(.+)$", front, re.M)
        desc = re.search(r"^description:\s*(.+(?:\n[ \t]+.+)*)", front, re.M)
        if desc:
            key = (name.group(1) if name else os.path.basename(os.path.dirname(path))).strip().strip("\"'")
            out.setdefault(key, " ".join(desc.group(1).split()).strip("\"'>| "))
    return out


def short(name):
    """A skill's name without its plugin: "anthropic-skills:deep-research" -> "deep-research"."""
    return name.rsplit(":", 1)[-1]


# Slash commands that do harm when typed into a conversation by someone who is not at its terminal: which, and what
# the harm is, as a code (the words for it are the reader's). REFUSED: never typed in; CONFIRM: only after the owner
# has said yes. From the bot's two tables as they were (stage 3b step 2.1).
LOGS_OUT, ENDS, LEAVES_PANE, RESTORE_PANEL = "logs_out_everything", "ends_conversation", "leaves_pane", "restore_panel"
REFUSED = {"/logout": LOGS_OUT}
CONFIRM = {"/exit": ENDS, "/quit": ENDS, "/background": LEAVES_PANE, "/fork": LEAVES_PANE, "/teleport": LEAVES_PANE,
           "/remote-control": LEAVES_PANE, "/rewind": RESTORE_PANEL, "/checkpoint": RESTORE_PANEL}


def command_of(text):
    """The slash command a text to be typed begins with, lower case ("/exit" of " /Exit now"), or "" when it is
    not one."""
    text = text if isinstance(text, str) else ""
    return text.split()[0].lower() if text.lstrip().startswith("/") else ""
