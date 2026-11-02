"""A plugin rewrites Claude's final reply before it is posted (C-71b): a drawing plugin draws what Discord cannot
show (formulas, tables, mermaid) and gives back the reply as Discord messages, text and pictures.

A plugin declares it in its plugin.json (altention/plugin.py checks the shape):
    "rewrite": {"command": "{python} \\"{plugin}/bin/draw\\" - --for-bot -o {out}", "timeout": 30, "when": "<regex>"}
- command: plugin.expand()'s substitutions, and {out}: a file path the bot gives (<state>/rewrite/<token>.png); the
  pictures are <token>-1.png, <token>-2.png … next to it.
- stdin: the whole reply (Markdown, UTF-8, masked already). Exit 0: stdout is one JSON array, one object a Discord
  message, in order: {"text": str (at most 1900 characters), "images": [absolute paths]}. Anything else (another
  exit status, no JSON, another shape, a picture not <token>-*.png in that folder): the reply goes as before (C-71a).
  A message may also have "sources": {picture path (one of its images): what it was made from} (C-91,
  altention/sources.py; the owner gets it with 📝, bot/sources.py); one not of that shape is left out, nothing else.
- timeout: seconds (TIMEOUT when not given, at most MAX_TIMEOUT); past it the plugin's whole process group is killed.
- when: a regex (re.MULTILINE, search) a reply has to match for the plugin to be asked; none: every reply.
Only one plugin's is used: the one with the lowest `order` among those "on" on the bot's machine (a draft, a plugin
turned off or disabled there is never asked). Which it is, is read in a thread (choose()), never in the event loop.
How it is posted, and the order kept, is TmuxBridge's (rewrite_and_post)."""
import asyncio
import json
import os
import re
import shlex
import signal
import time
import traceback
import uuid

from altention.sources import check as srccheck

from . import config

TIMEOUT, MAX_TIMEOUT = 30, 60
SHOW_AFTER = 5        # seconds a rewrite may take before the channel is told it is being laid out
KEEP_FILES = 24 * 3600
chosen = {"spec": None, "said": None}  # spec: {"plugin", "command" (all but {out} filled in), "timeout", "when"}


def out_dir():
    return os.path.join(config.STATE, "rewrite")


def choose(rows=None):
    """Which plugin's rewrite is used, into `chosen` (in a thread: it reads the instance's settings and every
    plugin.json). `rows`: plugin.on_this_machine()'s, for the tests. Returns the spec, or None."""
    try:
        from altention import plugin
        if rows is None:
            rows = plugin.on_this_machine()[0]
        found = [(d.get("order", 0) if isinstance(d.get("order", 0), int) else 0, name, folder, d["rewrite"])
                 for name, folder, d, where, _ in rows
                 if where == "on" and isinstance(d, dict) and isinstance(d.get("rewrite"), dict)]
        spec = None
        if found:
            _, name, folder, r = sorted(found, key=lambda f: (f[0], f[1]))[0]
            hole = "\x00OUT\x00"  # {out} is the bot's, filled in for each reply: kept through expand()
            command = plugin.expand(r["command"].replace("{out}", hole), folder).replace(hole, "{out}")
            timeout = r.get("timeout", TIMEOUT)
            timeout = timeout if isinstance(timeout, int) and not isinstance(timeout, bool) and 0 < timeout <= MAX_TIMEOUT \
                else TIMEOUT
            when = re.compile(r["when"], re.MULTILINE) if isinstance(r.get("when"), str) and r["when"] else None
            spec = {"plugin": name, "command": command, "timeout": timeout, "when": when, "others": len(found) - 1}
    except Exception as e:
        print("[rewrite] the plugins' rewrite cannot be read (%s: %s): replies go as before" % (type(e).__name__, e), flush=True)
        spec = None
    said = (spec or {}).get("plugin")
    if said != chosen["said"]:
        chosen["said"] = said
        print("[rewrite] %s" % ("replies are rewritten by %s%s" % (said, "（另有 %d 個插件也宣告了 rewrite，只用 order 最小的這個）"
                                                                   % spec["others"] if spec["others"] else "")
                                if said else "no plugin rewrites replies"), flush=True)
    chosen["spec"] = spec
    return spec


def wanted(text):
    """The spec to rewrite `text` with, or None (no plugin, or its `when` does not match)."""
    spec = chosen["spec"]
    if spec is None or (spec["when"] is not None and not spec["when"].search(text or "")):
        return None
    return spec


async def run(spec, text, started=None):
    """(messages, None, token) or (None, why, token): the plugin's messages [(text, [picture paths])] for `text`;
    `token`: what its pictures are named after (drop() them once posted). `started(pid)`: told its process group
    (a session of its own: pid = pgid) once it runs. Never raises."""
    token = uuid.uuid4().hex[:16]
    folder = out_dir()
    proc = None
    try:
        os.makedirs(folder, exist_ok=True)
        argv = shlex.split(fill_out(spec["command"], os.path.join(folder, token + ".png")))
        proc = await asyncio.create_subprocess_exec(*argv, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
                                                    stderr=asyncio.subprocess.PIPE, start_new_session=True)
        if started is not None:
            try:
                started(proc.pid)
            except Exception:
                traceback.print_exc()
        try:
            out, err = await asyncio.wait_for(proc.communicate((text or "").encode("utf-8")), spec["timeout"])
        except asyncio.TimeoutError:
            kill(proc)
            await proc.wait()
            return None, "%d 秒沒有結束" % spec["timeout"], token
        if proc.returncode != 0:
            tail = [l for l in err.decode("utf-8", "replace").splitlines() if l.strip()][-1:]
            return None, "結束碼 %d%s" % (proc.returncode, "：" + tail[0][:200] if tail else ""), token
        return read(out, folder, token)
    except Exception as e:
        traceback.print_exc()
        if proc is not None and proc.returncode is None:
            kill(proc)
        return None, "%s: %s" % (type(e).__name__, str(e)[:200]), token


def fill_out(command, path):
    """{out} in the command, quoted for the shell; quotes the plugin put around it are taken as its own ("{out}")."""
    return re.sub(r"([\"']?)\{out\}\1", lambda m: shlex.quote(path), command)


def started_at(pid):
    """When process `pid` started, as Linux keeps it (/proc/<pid>/stat field 22, clock ticks since boot), or None
    (no /proc: macOS). What tells the plugin's group from another that got the same number since."""
    try:
        with open("/proc/%d/stat" % pid) as f:
            return int(f.read().rsplit(")", 1)[1].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def stop_left(pgid, start):
    """At the bot's start: the process group of a plugin that was laying a reply out when the bot went down (C-71b),
    killed if it is still there. Only on a plain fact: its leader is the very process recorded (same start time);
    without one (macOS, nothing recorded, gone, another since) nothing is killed. True when killed."""
    if not isinstance(pgid, int) or isinstance(pgid, bool) or pgid <= 1 or start is None or started_at(pgid) != start:
        return False
    try:
        os.killpg(pgid, signal.SIGKILL)
        print("[rewrite] the plugin left laying a reply out (process group %d) is stopped" % pgid, flush=True)
        return True
    except OSError:
        return False


def kill(proc):
    try:
        os.killpg(proc.pid, signal.SIGKILL)  # the plugin's whole group: no headless browser left behind
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except Exception:
            pass


def read(out, folder, token):
    """(messages, None, token) or (None, why, token) from the plugin's stdout."""
    try:
        got = json.loads(out.decode("utf-8"))
    except ValueError:
        return None, "輸出不是 JSON", token
    if not isinstance(got, list) or not got:
        return None, "輸出不是一串訊息", token
    msgs, home = [], os.path.realpath(folder)
    for m in got:
        if not isinstance(m, dict) or not isinstance(m.get("text"), str) or not isinstance(m.get("images", []), list):
            return None, "訊息的格式不對", token
        pics, srcs, given = [], [], m.get("sources") if isinstance(m.get("sources"), dict) else {}
        for p in m.get("images", []):
            real = os.path.realpath(p) if isinstance(p, str) else ""
            name = os.path.basename(real)
            if os.path.dirname(real) != home or not (name.startswith(token + "-") and name.endswith(".png")) \
                    or not os.path.isfile(real):
                return None, "圖片不在機器人給的地方：%s" % str(p)[:200], token
            pics.append(real)
            srcs.append(srccheck(given[p])[0] if isinstance(p, str) and p in given else None)
        if not m["text"] and not pics:
            continue
        msgs.append((m["text"], pics, srcs) if any(srcs) else (m["text"], pics))
    if not msgs:
        return None, "沒有任何內容", token
    return msgs, None, token


def unchanged(msgs):
    """One message, no picture: nothing was drawn (the reply goes as before, C-71a)."""
    return msgs is not None and len(msgs) == 1 and not msgs[0][1]


def drop(token):
    """The pictures of one rewrite, once posted (or given up)."""
    folder = out_dir()
    try:
        for name in os.listdir(folder):
            if name.startswith(token + "-") or name == token + ".png":
                try:
                    os.remove(os.path.join(folder, name))
                except OSError:
                    pass
    except OSError:
        pass


def prune(now=None):
    """Pictures left behind (a restart in the middle), older than KEEP_FILES: the 10-minute chores."""
    now = time.time() if now is None else now
    folder = out_dir()
    try:
        for name in os.listdir(folder):
            path = os.path.join(folder, name)
            try:
                if now - os.path.getmtime(path) > KEEP_FILES:
                    os.remove(path)
            except OSError:
                pass
    except OSError:
        pass
