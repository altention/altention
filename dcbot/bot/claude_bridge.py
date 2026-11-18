"""Run one Claude Code turn for a Discord channel, keeping a persistent session per channel."""
import asyncio
import json
import os
import uuid

from altention.connectors.claude_code import launch as cc_launch

from . import cc_health, config

def deny():
    """Claude Code's own deny rules for the installation's secrets (they hold even in bypassPermissions mode), from
    the one list (altention/secrets.py); the guard hook (dchook --guard) is in force as well, for every Claude Code."""
    from altention import secrets
    return secrets.deny_rules()


def settings(dirs=()):
    """--settings of a print turn: the deny rules, and the folders it reads without asking besides its working
    directory (permissions.additionalDirectories, never --add-dir: cc_launch.command() says why)."""
    permissions = {"deny": deny()}
    if dirs:
        permissions[cc_launch.FOLDERS] = list(dirs)
    return json.dumps({"permissions": permissions})


class TurnResult(object):
    def __init__(self, text, session_id, cost=0.0, tools=None, error=None, duration=0.0):
        self.text, self.session_id, self.cost = text, session_id, cost
        self.tools, self.error, self.duration = tools or {}, error, duration
        self.broken = False  # claude ran and exited 0, yet gave no result event (C-162: layer 2 itself broken)
        self.not_started = False  # claude exited non-zero, killed by nobody (C-162 section 3.2)


def command(output, session_id, system_note, permission, model=None, dirs=()):
    """(argv, session id) of a print turn: `output` "stream-json" (layer 2) or "text" (layer 3, C-162), the same
    Claude Code file (cc_health.binary()), settings (deny rules; the guard hook holds as ever), permission mode and
    session. `model`: the channel's, as tmux_bridge.with_() gives it (None: config.MODEL)."""
    sid = session_id or str(uuid.uuid4())
    cmd = [cc_health.binary(), "-p", "--model", model or config.MODEL, "--output-format", output]
    if output == "stream-json":
        cmd.append("--verbose")
    cmd += ["--permission-mode", permission, "--settings", settings(dirs)]
    cmd += ["--append-system-prompt", system_note]
    cmd += ["--session-id", sid] if session_id is None else ["--resume", sid]
    return cmd, sid


async def started(cmd, prompt, cwd, channel_id, procs, key):
    env = dict(os.environ, DCBOT_ROOT=config.ROOT, DCBOT_CHANNEL=str(channel_id), **cc_health.pinned_env())  # (C-162)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        cwd=cwd, env=env, limit=64 * 1024 * 1024)
    if procs is not None and key is not None:
        procs[key] = proc
    proc.stdin.write(prompt.encode("utf-8"))
    await proc.stdin.drain()
    proc.stdin.close()
    return proc


async def run_turn(prompt, session_id, cwd, system_note, permission, on_progress=None, procs=None, key=None, channel_id="",
                   dirs=(), model=None):
    """Send `prompt` into the channel's session. Creates the session on first use. `permission`: the permission
    mode, as the caller chose it (tmux_bridge.permission_of(); never one this module picks: C-121).
    on_progress(str) is called with short tool-use notes while Claude works. `dirs`: what it reads without asking
    besides `cwd` (the channel's attachments folder: attachments.folders(), C-151). `model`: the channel's."""
    cmd, sid = command("stream-json", session_id, system_note, permission, model, dirs)
    proc = await started(cmd, prompt, cwd, channel_id, procs, key)

    tools, final = {}, None
    try:
        while True:
            line = await asyncio.wait_for(proc.stdout.readline(), timeout=config.TURN_TIMEOUT)
            if not line:
                break
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "assistant":
                for b in (ev.get("message") or {}).get("content") or []:
                    if b.get("type") == "tool_use":
                        name = b.get("name", "?")
                        tools[name] = tools.get(name, 0) + 1
                        if on_progress:
                            inp = b.get("input") or {}
                            hint = inp.get("description") or inp.get("command") or inp.get("file_path") or ""
                            await on_progress("%s %s" % (name, str(hint)[:80]))
            elif ev.get("type") == "result":
                final = ev
        await proc.wait()
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()  # (reaped, not left a zombie)
        return TurnResult("", sid, tools=tools, error="超過 %d 秒，已中止" % config.TURN_TIMEOUT)
    finally:
        if procs is not None and key is not None:
            procs.pop(key, None)
    if final is None:
        err = (await proc.stderr.read()).decode("utf-8", "replace")[-500:]
        if proc.returncode and proc.returncode < 0:
            err = "已被中止"
        got = TurnResult("", sid, tools=tools, error=err or "Claude 沒有回應（rc=%s）" % proc.returncode)
        got.broken = proc.returncode == 0  # (killed by !stop or the time limit: not 0, and returned above)
        got.not_started = bool(proc.returncode and proc.returncode > 0)  # claude itself exited non-zero (section 3.2)
        return got
    return TurnResult(final.get("result") or "", final.get("session_id") or sid,
                      cost=float(final.get("total_cost_usd") or 0), tools=tools,
                      error=final.get("result") if final.get("is_error") else None,
                      duration=(final.get("duration_ms") or 0) / 1000.0)


async def run_text(prompt, session_id, cwd, system_note, permission, procs=None, key=None, channel_id="", dirs=(),
                   model=None):
    """Layer 3 (C-162): the same turn with `--output-format text`. The reply is stdout as a whole; no tool progress,
    no cost. Exit status 0 with nothing written is no reply (an error: Claude Code cannot be run this way either)."""
    cmd, sid = command("text", session_id, system_note, permission, model, dirs)
    proc = await started(cmd, prompt, cwd, channel_id, procs, key)
    t0 = asyncio.get_running_loop().time()
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=config.TURN_TIMEOUT)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()  # (reaped, not left a zombie)
        return TurnResult("", sid, error="超過 %d 秒，已中止" % config.TURN_TIMEOUT)
    finally:
        if procs is not None and key is not None:
            procs.pop(key, None)
    text = out.decode("utf-8", "replace").strip()
    took = asyncio.get_running_loop().time() - t0
    if proc.returncode or not text:
        why = err.decode("utf-8", "replace")[-500:]
        if proc.returncode and proc.returncode < 0:
            why = "已被中止"
        return TurnResult("", sid, error=why or "Claude 沒有回應（rc=%s）" % proc.returncode, duration=took)
    return TurnResult(text, sid, duration=took)
